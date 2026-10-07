from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.db.session import get_db
from app.models.event import Event
from app.models.session import Session
from app.schemas.event import EventRead

router = APIRouter(tags=["events"])

MUSEUM_TZ = ZoneInfo("Europe/Paris")


@router.get("/events", response_model=list[EventRead])
async def list_events(
    date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[EventRead]:
    """Catalogue du musée : événements actifs et leurs séances.

    Avec `?date=YYYY-MM-DD`, ne renvoie que les événements ayant au moins
    une séance ce jour-là (journée civile Europe/Paris), en ne chargeant
    que les séances de cette date.
    """
    stmt = (
        select(Event)
        .where(Event.is_active.is_(True))
        .order_by(Event.title)
    )

    if date is not None:
        day_start = datetime.combine(date, time.min, tzinfo=MUSEUM_TZ).astimezone(
            UTC
        )
        day_end = day_start + timedelta(days=1)
        stmt = (
            stmt.join(Event.sessions)
            .where(Session.start_time >= day_start, Session.start_time < day_end)
            .options(contains_eager(Event.sessions))
            .order_by(Event.title, Session.start_time)
        )
    else:
        stmt = stmt.options(selectinload(Event.sessions))

    result = await db.execute(stmt)
    return result.scalars().unique().all()
