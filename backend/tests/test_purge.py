"""Purge des paniers expirés (purge_expired_reservations) — TTL 15 min.

Horloge : `datetime` de reservation_service figé au `now` du test
(fixture `frozen_now`) → `cutoff = T0 - 15 min` déterministe ;
`created_at` des réservations est ensuite positionné explicitement par
l'ORM relativement au cutoff. Aucune dépendance à l'heure réelle du
run, aucun skip — borne exacte du TTL testable.

Branches volontairement non testées (défensives) :
- `max(0, booked_seats - persons)` : le clamp suppose une jauge
  incohérente, jamais produite par create_reservation ;
- `scalars().unique()` : dédup interne du selectinload, couverte par
  usage.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.reservation_service as reservation_service
from app.models import (
    PaymentMethod,
    ReservationStatus,
    SalesChannel,
    Session,
)
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import (
    PENDING_TTL,
    add_payment,
    cancel_reservation,
    create_reservation,
    purge_expired_reservations,
)
from tests.factories import MUSEUM_TZ, UTC


def paris_today() -> date:
    return datetime.now(MUSEUM_TZ).date()


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def pay(amount) -> PaymentCreate:
    return PaymentCreate(
        method=PaymentMethod.CB,
        amount=Decimal(amount),
        idempotency_key=uuid.uuid4(),
    )


async def booked(db: AsyncSession, session_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(Session.booked_seats).where(Session.id == session_id)
        )
    ).scalar_one()


def day_of(session) -> date:
    return session.start_time.astimezone(MUSEUM_TZ).date()


@pytest.fixture
def frozen_now(monkeypatch) -> datetime:
    """Horloge figée dans reservation_service — cutoff déterministe."""
    fixed = datetime.now(UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed.replace(tzinfo=None)
            return fixed.astimezone(tz)

    monkeypatch.setattr(reservation_service, "datetime", _FrozenDatetime)
    return fixed


def cutoff(fixed: datetime) -> datetime:
    return fixed - PENDING_TTL


# --- Cas nominaux ---------------------------------------------------------------


async def test_pending_expire_est_purge_et_libere_la_jauge(
    db, catalog, frozen_now
):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    assert await booked(db, catalog.session.id) == 1
    resa.created_at = cutoff(frozen_now) - timedelta(seconds=1)
    await db.flush()
    purged = await purge_expired_reservations(db)
    assert purged == [resa.id]
    assert resa.status == ReservationStatus.EXPIRED
    assert await booked(db, catalog.session.id) == 0


async def test_pending_recent_conserve(db, catalog, frozen_now):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    resa.created_at = frozen_now - timedelta(minutes=5)
    await db.flush()
    assert await purge_expired_reservations(db) == []
    assert resa.status == ReservationStatus.PENDING
    assert await booked(db, catalog.session.id) == 1


async def test_confirmed_ancien_conserve(db, catalog, frozen_now):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    resa = (await add_payment(db, resa.id, pay("10.00"))).reservation
    resa.created_at = frozen_now - timedelta(hours=2)
    await db.flush()
    assert await purge_expired_reservations(db) == []
    assert resa.status == ReservationStatus.CONFIRMED
    assert await booked(db, catalog.session.id) == 1


async def test_cancelled_ancien_conserve(db, catalog, frozen_now):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    await cancel_reservation(db, resa.id)
    resa.created_at = frozen_now - timedelta(hours=2)
    await db.flush()
    assert await purge_expired_reservations(db) == []
    assert resa.status == ReservationStatus.CANCELLED


# --- Sélectivité -----------------------------------------------------------------


async def test_purge_selective_sur_meme_seance(db, catalog, frozen_now):
    # Trois commandes occupent la même séance : seule la commande
    # expirée rend ses sièges.
    old = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    recent = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="child",
                session_id=catalog.session.id,
            )
        ),
    )
    confirmed = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="reduced",
                session_id=catalog.session.id,
            )
        ),
    )
    confirmed = (
        await add_payment(db, confirmed.id, pay("8.00"))
    ).reservation
    old.created_at = cutoff(frozen_now) - timedelta(seconds=1)
    recent.created_at = frozen_now - timedelta(minutes=5)
    confirmed.created_at = frozen_now - timedelta(hours=2)
    await db.flush()
    assert await booked(db, catalog.session.id) == 3
    purged = await purge_expired_reservations(db)
    assert purged == [old.id]
    assert old.status == ReservationStatus.EXPIRED
    assert recent.status == ReservationStatus.PENDING
    assert confirmed.status == ReservationStatus.CONFIRMED
    assert await booked(db, catalog.session.id) == 2


# --- Restitution -----------------------------------------------------------------


async def test_restitution_multi_items_meme_seance(db, catalog, frozen_now):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            ),
            item(
                "theater_show", category="child",
                session_id=catalog.session.id,
            ),
        ),
    )
    assert await booked(db, catalog.session.id) == 2
    resa.created_at = cutoff(frozen_now) - timedelta(seconds=1)
    await db.flush()
    await purge_expired_reservations(db)
    assert await booked(db, catalog.session.id) == 0


async def test_restitution_plusieurs_seances(db, catalog, frozen_now):
    # Pass 2 Spectacles : 1 siège prélevé sur chaque séance, chaque
    # jauge est restituée indépendamment.
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_2_shows", category="adult",
                visit_date=day_of(catalog.session),
                session_ids=[catalog.session.id, catalog.session2.id],
            )
        ),
    )
    assert await booked(db, catalog.session.id) == 1
    assert await booked(db, catalog.session2.id) == 1
    resa.created_at = cutoff(frozen_now) - timedelta(seconds=1)
    await db.flush()
    purged = await purge_expired_reservations(db)
    assert purged == [resa.id]
    assert await booked(db, catalog.session.id) == 0
    assert await booked(db, catalog.session2.id) == 0


async def test_purge_seance_supprimee_sans_crash(db, catalog, frozen_now):
    # Séance retirée du catalogue entre commande et purge : la
    # restitution l'ignore (l. 847), le statut est tout de même posé.
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    resa.created_at = cutoff(frozen_now) - timedelta(seconds=1)
    await db.delete(catalog.session)
    await db.flush()
    purged = await purge_expired_reservations(db)
    assert purged == [resa.id]
    assert resa.status == ReservationStatus.EXPIRED


# --- Borne exacte du TTL ------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset_seconds", "expired"),
    [
        (-1, True),   # 1 s avant le cutoff -> expirée
        (0, False),   # exactement au cutoff -> conservée (< strict)
        (1, False),   # 1 s après -> conservée
    ],
)
async def test_borne_exacte_du_ttl(
    db, catalog, frozen_now, offset_seconds, expired
):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            )
        ),
    )
    resa.created_at = cutoff(frozen_now) + timedelta(seconds=offset_seconds)
    await db.flush()
    purged = await purge_expired_reservations(db)
    assert (resa.id in purged) is expired
