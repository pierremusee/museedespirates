"""Cycle de vie d'une réservation au niveau service : création (jauge),
encaissement, émission/fusion des billets, annulation.

Complète les E2E HTTP : mêmes règles, assertions directes sur les objets
et le compteur `booked_seats` en base.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FreeProfile,
    PaymentMethod,
    ReservationStatus,
    SalesChannel,
    Session,
)
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import (
    add_payment,
    cancel_reservation,
    create_reservation,
)
from tests.factories import MUSEUM_TZ


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def pay(amount, method=PaymentMethod.CB) -> PaymentCreate:
    return PaymentCreate(
        method=method,
        amount=Decimal(amount),
        idempotency_key=uuid.uuid4(),
    )


def day_of(session: Session) -> date:
    return session.start_time.astimezone(MUSEUM_TZ).date()


async def booked(db: AsyncSession, session_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(Session.booked_seats).where(Session.id == session_id)
        )
    ).scalar_one()


# --- Création : jauge et état ---------------------------------------------


async def test_creation_pending_reserve_la_jauge(db, catalog):
    before = await booked(db, catalog.session.id)
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    assert resa.status == ReservationStatus.PENDING
    assert resa.tickets == []
    assert resa.total_price == Decimal("10.00")
    assert await booked(db, catalog.session.id) == before + 1


async def test_panier_zero_confirme_et_emet_sans_paiement(db, catalog, today):
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry",
                visit_date=today,
                free_profile=FreeProfile.UNDER_4,
            )
        ),
    )
    assert resa.status == ReservationStatus.CONFIRMED
    assert resa.total_price == Decimal(0)
    assert len(resa.tickets) == 1


# --- Émission et fusion des billets ----------------------------------------


async def test_pass_emet_un_billet_deux_acces(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_1_show",
                category="adult",
                visit_date=day_of(catalog.session),
                session_id=catalog.session.id,
            )
        ),
    )
    resa = (await add_payment(db, resa.id, pay("20.00"))).reservation
    assert resa.status == ReservationStatus.CONFIRMED
    assert len(resa.tickets) == 1
    kinds = {a.access_type for a in resa.tickets[0].accesses}
    assert kinds == {"open_ticket", "session_standard"}


async def test_deux_personnes_deux_billets(db, catalog, today):
    # Deux lignes de même catégorie = deux personnes physiques distinctes
    # — la fusion gloutonne ne peut pas les réunir (même clé d'accès).
    resa = await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=today),
            item("museum_entry", category="adult", visit_date=today),
        ),
    )
    resa = (await add_payment(db, resa.id, pay("24.00"))).reservation
    assert len(resa.tickets) == 2
    assert all(len(t.accesses) == 1 for t in resa.tickets)


async def test_fusion_gloutonne_meme_categorie(db, catalog):
    # Limite documentée : deux lignes de même catégorie (musée + théâtre
    # achetés séparément) fusionnent sur UN billet.
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry", category="adult",
                visit_date=day_of(catalog.session),
            ),
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            ),
            channel=SalesChannel.POS,
        ),
    )
    resa = (
        await add_payment(db, resa.id, pay("22.00", PaymentMethod.CASH))
    ).reservation
    assert len(resa.tickets) == 1
    assert len(resa.tickets[0].accesses) == 2


async def test_categories_differentes_pas_de_fusion(db, catalog, today):
    resa = await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=today),
            item("museum_entry", category="child", visit_date=today),
        ),
    )
    resa = (await add_payment(db, resa.id, pay("20.00"))).reservation
    assert len(resa.tickets) == 2
    assert {t.ticket_category for t in resa.tickets} == {"adult", "child"}


# --- Paiements --------------------------------------------------------------


async def test_ancv_refuse_canal_web(db, catalog, today):
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("12.00", PaymentMethod.ANCV))
    assert e.value.status_code == 400


async def test_cb_partiel_refuse_canal_web(db, catalog, today):
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("5.00"))
    assert e.value.status_code == 400


async def test_ancv_excedentaire_pos_sans_rendu(db, catalog, today):
    resa = await create_reservation(
        db,
        order(
            item("museum_entry", category="child", visit_date=today),
            channel=SalesChannel.POS,
        ),
    )
    r = await add_payment(db, resa.id, pay("50.00", PaymentMethod.ANCV))
    payment, change, resa = r.payment, r.change_due, r.reservation
    assert payment.amount == Decimal("50.00")
    assert payment.applied_amount == Decimal("8.00")
    assert change == Decimal(0)
    assert resa.status == ReservationStatus.CONFIRMED


async def test_multi_paiement_pos_avec_rendu(db, catalog, today):
    resa = await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=today),
            channel=SalesChannel.POS,
        ),
    )
    r = await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH))
    assert r.amount_due == Decimal("7.00")
    assert r.reservation.status == ReservationStatus.PENDING
    r = await add_payment(db, resa.id, pay("10.00", PaymentMethod.CASH))
    assert r.change_due == Decimal("3.00")
    assert r.reservation.status == ReservationStatus.CONFIRMED


# --- Annulation -------------------------------------------------------------


async def test_annulation_restitue_la_jauge(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    assert await booked(db, catalog.session.id) == 1
    cancelled = await cancel_reservation(db, resa.id)
    assert cancelled.status == ReservationStatus.CANCELLED
    assert await booked(db, catalog.session.id) == 0


async def test_annulation_conserve_les_encaissements(db, catalog, today):
    resa = await create_reservation(
        db,
        order(
            item("museum_entry", category="adult", visit_date=today),
            channel=SalesChannel.POS,
        ),
    )
    await add_payment(db, resa.id, pay("5.00", PaymentMethod.CASH))
    cancelled = await cancel_reservation(db, resa.id)
    assert cancelled.status == ReservationStatus.CANCELLED
    assert len(cancelled.payments) == 1
    assert cancelled.payments[0].amount == Decimal("5.00")


async def test_annulation_commande_confirmee_refusee(db, catalog, today):
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    await add_payment(db, resa.id, pay("12.00"))
    with pytest.raises(HTTPException) as e:
        await cancel_reservation(db, resa.id)
    assert e.value.status_code == 400


async def test_paiement_sur_commande_annulee_refuse(db, catalog, today):
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    await cancel_reservation(db, resa.id)
    with pytest.raises(HTTPException) as e:
        await add_payment(db, resa.id, pay("12.00"))
    assert e.value.status_code == 400


async def test_annulation_inconnue_404(db):
    with pytest.raises(HTTPException) as e:
        await cancel_reservation(db, uuid.uuid4())
    assert e.value.status_code == 404


async def test_annulation_seance_supprimee(db, catalog):
    # Séance retirée entre commande et annulation : la restitution de
    # jauge ignore simplement la séance introuvable, l'annulation passe.
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
    cancelled = await cancel_reservation(db, resa.id)
    assert cancelled.status == ReservationStatus.CANCELLED


# --- Tarification figée ------------------------------------------------------


async def test_haute_saison_fige_le_modificateur(db, catalog, today, high_season):
    resa = await create_reservation(
        db, order(item("museum_entry", category="adult", visit_date=today))
    )
    item_ = resa.items[0]
    assert item_.computed_price == Decimal("14.00")  # 12 + 2
    assert item_.season_modifier == Decimal("2.00")
