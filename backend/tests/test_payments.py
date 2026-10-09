"""Règles d'encaissement (add_payment) et intégrité « commande → paiement ».

Complète test_reservation_flow.py : garde-fous par canal, plafonds
d'imputation, et rejets 409 quand le catalogue ou la séance a changé
entre la création du panier et l'encaissement.

Non testés ici (déjà couverts ou volontairement exclus) :
- paiement sur commande annulée, multi-paiements cash avec rendu,
  ANCV excédentaire : test_reservation_flow.py ;
- expiration réelle d'un panier (purge + TTL) : lot dédié ;
- `amount <= 0` : verrouillé par le schéma PaymentCreate (Field gt=0),
  vérifié ici au niveau Pydantic plutôt que via le service.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app.models import (
    ComponentType,
    PaymentMethod,
    PaymentTransaction,
    ProductComponent,
    Reservation,
    ReservationStatus,
    SalesChannel,
    Session,
    Ticket,
    TicketAccess,
)
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
    TicketRead,
)
from app.services.reservation_service import (
    add_payment,
    cancel_reservation,
    create_reservation,
)
from tests.conftest import TEST_DATABASE_URL
from tests.factories import MUSEUM_TZ


def paris_today() -> date:
    return datetime.now(MUSEUM_TZ).date()


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def pay(
    amount, method=PaymentMethod.CB, key: uuid.UUID | None = None
) -> PaymentCreate:
    return PaymentCreate(
        method=method,
        amount=Decimal(amount),
        idempotency_key=key or uuid.uuid4(),
    )


async def pending_simple(db: AsyncSession, channel=SalesChannel.WEB):
    """Panier musée adulte standard (12 € basse saison) en pending."""
    return await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=paris_today()),
            channel=channel,
        ),
    )


# --- Garde-fous sur l'état de la commande -------------------------------------


async def test_paiement_reservation_inconnue_404(db):
    with pytest.raises(HTTPException) as e:
        await add_payment(db, uuid.uuid4(), pay("12.00"))
    assert e.value.status_code == 404


async def test_paiement_commande_confirmee_refuse(db, catalog):
    resa = await pending_simple(db)
    await add_payment(db, resa.id, pay("12.00"))
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("1.00"))
    assert e.value.status_code == 400
    assert "déjà soldée" in e.value.detail


async def test_paiement_commande_expiree_refuse(db, catalog):
    # Expiration réelle = purge + TTL (lot suivant) ; on vérifie ici
    # que le garde-fou de statut rejette bien l'encaissement.
    resa = await pending_simple(db)
    resa.status = ReservationStatus.EXPIRED
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("12.00"))
    assert e.value.status_code == 400
    assert "expirée" in e.value.detail


def test_montant_invalide_rejete_par_le_schema():
    for bad in ("0", "-5.00"):
        with pytest.raises(ValidationError):
            PaymentCreate(method=PaymentMethod.CB, amount=Decimal(bad))


# --- Chèque : réservé aux groupes/scolaires ------------------------------------


async def test_cheque_refuse_hors_groupe_pos(db, catalog):
    resa = await pending_simple(db, channel=SalesChannel.POS)
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("12.00", PaymentMethod.CHECK))
    assert e.value.status_code == 400
    assert "réservé aux groupes" in e.value.detail


async def test_cheque_accepte_pour_groupe_pos(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item("group_visit", group_size=8, visit_date=paris_today()),
            channel=SalesChannel.POS,
        ),
    )
    r = await add_payment(db, resa.id, pay("80.00", PaymentMethod.CHECK))
    assert r.payment.applied_amount == Decimal("80.00")
    assert r.change_due == Decimal(0)
    assert r.amount_due == Decimal(0)
    assert r.reservation.status == ReservationStatus.CONFIRMED
    assert len(r.reservation.tickets) == 8


# --- Plafonds d'imputation ------------------------------------------------------


async def test_cb_superieur_au_solde_pos_refuse(db, catalog):
    resa = await pending_simple(db, channel=SalesChannel.POS)
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("50.00"))
    assert e.value.status_code == 400
    assert "supérieur au reste" in e.value.detail


async def test_cheque_superieur_au_solde_pos_refuse(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item("group_visit", group_size=8, visit_date=paris_today()),
            channel=SalesChannel.POS,
        ),
    )
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("90.00", PaymentMethod.CHECK))
    assert e.value.status_code == 400
    assert "supérieur au reste" in e.value.detail


async def test_ancv_partiel_pos_applique_sans_rendu(db, catalog):
    resa = await pending_simple(db, channel=SalesChannel.POS)
    r = await add_payment(db, resa.id, pay("5.00", PaymentMethod.ANCV))
    assert r.payment.applied_amount == Decimal("5.00")
    assert r.change_due == Decimal(0)  # jamais de rendu sur ANCV
    assert r.amount_due == Decimal("7.00")
    assert r.reservation.status == ReservationStatus.PENDING


# --- Intégrité « commande → paiement » (409) ------------------------------------


async def test_paiement_produit_modifie_entre_temps_409(db, catalog):
    # La commande fige les session_ids au panier ; si le produit gagne
    # un composant séance entre-temps, l'émission ne peut plus mapper
    # les slots → 409 explicite plutôt que billets partiels.
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    catalog.theater_show.components.append(
        ProductComponent(component_type=ComponentType.THEATER_SESSION, quantity=1)
    )
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("10.00"))
    assert e.value.status_code == 409
    assert "billets non émis" in e.value.detail


async def test_paiement_seance_supprimee_entre_temps_409(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    await db.delete(catalog.session)
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("10.00"))
    assert e.value.status_code == 409
    assert "supprimée" in e.value.detail


# --- Idempotence des encaissements ----------------------------------------
#
# `idempotency_key` identifie l'opération logique : rejeu verbatim du
# snapshot initial pour le même contenu, 409 si la clé est réutilisée
# avec une autre réservation/méthode/montant. Une tentative qui échoue
# avant l'enregistrement ne consomme pas la clé. La course réelle
# (N requêtes simultanées, même clé) reste démontrée par
# scripts/test_concurrency.py sur PostgreSQL réel.


async def _payment_count(db: AsyncSession, reservation_id) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(PaymentTransaction)
            .where(PaymentTransaction.reservation_id == reservation_id)
        )
    ).scalar_one()


async def test_rejeu_meme_cle_meme_contenu(db, catalog):
    # Timeout/réponse perdue côté caisse : le retry de la même opération
    # ne doit pas créer un second encaissement.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    first = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    assert not first.replayed
    second = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    assert second.replayed
    assert second.result == first.result  # snapshot identique, verbatim
    assert second.payment.id == first.payment.id
    assert await _payment_count(db, resa.id) == 1
    assert second.reservation.paid_amount == Decimal("5.00")


async def test_rejeu_conserve_le_snapshot_initial(db, catalog):
    # Tranche 1 (5 €, solde 7) puis tranche 2 solde la commande : le
    # rejeu de la tranche 1 doit restituer son solde historique, pas
    # l'état courant (confirmed / 0).
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key_a = uuid.uuid4()
    first = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key_a)
    )
    assert first.result["amount_due"] == "7.00"
    await add_payment(db, resa.id, pay("7.00", PaymentMethod.CASH))

    replay = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key_a)
    )
    assert replay.replayed
    assert replay.result == first.result
    assert replay.amount_due == Decimal("7.00")
    assert replay.result["reservation_status"] == "pending"
    assert replay.reservation.status == ReservationStatus.CONFIRMED


async def test_cle_reutilisee_montant_different_409(db, catalog):
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH, key=key))
    with pytest.raises(HTTPException) as e:
        await add_payment(
            db, resa.id, pay("6.00", PaymentMethod.CASH, key=key)
        )
    assert e.value.status_code == 409
    assert await _payment_count(db, resa.id) == 1


async def test_cle_reutilisee_methode_differente_409(db, catalog):
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH, key=key))
    with pytest.raises(HTTPException) as e:
        await add_payment(
            db, resa.id, pay("5.00", PaymentMethod.ANCV, key=key)
        )
    assert e.value.status_code == 409


async def test_cle_reutilisee_autre_reservation_409(db, catalog, today):
    # Une clé n'est pas recyclable sur une autre commande — rejet 409,
    # sans transaction ni billet supplémentaire.
    resa_a = await pending_simple(db, channel=SalesChannel.POS)
    resa_b = await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=today),
            channel=SalesChannel.POS,
        ),
    )
    key = uuid.uuid4()
    await add_payment(
        db, resa_a.id, pay("12.00", PaymentMethod.CASH, key=key)
    )
    with pytest.raises(HTTPException) as e:
        await add_payment(
            db, resa_b.id, pay("12.00", PaymentMethod.CASH, key=key)
        )
    assert e.value.status_code == 409
    assert await _payment_count(db, resa_b.id) == 0
    assert resa_b.status == ReservationStatus.PENDING


async def test_cles_distinctes_multi_paiements_pos(db, catalog):
    # Deux opérations réellement distinctes (clés différentes) restent
    # possibles : le multi-encaissement POS n'est pas bridé.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    r1 = await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH))
    r2 = await add_payment(db, resa.id, pay("7.00", PaymentMethod.CASH))
    assert not r1.replayed and not r2.replayed
    assert r2.reservation.status == ReservationStatus.CONFIRMED
    assert await _payment_count(db, resa.id) == 2


async def test_echec_ne_consomme_pas_la_cle(db, catalog):
    # Rejet métier avant enregistrement (méthode interdite au canal) :
    # la clé n'est pas persistée — une nouvelle tentative légitime avec
    # la même clé est un nouvel encaissement, pas un conflit.
    resa = await pending_simple(db)  # canal web : ANCV interdit
    key = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        await add_payment(
            db, resa.id, pay("12.00", PaymentMethod.ANCV, key=key)
        )
    assert e.value.status_code == 400
    retry = await add_payment(
        db, resa.id, pay("12.00", PaymentMethod.CB, key=key)
    )
    assert not retry.replayed
    assert retry.reservation.status == ReservationStatus.CONFIRMED


async def test_rejeu_apres_annulation_restitue_snapshot(db, catalog):
    # Le snapshot survit au changement d'état de la commande : annulée
    # entre-temps, le rejeu restitue la réponse de l'opération initiale.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    first = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    await cancel_reservation(db, resa.id)
    replay = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    assert replay.replayed
    assert replay.result == first.result
    assert replay.result["reservation_status"] == "pending"
    assert replay.reservation.status == ReservationStatus.CANCELLED


async def test_rejeu_web_confirme_memes_billets(db, catalog, today):
    # Rejeu du paiement unique web : un seul encaissement, les mêmes
    # billets dans le snapshot — pas de double émission.
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    key = uuid.uuid4()
    first = await add_payment(db, resa.id, pay("12.00", key=key))
    replay = await add_payment(db, resa.id, pay("12.00", key=key))
    assert replay.replayed
    assert replay.result == first.result
    assert await _payment_count(db, resa.id) == 1
    assert len(replay.result["tickets"]) == 1


async def _ticket_count(db: AsyncSession, reservation_id) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.reservation_id == reservation_id)
        )
    ).scalar_one()


async def test_rejeu_ne_duplique_pas_les_billets_en_base(db, catalog, today):
    # Le snapshot rejoué ne réémet rien : le nombre de billets en base
    # reste celui de l'opération initiale.
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    key = uuid.uuid4()
    await add_payment(db, resa.id, pay("12.00", key=key))
    assert await _ticket_count(db, resa.id) == 1

    replay = await add_payment(db, resa.id, pay("12.00", key=key))
    assert replay.replayed
    assert await _ticket_count(db, resa.id) == 1


async def test_snapshot_persiste_et_rejoue_apres_relecture(db, catalog):
    # Redémarrage simulé : invalidation du cache d'identité SQLAlchemy,
    # puis rejeu — la réponse provient de la colonne JSONB persistée,
    # pas d'un état en mémoire.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    first = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    pid = first.payment.id
    rid = resa.id  # capturé avant expiration (lazy-load interdit)
    db.expire_all()  # tout l'état ORM en mémoire est invalidé

    stored = (
        await db.execute(
            select(PaymentTransaction.response).where(
                PaymentTransaction.id == pid
            )
        )
    ).scalar_one()
    assert stored == first.result  # snapshot réellement en base

    replay = await add_payment(
        db, rid, pay("5.00", PaymentMethod.CASH, key=key)
    )
    assert replay.replayed
    assert replay.result == first.result


async def test_canal_web_ne_peut_pas_etre_partiellement_paye(
    db, catalog, today
):
    # Invariant derrière l'abandon côté billetterie : une réservation
    # web « pending » ne peut porter AUCUN encaissement — le canal exige
    # CB au montant exact (DFC n°7). Abandonner une opération pending
    # (oubli local de la clé) ne peut donc pas égarer un paiement.
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    for method in (PaymentMethod.CASH, PaymentMethod.ANCV,
                   PaymentMethod.CHECK):
        with pytest.raises(HTTPException) as e:
            await add_payment(db, resa.id, pay("12.00", method))
        assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        await add_payment(db, resa.id, pay("5.00"))  # CB partiel
    assert await _payment_count(db, resa.id) == 0
    assert resa.status == ReservationStatus.PENDING


async def test_rejeu_apres_expiration_restitue_snapshot(db, catalog):
    # Même garantie qu'après annulation : la purge TTL a pu expirer la
    # commande entre-temps, le rejeu restitue la réponse initiale.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    first = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    resa.status = ReservationStatus.EXPIRED
    await db.flush()
    replay = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.CASH, key=key)
    )
    assert replay.replayed
    assert replay.result == first.result
    assert replay.result["reservation_status"] == "pending"
    assert replay.reservation.status == ReservationStatus.EXPIRED
    # La trace de l'encaissement persiste : la purge ne l'efface pas.
    assert await _payment_count(db, resa.id) == 1
    applied = (
        await db.execute(
            select(func.sum(PaymentTransaction.applied_amount)).where(
                PaymentTransaction.reservation_id == resa.id
            )
        )
    ).scalar_one()
    assert applied == Decimal("5.00")


async def test_encaissement_reste_trace_apres_annulation(db, catalog):
    # Scénario guichet : espèces remises, réponse perdue, commande
    # annulée entre-temps. La transaction enregistrée n'est jamais
    # effacée — la traçabilité permet le remboursement manuel.
    resa = await pending_simple(db, channel=SalesChannel.POS)
    key = uuid.uuid4()
    await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH, key=key))
    await cancel_reservation(db, resa.id)
    await db.refresh(resa)
    assert resa.status == ReservationStatus.CANCELLED
    assert await _payment_count(db, resa.id) == 1
    applied = (
        await db.execute(
            select(func.sum(PaymentTransaction.applied_amount)).where(
                PaymentTransaction.reservation_id == resa.id
            )
        )
    ).scalar_one()
    assert applied == Decimal("5.00")


async def test_echec_emission_ne_laisse_aucun_etat_partiel(db, catalog):
    # Échec pendant la transaction (409 émission, séance supprimée) :
    # rollback complet — ni paiement, ni billet, ni statut modifié, et
    # la clé n'est pas consommée (un rejeu retombe sur le même 409
    # métier, pas sur un snapshot fantôme).
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    await db.delete(catalog.session)
    await db.flush()
    key = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("10.00", key=key))
    assert e.value.status_code == 409

    await db.rollback()  # ce que fait la frontière HTTP en fin de requête
    await db.refresh(resa)
    assert await _payment_count(db, resa.id) == 0
    assert await _ticket_count(db, resa.id) == 0
    assert resa.status == ReservationStatus.PENDING

    # Le rollback a aussi restauré la séance supprimée : on la retire à
    # nouveau, puis le même appel avec la même clé retombe sur le 409
    # métier — la clé n'a pas été consommée par l'échec précédent.
    await db.delete(catalog.session)
    await db.flush()
    with pytest.raises(HTTPException) as e2:
        await add_payment(db, resa.id, pay("10.00", key=key))
    assert e2.value.status_code == 409
    await db.rollback()


# --- Snapshot de paiement : cohérence avec la relecture (AUDIT-007) ------
#
# Le `response` JSONB est figé à l'opération initiale : si les accès émis
# n'ont pas leur relation `session` peuplée à la sérialisation, le
# snapshot conserve session_start/session_event_title à null à vie —
# et le rejeu propage le défaut.


async def test_snapshot_paiement_coherent_avec_relecture(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    key = uuid.uuid4()
    first = await add_payment(db, resa.id, pay("10.00", key=key))
    snap = first.result["tickets"][0]["accesses"][0]
    assert snap["session_id"] == str(catalog.session.id)
    assert snap["session_start"] is not None
    assert snap["session_event_title"] == "Théâtre (pytest)"

    rid = resa.id  # capturé avant expiration (lazy-load interdit)
    db.expire_all()
    # Même graphe eager que GET /reservations/{id}, mêmes schémas de
    # sérialisation : la relecture doit donner les mêmes champs.
    reread = (
        await db.execute(
            select(Reservation)
            .options(
                selectinload(Reservation.tickets)
                .selectinload(Ticket.accesses)
                .selectinload(TicketAccess.session)
                .selectinload(Session.event)
            )
            .where(Reservation.id == rid)
        )
    ).scalar_one()
    fresh = TicketRead.model_validate(reread.tickets[0]).model_dump(
        mode="json"
    )
    assert fresh["accesses"][0]["session_start"] == snap["session_start"]
    assert (
        fresh["accesses"][0]["session_event_title"]
        == snap["session_event_title"]
    )

    # Le rejeu restitue le snapshot initial correctement renseigné.
    replay = await add_payment(db, rid, pay("10.00", key=key))
    assert replay.replayed
    assert replay.result == first.result


# --- Collision de clé : récupération après IntegrityError (AUDIT-001) ----
#
# L'index unique sur la clé est le filet ultime des courses
# inter-réservations (le FOR UPDATE ne sérialise que la même commande).
# La course réelle reste démontrée par scripts/test_concurrency.py ; les
# tests ci-dessous exercent les branches de récupération de façon
# déterministe : un « gagnant » commité par une autre connexion simule
# la transaction concurrente qui a validé entre le lookup et l'INSERT.


@pytest_asyncio.fixture
async def external_engine():
    """Engine hors transaction de test : les commits y sont réellement
    visibles par la session `db` (READ COMMITTED) — permet de simuler
    une transaction concurrente. statement_timeout court : une attente
    d'insertion spéculative PostgreSQL (version défectueuse qui flushe
    la clé hors du périmètre de récupération) échoue au lieu de
    bloquer indéfiniment."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={
            "server_settings": {"statement_timeout": "5000"}
        },
    )
    yield engine
    await engine.dispose()


async def _commit_winner(engine, key: uuid.UUID) -> uuid.UUID:
    """Commit, hors transaction de test, une réservation + un
    encaissement portant `key` — l'opération « gagnante » d'une course
    inter-réservations."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db2:
        resa_b = Reservation(
            customer_email="gagnant@pirates.fr",
            total_price=Decimal("10.00"),
            sales_channel=SalesChannel.POS,
        )
        db2.add(resa_b)
        db2.add(
            PaymentTransaction(
                reservation=resa_b,
                method=PaymentMethod.CASH,
                amount=Decimal("5.00"),
                applied_amount=Decimal("5.00"),
                idempotency_key=key,
            )
        )
        await db2.commit()
        return resa_b.id


async def _delete_committed(engine, reservation_id: uuid.UUID) -> None:
    """Lignes commitées hors de la transaction de test — le rollback
    final ne les voit pas, nettoyage explicite."""
    async with engine.connect() as conn:
        await conn.execute(
            delete(PaymentTransaction).where(
                PaymentTransaction.reservation_id == reservation_id
            )
        )
        await conn.execute(
            delete(Reservation).where(Reservation.id == reservation_id)
        )
        await conn.commit()


async def _resa_a_seance(db, catalog):
    """Commande POS à séance dont l'encaissement soldait le compte :
    _emit_tickets est traversé — là où l'autoflush laissait échapper
    l'IntegrityError avant la correction (500 au lieu de 409)."""
    return await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            ),
            channel=SalesChannel.POS,
        ),
    )


async def test_collision_cle_au_flush_recuperee_409(
    db, catalog, monkeypatch, external_engine
):
    # La même clé est commitée par un concurrent pendant notre flush :
    # IntegrityError sur l'index unique → rollback → relecture de
    # l'opération gagnante → 409 (empreinte différente), et aucun état
    # partiel côté perdant. Échec prouvé sur l'ancien code : l'INSERT
    # échappait au périmètre de récupération via l'autoflush.
    resa = await _resa_a_seance(db, catalog)
    key = uuid.uuid4()
    winner_resa_id: uuid.UUID | None = None
    injected = False
    real_flush = db.flush

    async def racing_flush(*args, **kwargs):
        nonlocal injected, winner_resa_id
        if not injected:
            injected = True
            winner_resa_id = await _commit_winner(external_engine, key)
        return await real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", racing_flush)
    try:
        with pytest.raises(HTTPException) as exc:
            await add_payment(
                db, resa.id, pay("10.00", PaymentMethod.CASH, key=key)
            )
        assert exc.value.status_code == 409
        # Le rejet vient du conflit d'empreinte, pas d'un autre 409
        # (ex. émission « billets non émis »).
        assert "opération différente" in exc.value.detail
    finally:
        if winner_resa_id is not None:
            await _delete_committed(external_engine, winner_resa_id)

    # Aucun état partiel : la tentative perdante est intégralement
    # rollbackée — ni paiement, ni billet, statut inchangé.
    await db.refresh(resa)
    assert resa.status == ReservationStatus.PENDING
    assert await _payment_count(db, resa.id) == 0
    assert await _ticket_count(db, resa.id) == 0


async def test_cle_commitee_pendant_le_verrou_recuperee_409(
    db, catalog, monkeypatch, external_engine
):
    # Course vue du côté « second lookup sous FOR UPDATE » : la clé est
    # commitée entre le chemin rapide et l'acquisition du verrou — le
    # rejeu de l'opération gagnante (ici 409, autre réservation) doit
    # primer sur la garde « déjà soldée ». Couverture déterministe
    # d'une branche jusque-là non exercée : elle existait et
    # fonctionnait déjà avant la correction AUDIT-001 — ce test passe
    # aussi sur l'ancien code et ne prouve pas le fix, il verrouille
    # la branche de récupération.
    resa = await _resa_a_seance(db, catalog)
    key = uuid.uuid4()
    winner_resa_id: uuid.UUID | None = None
    injected = False
    real_execute = db.execute

    async def racing_execute(statement, *args, **kwargs):
        # Le concurrent commit juste avant la fin du verrou FOR UPDATE :
        # le second lookup de la clé le voit alors en base.
        nonlocal injected, winner_resa_id
        if not injected and any(
            d.get("entity") is Reservation
            for d in getattr(statement, "column_descriptions", ())
        ):
            injected = True
            winner_resa_id = await _commit_winner(external_engine, key)
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", racing_execute)
    try:
        with pytest.raises(HTTPException) as exc:
            await add_payment(
                db, resa.id, pay("10.00", PaymentMethod.CASH, key=key)
            )
        assert exc.value.status_code == 409
        assert "opération différente" in exc.value.detail
    finally:
        if winner_resa_id is not None:
            await _delete_committed(external_engine, winner_resa_id)

    await db.refresh(resa)
    assert resa.status == ReservationStatus.PENDING
    assert await _payment_count(db, resa.id) == 0
    assert await _ticket_count(db, resa.id) == 0
