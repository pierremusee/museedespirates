from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seasonal_period import SeasonalPeriod
from app.models.ticket import TicketCategory

# Modificateurs « Haute Saison » (DFC n°5 §4) — appliqués au calcul,
# jamais stockés comme tarifs en base.
MODIFIER_ADULT_REDUCED = Decimal("2.00")
MODIFIER_CHILD = Decimal("1.00")
MODIFIER_FAMILY = Decimal("5.00")


async def is_high_season(db: AsyncSession, day: date) -> bool:
    stmt = (
        select(SeasonalPeriod.id)
        .where(SeasonalPeriod.start_date <= day, SeasonalPeriod.end_date >= day)
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


def individual_modifier(category: TicketCategory, high_season: bool) -> Decimal:
    if not high_season:
        return Decimal(0)
    if category in (TicketCategory.ADULT, TicketCategory.REDUCED):
        return MODIFIER_ADULT_REDUCED
    return MODIFIER_CHILD
