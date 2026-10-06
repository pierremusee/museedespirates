"""Seed la base : événements, séances, catalogue produits (DFC n°5).

Lancer depuis backend/ :  ./venv/Scripts/python.exe scripts/seed_db.py
"""

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import (
    ComponentType,
    Event,
    EventType,
    Product,
    ProductComponent,
    ProductKind,
    SeasonalPeriod,
    Session,
)

MUSEUM_TZ = ZoneInfo("Europe/Paris")


def session_at(day: date, hour: int, minute: int = 0) -> datetime:
    """Séance à heure fixe Europe/Paris, convertie en UTC."""
    return datetime.combine(day, time(hour, minute), tzinfo=MUSEUM_TZ).astimezone(
        ZoneInfo("UTC")
    )


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as db:
        # Idempotent : les événements existants sont réutilisés, les
        # produits manquants sont ajoutés (le catalogue peut évoluer).
        musee = (
            await db.execute(
                select(Event).where(Event.title == "Musée des Pirates")
            )
        ).scalars().first()
        kraken = (
            await db.execute(
                select(Event).where(Event.title == "Théâtre du Kraken")
            )
        ).scalars().first()

        if musee is None:
            musee = Event(
                title="Musée des Pirates",
                event_type=EventType.PERMANENT_EXHIBITION,
            )
            db.add(musee)
        if kraken is None:
            kraken = Event(
                title="Théâtre du Kraken",
                event_type=EventType.THEATER,
            )
            db.add(kraken)
            await db.flush()

            # Théâtre : séances à 10h30 et 15h00 sur les 7 prochains jours.
            today = datetime.now(MUSEUM_TZ).date()
            db.add_all(
                Session(
                    event_id=kraken.id,
                    start_time=session_at(
                        today + timedelta(days=d), hour, minute
                    ),
                    max_capacity=80,
                )
                for d in range(7)
                for hour, minute in ((10, 30), (15, 0))
            )
        await db.flush()

        # Catalogue produits — grille basse saison (DFC n°5).
        products = [
            Product(
                code="museum_entry",
                label="Entrée Musée",
                kind=ProductKind.SIMPLE,
                price_adult=Decimal("12.00"),
                price_child=Decimal("8.00"),
                price_reduced=Decimal("9.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.MUSEUM_DAY,
                        quantity=1,
                        event_id=musee.id,
                    )
                ],
            ),
            Product(
                code="theater_show",
                label="Billet Théâtre — 1 séance",
                kind=ProductKind.SIMPLE,
                price_adult=Decimal("10.00"),
                price_child=Decimal("7.00"),
                price_reduced=Decimal("8.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.THEATER_SESSION,
                        quantity=1,
                    )
                ],
            ),
            Product(
                # Séance additionnelle à tarif réduit : se combine à un
                # pass/billet dans la même réservation — les droits sont
                # fusionnés sur le même QR (1 billet par personne).
                code="extra_show",
                label="Séance supplémentaire (tarif réduit)",
                kind=ProductKind.SIMPLE,
                price_adult=Decimal("5.00"),
                price_child=Decimal("3.50"),
                price_reduced=Decimal("4.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.THEATER_SESSION,
                        quantity=1,
                    )
                ],
            ),
            Product(
                code="pass_1_show",
                label="Pass 1 Spectacle (Musée + Théâtre)",
                kind=ProductKind.PASS,
                price_adult=Decimal("20.00"),
                price_child=Decimal("13.00"),
                price_reduced=Decimal("15.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.MUSEUM_DAY,
                        quantity=1,
                        event_id=musee.id,
                    ),
                    ProductComponent(
                        component_type=ComponentType.THEATER_SESSION,
                        quantity=1,
                    ),
                ],
            ),
            Product(
                code="pass_2_shows",
                label="Pass 2 Spectacles (Musée + 2 séances)",
                kind=ProductKind.PASS,
                price_adult=Decimal("26.00"),
                price_child=Decimal("18.00"),
                price_reduced=Decimal("21.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.MUSEUM_DAY,
                        quantity=1,
                        event_id=musee.id,
                    ),
                    ProductComponent(
                        component_type=ComponentType.THEATER_SESSION,
                        quantity=2,
                    ),
                ],
            ),
            Product(
                code="family_museum",
                label="Forfait Famille — Musée seul (2A + 2E)",
                kind=ProductKind.FAMILY,
                family_base_price=Decimal("35.00"),
                extra_child_price=Decimal("6.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.MUSEUM_DAY,
                        quantity=1,
                        event_id=musee.id,
                    )
                ],
            ),
            Product(
                code="family_pass_1_show",
                label="Forfait Famille — Pass 1 Spectacle (2A + 2E)",
                kind=ProductKind.FAMILY,
                family_base_price=Decimal("58.00"),
                extra_child_price=Decimal("10.00"),
                components=[
                    ProductComponent(
                        component_type=ComponentType.MUSEUM_DAY,
                        quantity=1,
                        event_id=musee.id,
                    ),
                    ProductComponent(
                        component_type=ComponentType.THEATER_SESSION,
                        quantity=1,
                    ),
                ],
            ),
        ]
        existing_codes = set(
            (await db.execute(select(Product.code))).scalars()
        )
        new_products = [p for p in products if p.code not in existing_codes]
        db.add_all(new_products)

        # Période haute saison de référence (vacances d'été 2026).
        has_period = await db.scalar(
            select(SeasonalPeriod.id).limit(1)
        )
        if has_period is None:
            db.add(
                SeasonalPeriod(
                    name="Haute saison — Été 2026",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 8, 31),
                )
            )

        await db.commit()
        print(
            f"Seed OK : {len(new_products)} produit(s) ajouté(s) "
            f"sur {len(products)} au catalogue."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
