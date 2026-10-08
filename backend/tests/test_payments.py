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
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ComponentType,
    PaymentMethod,
    ProductComponent,
    ReservationStatus,
    SalesChannel,
)
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import add_payment, create_reservation
from tests.factories import MUSEUM_TZ


def paris_today() -> date:
    return datetime.now(MUSEUM_TZ).date()


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def pay(amount, method=PaymentMethod.CB) -> PaymentCreate:
    return PaymentCreate(method=method, amount=Decimal(amount))


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
    payment, change, due, resa = await add_payment(
        db, resa.id, pay("80.00", PaymentMethod.CHECK)
    )
    assert payment.applied_amount == Decimal("80.00")
    assert change == Decimal(0)
    assert due == Decimal(0)
    assert resa.status == ReservationStatus.CONFIRMED
    assert len(resa.tickets) == 8


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
    payment, change, due, resa = await add_payment(
        db, resa.id, pay("5.00", PaymentMethod.ANCV)
    )
    assert payment.applied_amount == Decimal("5.00")
    assert change == Decimal(0)  # jamais de rendu sur ANCV
    assert due == Decimal("7.00")
    assert resa.status == ReservationStatus.PENDING


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
