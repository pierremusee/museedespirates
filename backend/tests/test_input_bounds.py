"""Bornes d'entrée des montants et quantités (AUDIT-008).

Sans limites explicites, une valeur démesurée provoquait un
dépassement PostgreSQL évitable (Numeric(10,2), varchar(320),
Integer) renvoyé en 500, ou une création massive de billets sur
les produits sans jauge.

Bornes retenues (décision métier, voir schemas/reservation.py) :
- 120 personnes couvertes par commande ;
- 10 lignes (items) par commande ;
- 50 000 € par tranche d'encaissement ;
- 320 caractères pour l'email (capacité de la colonne).

Champ hors borne → ValidationError Pydantic (422 en HTTP).
Total de personnes d'une commande valide par champ mais trop
lourde en agrégat → 400 en service, comme « capacité insuffisante ».
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.reservation import (
    MAX_ITEMS_PER_RESERVATION,
    MAX_PAYMENT_AMOUNT,
    MAX_PERSONS_PER_RESERVATION,
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import create_reservation
from tests.test_reservation_rules import item, order

TODAY = date.today()

# --- PaymentCreate.amount ------------------------------------------------


def test_montant_au_plafond_accepte():
    pay = PaymentCreate(
        method="cash",
        amount=MAX_PAYMENT_AMOUNT,
        idempotency_key=uuid.uuid4(),
    )
    assert pay.amount == Decimal("50000.00")


def test_montant_juste_au_dessus_du_plafond():
    with pytest.raises(ValidationError):
        PaymentCreate(
            method="cash",
            amount=MAX_PAYMENT_AMOUNT + Decimal("0.01"),
            idempotency_key=uuid.uuid4(),
        )


def test_montant_extreme_rejete_avant_la_base():
    # Avant la borne : ce montant débordait Numeric(10,2) au flush → 500.
    with pytest.raises(ValidationError):
        PaymentCreate(
            method="cash",
            amount=Decimal(99999999999),
            idempotency_key=uuid.uuid4(),
        )


# --- Quantités des lignes de commande ------------------------------------


def test_group_size_au_plafond_passe_le_schema():
    it = ReservationItemCreate(
        product_code="group_visit",
        group_size=MAX_PERSONS_PER_RESERVATION,
        visit_date=TODAY,
    )
    assert it.group_size == MAX_PERSONS_PER_RESERVATION


def test_group_size_au_dessus_du_plafond():
    with pytest.raises(ValidationError):
        ReservationItemCreate(
            product_code="group_visit",
            group_size=MAX_PERSONS_PER_RESERVATION + 1,
        )


def test_group_size_depassement_integer():
    # Au-delà de la capacité Integer PostgreSQL : rejeté en 422
    # avant toute insertion (auparavant → erreur SQL → 500).
    with pytest.raises(ValidationError):
        ReservationItemCreate(
            product_code="group_visit",
            group_size=3_000_000_000,
        )


def test_extra_children_au_dessus_du_plafond():
    with pytest.raises(ValidationError):
        ReservationItemCreate(
            product_code="family_museum",
            extra_children=MAX_PERSONS_PER_RESERVATION + 1,
        )


def test_trop_de_lignes_commande():
    with pytest.raises(ValidationError):
        order(
            *[
                item("museum_entry", category="adult", visit_date=TODAY)
            ]
            * (MAX_ITEMS_PER_RESERVATION + 1)
        )


def test_max_lignes_commande_accepte():
    resa = order(
        *[item("museum_entry", category="adult", visit_date=TODAY)]
        * MAX_ITEMS_PER_RESERVATION
    )
    assert len(resa.items) == MAX_ITEMS_PER_RESERVATION


def test_email_extreme_rejete():
    # EmailStr borne l'adresse à 254 car. (RFC 5321) — sous la colonne
    # varchar(320) : la borne existe déjà, on verrouille le comportement.
    with pytest.raises(ValidationError):
        ReservationCreate(
            customer_email=f"{'a' * 310}@pirates-tres-long.fr",
            items=[item("museum_entry", category="adult")],
        )


# --- Plafond agrégé vérifié en service ------------------------------------


async def test_total_personnes_plafonne_en_agregat(db, catalog, today):
    # Deux lignes valides chacune (≤ 120), mais 60 + 61 > 120 en somme.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("group_visit", group_size=60, visit_date=today),
                item("group_visit", group_size=61, visit_date=today),
            ),
        )
    assert e.value.status_code == 400
    assert "120" in e.value.detail


async def test_famille_depasse_le_plafond_agrege(db, catalog, today):
    # extra_children=117 est sous la borne du champ, mais la famille
    # couvre 2 adultes + 2 + 117 enfants = 121 personnes → 400.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("family_museum", extra_children=117, visit_date=today)
            ),
        )
    assert e.value.status_code == 400
    assert "120" in e.value.detail


async def test_commande_exactement_120_personnes(db, catalog, today):
    # Borne haute incluse : le produit groupe sans jauge est le seul
    # cas où 120 personnes tiennent sur une seule ligne.
    resa = await create_reservation(
        db,
        order(item("group_visit", group_size=120, visit_date=today)),
    )
    assert resa.total_price == Decimal("1200.00")
