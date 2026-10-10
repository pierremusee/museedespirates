"""Bornes d'entrée des montants et quantités (AUDIT-008).

Sans limites explicites, une valeur démesurée provoquait un
dépassement PostgreSQL évitable (Numeric(10,2), Integer) renvoyé
en 500, ou une création massive de billets sur les produits sans
jauge.

Bornes retenues (décision métier, voir schemas/reservation.py) :
- 120 personnes couvertes par commande ;
- 120 lignes (items) par commande — les interfaces émettent 1 ligne
  par personne individuelle, la borne des lignes ne peut donc pas
  être inférieure au plafond de personnes ;
- 50 000 € par tranche d'encaissement ;
- email déjà borné à 254 car. par EmailStr (RFC 5321) < colonne 320.

Comptage des personnes : les produits `is_addon` (séance
supplémentaire) ne comptent pas — chaque ligne add-on exige une
personne couverte par un produit de base dans la même commande
(règle de couverture), ce sont des accès supplémentaires, pas des
personnes en plus.

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

from app.models import ProductKind
from app.schemas.reservation import (
    MAX_ITEMS_PER_RESERVATION,
    MAX_PAYMENT_AMOUNT,
    MAX_PERSONS_PER_RESERVATION,
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import create_reservation
from tests.factories import add_product, add_session, museum_comp
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


# --- Email : borne EmailStr (254 car., < colonne varchar(320)) ------------


def _email_of(total_len: int) -> str:
    """Adresse valide de `total_len` caractères exacts : local part de
    64 car. (borne RFC), domaine en labels ≤ 63 car."""
    domain_len = total_len - 65  # 64 local + '@'
    labels = ["b" * 63, "c" * 63]
    remaining = domain_len - sum(len(label) for label in labels) - 2
    labels.append("d" * (remaining - 3) if remaining > 3 else "x")
    labels.append("fr")
    return "a" * 64 + "@" + ".".join(labels)


def test_email_a_la_borne_254_accepte():
    email = _email_of(254)
    assert len(email) == 254
    resa = ReservationCreate(
        customer_email=email,
        items=[item("museum_entry", category="adult")],
    )
    assert resa.customer_email == email


def test_email_au_dessus_de_la_borne_rejete():
    with pytest.raises(ValidationError):
        ReservationCreate(
            customer_email=_email_of(255),
            items=[item("museum_entry", category="adult")],
        )


def test_email_extreme_rejete():
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


async def test_onze_billets_individuels_acceptes(db, catalog, today):
    # Les interfaces émettent 1 item par billet individuel : 11 lignes
    # = 11 personnes — doit passer (la borne lignes est au plafond
    # personnes, pas en dessous).
    resa = await create_reservation(
        db,
        order(
            *[
                item("museum_entry", category="adult", visit_date=today)
                for _ in range(11)
            ]
        ),
    )
    assert len(resa.items) == 11
    assert resa.total_price == Decimal("132.00")


async def test_seance_supplementaire_ne_double_compte_pas(db, catalog):
    # 12 billets théâtre + 12 séances supplémentaires : les add-ons
    # portent des accès pour des personnes déjà comptées — le total
    # reste 12, pas 24.
    sess_a = await add_session(db, catalog.theater, days=10, cap=50)
    sess_b = await add_session(db, catalog.theater, days=10, hour=18, cap=50)
    resa = await create_reservation(
        db,
        order(
            *[
                item("theater_show", category="adult", session_id=sess_a.id)
                for _ in range(12)
            ],
            *[
                item("extra_show", category="adult", session_id=sess_b.id)
                for _ in range(12)
            ],
        ),
    )
    assert len(resa.items) == 24
    assert resa.total_price == Decimal("180.00")  # 12 x 10 + 12 x 5


async def test_addons_au_plafond_exact_de_lignes(db, catalog):
    # Plafond doublement exact : 60 billets théâtre + 60 add-ons =
    # 120 lignes (borne items) portant 60 personnes physiques. Un
    # comptage des personnes add-on donnerait 120 = plafond → le test
    # ne distingue pas le double comptage (voir le test groupe
    # ci-dessous qui le rend observable).
    sess_a = await add_session(db, catalog.theater, days=11, cap=100)
    sess_b = await add_session(db, catalog.theater, days=11, hour=18, cap=100)
    resa = await create_reservation(
        db,
        order(
            *[
                item("theater_show", category="adult", session_id=sess_a.id)
                for _ in range(60)
            ],
            *[
                item("extra_show", category="adult", session_id=sess_b.id)
                for _ in range(60)
            ],
        ),
    )
    assert len(resa.items) == 120


async def test_addon_groupe_compte_une_seule_fois(db, catalog, today):
    # Règle de comptage rendue observable : avec des add-ons SIMPLE
    # (1 personne/ligne) et la borne de 120 lignes, le double
    # comptage ne pourrait jamais dépasser le plafond. Un add-on
    # groupe — combinaison que le modèle permet — porte 60 personnes
    # déjà couvertes par le groupe de base : 70 comptées, pas 130.
    await add_product(
        db,
        code="group_museum_extra",
        label="Journée musée supplémentaire (groupe)",
        kind=ProductKind.GROUP,
        is_addon=True,
        price_adult=Decimal("4.00"),
        components=[museum_comp(catalog.musee)],
    )
    resa = await create_reservation(
        db,
        order(
            item("group_visit", group_size=70, visit_date=today),
            item("group_museum_extra", group_size=60, visit_date=today),
        ),
    )
    assert len(resa.items) == 2


async def test_addons_ne_masquent_pas_le_depassement(db, catalog, today):
    # Une commande contenant des add-ons qui dépasse le plafond par
    # ses lignes de base reste rejetée — la présence d'items
    # `is_addon` ne neutralise pas le contrôle agrégé.
    sess = await add_session(db, catalog.theater, days=12, cap=150)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("group_visit", group_size=60, visit_date=today),
                item("group_visit", group_size=61, visit_date=today),
                item("extra_show", category="adult", session_id=sess.id),
            ),
        )
    assert e.value.status_code == 400
    assert "120" in e.value.detail
