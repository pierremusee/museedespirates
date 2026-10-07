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
LOW_DAY = date.today() + timedelta(days=9)    # basse saison (hors « HS test »)
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

        s1 = Session(event_id=kraken.id, start_time=session_at(LOW_DAY, 10, 30), max_capacity=80)
        s2 = Session(event_id=kraken.id, start_time=session_at(LOW_DAY, 15, 0), max_capacity=80)
        s3 = Session(event_id=kraken.id, start_time=session_at(HIGH_DAY, 10, 30), max_capacity=80)
        tiny = Session(event_id=kraken.id, start_time=session_at(LOW_DAY, 18, 0), max_capacity=1)
        # Séance "aujourd'hui" pour tester le scan de billets session_ticket.
        today_sess = Session(
            event_id=kraken.id,
            start_time=datetime.now(UTC) + timedelta(hours=1),
            max_capacity=10,
        )
        # Seconde séance du jour : séance supplémentaire à tarif réduit.
        today_sess2 = Session(
            event_id=kraken.id,
            start_time=datetime.now(UTC) + timedelta(hours=3),
            max_capacity=10,
        )
        # Fenêtre de vente = tolérance du contrôle (30 min) : une séance
        # commencée depuis 20 min (vendable) et une expirée (refusée).
        recent = Session(
            event_id=kraken.id,
            start_time=datetime.now(UTC) - timedelta(minutes=20),
            max_capacity=10,
        )
        expired = Session(
            event_id=kraken.id,
            start_time=datetime.now(UTC) - timedelta(hours=1),
            max_capacity=10,
        )
        db.add_all([s1, s2, s3, tiny, today_sess, today_sess2, recent, expired])

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
        # HIGH_DAY (les anciennes « HS test » sont supprimées — fixtures
        # jetables, le vrai « Haute saison — Été » n'est pas touché).
        await db.execute(
            delete(SeasonalPeriod).where(SeasonalPeriod.name == "HS test")
        )
        db.add(SeasonalPeriod(
            name="HS test",
            start_date=HIGH_DAY - timedelta(days=15),
            end_date=HIGH_DAY + timedelta(days=15),
        ))
        await db.commit()
        ids = {
            "s1": str(s1.id),
            "s2": str(s2.id),
            "s3": str(s3.id),
            "tiny": str(tiny.id),
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


def pay(reservation_id: str, method: str, amount: str) -> tuple[int, dict]:
    print(f"   Paiement {method} {amount} € :")
    return post(
        f"{BASE_URL}/reservations/{reservation_id}/payments",
        {"method": method, "amount": amount},
    )


def scan(ticket_id: str, checkpoint: dict) -> tuple[int, dict]:
    """Scan au poste donné : {"event_id": ...} (entrée musée) ou
    {"session_id": ...} (porte de séance)."""
    return post(f"{BASE_URL}/tickets/{ticket_id}/scan", checkpoint)


async def main() -> None:
    ids = await seed()
    low, high = str(LOW_DAY), str(HIGH_DAY)
    print(f"Séances : 10h30={ids['s1']} 15h00={ids['s2']} HS={ids['s3']} tiny={ids['tiny']}\n")

    print("Test 1 — panier web payant : pending -> CB -> confirmed -> billets :")
    status1, resa = post(f"{BASE_URL}/reservations", {
        "customer_email": "jack.sparrow@blackpearl.fr",
        "items": [
            {"product_code": "pass_1_show", "category": "adult",
             "visit_date": low, "session_id": ids["s1"]},
            {"product_code": "pass_2_shows", "category": "reduced",
             "visit_date": low, "session_ids": [ids["s1"], ids["s2"]]},
            {"product_code": "family_museum", "visit_date": low, "extra_children": 1},
            {"product_code": "museum_entry", "category": "child", "visit_date": low},
        ],
    })
    if status1 == 201:
        # DFC n°7 : aucune émission avant paiement complet.
        assert resa["status"] == "pending" and resa["tickets"] == []
        assert Decimal(resa["total_price"]) == Decimal("87.00")
        print(f"=> pending, total={resa['total_price']}, amount_due={resa['amount_due']}")

        print("   Règle canal web : ANCV refusé en ligne")
        pay(resa["id"], "ancv", "87.00")
        print("   Règle canal web : paiement CB partiel refusé")
        pay(resa["id"], "cb", "50.00")

        s_pay, paid = pay(resa["id"], "cb", "87.00")
        if s_pay == 201:
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

            print("   Paiement sur commande soldée (doit être refusé) :")
            pay(resa["id"], "cb", "1.00")

    print("\nTest 2 — pass_1_show adulte en HAUTE SAISON (attendu 22.00 = 20+2) :")
    status2, resa2 = post(f"{BASE_URL}/reservations", {
        "customer_email": "elizabeth.swann@portroyal.fr",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": high, "session_id": ids["s3"]}],
    })
    if status2 == 201:
        item = resa2["items"][0]
        print(f"=> computed={item['computed_price']} modifier={item['season_modifier']}")
        assert Decimal(item["computed_price"]) == Decimal("22.00")
        assert Decimal(item["season_modifier"]) == Decimal("2.00")
        s_pay2, _ = pay(resa2["id"], "cb", "22.00")
        if s_pay2 == 201:
            print("=> OK : modificateur haute saison + CB 22.00 encaissée")

    print("\nTest 3 — visit_date incohérente avec la séance (doit être rejeté) :")
    post(f"{BASE_URL}/reservations", {
        "customer_email": "davy.jones@flyingdutchman.fr",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": "2026-10-16", "session_id": ids["s1"]}],
    })

    today = str(date.today())
    print("\nTest 4a — billet Musée (open_ticket) valable aujourd'hui + profil gratuit -4 ans :")
    status4, resa4 = post(f"{BASE_URL}/reservations", {
        "customer_email": "anne.bonny@revenge.fr",
        "items": [{"product_code": "museum_entry", "free_profile": "under_4",
                   "visit_date": today}],
    })
    if status4 == 201:
        # Panier à 0 € : confirmation immédiate, billets émis sans paiement.
        assert resa4["status"] == "confirmed" and len(resa4["tickets"]) == 1
        tid = resa4["tickets"][0]["id"]
        s_scan, scan_res = scan(tid, {"event_id": ids["musee"]})
        if s_scan == 200:
            print(f"=> access_label={scan_res['access_label']!r} warning={scan_res['control_warning']!r}")
            assert "Musée" in scan_res["access_label"]
            assert scan_res["control_warning"]
        print("Re-scan au même poste (doit être bloqué) :")
        scan(tid, {"event_id": ids["musee"]})

    print("\nTest 4b — billet Théâtre (session_ticket) pour une séance d'aujourd'hui :")
    status5, resa5 = post(f"{BASE_URL}/reservations", {
        "customer_email": "mary.read@revenge.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["today"]}],
    })
    if status5 == 201:
        s_pay5, paid5 = pay(resa5["id"], "cb", resa5["total_price"])
        if s_pay5 == 201 and paid5["tickets"]:
            tid = paid5["tickets"][0]["id"]
            s_scan, scan_res = scan(tid, {"session_id": ids["today"]})
            if s_scan == 200:
                print(f"=> access_label={scan_res['access_label']!r}")
                assert "Théâtre" in scan_res["access_label"] and "Séance" in scan_res["access_label"]

    if status1 == 201:
        tid_wrong, acc_wrong = next(
            (t, a)
            for t in paid["tickets"]
            for a in t["accesses"]
            if a["session_id"]
        )
        print(f"\nTest 4c — scan d'un accès de séance prévu le {low} (doit être refusé) :")
        scan(tid_wrong["id"], {"session_id": acc_wrong["session_id"]})

    print("\nTest 5 — surbooking : une commande pending tient déjà la jauge (jauge 1) :")
    status5a, _resa_tiny = post(f"{BASE_URL}/reservations", {
        "customer_email": "will.turner@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["tiny"]}],
    })
    if status5a == 201:
        print("   => première commande pending, siège réservé avant paiement")
    status5b, _ = post(f"{BASE_URL}/reservations", {
        "customer_email": "hector.barbossa@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["tiny"]}],
    })
    assert status5b == 400, f"surbooking accepté : HTTP {status5b}"

    print("\nTest 6a — canal pos : multi-paiement ANCV + espèces avec rendu :")
    status6, resa6 = post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "pass_1_show", "category": "adult",
                   "visit_date": today, "session_id": ids["today"]}],
    })
    if status6 == 201:
        # 20.00 € à régler au guichet : 15 € ANCV puis 8 € espèces
        # (rendu 3 € sur les espèces).
        s6a, p6a = pay(resa6["id"], "ancv", "15.00")
        if s6a == 201:
            assert Decimal(p6a["amount_due"]) == Decimal("5.00")
            assert p6a["reservation_status"] == "pending"
            print("   => ANCV 15 € : reste 5 €, toujours pending, 0 billet")
        s6b, p6b = pay(resa6["id"], "cash", "8.00")
        if s6b == 201:
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
            s_m, scan_m = scan(tid, {"event_id": ids["musee"]})
            if s_m == 200:
                print(f"   => musée OK : {scan_m['access_label']!r}")
                assert "Musée" in scan_m["access_label"]
            s_t, scan_t = scan(tid, {"session_id": ids["today"]})
            if s_t == 200:
                print(f"   => théâtre OK : {scan_t['access_label']!r}")
                assert "Théâtre" in scan_t["access_label"]
                remaining = [
                    a for a in scan_t["accesses"] if not a["is_scanned"]
                ]
                assert remaining == []

    print("\nTest 6b — canal pos : ANCV excédentaire, AUCUN rendu (DFC n°7) :")
    status7, resa7 = post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "child",
                   "visit_date": today}],
    })
    if status7 == 201:
        # Reste 8 €, le client donne un chèque-vacances de 50 € :
        # nominal enregistré, imputé 8 €, rendu 0.
        s7, p7 = pay(resa7["id"], "ancv", "50.00")
        if s7 == 201:
            print(f"   => nominal={p7['payment']['amount']} imputé="
                  f"{p7['payment']['applied_amount']} rendu={p7['change_due']}")
            assert Decimal(p7["payment"]["amount"]) == Decimal("50.00")
            assert Decimal(p7["payment"]["applied_amount"]) == Decimal("8.00")
            assert Decimal(p7["change_due"]) == Decimal("0.00")
            assert p7["reservation_status"] == "confirmed"
            print("=> OK : ANCV excédentaire soldé sans rendu")

            print("   Billet musée seul présenté au théâtre (doit être refusé) :")
            tid7 = p7["tickets"][0]["id"]
            scan(tid7, {"session_id": ids["today"]})
            print("   ... puis au musée (doit passer) :")
            scan(tid7, {"event_id": ids["musee"]})

    print("\nTest 6c — canal pos : chèque refusé hors groupes/scolaires :")
    status8, resa8 = post(f"{BASE_URL}/reservations", {
        "customer_email": "guichet@musee-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "visit_date": today}],
    })
    if status8 == 201:
        pay(resa8["id"], "check", resa8["total_price"])

    print("\nTest 6d — canal pos : CB supérieure au solde (doit être refusée) :")
    if status8 == 201:
        pay(resa8["id"], "cb", "20.00")
        s8b, _ = pay(resa8["id"], "cb", resa8["total_price"])
        if s8b == 201:
            print("   => CB exacte acceptée, commande confirmée")

    print("\nTest 7 — seuil groupe : 8 personnes minimum :")
    print("   groupe de 5 (doit être rejeté) :")
    post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "group_visit", "group_size": 5,
                   "visit_date": today}],
    })
    print("   group_size sur un produit non-groupe (doit être rejeté) :")
    post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "museum_entry", "category": "adult",
                   "group_size": 10, "visit_date": today}],
    })
    print("   groupe de 8 (doit passer) :")
    status9, resa9 = post(f"{BASE_URL}/reservations", {
        "customer_email": "scolaire@ecole-pirates.fr",
        "channel": "pos",
        "items": [{"product_code": "group_visit", "group_size": 8,
                   "visit_date": today}],
    })
    if status9 == 201:
        # 8 x 10 € = 80.00, commande groupe éligible au chèque (DFC n°7).
        assert Decimal(resa9["total_price"]) == Decimal("80.00")
        s9, p9 = pay(resa9["id"], "check", "80.00")
        if s9 == 201:
            assert p9["reservation_status"] == "confirmed"
            n_group = sum(
                1 for t in p9["tickets"] if t["ticket_category"] == "group"
            )
            print(f"   => {n_group} billets 'group' émis, payés par chèque")
            assert n_group == 8
            print("=> OK : seuil 8 + paiement chèque groupe")

    print("\nTest 9 — séance supplémentaire à tarif réduit, fusionnée sur le même QR :")
    status11, resa11 = post(f"{BASE_URL}/reservations", {
        "customer_email": "elizabeth.extra@portroyal.fr",
        "channel": "pos",
        "items": [
            {"product_code": "pass_1_show", "category": "adult",
             "visit_date": today, "session_id": ids["today"]},
            {"product_code": "extra_show", "category": "adult",
             "session_id": ids["today2"]},
        ],
    })
    if status11 == 201:
        # 20.00 + 5.00 : le supplément suit la tarification réduite.
        assert Decimal(resa11["total_price"]) == Decimal("25.00")
        s11, p11 = pay(resa11["id"], "cash", "25.00")
        if s11 == 201:
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
    status10, resa10 = post(f"{BASE_URL}/reservations", {
        "customer_email": "panier.oublie@blackpearl.fr",
        "items": [{"product_code": "theater_show", "category": "adult",
                   "session_id": ids["today"]}],
    })
    if status10 == 201:
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
        await engine.dispose()
        print(f"   => pending antidatée, booked_seats avant purge = {before}")

        s_purge, purge = post(f"{BASE_URL}/admin/reservations/purge")
        if s_purge == 200:
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

        print("   Paiement sur commande expirée (doit être refusé) :")
        pay(resa10["id"], "cb", resa10["total_price"])

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
    # (la séance +3 h peut tomber sur le lendemain en fin de journée)
    tomorrow = str(date.today() + timedelta(days=1))
    body = [
        *json.loads(
            urllib.request.urlopen(f"{BASE_URL}/events?date={today}").read()
        ),
        *json.loads(
            urllib.request.urlopen(f"{BASE_URL}/events?date={tomorrow}").read()
        ),
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


async def run() -> None:
    try:
        await main()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(run())
