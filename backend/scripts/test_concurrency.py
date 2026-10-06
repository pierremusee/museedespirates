"""Démonstration de concurrence (anti-surbooking + anti double-scan).

Seed dédié minimal, puis via l'API :

  1. Surbooking concurrent : N requêtes POST /reservations tirées
     simultanément (barrière) sur une séance de capacité K.
     Attendu : exactement K succès (201), N-K rejets (400), et
     booked_seats == K en base — FOR UPDATE + CheckConstraint.
  2. Double-scan concurrent : M scans simultanés du même accès.
     Attendu : exactement un 200, les autres 400 — FOR UPDATE sur
     ticket_accesses.

Lancer depuis backend/ :
    ./venv/Scripts/python.exe scripts/test_concurrency.py
"""

import asyncio
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models import (
    ComponentType,
    Event,
    EventType,
    Product,
    ProductComponent,
    ProductKind,
    Session,
)

BASE_URL = "http://127.0.0.1:8000"
UTC = ZoneInfo("UTC")

K = 5    # capacité de la séance
N = 30   # requêtes concurrentes sur la jauge
M = 8    # scans concurrents du même accès


def post(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


async def seed() -> dict:
    engine = create_async_engine(settings.DATABASE_URL)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with maker() as db:
        theater = (
            await db.execute(
                select(Event).where(
                    Event.title == "Concurrence Théâtre (test)").limit(1)
            )
        ).scalars().first()
        musee = (
            await db.execute(
                select(Event).where(
                    Event.title == "Concurrence Musée (test)").limit(1)
            )
        ).scalars().first()
        if theater is None:
            theater = Event(
                title="Concurrence Théâtre (test)",
                event_type=EventType.THEATER,
            )
        if musee is None:
            musee = Event(
                title="Concurrence Musée (test)",
                event_type=EventType.PERMANENT_EXHIBITION,
            )
        theater.is_active = True
        musee.is_active = True
        db.add_all([theater, musee])
        await db.flush()
        # Nouvelle séance à chaque run : jauge vierge garantie.
        session = Session(
            event_id=theater.id,
            start_time=datetime.now(UTC) + timedelta(days=2),
            max_capacity=K,
        )
        db.add(session)
        wanted = {
            "concurrency_theater": Product(
                code="concurrency_theater", label="Concurrence Théâtre",
                kind=ProductKind.SIMPLE, price_adult=Decimal("10"),
                components=[ProductComponent(
                    component_type=ComponentType.THEATER_SESSION,
                    quantity=1)],
            ),
            "concurrency_museum": Product(
                code="concurrency_museum", label="Concurrence Musée",
                kind=ProductKind.SIMPLE, price_adult=Decimal("5"),
                components=[ProductComponent(
                    component_type=ComponentType.MUSEUM_DAY,
                    quantity=1, event_id=musee.id)],
            ),
        }
        existing = set(
            (await db.execute(select(Product.code))).scalars()
        )
        db.add_all(
            p for code, p in wanted.items() if code not in existing
        )
        # Réactivation non destructive : les fixtures sont désactivées en
        # fin de run (cleanup), donc le seed doit les rendre vendables.
        await db.execute(
            update(Product)
            .where(Product.code.in_(list(wanted)))
            .values(is_active=True)
        )
        # Le composant musée du produit test doit viser l'événement dédié —
        # test_booking.py realigne les composants museum_day sur le vrai
        # musée, ce qui casserait le checkpoint de scan sinon.
        await db.execute(
            update(ProductComponent)
            .where(
                ProductComponent.component_type == ComponentType.MUSEUM_DAY,
                ProductComponent.product_id
                == select(Product.id)
                .where(Product.code == "concurrency_museum")
                .scalar_subquery(),
            )
            .values(event_id=musee.id)
        )
        await db.commit()
        ids = {
            "session": str(session.id),
            "musee": str(musee.id),
        }
    await engine.dispose()
    return ids


async def cleanup() -> None:
    """Retire les fixtures du catalogue visible : le script laisse la base
    comme il l'a trouvée. Désactivation non destructive (is_active=False) —
    les lignes restent en base et le seed les réactive au run suivant."""
    engine = create_async_engine(settings.DATABASE_URL)
    maker = async_sessionmaker(engine, class_=AsyncSession)
    async with maker() as db:
        await db.execute(
            update(Product)
            .where(
                Product.code.in_(
                    ["concurrency_theater", "concurrency_museum"]
                )
            )
            .values(is_active=False)
        )
        await db.execute(
            update(Event)
            .where(
                Event.title.in_(
                    [
                        "Concurrence Théâtre (test)",
                        "Concurrence Musée (test)",
                    ]
                )
            )
            .values(is_active=False)
        )
        await db.commit()
    await engine.dispose()


def concurrent(calls: int, fn):
    """Tire `calls` requêtes le plus simultanément possible (barrière)."""
    barrier = threading.Barrier(calls)

    def worker(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=calls) as pool:
        return list(pool.map(worker, range(calls)))


async def check_booked_seats(session_id: str) -> int:
    engine = create_async_engine(settings.DATABASE_URL)
    maker = async_sessionmaker(engine, class_=AsyncSession)
    async with maker() as db:
        seats = (
            await db.execute(
                select(Session.booked_seats)
                .where(Session.id == uuid.UUID(session_id))
            )
        ).scalar_one()
    await engine.dispose()
    return seats


async def main() -> None:
    ids = await seed()
    print(f"Séance test : capacité K={K}, requêtes concurrentes N={N}")

    # --- 1. Surbooking concurrent -------------------------------------
    def try_book(i: int):
        return post(f"{BASE_URL}/reservations", {
            "customer_email": f"concurrent{i}@test.fr",
            "items": [{
                "product_code": "concurrency_theater",
                "category": "adult",
                "session_id": ids["session"],
            }],
        })

    results = concurrent(N, try_book)
    statuses = [s for s, _ in results]
    wins = sum(1 for s in statuses if s == 201)
    rejects = [s for s in statuses if s != 201]
    print(f"Résultats : {wins} × 201, rejets = {sorted(set(rejects))}")
    assert wins == K, f"{wins} réservations acceptées au lieu de {K}"
    assert all(s == 400 for s in rejects), \
        f"rejets inattendus : {sorted(set(rejects))}"

    seats = await check_booked_seats(ids["session"])
    print(f"booked_seats = {seats} (capacité {K})")
    assert seats == K, f"booked_seats={seats} != capacité {K}"
    print(f"=> OK : {N} concurrents, {K} acceptés, 0 dépassement\n")

    # --- 2. Double-scan concurrent -------------------------------------
    print(f"Scan concurrent : M={M} scans simultanés du même accès")
    s, resa = post(f"{BASE_URL}/reservations", {
        "customer_email": "scan@test.fr",
        "items": [{
            "product_code": "concurrency_museum",
            "category": "adult",
            "visit_date": str(date.today()),
        }],
    })
    assert s == 201
    s, pay_result = post(
        f"{BASE_URL}/reservations/{resa['id']}/payments",
        {"method": "cb", "amount": resa["total_price"]},
    )
    assert s == 201 and pay_result["reservation_status"] == "confirmed"
    ticket_id = pay_result["tickets"][0]["id"]

    def try_scan(i: int):
        return post(
            f"{BASE_URL}/tickets/{ticket_id}/scan",
            {"event_id": ids["musee"]},
        )

    scans = concurrent(M, try_scan)
    scan_ok = sum(1 for s, _ in scans if s == 200)
    scan_rejects = [s for s, _ in scans if s != 200]
    print(f"Résultats : {scan_ok} × 200, rejets = {sorted(set(scan_rejects))}")
    assert scan_ok == 1, f"{scan_ok} scans acceptés au lieu de 1"
    assert all(s == 400 for s in scan_rejects), \
        f"rejets inattendus : {sorted(set(scan_rejects))}"
    print("=> OK : 1 seul scan accepté sur M concurrents")

    print("\nM1 — concurrence démontrée : anti-surbooking + anti double-scan.")


async def run() -> None:
    try:
        await main()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(run())
