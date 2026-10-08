"""Factories de fixtures — objets de catalogue créés par test
(dans la transaction rollbackée de la fixture `db`)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ComponentType,
    Event,
    EventType,
    Product,
    ProductComponent,
    ProductKind,
    Session,
)

MUSEUM_TZ = ZoneInfo("Europe/Paris")
UTC = ZoneInfo("UTC")


def future_at(days: int, hour: int, minute: int = 0) -> datetime:
    """Datetime UTC d'un jour futur (Europe/Paris) — les séances passées
    ne sont plus vendables, les fixtures doivent rester dans le futur."""
    return datetime.combine(
        date.today() + timedelta(days=days), time(hour, minute),
        tzinfo=MUSEUM_TZ,
    ).astimezone(UTC)


async def add_event(
    db: AsyncSession,
    title: str,
    event_type: EventType,
    is_active: bool = True,
) -> Event:
    event = Event(title=title, event_type=event_type, is_active=is_active)
    db.add(event)
    await db.flush()
    return event


async def add_session(
    db: AsyncSession,
    event: Event,
    days: int = 10,
    hour: int = 14,
    cap: int = 10,
) -> Session:
    session = Session(
        event_id=event.id,
        start_time=future_at(days, hour),
        max_capacity=cap,
    )
    db.add(session)
    await db.flush()
    return session


def museum_comp(event: Event, qty: int = 1) -> ProductComponent:
    return ProductComponent(
        component_type=ComponentType.MUSEUM_DAY, quantity=qty,
        event_id=event.id,
    )


def theater_comp(qty: int = 1) -> ProductComponent:
    return ProductComponent(
        component_type=ComponentType.THEATER_SESSION, quantity=qty
    )


async def add_product(db: AsyncSession, **kw) -> Product:
    product = Product(**kw)
    db.add(product)
    await db.flush()
    return product


def simple_product(
    code: str, a: str, c: str, r: str, comps: list, **kw
) -> Product:
    return Product(
        code=code,
        label=code,
        kind=ProductKind.SIMPLE,
        price_adult=Decimal(a),
        price_child=Decimal(c),
        price_reduced=Decimal(r),
        components=comps,
        **kw,
    )
