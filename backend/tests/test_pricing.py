"""Tarification DFC n°5 — modificateurs haute saison et bornes de période."""

from datetime import date
from decimal import Decimal

import pytest

from app.models import SeasonalPeriod, TicketCategory
from app.services.pricing import individual_modifier, is_high_season


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
