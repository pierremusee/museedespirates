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


def get(url: str) -> tuple[int, dict | list]:
    req = urllib.request.Request(url, method="GET")
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
        # Seconde séance pour la course de clé sur commandes à séance
        # (la première est saturée par le test de surbooking).
        session_pay = Session(
            event_id=theater.id,
            start_time=datetime.now(UTC) + timedelta(days=2),
            max_capacity=10,
        )
        db.add(session_pay)
        wanted = {
            "concurrency_theater": Product(
                code="concurrency_theater", label="Concurrence Théâtre",
                kind=ProductKind.SIMPLE, price_adult=Decimal(10),
                components=[ProductComponent(
                    component_type=ComponentType.THEATER_SESSION,
                    quantity=1)],
            ),
            "concurrency_museum": Product(
                code="concurrency_museum", label="Concurrence Musée",
                kind=ProductKind.SIMPLE, price_adult=Decimal(5),
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
            "session_pay": str(session_pay.id),
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
        {"method": "cb", "amount": resa["total_price"],
         "idempotency_key": str(uuid.uuid4())},
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

    # --- 3. Paiements concurrents, même clé d'idempotence --------------
    # Double-clic/retry côté client : M requêtes identiques portant la
    # même clé sur la même commande — exactement une exécution.
    print(f"\nPaiement concurrent : {M} requêtes, même clé d'idempotence")
    s, resa_p = post(f"{BASE_URL}/reservations", {
        "customer_email": "payconcurrent@test.fr",
        "items": [{
            "product_code": "concurrency_museum",
            "category": "adult",
            "visit_date": str(date.today()),
        }],
    })
    assert s == 201
    pay_key = str(uuid.uuid4())

    def try_pay(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_p['id']}/payments",
            {"method": "cb", "amount": resa_p["total_price"],
             "idempotency_key": pay_key},
        )

    pays = concurrent(M, try_pay)
    statuses = [s for s, _ in pays]
    created = sum(1 for s in statuses if s == 201)
    replays = sum(1 for s in statuses if s == 200)
    payment_ids = {b["payment"]["id"] for _, b in pays}
    print(f"Résultats : {created} × 201, {replays} × 200, "
          f"payment ids distincts = {len(payment_ids)}")
    assert created == 1, f"{created} créations au lieu de 1"
    assert created + replays == M, f"statuts inattendus : {statuses}"
    assert len(payment_ids) == 1, "transactions dupliquées sous la même clé"

    s, detail = get(f"{BASE_URL}/reservations/{resa_p['id']}")
    assert s == 200 and len(detail["payments"]) == 1
    assert detail["status"] == "confirmed" and len(detail["tickets"]) == 1
    print("=> OK : 1 transaction, 1 confirmation, 1 billet")

    # --- 4. Course de clé entre deux réservations différentes ----------
    # La même clé visant deux commandes : le FOR UPDATE ne les
    # sérialise pas — l'index unique tranche, le perdant obtient 409.
    print("\nCourse de clé : même clé sur deux réservations distinctes")
    resa_ids = []
    for i in range(2):
        s, r = post(f"{BASE_URL}/reservations", {
            "customer_email": f"keyrace{i}@test.fr",
            "channel": "pos",
            "items": [{
                "product_code": "concurrency_museum",
                "category": "adult",
                "visit_date": str(date.today()),
            }],
        })
        assert s == 201
        resa_ids.append(r["id"])
    shared_key = str(uuid.uuid4())

    def try_pay_other(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_ids[i]}/payments",
            {"method": "cash", "amount": "5.00",
             "idempotency_key": shared_key},
        )

    race = concurrent(2, try_pay_other)
    codes = sorted(s for s, _ in race)
    print(f"Résultats : {codes}")
    assert codes == [201, 409], \
        f"attendu 1 succès + 1 conflit, obtenu {codes}"
    for rid in resa_ids:
        s, detail = get(f"{BASE_URL}/reservations/{rid}")
        assert s == 200
        assert len(detail["payments"]) <= 1
    total_payments = sum(
        len(get(f"{BASE_URL}/reservations/{rid}")[1]["payments"])
        for rid in resa_ids
    )
    assert total_payments == 1, "la clé a été honorée deux fois"
    print("=> OK : l'index unique tranche la course, 1 seul encaissement")

    # --- 5. Même clé, contenus différents, en concurrence --------------
    # Bug client ou fraude : même clé, méthodes/montants divergents —
    # le gagnant enregistre, le perdant obtient 409 (jamais 2 lignes).
    print("\nMême clé, contenus différents (concurrence)")
    s, resa_d = post(f"{BASE_URL}/reservations", {
        "customer_email": "diffkey@test.fr",
        "channel": "pos",
        "items": [{
            "product_code": "concurrency_museum",
            "category": "adult",
            "visit_date": str(date.today()),
        }],
    })
    assert s == 201
    diff_key = str(uuid.uuid4())
    variants = [
        {"method": "cash", "amount": "2.00", "idempotency_key": diff_key},
        {"method": "ancv", "amount": "3.00", "idempotency_key": diff_key},
    ]

    def try_pay_diff(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_d['id']}/payments",
            variants[i],
        )

    diff = concurrent(2, try_pay_diff)
    codes = sorted(s for s, _ in diff)
    print(f"Résultats : {codes}")
    assert codes == [201, 409], \
        f"attendu 1 succès + 1 conflit, obtenu {codes}"
    s, detail = get(f"{BASE_URL}/reservations/{resa_d['id']}")
    assert s == 200 and len(detail["payments"]) == 1
    print("=> OK : une seule empreinte honorée, l'autre rejetée 409")

    # --- 6. Tranches concurrentes proches du solde (clés distinctes) ---
    # Deux opérations légitimes simultanées : le FOR UPDATE sérialise,
    # le second voit le solde mis à jour — jamais de sur-encaissement.
    print("\nTranches concurrentes proches du solde (clés distinctes)")
    s, resa_e = post(f"{BASE_URL}/reservations", {
        "customer_email": "nearsold@test.fr",
        "channel": "pos",
        "items": [{
            "product_code": "concurrency_museum",
            "category": "adult",
            "visit_date": str(date.today()),
        }],
    })
    assert s == 201  # total 5.00

    # Deux ANCV de 3.00 sur un solde de 5.00 : le second est plafonné
    # au solde restant (2.00, excédent perdu — DFC n°7, jamais de rendu
    # ANCV). Les deux tranches sont légitimes : invariant = la somme
    # imputée ne dépasse jamais le total.
    def try_near(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_e['id']}/payments",
            {"method": "ancv", "amount": "3.00",
             "idempotency_key": str(uuid.uuid4())},
        )

    near = concurrent(2, try_near)
    codes = sorted(s for s, _ in near)
    print(f"Résultats : {codes}")
    assert codes == [201, 201], f"attendu [201, 201], obtenu {codes}"
    s, detail = get(f"{BASE_URL}/reservations/{resa_e['id']}")
    assert len(detail["payments"]) == 2
    applied = sum(Decimal(p["applied_amount"]) for p in detail["payments"])
    assert applied == Decimal("5.00"), f"sur-encaissement : {applied}"
    assert Decimal(detail["amount_due"]) == Decimal("0.00")
    assert detail["status"] == "confirmed"
    print("=> OK : 2e tranche plafonnée au solde, total exact, confirmée")

    # --- 7. N clés distinctes, même réservation ------------------------
    # Quatre tranches cash concurrentes : toutes légitimes, toutes
    # sérialisées — le total encaissé reflète exactement les 4 lignes.
    print("\nQuatre tranches cash concurrentes (clés distinctes)")
    s, resa_f = post(f"{BASE_URL}/reservations", {
        "customer_email": "multikey@test.fr",
        "channel": "pos",
        "items": [{
            "product_code": "concurrency_museum",
            "category": "adult",
            "visit_date": str(date.today()),
        }],
    })
    assert s == 201  # total 5.00

    def try_cash(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_f['id']}/payments",
            {"method": "cash", "amount": "1.00",
             "idempotency_key": str(uuid.uuid4())},
        )

    multi = concurrent(4, try_cash)
    codes = sorted(s for s, _ in multi)
    print(f"Résultats : {codes}")
    assert all(s == 201 for s, _ in multi), \
        f"statuts inattendus : {codes}"
    s, detail = get(f"{BASE_URL}/reservations/{resa_f['id']}")
    assert len(detail["payments"]) == 4
    assert Decimal(detail["paid_amount"]) == Decimal("4.00")
    assert Decimal(detail["amount_due"]) == Decimal("1.00")
    assert detail["status"] == "pending"
    print("=> OK : 4 tranches distinctes enregistrées, solde exact")

    # --- 8. Course de clé : commandes à séance soldées -----------------
    # Régression AUDIT-001 : même clé sur deux réservations comportant
    # une séance, l'encaissement soldant la commande — l'autoflush
    # déclenché par _emit_tickets laissait échapper l'IntegrityError
    # (500 au lieu de 409). Ce qui est déterministe ici, c'est le
    # CONTRAT : un 201, un 409, un seul encaissement, aucun état
    # partiel. La branche qui produit le 409 (second lookup sous le
    # verrou ou récupération après IntegrityError) dépend du timing
    # réel de la course — les deux branches sont exercées
    # déterministiquement par tests/test_payments.py.
    print("\nCourse de clé à séance : même clé, commandes soldées")
    resa_ids = []
    for i in range(2):
        s, r = post(f"{BASE_URL}/reservations", {
            "customer_email": f"keyrace_session{i}@test.fr",
            "channel": "pos",
            "items": [{
                "product_code": "concurrency_theater",
                "category": "adult",
                "session_id": ids["session_pay"],
            }],
        })
        assert s == 201, r
        resa_ids.append(r["id"])
    session_key = str(uuid.uuid4())

    def try_pay_session(i: int):
        return post(
            f"{BASE_URL}/reservations/{resa_ids[i]}/payments",
            {"method": "cash", "amount": "10.00",
             "idempotency_key": session_key},
        )

    race = concurrent(2, try_pay_session)
    codes = sorted(s for s, _ in race)
    print(f"Résultats : {codes}")
    assert codes == [201, 409], \
        f"attendu 1 succès + 1 conflit, obtenu {codes}"
    winner_resp = next(b for s, b in race if s == 201)
    loser_resp = next(b for s, b in race if s == 409)
    # Le 409 doit provenir du rejet d'empreinte d'idempotence, pas
    # d'un autre conflit (ex. émission impossible).
    assert "opération différente" in loser_resp.get("detail", ""), \
        f"409 inattendu : {loser_resp}"
    details = []
    for rid in resa_ids:
        s, detail = get(f"{BASE_URL}/reservations/{rid}")
        assert s == 200
        details.append(detail)
    assert sum(len(d["payments"]) for d in details) == 1, \
        "la clé a été honorée deux fois"
    winner = next(d for d in details if d["payments"])
    loser = next(d for d in details if not d["payments"])
    assert winner["status"] == "confirmed" and len(winner["tickets"]) == 1
    # AUDIT-007 : le corps du 201 EST le snapshot `response` figé en
    # base — ses champs de séance doivent être renseignés, et la
    # relecture GET doit restituer les mêmes valeurs.
    snap_access = winner_resp["tickets"][0]["accesses"][0]
    assert snap_access["session_id"] == ids["session_pay"]
    assert snap_access["session_start"] is not None
    assert snap_access["session_event_title"] == \
        "Concurrence Théâtre (test)"
    get_access = winner["tickets"][0]["accesses"][0]
    assert get_access["session_start"] == snap_access["session_start"]
    assert get_access["session_event_title"] == \
        snap_access["session_event_title"]
    assert loser["status"] == "pending" and loser["tickets"] == []
    # Le perdant reste encaissable avec une clé nouvelle : la tentative
    # rejetée n'a laissé ni verrou ni état résiduel.
    s, retry = post(
        f"{BASE_URL}/reservations/{loser['id']}/payments",
        {"method": "cash", "amount": "10.00",
         "idempotency_key": str(uuid.uuid4())},
    )
    assert s == 201 and retry["reservation_status"] == "confirmed", retry
    print("=> OK : 201 + 409 sur commandes à séance, snapshot fidèle, "
          "perdant soldable")

    print("\nConcurrence démontrée : anti-surbooking, anti double-scan, "
          "idempotence des paiements.")


async def run() -> None:
    try:
        await main()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(run())
