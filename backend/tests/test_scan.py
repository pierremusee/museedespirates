"""Contrôle d'accès (scan_ticket) — règles métier au niveau service.

Complète les E2E : mêmes règles, assertions directes sur les objets et la
réponse de scan. La sérialisation anti double-scan (SELECT FOR UPDATE)
reste prouvée par scripts/test_concurrency.py — non dupliquée ici.

Horloge : les tests de fenêtre horaire figent `datetime` dans
ticket_service à 14h00 Europe/Paris (fixture `frozen_clock`) — aucun mock
d'horloge globale, aucune dépendance à l'heure réelle, borne des 30 min
testable exactement. Les billets sont émis sur une séance vendable (J+10)
puis `start_time` est déplacé à l'instant voulu : la chaîne
réservation → paiement → émission reste réelle.

Branches volontairement non testées (défensives / inatteignables) :
- « Accès déjà utilisé » sans date (is_scanned sans scanned_at : état
  incohérent jamais produit — les deux sont écrits ensemble) ;
- « Accès sans séance associée » : session_id NULL ne matche jamais un
  checkpoint séance, et une séance référencée est protégée par la FK ;
- le libellé SESSION_DINING : aucun produit n'émet ce type aujourd'hui.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.services.ticket_service as ticket_service
from app.models import (
    FreeProfile,
    PaymentMethod,
    ReservationStatus,
    SalesChannel,
    TicketType,
)
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.schemas.ticket import TicketScanRequest
from app.services.reservation_service import add_payment, create_reservation
from tests.factories import MUSEUM_TZ, UTC


def paris_today() -> date:
    """Jour civil Europe/Paris — la référence du service, pas celle
    de la machine (CI en UTC)."""
    return datetime.now(MUSEUM_TZ).date()


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def at_event(event_id) -> TicketScanRequest:
    return TicketScanRequest(event_id=event_id)


def at_session(session_id) -> TicketScanRequest:
    return TicketScanRequest(session_id=session_id)


async def paid_ticket(db, payload: ReservationCreate, amount: str):
    """Chaîne réelle : réservation → paiement soldé → billet émis."""
    resa = await create_reservation(db, payload)
    resa = (
        await add_payment(
            db,
            resa.id,
            PaymentCreate(
                method=PaymentMethod.CB,
                amount=Decimal(amount),
                idempotency_key=uuid.uuid4(),
            ),
        )
    ).reservation
    assert resa.status == ReservationStatus.CONFIRMED
    assert len(resa.tickets) == 1
    return resa.tickets[0]


@pytest.fixture
def frozen_clock(monkeypatch) -> datetime:
    """Fige `datetime.now()` de ticket_service à 14h00 Europe/Paris
    (jour réel courant). Retourne l'instant figé pour calculer les
    start_time relatifs."""
    fixed = datetime.combine(
        paris_today(), time(14, 0), tzinfo=MUSEUM_TZ
    ).astimezone(UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed.replace(tzinfo=None)
            return fixed.astimezone(tz)

    monkeypatch.setattr(ticket_service, "datetime", _FrozenDatetime)
    return fixed


def access_of(ticket, access_type: TicketType):
    return next(a for a in ticket.accesses if a.access_type == access_type)


def day_of(session) -> date:
    return session.start_time.astimezone(MUSEUM_TZ).date()


# --- Schéma : un poste de contrôle exactement --------------------------------


def test_un_seul_poste_requis():
    with pytest.raises(ValidationError):
        TicketScanRequest()
    with pytest.raises(ValidationError):
        TicketScanRequest(event_id=uuid.uuid4(), session_id=uuid.uuid4())


# --- Rejets -------------------------------------------------------------------


async def test_billet_inconnu_404(db):
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(db, uuid.uuid4(), at_event(uuid.uuid4()))
    assert e.value.status_code == 404


async def test_billet_musee_refuse_au_poste_seance(db, catalog):
    ticket = await paid_ticket(
        db,
        order(item("museum_entry", category="adult", visit_date=paris_today())),
        "12.00",
    )
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(
            db, ticket.id, at_session(catalog.session.id)
        )
    assert e.value.status_code == 400
    assert "pas droit" in e.value.detail


async def test_billet_seance_refuse_a_lentree_musee(db, catalog):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(db, ticket.id, at_event(catalog.musee.id))
    assert e.value.status_code == 400
    assert "pas droit" in e.value.detail


async def test_billet_musee_mauvais_evenement(db, catalog):
    ticket = await paid_ticket(
        db,
        order(item("museum_entry", category="adult", visit_date=paris_today())),
        "12.00",
    )
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(
            db, ticket.id, at_event(catalog.theater.id)
        )
    assert e.value.status_code == 400
    assert "pas droit" in e.value.detail


async def test_double_scan_refuse_avec_date(db, catalog):
    ticket = await paid_ticket(
        db,
        order(item("museum_entry", category="adult", visit_date=paris_today())),
        "12.00",
    )
    await ticket_service.scan_ticket(db, ticket.id, at_event(catalog.musee.id))
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(db, ticket.id, at_event(catalog.musee.id))
    assert e.value.status_code == 400
    assert "Accès déjà utilisé le" in e.value.detail


# --- Billet Musée (journée civile) --------------------------------------------


async def test_scan_musee_valide_aujourdhui(db, catalog):
    ticket = await paid_ticket(
        db,
        order(item("museum_entry", category="adult", visit_date=paris_today())),
        "12.00",
    )
    resp = await ticket_service.scan_ticket(
        db, ticket.id, at_event(catalog.musee.id)
    )
    assert resp.is_scanned is True
    assert resp.scanned_at is not None
    assert resp.id == ticket.id
    assert resp.access_type == TicketType.OPEN_TICKET
    assert resp.event_id == catalog.musee.id
    assert resp.session_id is None
    assert resp.valid_date == paris_today()
    assert resp.session_start is None
    assert resp.ticket_category == "adult"
    assert resp.access_label == "Accès Musée — Billet Journée"
    assert resp.message == resp.access_label
    assert resp.control_warning is None
    assert {a.access_type for a in resp.accesses} == {
        TicketType.OPEN_TICKET
    }


async def test_billet_musee_autre_jour_refuse(db, catalog):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "museum_entry", category="adult",
                visit_date=paris_today() + timedelta(days=2),
            )
        ),
        "12.00",
    )
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(db, ticket.id, at_event(catalog.musee.id))
    assert e.value.status_code == 400
    assert "Billet Musée valable le" in e.value.detail


# --- Billet séance (fenêtre horaire, horloge figée) ---------------------------


async def test_scan_seance_valide_meme_jour(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    # Séance de 16h00 le jour même : scannable sans borne « trop tôt ».
    catalog.session.start_time = frozen_clock + timedelta(hours=2)
    await db.flush()
    resp = await ticket_service.scan_ticket(
        db, ticket.id, at_session(catalog.session.id)
    )
    assert resp.is_scanned is True
    assert resp.access_type == TicketType.SESSION_STANDARD
    assert resp.session_id == catalog.session.id
    assert resp.session_start == catalog.session.start_time
    assert "Théâtre (pytest)" in resp.access_label
    assert "Séance de 16h00" in resp.access_label


async def test_scan_seance_dans_la_tolerance(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    # Séance commencée il y a 20 min : encore valable (tolérance 30 min).
    catalog.session.start_time = frozen_clock - timedelta(minutes=20)
    await db.flush()
    resp = await ticket_service.scan_ticket(
        db, ticket.id, at_session(catalog.session.id)
    )
    assert resp.is_scanned is True


async def test_scan_seance_borne_exacte_30_min(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    # Rejet strictement APRÈS début + 30 min : à exactement 30 min le
    # billet est encore valable.
    catalog.session.start_time = frozen_clock - timedelta(minutes=30)
    await db.flush()
    resp = await ticket_service.scan_ticket(
        db, ticket.id, at_session(catalog.session.id)
    )
    assert resp.is_scanned is True


async def test_scan_seance_expiree_refusee(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    # Séance commencée il y a 31 min — déterministe quel que soit
    # l'horodatage réel du run.
    catalog.session.start_time = frozen_clock - timedelta(minutes=31)
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(
            db, ticket.id, at_session(catalog.session.id)
        )
    assert e.value.status_code == 400
    assert "passée depuis plus de" in e.value.detail


async def test_scan_seance_autre_jour_refusee(db, catalog):
    # Séance J+10 scannée aujourd'hui : le contrôle de jour prime sur la
    # fenêtre horaire — pas besoin de figer l'horloge.
    ticket = await paid_ticket(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
        "10.00",
    )
    with pytest.raises(HTTPException) as e:
        await ticket_service.scan_ticket(
            db, ticket.id, at_session(catalog.session.id)
        )
    assert e.value.status_code == 400
    assert "pas aujourd'hui" in e.value.detail


# --- Consommation indépendante des accès d'un même billet ----------------------


async def test_scan_consomme_un_seul_acces(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "pass_1_show", category="adult",
                visit_date=day_of(catalog.session),
                session_id=catalog.session.id,
            )
        ),
        "20.00",
    )
    catalog.session.start_time = frozen_clock + timedelta(hours=2)
    await db.flush()
    resp = await ticket_service.scan_ticket(
        db, ticket.id, at_session(catalog.session.id)
    )
    scanned = {a.access_type: a.is_scanned for a in resp.accesses}
    assert scanned[TicketType.SESSION_STANDARD] is True
    assert scanned[TicketType.OPEN_TICKET] is False


async def test_meme_billet_aux_deux_postes(db, catalog, frozen_clock):
    ticket = await paid_ticket(
        db,
        order(
            item(
                "pass_1_show", category="adult",
                visit_date=day_of(catalog.session),
                session_id=catalog.session.id,
            )
        ),
        "20.00",
    )
    # Alignement des deux droits sur le jour figé (post-émission) :
    # le jour du musée est libre, celui de la séance est lié à la séance.
    access_of(ticket, TicketType.OPEN_TICKET).valid_date = paris_today()
    catalog.session.start_time = frozen_clock + timedelta(hours=2)
    await db.flush()
    musee = await ticket_service.scan_ticket(
        db, ticket.id, at_event(catalog.musee.id)
    )
    theatre = await ticket_service.scan_ticket(
        db, ticket.id, at_session(catalog.session.id)
    )
    assert musee.access_type == TicketType.OPEN_TICKET
    assert theatre.access_type == TicketType.SESSION_STANDARD
    assert all(a.is_scanned for a in theatre.accesses)


# --- Gratuités : avertissement de contrôle -------------------------------------


async def test_control_warning_moins_de_4_ans(db, catalog):
    # Panier à 0 € : confirmé et émis sans paiement.
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry", visit_date=paris_today(),
                free_profile=FreeProfile.UNDER_4,
            )
        ),
    )
    assert len(resa.tickets) == 1
    resp = await ticket_service.scan_ticket(
        db, resa.tickets[0].id, at_event(catalog.musee.id)
    )
    assert resp.control_warning == "GRATUIT — Moins de 4 ans (vérifier l'âge)"


async def test_control_warning_invalidite(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry", category="adult", visit_date=paris_today(),
                free_profile=FreeProfile.DISABILITY,
            )
        ),
    )
    resp = await ticket_service.scan_ticket(
        db, resa.tickets[0].id, at_event(catalog.musee.id)
    )
    assert resp.control_warning == "GRATUIT — Vérifier la carte d'invalidité"


async def test_control_warning_accompagnateur_pmr(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry", category="adult", visit_date=paris_today(),
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "museum_entry", visit_date=paris_today(),
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert len(resa.tickets) == 2
    warnings = set()
    for t in resa.tickets:
        resp = await ticket_service.scan_ticket(
            db, t.id, at_event(catalog.musee.id)
        )
        warnings.add(resp.control_warning)
    assert warnings == {
        "GRATUIT — Vérifier la carte d'invalidité",
        "GRATUIT — Accompagnateur PMR",
    }
