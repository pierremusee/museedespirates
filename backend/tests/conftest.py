"""Fixtures pytest — tests métier contre Postgres réel (musee_test).

Isolation : chaque test tourne dans une transaction externe rollbackée.
Les commit() des services libèrent un savepoint interne
(join_transaction_mode="create_savepoint") sans valider la transaction
externe — la base reste vierge entre les tests.
"""

import os
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import (
    EventType,
    Product,
    ProductKind,
    SeasonalPeriod,
)
from tests.factories import (
    add_event,
    add_session,
    museum_comp,
    simple_product,
    theater_comp,
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/musee_test",
)


@pytest_asyncio.fixture
async def db():
    """Session sur musee_test, rollbackée en fin de test."""
    engine = create_async_engine(TEST_DATABASE_URL)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def catalog(db: AsyncSession) -> SimpleNamespace:
    """Mini-catalogue de test calqué sur la grille basse saison de
    scripts/seed_db.py — recréé par test (rollback), jamais partagé."""
    musee = await add_event(db, "Musée (pytest)", EventType.PERMANENT_EXHIBITION)
    theater = await add_event(db, "Théâtre (pytest)", EventType.THEATER)
    inactive_theater = await add_event(
        db, "Théâtre inactif (pytest)", EventType.THEATER, is_active=False
    )
    session = await add_session(db, theater, days=10, hour=14)
    session2 = await add_session(db, theater, days=10, hour=18)
    museum_session = await add_session(db, musee, days=10, hour=9)

    products = {
        "museum_entry": simple_product(
            "museum_entry", "12", "8", "9", [museum_comp(musee)]
        ),
        "theater_show": simple_product(
            "theater_show", "10", "7", "8", [theater_comp()]
        ),
        "extra_show": simple_product(
            "extra_show", "5", "3.50", "4", [theater_comp()], is_addon=True
        ),
        "pass_1_show": Product(
            code="pass_1_show",
            label="Pass 1 Spectacle",
            kind=ProductKind.PASS,
            price_adult=Decimal(20),
            price_child=Decimal(13),
            price_reduced=Decimal(15),
            components=[museum_comp(musee), theater_comp()],
        ),
        "pass_2_shows": Product(
            code="pass_2_shows",
            label="Pass 2 Spectacles",
            kind=ProductKind.PASS,
            price_adult=Decimal(24),
            price_child=Decimal(16),
            price_reduced=Decimal(18),
            components=[museum_comp(musee), theater_comp(2)],
        ),
        "family_museum": Product(
            code="family_museum",
            label="Famille Musée",
            kind=ProductKind.FAMILY,
            family_base_price=Decimal(35),
            extra_child_price=Decimal(6),
            components=[museum_comp(musee)],
        ),
        "group_visit": Product(
            code="group_visit",
            label="Visite Groupe",
            kind=ProductKind.GROUP,
            price_adult=Decimal(10),
            components=[museum_comp(musee)],
        ),
    }
    db.add_all(products.values())
    await db.flush()
    return SimpleNamespace(
        musee=musee,
        theater=theater,
        inactive_theater=inactive_theater,
        session=session,
        session2=session2,
        museum_session=museum_session,
        **products,
    )


@pytest_asyncio.fixture
async def high_season(db: AsyncSession) -> SeasonalPeriod:
    """Période haute saison couvrant les jours de test relatifs."""
    period = SeasonalPeriod(
        name="HS (pytest)",
        start_date=date.today() - timedelta(days=365),
        end_date=date.today() + timedelta(days=365),
    )
    db.add(period)
    await db.flush()
    return period


@pytest_asyncio.fixture
def today() -> date:
    return date.today()
