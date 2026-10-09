"""Test de bout en bout du moteur panier (Chantiers A & B / DFC n°5 à 7).

Seed minimal dédié (événements, séances, produits, période haute saison),
puis via l'API :
  1. POST /reservations + paiement CB — panier mixte : pass_1_show adulte
     + pass_2_shows réduit + family_museum (1 enfant suppl.) + museum_entry
     enfant. Vérifie pending -> payments -> confirmed -> émission des
     billets.
  2. pass_1_show en haute saison -> modificateur +2 €.
  3. visit_date incohérente avec la séance -> 400.
  4. Scans conditionnels : open_ticket du jour, session_ticket du jour,
     billet d'un autre jour rejeté, double-scan bloqué.
  5. Surbooking : une commande pending tient déjà la jauge -> 400.
  6. Chantier B — canal pos : multi-paiement cash (rendu) + ANCV
     (nominal > solde, aucun rendu), chèque refusé hors groupes,
     restrictions canal web, rejet d'un paiement sur commande soldée.
  7. Lecture : GET /products, /seasonal/check, /reservations/{id} —
     smoke HTTP : statut, données attendues, sérialisation complète du
     response_model (aucun lazy-loading/MissingGreenlet).

Lancer depuis backend/ :  ./venv/Scripts/python.exe scripts/test_booking.py
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import (
    ComponentType,
    Event,
    EventType,
    Product,
    ProductComponent,
    ProductKind,
    Reservation,
    SeasonalPeriod,
    Session,
)

BASE_URL = "http://127.0.0.1:8000"
MUSEUM_TZ = ZoneInfo("Europe/Paris")
UTC = ZoneInfo("UTC")

# Jours relatifs — la vente d'une séance expirée est refusée : les
# séances du test doivent rester dans le futur.
# HIGH_DAY est toujours couvert par la période « HS test » recréée au
# seed. LOW_DAY (basse saison) est choisi dans seed() : hors de toute
# SeasonalPeriod existante ET hors de la fenêtre « HS test » — sinon le
# total du test 1 dépendrait du calendrier réel (HS juillet-août seedée).
HIGH_DAY = date.today() + timedelta(days=45)  # couvert par « HS test » du seed


def session_at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MUSEUM_TZ).astimezone(UTC)


async def seed() -> dict:
    engine = create_async_engine(settings.DATABASE_URL)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as db:
        # Réutilise les événements de test s'ils existent : les produits
        # persistés pointent vers leur id (musée) — les recréer rendrait
        # les accès musée orphelins au scan.
        musee_id = (
            await db.execute(
                select(ProductComponent.event_id)
                .where(
                    ProductComponent.component_type == ComponentType.MUSEUM_DAY
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        musee = (
            (
                await db.execute(
                    select(Event).where(Event.id == musee_id)
                )
            ).scalar_one_or_none()
            if musee_id
            else None
        )
        # Déterministe : les produits doivent viser le vrai musée (les
        # doublons « (test) » ont été désactivés — un composant pointant
        # vers l'un d'eux rend toute réservation musée impossible).
        if musee is None or not musee.is_active:
            musee = (
                await db.execute(
                    select(Event).where(Event.title == "Musée des Pirates")
                )
            ).scalars().first()
        kraken = (
            await db.execute(
                select(Event).where(Event.title == "Théâtre (test)").limit(1)
            )
        ).scalars().first()
        if musee is None:
            musee = Event(title="Musée (test)", event_type=EventType.PERMANENT_EXHIBITION)
        if kraken is None:
            kraken = Event(title="Théâtre (test)", event_type=EventType.THEATER)
        # Réactivation non destructive : un événement désactivé lors d'un
        # ménage du catalogue ne peut plus recevoir de réservations.
        musee.is_active = True
        kraken.is_active = True
        db.add_all([musee, kraken])
        await db.flush()
        # Tous les composants musée visent l'événement résolu (d'anciens
        # produits peuvent encore pointer vers un doublon désactivé).
        await db.execute(
            update(ProductComponent)
            .where(ProductComponent.component_type == ComponentType.MUSEUM_DAY)
            .values(event_id=musee.id)
        )

        # Purge des « HS test » d'anciens runs AVANT de choisir LOW_DAY.
        await db.execute(
            delete(SeasonalPeriod).where(SeasonalPeriod.name == "HS test")
        )
        # LOW_DAY : premier jour ≥ J+9 hors de toute période saisonnière
        # existante et hors de la fenêtre « HS test » (HIGH_DAY ± 15 j).
        periods = (
            await db.execute(
                select(SeasonalPeriod.start_date, SeasonalPeriod.end_date)
            )
        ).all()
        hs_lo, hs_hi = HIGH_DAY - timedelta(days=15), HIGH_DAY + timedelta(days=15)
        low_day = date.today() + timedelta(days=9)
        for _ in range(370):
            if hs_lo <= low_day <= hs_hi or any(
                start <= low_day <= end for start, end in periods
            ):
                low_day += timedelta(days=1)
            else:
                break
        else:
            raise RuntimeError("aucun jour basse saison libre sous 370 jours")

        s1 = Session(event_id=kraken.id, start_time=session_at(low_day, 10, 30), max_capacity=80)
        s2 = Session(event_id=kraken.id, start_time=session_at(low_day, 15, 0), max_capacity=80)
        s3 = Session(event_id=kraken.id, start_time=session_at(HIGH_DAY, 10, 30), max_capacity=80)
        tiny = Session(event_id=kraken.id, start_time=session_at(low_day, 18, 0), max_capacity=1)
        # Jauge 1 dédiée au test d'annulation (tiny reste occupée par la
        # commande pending du test 5 jusqu'à la purge).
        cancel_sess = Session(event_id=kraken.id, start_time=session_at(low_day, 20, 0), max_capacity=1)
        # Séances « du jour » (jour civil Europe/Paris) : now+1h/3h peut
        # basculer sur le lendemain après 23h locales, et now-20min peut
        # rester « hier » après minuit — or la vente et le scan d'une
        # séance exigent le même jour civil. Repli : borne dans la
        # journée en cours.
        now_utc = datetime.now(UTC)
        paris_today = datetime.now(MUSEUM_TZ).date()

        def session_today(offset: timedelta, fallback: time) -> datetime:
            start = now_utc + offset
            if start.astimezone(MUSEUM_TZ).date() != paris_today:
                start = session_at(
                    paris_today, fallback.hour, fallback.minute
                )
            return start

        # Séance "aujourd'hui" pour tester le scan de billets session_ticket.
        today_sess = Session(
            event_id=kraken.id,
            start_time=session_today(timedelta(hours=1), time(23, 59)),
            max_capacity=10,
        )
        # Seconde séance du jour : séance supplémentaire à tarif réduit.
        today_sess2 = Session(
            event_id=kraken.id,
            start_time=session_today(timedelta(hours=3), time(23, 58)),
            max_capacity=10,
        )
        # Fenêtre de vente = tolérance du contrôle (30 min) : une séance
        # commencée depuis 20 min (vendable) et une expirée (refusée).
        recent = Session(
            event_id=kraken.id,
            start_time=session_today(timedelta(minutes=-20), time(0, 0)),
            max_capacity=10,
        )
        expired = Session(
            event_id=kraken.id,
            start_time=datetime.now(UTC) - timedelta(hours=1),
            max_capacity=10,
        )
        db.add_all([s1, s2, s3, tiny, cancel_sess, today_sess, today_sess2, recent, expired])

        def simple(code, label, a, c, r, comps):
            return Product(
                code=code, label=label, kind=ProductKind.SIMPLE,
                price_adult=Decimal(a), price_child=Decimal(c),
                price_reduced=Decimal(r), components=comps,
            )

        def museum_comp():
            return ProductComponent(component_type=ComponentType.MUSEUM_DAY, quantity=1, event_id=musee.id)

        def theater_comp(qty=1):
            return ProductComponent(component_type=ComponentType.THEATER_SESSION, quantity=qty)

        wanted = {
            "museum_entry": simple("museum_entry", "Entrée Musée", "12", "8", "9", [museum_comp()]),
            "theater_show": simple("theater_show", "Billet Théâtre", "10", "7", "8", [theater_comp()]),
            "extra_show": Product(code="extra_show", label="Séance supplémentaire (test)", kind=ProductKind.SIMPLE,
                    is_addon=True,
                    price_adult=Decimal(5), price_child=Decimal("3.50"),
                    price_reduced=Decimal(4),
                    components=[theater_comp()]),
            "pass_1_show": Product(code="pass_1_show", label="Pass 1 Spectacle", kind=ProductKind.PASS,
                    price_adult=Decimal(20), price_child=Decimal(13),
                    price_reduced=Decimal(15),
                    components=[museum_comp(), theater_comp()]),
            "pass_2_shows": Product(code="pass_2_shows", label="Pass 2 Spectacles", kind=ProductKind.PASS,
                    price_adult=Decimal(24), price_child=Decimal(16),
                    price_reduced=Decimal(18),
                    components=[museum_comp(), theater_comp(2)]),
            "family_museum": Product(code="family_museum", label="Famille Musée", kind=ProductKind.FAMILY,
                    family_base_price=Decimal(35), extra_child_price=Decimal(6),
                    components=[museum_comp()]),
            "group_visit": Product(code="group_visit", label="Visite Groupe (test)", kind=ProductKind.GROUP,
                    price_adult=Decimal(10),
                    components=[museum_comp()]),
        }
        existing_codes = set(
            (await db.execute(select(Product.code))).scalars()
        )
        db.add_all(
            p for code, p in wanted.items() if code not in existing_codes
        )
        # Réactivation non destructive : les fixtures « (test) » sont
        # désactivées en fin de run (cleanup) — seul `group_visit` est
        # un produit dédié au test ; le reste du catalogue est réel.
        await db.execute(
            update(Product)
            .where(Product.code == "group_visit")
            .values(is_active=True)
        )
        # Fenêtre haute saison de test recalée à chaque run pour couvrir
        # HIGH_DAY (les anciennes « HS test » ont été purgées plus haut —
        # fixtures jetables, le vrai « Haute saison — Été » n'est pas touché).
        db.add(SeasonalPeriod(
            name="HS test",
            start_date=HIGH_DAY - timedelta(days=15),
            end_date=HIGH_DAY + timedelta(days=15),
        ))
        await db.commit()
        ids = {
            "low_day": str(low_day),
            "s1": str(s1.id),
            "s2": str(s2.id),
            "s3": str(s3.id),
            "tiny": str(tiny.id),
            "cancel": str(cancel_sess.id),
            "today": str(today_sess.id),
            "today2": str(today_sess2.id),
            "recent": str(recent.id),
            "expired": str(expired.id),
            "musee": str(musee.id),
            "kraken": str(kraken.id),
        }
    await engine.dispose()
    return ids


async def cleanup() -> None:
    """Retire les fixtures « (test) » du catalogue visible : le script
    laisse la base comme il l'a trouvée. Désactivation non destructive
    (is_active=False) — le seed les réactive au run suivant. Les
    événements réels (« Musée des Pirates », les pièces du Kraken) et
    les produits du catalogue ne sont jamais touchés."""
    engine = create_async_engine(settings.DATABASE_URL)
    sm = async_sessionmaker(engine, class_=AsyncSession)
    async with sm() as db:
        await db.execute(
            update(Event)
            .where(Event.title.like("%(test)%"))
            .values(is_active=False)
        )
        await db.execute(
            update(Product)
            .where(Product.code == "group_visit")
            .values(is_active=False)
        )
        await db.commit()
    await engine.dispose()


def post(url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        body = json.loads(resp.read().decode())
        status = resp.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        status = e.code
    print(f"-> HTTP {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return status, body


def get(url: str) -> tuple[int, dict | list]:
    req = urllib.request.Request(url, method="GET")
    try:
        resp = urllib.request.urlopen(req)
        body = json.loads(resp.read().decode())
        status = resp.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        status = e.code
    print(f"-> HTTP {status}")
    return status, body


def pay(
    reservation_id: str,
    method: str,
    amount: str,
    key: str | None = None,
) -> tuple[int, dict]:
    print(f"   Paiement {method} {amount} € :")
    return post(
        f"{BASE_URL}/reservations/{reservation_id}/payments",
        {
            "method": method,
            "amount": amount,
            # Clé d'idempotence : une nouvelle par défaut (nouvelle
            # opération), réutilisée explicitement pour tester le rejeu.
            "idempotency_key": key or str(uuid.uuid4()),
        },
    )


def scan(ticket_id: str, checkpoint: dict) -> tuple[int, dict]:
    """Scan au poste donné : {"event_id": ...} (entrée musée) ou
    {"session_id": ...} (porte de séance)."""
    return post(f"{BASE_URL}/tickets/{ticket_id}/scan", checkpoint)


def expect(resp: tuple[int, dict], expected: int, ctx: str) -> dict:
    """Assertion de statut HTTP : rend chaque appel bloquant au lieu de
    l'afficher « à l'œil ». Retourne le corps en cas de succès."""
    code, body = resp
    assert code == expected, (
        f"{ctx} : HTTP {code} (attendu {expected}) — {body}"
    )
    return body


async def main() -> None:
    ids = await seed()
    low, high = ids["low_day"], str(HIGH_DAY)
    print(f"Séances : 10h30={ids['s1']} 15h00={ids['s2']} HS={ids['s3']} "
          f"tiny={ids['tiny']} cancel={ids['cancel']} (jour BS={low})\n")

    print("Test 1 — panier web payant : pending -> CB -> confirmed -> billets :")
    resa = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "jack.sparrow@blackpearl.fr",
        "items": [
            {"product_code": "pass_1_show", "category": "adult",
             "visit_date": low, "session_id": ids["s1"]},
            {"product_code": "pass_2_shows", "category": "reduced",
             "visit_date": low, "session_ids": [ids["s1"], ids["s2"]]},
            {"product_code": "family_museum", "visit_date": low, "extra_children": 1},
            {"product_code": "museum_entry", "category": "child", "visit_date": low},
        ],
    }), 201, "test 1 — création panier mixte")
    # DFC n°7 : aucune émission avant paiement complet.
    assert resa["status"] == "pending" and resa["tickets"] == []
    assert Decimal(resa["total_price"]) == Decimal("87.00")
    print(f"=> pending, total={resa['total_price']}, amount_due={resa['amount_due']}")

    expect(pay(resa["id"], "ancv", "87.00"), 400, "ANCV refusé en ligne")
    expect(pay(resa["id"], "cb", "50.00"), 400, "CB partiel refusé en ligne")

    key1 = str(uuid.uuid4())
    paid = expect(
        pay(resa["id"], "cb", "87.00", key=key1), 201,
        "test 1 — paiement CB")
    assert paid["reservation_status"] == "confirmed"
    assert Decimal(paid["amount_due"]) == Decimal(0)
    tickets = paid["tickets"]
    accesses = [a for t in tickets for a in t["accesses"]]
    n_open = sum(1 for a in accesses if a["access_type"] == "open_ticket")
    n_sess = sum(1 for a in accesses if a["access_type"] == "session_standard")
    print(f"=> {len(tickets)} billets émis ({n_open} open, {n_sess} session)")
    # 1 billet par personne, accès fusionnés : 8 personnes, 11 accès.
    assert len(tickets) == 8 and n_open == 8 and n_sess == 3
    print("=> OK : billets multi-accès + total 87.00 conformes DFC n°5")

    expect(pay(resa["id"], "cb", "1.00"), 400, "paiement sur commande soldée")

    # Idempotence : même clé + même contenu → rejeu 200 du snapshot
    # initial (même transaction, aucun doublon) ; même clé + contenu
    # différent → 409. Le test 12c re-vérifiera len(payments) == 1.
    replay = expect(
        pay(resa["id"], "cb", "87.00", key=key1), 200,
        "test 1 — rejeu même clé")
    assert replay == paid, "le rejeu doit restituer le snapshot initial"
    expect(pay(resa["id"], "cb", "50.00", key=key1), 409,
           "test 1 — même clé, montant différent")
    expect(pay(resa["id"], "ancv", "87.00", key=key1), 409,
           "test 1 — même clé, méthode différente")
    print("=> OK : rejeu idempotent 200 + conflits de clé 409")

    print("\nTest 2 — pass_1_show adulte en HAUTE SAISON (attendu 22.00 = 20+2) :")
    resa2 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "elizabeth.swann@portroyal.fr",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": high, "session_id": ids["s3"]}],
    }), 201, "test 2 — création pass HS")
    item = resa2["items"][0]
    print(f"=> computed={item['computed_price']} modifier={item['season_modifier']}")
    assert Decimal(item["computed_price"]) == Decimal("22.00")
    assert Decimal(item["season_modifier"]) == Decimal("2.00")
    expect(pay(resa2["id"], "cb", "22.00"), 201, "test 2 — paiement CB")
    print("=> OK : modificateur haute saison + CB 22.00 encaissée")

    print("\nTest 3 — visit_date incohérente avec la séance (doit être rejeté) :")
    # LOW_DAY étant dynamique, l'incohérence est relative : lendemain du
    # jour de la séance.
    wrong_day = str(date.fromisoformat(low) + timedelta(days=1))
    expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "davy.jones@flyingdutchman.fr",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": wrong_day, "session_id": ids["s1"]}],
    }), 400, "test 3 — visit_date ≠ jour de la séance")

    # Jour de référence = jour civil Paris (règle métier de validité),
    # pas la date UTC du runner — entre 0h et 2h Paris (été), les deux
    # divergent et le billet seedé « aujourd'hui » arrivait daté de
    # la veille → scan refusé (flake CI).
    today = str(datetime.now(MUSEUM_TZ).date())
    print("\nTest 4a — billet Musée (open_ticket) valable aujourd'hui + profil gratuit -4 ans :")
    resa4 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "anne.bonny@revenge.fr",
        "items": [{"product_code": "museum_entry", "free_profile": "under_4",
                   "visit_date": today}],
    }), 201, "test 4a — création gratuit -4 ans")
    # Panier à 0 € : confirmation immédiate, billets émis sans paiement.
    assert resa4["status"] == "confirmed" and len(resa4["tickets"]) == 1
    tid = resa4["tickets"][0]["id"]
    scan_res = expect(scan(tid, {"event_id": ids["musee"]}), 200,
                      "test 4a — scan musée du jour")
    print(f"=> access_label={scan_res['access_label']!r} warning={scan_res['control_warning']!r}")
    assert "Musée" in scan_res["access_label"]
    assert scan_res["control_warning"]
    expect(scan(tid, {"event_id": ids["musee"]}), 400,
           "test 4a — re-scan au même poste")

    print("\nTest 4b — billet Théâtre (session_ticket) pour une séance d'aujourd'hui :")
    resa5 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "mary.read@revenge.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["today"]}],
    }), 201, "test 4b — création billet théâtre")
    paid5 = expect(pay(resa5["id"], "cb", resa5["total_price"]), 201,
                   "test 4b — paiement CB")
    assert paid5["tickets"], "test 4b — aucun billet émis"
    tid = paid5["tickets"][0]["id"]
    scan_res = expect(scan(tid, {"session_id": ids["today"]}), 200,
                      "test 4b — scan séance du jour")
    print(f"=> access_label={scan_res['access_label']!r}")
    assert "Théâtre" in scan_res["access_label"] and "Séance" in scan_res["access_label"]

    tid_wrong, acc_wrong = next(
        (t, a)
        for t in paid["tickets"]
        for a in t["accesses"]
        if a["session_id"]
    )
    print(f"\nTest 4c — scan d'un accès de séance prévu le {low} (doit être refusé) :")
    expect(scan(tid_wrong["id"], {"session_id": acc_wrong["session_id"]}), 400,
           "test 4c — scan séance d'un autre jour")

    print("\nTest 5 — surbooking : une commande pending tient déjà la jauge (jauge 1) :")
    expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "will.turner@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["tiny"]}],
    }), 201, "test 5 — première réservation sur jauge 1")
    print("   => première commande pending, siège réservé avant paiement")
    status5b, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "hector.barbossa@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["tiny"]}],
    })
    assert status5b == 400, f"surbooking accepté : HTTP {status5b}"

    print("\nTest 6a — canal pos : multi-paiement ANCV + espèces avec rendu :")
    resa6 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": today, "session_id": ids["today"]}],
    }), 201, "test 6a — création pass POS")
    # 20.00 € à régler au guichet : 15 € ANCV puis 8 € espèces
    # (rendu 3 € sur les espèces).
    p6a = expect(pay(resa6["id"], "ancv", "15.00"), 201, "test 6a — acompte ANCV")
    assert Decimal(p6a["amount_due"]) == Decimal("5.00")
    assert p6a["reservation_status"] == "pending"
    print("   => ANCV 15 € : reste 5 €, toujours pending, 0 billet")
    p6b = expect(pay(resa6["id"], "cash", "8.00"), 201, "test 6a — solde espèces")
    print(f"   => espèces 8 € : rendu={p6b['change_due']} €, "
          f"statut={p6b['reservation_status']}")
    assert Decimal(p6b["change_due"]) == Decimal("3.00")
    assert p6b["reservation_status"] == "confirmed"
    # Pass musée + théâtre : 1 billet portant les 2 accès.
    assert len(p6b["tickets"]) == 1
    assert len(p6b["tickets"][0]["accesses"]) == 2
    print("=> OK : multi-paiement + rendu monnaie + émission au solde 0")

    print("   Même QR au musée puis au théâtre (2 postes distincts) :")
    tid = p6b["tickets"][0]["id"]
    scan_m = expect(scan(tid, {"event_id": ids["musee"]}), 200,
                    "test 6a — scan musée")
    print(f"   => musée OK : {scan_m['access_label']!r}")
    assert "Musée" in scan_m["access_label"]
    scan_t = expect(scan(tid, {"session_id": ids["today"]}), 200,
                    "test 6a — scan théâtre")
    print(f"   => théâtre OK : {scan_t['access_label']!r}")
    assert "Théâtre" in scan_t["access_label"]
    remaining = [a for a in scan_t["accesses"] if not a["is_scanned"]]
    assert remaining == []

    print("\nTest 6b — canal pos : ANCV excédentaire, AUCUN rendu (DFC n°7) :")
    resa7 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "child",
                   "visit_date": today}],
    }), 201, "test 6b — création musée enfant")
    # Reste 8 €, le client donne un chèque-vacances de 50 € :
    # nominal enregistré, imputé 8 €, rendu 0.
    p7 = expect(pay(resa7["id"], "ancv", "50.00"), 201, "test 6b — ANCV 50 €")
    print(f"   => nominal={p7['payment']['amount']} imputé="
          f"{p7['payment']['applied_amount']} rendu={p7['change_due']}")
    assert Decimal(p7["payment"]["amount"]) == Decimal("50.00")
    assert Decimal(p7["payment"]["applied_amount"]) == Decimal("8.00")
    assert Decimal(p7["change_due"]) == Decimal("0.00")
    assert p7["reservation_status"] == "confirmed"
    print("=> OK : ANCV excédentaire soldé sans rendu")

    tid7 = p7["tickets"][0]["id"]
    expect(scan(tid7, {"session_id": ids["today"]}), 400,
           "test 6b — billet musée refusé au théâtre")
    expect(scan(tid7, {"event_id": ids["musee"]}), 200,
           "test 6b — billet musée accepté au musée")

    print("\nTest 6c — canal pos : chèque refusé hors groupes/scolaires :")
    resa8 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}],
    }), 201, "test 6c — création musée adulte")
    expect(pay(resa8["id"], "check", resa8["total_price"]), 400,
           "test 6c — chèque hors groupe refusé")

    print("\nTest 6d — canal pos : CB supérieure au solde (doit être refusée) :")
    expect(pay(resa8["id"], "cb", "20.00"), 400,
           "test 6d — CB supérieure au solde refusée")
    expect(pay(resa8["id"], "cb", resa8["total_price"]), 201,
           "test 6d — CB exacte acceptée")
    print("   => CB exacte acceptée, commande confirmée")

    print("\nTest 7 — seuil groupe : 8 personnes minimum :")
    expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "group_visit", "group_size": 5,
                   "visit_date": today}],
    }), 400, "test 7 — groupe de 5 refusé (< 8)")
    expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "group_size": 10, "visit_date": today}],
    }), 400, "test 7 — group_size sur produit non-groupe")
    print("   groupe de 8 (doit passer) :")
    resa9 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "group_visit", "group_size": 8,
                   "visit_date": today}],
    }), 201, "test 7 — groupe de 8")
    # 8 x 10 € = 80.00, commande groupe éligible au chèque (DFC n°7).
    assert Decimal(resa9["total_price"]) == Decimal("80.00")
    p9 = expect(pay(resa9["id"], "check", "80.00"), 201, "test 7 — chèque groupe")
    assert p9["reservation_status"] == "confirmed"
    n_group = sum(
        1 for t in p9["tickets"] if t["ticket_category"] == "group"
    )
    print(f"   => {n_group} billets 'group' émis, payés par chèque")
    assert n_group == 8
    print("=> OK : seuil 8 + paiement chèque groupe")

    print("\nTest 9 — séance supplémentaire à tarif réduit, fusionnée sur le même QR :")
    resa11 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "elizabeth.extra@portroyal.fr",
        "channel": "pos",
        "items": [
            {"product_code": "pass_1_show", "category": "adult",
             "visit_date": today, "session_id": ids["today"]},
            {"product_code": "extra_show", "category": "adult",
             "session_id": ids["today2"]},
        ],
    }), 201, "test 9 — pass + séance supplémentaire")
    # 20.00 + 5.00 : le supplément suit la tarification réduite.
    assert Decimal(resa11["total_price"]) == Decimal("25.00")
    p11 = expect(pay(resa11["id"], "cash", "25.00"), 201, "test 9 — solde espèces")
    assert p11["reservation_status"] == "confirmed"
    assert len(p11["tickets"]) == 1
    accesses = p11["tickets"][0]["accesses"]
    kinds = [a["access_type"] for a in accesses]
    print(f"   => 1 billet, {len(accesses)} accès : {kinds}")
    assert len(accesses) == 3 and kinds.count("session_standard") == 2
    print("=> OK : séance supplémentaire fusionnée sur le même QR")

    print("\nTest 9b — séance supplémentaire sans droit de base (rejetée) :")
    s12, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "barbe.noire@queenanne.fr",
        "channel": "pos",
        "items": [
            {"product_code": "museum_entry", "category": "adult",
             "visit_date": today},
            {"product_code": "extra_show", "category": "adult",
             "session_id": ids["today2"]},
        ],
    })
    assert s12 == 400, "musée + extra_show doit être rejeté (add-on)"
    s13, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "barbe.noire@queenanne.fr",
        "channel": "pos",
        "items": [
            {"product_code": "extra_show", "category": "adult",
             "session_id": ids["today2"]},
        ],
    })
    assert s13 == 400, "extra_show seul doit être rejeté (add-on)"
    print("=> OK : add-on sans billet/pass à séance refusé (grille cohérente)")

    print("\nTest 8 — purge des paniers abandonnés (pending > 15 min) :")
    resa10 = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "panier.oublie@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["today"]}],
    }), 201, "test 8 — création panier à purger")
    engine = create_async_engine(settings.DATABASE_URL)
    sm = async_sessionmaker(engine, class_=AsyncSession,
                            expire_on_commit=False)
    async with sm() as db:
        before = (
            await db.execute(
                select(Session.booked_seats).where(
                    Session.id == uuid.UUID(ids["today"])
                )
            )
        ).scalar_one()
        # Antidate la commande pour simuler un panier de +20 min.
        await db.execute(
            update(Reservation)
            .where(Reservation.id == uuid.UUID(resa10["id"]))
            .values(created_at=datetime.now(UTC) - timedelta(minutes=20))
        )
        await db.commit()
    print(f"   => pending antidatée, booked_seats avant purge = {before}")

    purge = expect(post(f"{BASE_URL}/admin/reservations/purge"), 200,
                   "test 8 — purge")
    print(f"   => {purge['purged']} réservation(s) expirée(s)")
    assert resa10["id"] in [str(i) for i in purge["reservation_ids"]]

    async with sm() as db:
        after = (
            await db.execute(
                select(Session.booked_seats).where(
                    Session.id == uuid.UUID(ids["today"])
                )
            )
        ).scalar_one()
    await engine.dispose()
    print(f"   => booked_seats après purge = {after} (jauge restituée)")
    assert after == before - 1

    expect(pay(resa10["id"], "cb", resa10["total_price"]), 400,
           "test 8 — paiement sur commande expirée")

    print("\nTest 10 — fenêtre de vente d'une séance (règle commune au scan) :")
    # Séance future : déjà démontrée par toutes les ventes ci-dessus.
    # Séance commencée il y a 20 min (tolérance 30 min) → vendue + scannable.
    s_rec, resa_rec = post(f"{BASE_URL}/reservations", {
        "customer_email": "juste.a.temps@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["recent"]}],
    })
    assert s_rec == 201, f"séance dans la tolérance refusée à la vente ({s_rec})"
    s_pr, paid_rec = pay(resa_rec["id"], "cb", resa_rec["total_price"])
    assert s_pr == 201 and paid_rec["reservation_status"] == "confirmed"
    s_scan, _ = scan(paid_rec["tickets"][0]["id"], {"session_id": ids["recent"]})
    assert s_scan == 200, f"scan d'une séance dans la tolérance refusé ({s_scan})"
    print("=> OK : séance dans la tolérance vendue et scannée")

    # Séance expirée (début + 30 min < now) → vente refusée.
    s_exp, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "trop.tard@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["expired"]}],
    })
    assert s_exp == 400, f"vente d'une séance expirée acceptée ({s_exp})"
    print("=> OK : séance expirée refusée à la vente")

    # Contrat API pour les clients : `is_expired` est calculé côté
    # serveur (Session.is_expired) — le POS grise sans dupliquer la règle.
    # Les trois jours sont interrogés : +3 h peut tomber sur le
    # lendemain en fin de journée, et -1 h sur la veille après minuit.
    yesterday = str(datetime.now(MUSEUM_TZ).date() - timedelta(days=1))
    tomorrow = str(datetime.now(MUSEUM_TZ).date() + timedelta(days=1))
    body = [
        e
        for day in (yesterday, today, tomorrow)
        for e in json.loads(
            urllib.request.urlopen(f"{BASE_URL}/events?date={day}").read()
        )
    ]
    flags = {
        s["id"]: s["is_expired"]
        for e in body for s in e["sessions"]
    }
    assert flags.get(ids["recent"]) is False
    assert flags.get(ids["expired"]) is True
    assert flags.get(ids["today"]) is False
    assert flags.get(ids["today2"]) is False
    print("=> OK : is_expired exposé (tolérance=False, expirée=True, futures=False)")

    print("\nTest 11 — annulation guichet d'une commande pending :")

    # 11a — restitution fonctionnelle de la jauge : la séance cap-1
    # libérée par l'annulation redevient vendable.
    resa_c = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "change.davis@blackpearl.fr",
        "channel": "pos",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["cancel"]}],
    }), 201, "test 11a — création sur jauge 1")
    cancelled = expect(
        post(f"{BASE_URL}/reservations/{resa_c['id']}/cancel"),
        200, "test 11a — annulation pending")
    assert cancelled["status"] == "cancelled"
    expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "rebooking@blackpearl.fr",
        "channel": "pos",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["cancel"]}],
    }), 201, "test 11a — re-réservation après annulation")
    print("=> OK : annulation + siège restitué (re-réservation acceptée)")

    # 11b — les encaissements restent tracés (remboursement manuel, DFC n°7).
    resa_p = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "acompte@blackpearl.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}],
    }), 201, "test 11b — création musée")
    key_p = str(uuid.uuid4())
    paid_p = expect(
        pay(resa_p["id"], "cash", "5.00", key=key_p), 201,
        "test 11b — acompte espèces")
    cancelled_p = expect(
        post(f"{BASE_URL}/reservations/{resa_p['id']}/cancel"),
        200, "test 11b — annulation après acompte")
    assert len(cancelled_p["payments"]) == 1
    assert Decimal(cancelled_p["payments"][0]["amount"]) == Decimal("5.00")
    print("=> OK : encaissement conservé sur commande annulée")

    # 11b-bis — réponse perdue puis annulation (scénario guichet) : le
    # rejeu HTTP retrouve la tranche initiale (200 + snapshot identique),
    # sans second encaissement ni changement de statut.
    replay_p = expect(
        pay(resa_p["id"], "cash", "5.00", key=key_p), 200,
        "test 11b — rejeu de l'acompte après annulation")
    assert replay_p == paid_p, \
        "le rejeu doit restituer le snapshot de l'opération initiale"
    s, detail_p = get(f"{BASE_URL}/reservations/{resa_p['id']}")
    assert s == 200 and len(detail_p["payments"]) == 1
    assert Decimal(detail_p["paid_amount"]) == Decimal("5.00")
    assert detail_p["status"] == "cancelled"
    print("=> OK : rejeu après annulation — snapshot, aucune duplication")

    # Cas d'erreur : payée/confirmée/annulée/inconnue → rejet.
    expect(pay(resa_p["id"], "cash", "7.00"), 400,
           "test 11 — paiement sur commande annulée")
    expect(post(f"{BASE_URL}/reservations/{resa4['id']}/cancel"), 400,
           "test 11 — annulation d'une commande confirmée")
    expect(post(f"{BASE_URL}/reservations/{resa_c['id']}/cancel"), 400,
           "test 11 — double annulation")
    expect(post(f"{BASE_URL}/reservations/{uuid.uuid4()}/cancel"), 404,
           "test 11 — réservation inconnue")
    # La re-réservation reste pending : la purge la libérera (jetable).
    print("=> OK : annulation rejetée sur confirmée/annulée/inconnue")

    print("\nTest 12 — endpoints de lecture : /products, /seasonal/check, "
          "/reservations/{id} :")

    # 12a — catalogue actif : liste ProductRead complète, composants
    # inclus (selectinload — un lazy-loading pendant la sérialisation
    # lèverait MissingGreenlet et renverrait 500).
    products = expect(get(f"{BASE_URL}/products"), 200,
                      "test 12a — GET /products")
    assert isinstance(products, list) and products
    by_code = {p["code"]: p for p in products}
    for code in ("museum_entry", "theater_show", "extra_show",
                 "pass_1_show", "pass_2_shows", "family_museum",
                 "group_visit"):
        assert code in by_code, f"produit seedé absent du catalogue : {code}"
    p1 = by_code["pass_1_show"]
    assert p1["kind"] == "pass"
    assert {c["component_type"] for c in p1["components"]} == {
        "museum_day", "theater_session"}
    assert Decimal(by_code["museum_entry"]["price_adult"]) == Decimal(12)
    print(f"   => {len(products)} produits actifs, composants sérialisés")

    # 12b — même règle de saison que la grille tarifaire : low_day hors
    # de toute période, HIGH_DAY couvert par « HS test ».
    chk_low = expect(get(f"{BASE_URL}/seasonal/check?date={low}"),
                     200, "test 12b — seasonal/check basse saison")
    assert chk_low == {"date": low, "high_season": False}
    chk_high = expect(get(f"{BASE_URL}/seasonal/check?date={high}"),
                      200, "test 12b — seasonal/check haute saison")
    assert chk_high == {"date": high, "high_season": True}
    print(f"   => {low} = basse saison, {high} = haute saison")

    # 12c — la commande du test 1 relue par l'API : graphe complet
    # items/tickets/accès/paiements sérialisé + 404 propre sur inconnu.
    r = expect(get(f"{BASE_URL}/reservations/{resa['id']}"),
               200, "test 12c — GET /reservations/{id}")
    assert r["id"] == resa["id"] and r["status"] == "confirmed"
    assert r["customer_email"] == "jack.sparrow@blackpearl.fr"
    assert Decimal(r["total_price"]) == Decimal("87.00")
    assert len(r["items"]) == 4
    assert len(r["tickets"]) == 8 and len(r["payments"]) == 1
    titles = {a["session_event_title"]
              for t in r["tickets"] for a in t["accesses"]
              if a["access_type"] == "session_standard"}
    assert titles == {"Théâtre (test)"}
    expect(get(f"{BASE_URL}/reservations/{uuid.uuid4()}"),
           404, "test 12c — réservation inconnue")
    print("   => graphe complet sérialisé, 404 propre sur inconnu")

    print("\nTest 13 — bornes d'entrée : montants et quantités "
          "(422 propre, jamais 500) :")
    # AUDIT-008 : un champ hors borne est rejeté en 422 par le schéma —
    # avant, une valeur extrême débordait la colonne PostgreSQL (500)
    # ou déclenchait une création massive de billets.
    s, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "capitaine@bornes.fr",
        "items": [{"product_code": "group_visit", "group_size": 121,
                   "visit_date": today}],
    })
    assert s == 422, f"groupe de 121 personnes accepté ({s})"
    # 11 billets individuels = 11 lignes : doit passer (les interfaces
    # émettent 1 item par personne — la borne lignes est au plafond
    # de 120 personnes, pas en dessous).
    s, resa11i = post(f"{BASE_URL}/reservations", {
        "customer_email": "capitaine@bornes.fr",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}] * 11,
    })
    assert s == 201 and len(resa11i["items"]) == 11, (
        f"11 billets individuels refusés ({s})"
    )
    s, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "capitaine@bornes.fr",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}] * 121,
    })
    assert s == 422, f"commande de 121 lignes acceptée ({s})"
    s, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "a" * 310 + "@pirates-tres-long.fr",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}],
    })
    # EmailStr borne l'adresse à 254 car. (RFC 5321) < colonne 320.
    assert s == 422, f"email > 254 caractères accepté ({s})"

    resa_b = expect(post(f"{BASE_URL}/reservations", {
        "customer_email": "capitaine@bornes.fr",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}],
    }), 201, "test 13 — commande de référence")
    s, _ = pay(resa_b["id"], "cash", "50000.01")
    assert s == 422, f"tranche > 50 000 € acceptée ({s})"
    s, _ = pay(resa_b["id"], "cash", "99999999999")
    assert s == 422, f"montant débordant Numeric accepté ({s})"

    # Borne incluse : 120 personnes, produit groupe sans jauge.
    s, resa_g = post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "group_visit", "group_size": 120,
                   "visit_date": today}],
    })
    assert s == 201, f"groupe de 120 refusé ({s})"
    assert Decimal(resa_g["total_price"]) == Decimal("1200.00")

    # Deux lignes valides en champ mais 60 + 61 > 120 en agrégat → 400.
    s, detail = post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [
            {"product_code": "group_visit", "group_size": 60,
             "visit_date": today},
            {"product_code": "group_visit", "group_size": 61,
             "visit_date": today},
        ],
    })
    assert s == 400 and "120" in str(detail), (
        f"agrégat > 120 personnes accepté ({s})"
    )

    # Séances supplémentaires = accès pour des personnes déjà
    # comptées : 12 billets théâtre + 12 add-ons → 12 personnes.
    items_show = [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["s1"]}] * 12
    items_extra = [{"product_code": "extra_show", "category": "adult",
                    "session_id": ids["s2"]}] * 12
    s, resa_x = post(f"{BASE_URL}/reservations", {
        "customer_email": "capitaine@bornes.fr",
        "items": items_show + items_extra,
    })
    assert s == 201 and len(resa_x["items"]) == 24, (
        f"12 personnes + 12 séances supp. refusées ({s})"
    )
    print("=> OK : 422 sur champs hors borne, 400 sur agrégat > 120 "
          "personnes, bornes exactes acceptées, add-ons non "
          "double-comptés")


async def run() -> None:
    try:
        await main()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(run())
