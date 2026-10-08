"""Tarification DFC n°5 — modificateurs haute saison, bornes de période,
forfaits famille et tarifs groupe (au niveau create_reservation)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import SalesChannel, SeasonalPeriod, TicketCategory
from app.schemas.reservation import ReservationCreate, ReservationItemCreate
from app.services.pricing import individual_modifier, is_high_season
from app.services.reservation_service import create_reservation


def _item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def _order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (TicketCategory.ADULT, Decimal("2.00")),
        (TicketCategory.REDUCED, Decimal("2.00")),
        (TicketCategory.CHILD, Decimal("1.00")),
    ],
)
def test_modificateur_haute_saison_par_categorie(category, expected):
    assert individual_modifier(category, high_season=True) == expected


@pytest.mark.parametrize(
    "category",
    [TicketCategory.ADULT, TicketCategory.REDUCED, TicketCategory.CHILD],
)
def test_modificateur_basse_saison_nul(category):
    assert individual_modifier(category, high_season=False) == Decimal(0)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2100, 6, 30), False),   # veille de la période
        (date(2100, 7, 1), True),     # borne de début incluse
        (date(2100, 8, 15), True),
        (date(2100, 8, 31), True),    # borne de fin incluse
        (date(2100, 9, 1), False),
    ],
)
async def test_is_high_season_bornes_inclusives(db, day, expected):
    db.add(
        SeasonalPeriod(
            name="HS (pytest)",
            start_date=date(2100, 7, 1),
            end_date=date(2100, 8, 31),
        )
    )
    await db.flush()
    assert await is_high_season(db, day) is expected


async def test_is_high_season_sans_periode(db):
    assert await is_high_season(db, date(2100, 7, 15)) is False


# --- Tarifs forfaitaires (famille) et par personne (groupe) ------------------


async def _season(db, hs: bool, today: date) -> None:
    if hs:
        db.add(
            SeasonalPeriod(
                name="HS (pytest)",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=1),
            )
        )
        await db.flush()


@pytest.mark.parametrize(
    ("hs", "expected_total", "expected_mod"),
    [
        # forfait 35 € + 1 enfant supp. à 6 € ; modificateur famille 5 €.
        (False, Decimal("41.00"), Decimal("0.00")),
        (True, Decimal("46.00"), Decimal("5.00")),
    ],
)
async def test_forfait_famille_prix_et_saison(
    db, catalog, today, hs, expected_total, expected_mod
):
    await _season(db, hs, today)
    resa = await create_reservation(
        db,
        _order(
            _item("family_museum", visit_date=today, extra_children=1)
        ),
    )
    item_ = resa.items[0]
    assert resa.total_price == expected_total
    assert item_.computed_price == expected_total
    assert item_.season_modifier == expected_mod


@pytest.mark.parametrize(
    ("hs", "expected_total", "expected_mod"),
    [
        # 10 €/personne x 8 ; modificateur adulte 2 €/personne en HS.
        (False, Decimal("80.00"), Decimal("0.00")),
        (True, Decimal("96.00"), Decimal("16.00")),
    ],
)
async def test_tarif_groupe_par_personne(
    db, catalog, today, hs, expected_total, expected_mod
):
    await _season(db, hs, today)
    resa = await create_reservation(
        db,
        _order(_item("group_visit", group_size=8, visit_date=today)),
    )
    item_ = resa.items[0]
    assert resa.total_price == expected_total
    assert item_.season_modifier == expected_mod
    assert item_.category == "group"
