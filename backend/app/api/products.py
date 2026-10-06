from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.product import Product
from app.schemas.product import ProductRead, SeasonCheck
from app.services.pricing import is_high_season

router = APIRouter(tags=["products"])


@router.get("/products", response_model=list[ProductRead])
async def list_products(
    db: AsyncSession = Depends(get_db),
) -> list[ProductRead]:
    """Catalogue des produits vendables (billets, pass, forfaits)."""
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.components))
        .where(Product.is_active.is_(True))
        .order_by(Product.code)
    )
    return result.scalars().all()


@router.get("/seasonal/check", response_model=SeasonCheck)
async def check_high_season(
    date: date_type = Query(...),
    db: AsyncSession = Depends(get_db),
) -> SeasonCheck:
    """Indique si une date tombe en haute saison (modificateur DFC n°5)."""
    return SeasonCheck(
        date=date, high_season=await is_high_season(db, date)
    )
