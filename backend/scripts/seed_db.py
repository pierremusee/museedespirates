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

# Programme du Théâtre du Kraken : chaque production est un événement
# `theater` porteur de ses propres séances à heure fixe (jauge 80).
SHOWS = [
    {
        "title": "À l'Abordage !",
        # Renommage 2026-10-06 (ex « Le Dernier Voyage du Black Kraken »).
        "former": "Le Dernier Voyage du Black Kraken",
        "description": (
            "Alors que le Black Kraken s'apprête à quitter le port pour sa "
            "dernière traversée, son capitaine reçoit une mystérieuse lettre "
            "qui pourrait bouleverser son destin. Entre mutinerie, chasse au "
            "trésor et secrets de famille, l'équipage devra choisir entre la "
            "fortune et la liberté. Une aventure théâtrale mêlant suspense, "
            "humour et grands récits de piraterie."
        ),
        "hour": 10,
        "minute": 30,
    },
    {
        "title": "Les Conjurés",
        # Renommage 2026-10-06 (ex « La Malédiction de l'Île aux Brumes »).
        "former": "La Malédiction de l'Île aux Brumes",
        "description": (
            "Une légende raconte qu'un trésor oublié repose sur une île qui "
            "n'apparaît sur aucune carte. Lorsqu'un jeune équipage découvre "
            "par hasard sa position, il se lance à sa recherche. Mais l'île "
            "semble vivante et chaque énigme les rapproche un peu plus d'une "
            "ancienne malédiction. Un spectacle familial mêlant aventure, "
            "magie, humour et effets scéniques."
        ),
        "hour": 15,
        "minute": 0,
    },
]


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

        # Productions en carte : un événement `theater` par pièce, avec sa
        # description (mise à jour si le texte a évolué).
        shows: dict[tuple[int, int], Event] = {}
        for spec in SHOWS:
            show = (
                await db.execute(
                    select(Event).where(Event.title == spec["title"])
                )
            ).scalars().first()
            if show is None and spec.get("former"):
                # Renommage : reprendre l'événement sous son ancien titre
                # plutôt que d'en créer un doublon.
                show = (
                    await db.execute(
                        select(Event).where(Event.title == spec["former"])
                    )
                ).scalars().first()
                if show is not None:
                    show.title = spec["title"]
            if show is None:
                show = Event(
                    title=spec["title"],
                    description=spec["description"],
                    event_type=EventType.THEATER,
                )
                db.add(show)
                await db.flush()
            elif show.description != spec["description"]:
                show.description = spec["description"]
            shows[(spec["hour"], spec["minute"])] = show

        if kraken is not None:
            # La salle « Théâtre du Kraken » accueille désormais les
            # productions ci-dessus : ses séances existantes sont rattachées
            # à la pièce correspondant à leur horaire, puis la salle est
            # retirée du catalogue (désactivation, non destructif).
            old_sessions = (
                await db.execute(
                    select(Session).where(Session.event_id == kraken.id)
                )
            ).scalars().all()
            for sess in old_sessions:
                paris = sess.start_time.astimezone(MUSEUM_TZ)
                target = shows.get((paris.hour, paris.minute))
                if target is not None:
                    sess.event_id = target.id
            kraken.is_active = False
            # L'événement salle ne doit pas être recréé par la suite.
            kraken = None

        # Séances des 7 prochains jours pour chaque production (idempotent :
        # on ne crée que les créneaux absents — y compris ceux déjà
        # réassignés depuis l'ancienne salle).
        today = datetime.now(MUSEUM_TZ).date()
        for (hour, minute), show in shows.items():
            existing = set(
                (
                    await db.execute(
                        select(Session.start_time).where(
                            Session.event_id == show.id
                        )
                    )
                ).scalars()
            )
            db.add_all(
                Session(
                    event_id=show.id,
                    start_time=start,
                    max_capacity=80,
                )
                for d in range(7)
                if (start := session_at(today + timedelta(days=d), hour, minute))
                not in existing
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
                # Séance additionnelle à tarif réduit : add-on qui exige
                # un billet/pass avec séance dans la même commande
                # (règle serveur is_addon) — sinon musée + supp. à 17 €
                # court-circuiterait le Pass 1 Spectacle à 20 €.
                code="extra_show",
                label="Séance supplémentaire (tarif réduit)",
                kind=ProductKind.SIMPLE,
                is_addon=True,
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
                # Grille cohérente : chaque tarif reste strictement sous
                # la composition pass_1_show + extra_show (25/16,50/19 €).
                code="pass_2_shows",
                label="Pass 2 Spectacles (Musée + 2 séances)",
                kind=ProductKind.PASS,
                price_adult=Decimal("24.00"),
                price_child=Decimal("16.00"),
                price_reduced=Decimal("18.00"),
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
        # Le seed est la source de vérité de la grille : les produits
        # existants sont resynchronisés (label, prix, kind, is_addon) —
        # les composants, eux, ne sont pas retouchés.
        existing = {
            p.code: p
            for p in (
                await db.execute(
                    select(Product).where(
                        Product.code.in_([p.code for p in products])
                    )
                )
            ).scalars()
        }
        new_products = []
        for spec in products:
            row = existing.get(spec.code)
            if row is None:
                new_products.append(spec)
                continue
            row.label = spec.label
            row.kind = spec.kind
            row.is_addon = bool(spec.is_addon)
            row.price_adult = spec.price_adult
            row.price_child = spec.price_child
            row.price_reduced = spec.price_reduced
            row.family_base_price = spec.family_base_price
            row.extra_child_price = spec.extra_child_price
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
