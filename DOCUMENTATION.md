# Musée des Pirates — Documentation du projet

> **Dernière mise à jour : 2026-10-10**
> Ce document est la référence vivante du projet. Il doit être mis à jour à
> chaque évolution (voir §13 — Maintenance). Cible : `OBJECTIFS.md` ;
> pilotage : `PILOTAGE.md`. La mémoire Honcho (peer `user-default-dev`) est
> une couche de contexte **facultative** — jamais source de vérité.

---

## 1. Vue d'ensemble

Le **Musée des Pirates** est une plateforme web complète de billetterie et de
contrôle d'accès pour un complexe culturel **fictif**. C'est un bac à sable
métier : la complexité vient des interactions entre fonctionnalités
(concurrence, capacité, tarification, produits composés, annulation,
multi-paiements, contrôle d'accès, ventes multicanal), pas du site lui-même.

**Le Complexe** (nomenclature réservée à l'ensemble) comprend :

| Espace | Flux | Modèle |
|---|---|---|
| Musée des Pirates | Flux continu | Accès journée (`open_ticket`) |
| Théâtre du Kraken | Flux ponctuel | Séances à heure fixe, jauge stricte |
| Restaurant / Taverne | Flux séquencé | Créneaux midi/soir + dîner-spectacle |

**Défi technique central** (Point 13 du cahier des charges) : gestion de la
concurrence et du **surbooking** en temps réel — deux clients ne doivent jamais
acheter la même place.

**Validation finale** : Pierre (humain).

**Répertoire** : `C:\Users\pierr\dev\musee`

---

## 2. Architecture & stack

Architecture **API-first**, 3 tiers :

```
Next.js :3000 ──HTTP──> FastAPI :8000 ──asyncpg──> PostgreSQL :5432 (Docker)
 (frontend/)              (backend/)                  (docker-compose)
```

| Couche | Technologie |
|---|---|
| Frontend | Next.js 16.3.8 (App Router), React 19, TypeScript, Tailwind CSS 4, shadcn/ui (radix), sonner, date-fns, `@yudiel/react-qr-scanner`, `qrcode.react`, lucide-react |
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 **async**, Pydantic v2, Alembic, asyncpg, uvicorn |
| Base | PostgreSQL 15 (image `postgres:15-alpine`, conteneur `musee_postgres`, volume `musee_pgdata`) |

**Config** : `.env` racine → `DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/musee_db`
(lu par `backend/app/core/config.py`). Frontend : `frontend/.env.local` →
`NEXT_PUBLIC_API_URL=http://localhost:8000`.

**CORS** : ouvert (`*`) — environnement de dev uniquement.

**Dépôt GitHub** : `github.com/pierremusee/museedespirates` (**public**,
branche `main`,
compte dédié `pierremusee` — séparé du compte perso `pierrusthemaboul`).
Auth : PAT fine-grained stocké dans Git Credential Manager pour
`pierremusee@github.com` (remote `https://pierremusee@github.com/...` →
isolation par repo, aucun mélange de credentials avec l'autre compte) ;
commandes `gh` API via `GH_TOKEN` lu depuis `C:\Users\pierr\github-token.txt.txt`.
Commits signés `pierremusee <338410385+pierremusee@users.noreply.github.com>`.

---

## 3. Arborescence

```
musee/
├── .github/workflows/ci.yml    # CI M2 : Postgres + tests + lints
├── docker-compose.yml          # PostgreSQL 15 + healthcheck
├── .env                        # DATABASE_URL
├── DOCUMENTATION.md            # ← ce fichier
├── 01_initialisation_backend.md.md   # mission fondatrice backend
├── 02_modeles_bdd.md.md              # mission fondatrice modèles
├── gemini-code-1791050970318.md      # mission fondatrice frontend
├── backend/
│   ├── requirements.txt
│   ├── requirements-dev.txt    # ruff + pytest/pytest-asyncio/pytest-cov
│   ├── pyproject.toml          # config ruff + pytest + coverage
│   ├── alembic.ini, alembic/         # migrations (12 versions)
│   ├── venv/
│   ├── app/
│   │   ├── main.py                   # FastAPI + lifespan (purge loop 60 s)
│   │   ├── core/config.py            # Settings pydantic-settings
│   │   ├── db/                       # Base + AsyncSessionLocal + get_db
│   │   ├── models/                   # 9 modèles SQLAlchemy
│   │   ├── schemas/                  # Pydantic v2 (in/out)
│   │   ├── api/                      # events, products, reservations,
│   │   │                             # tickets, admin
│   │   └── services/                 # reservation_service, ticket_service,
│   │                                 # pricing  ← moteur métier unique
│   ├── scripts/
│   │   ├── seed_db.py                # catalogue + événements (idempotent)
│   │   ├── test_booking.py           # suite E2E via API HTTP
│   │   └── test_concurrency.py       # preuve concurrence (M1)
│   └── tests/                        # suite métier pytest (PostgreSQL
│       ├── conftest.py               #   musee_test, isolation transaction
│       ├── factories.py              #   rollbackée par test)
│       ├── test_pricing.py
│       ├── test_reservation_rules.py
│       ├── test_reservation_flow.py
│       ├── test_payments.py
│       ├── test_input_bounds.py
│       ├── test_scan.py
│       └── test_purge.py
└── frontend/
    ├── app/
    │   ├── page.tsx                  # accueil
    │   ├── layout.tsx                # Geist + Toaster (sonner)
    │   ├── (client)/reserver/        # page + date-picker + dialog
    │   ├── (client)/succes/          # confirmation + cartes-billets
    │   └── (admin)/scanner/          # contrôle d'accès QR
    │   └── (admin)/caisse/           # terminal POS (pos-terminal.tsx +
    │                                 #   cart.ts moteur panier + cart.test.ts)
    ├── components/ui/                # shadcn/ui
    ├── components/                   # ticket-card, ticket-qr,
    │                                 # print-tickets-button (partagés)
    └── lib/utils.ts
```

---

## 4. Modèle de données

### Tables et rôle

| Table | Modèle | Rôle |
|---|---|---|
| `events` | `Event` | Entité du Complexe (musée, pièce de théâtre…). `event_type` : `permanent_exhibition` / `theater` / `guided_tour`. `description` = texte éditorial optionnel (affiche des pièces). `is_active` = retrait non destructif du catalogue. |
| `sessions` | `Session` | Séance d'un événement. `max_capacity`, `booked_seats` (compteur anti-surbooking, contrainte `booked_seats <= max_capacity`). `remaining_capacity` exposé en API. |
| `products` | `Product` | Produit vendable. `kind` : `simple` / `pass` / `family` / `group`. Prix de base **basse saison** (`price_adult`, `price_child`, `price_reduced`, `family_base_price`, `extra_child_price`). |
| `product_components` | `ProductComponent` | Contenu d'un produit **par personne**. `component_type` : `museum_day` / `theater_session` / `dining_session` (+ `quantity`, `event_id` pour les accès musée). |
| `seasonal_periods` | `SeasonalPeriod` | Plages « haute saison » déclenchant le modificateur tarifaire (DFC n°5 §4). |
| `reservations` | `Reservation` | Commande/panier. `status` : `pending` / `confirmed` / `cancelled` / `expired`. `sales_channel` : `web` / `pos`. Propriétés `paid_amount`, `amount_due`. |
| `reservation_items` | `ReservationItem` | Ligne de commande. Fige `computed_price`, `season_modifier`, `visit_date`, `session_ids` (JSONB), `group_size`, `free_profile`, `category`, `extra_children`. |
| `payment_transactions` | `PaymentTransaction` | Tranche d'encaissement (DFC n°7). `method` : `cb` / `cash` / `ancv` / `check`. `amount` = nominal remis, `applied_amount` = part imputée (diffèrent pour ANCV excédentaire). `status` : `completed` (+ `refunded`/`cancelled` envisagés). `idempotency_key` : UUID unique identifiant l'opération logique (NULL pour l'historique). `response` : snapshot JSONB du `PaymentResult` initial, restitué verbatim au rejeu. |
| `tickets` | `Ticket` | **1 billet = 1 personne**. `id` (UUID) = jeton encodé dans le QR. `ticket_category` : `adult` / `child` / `reduced` / `group` / `school`. `item_id` = item « ancre » (warning gratuité au scan). `menu_choice` : `viande` / `poisson` / `vegetarien` / `enfant` (optionnel). |
| `ticket_accesses` | `TicketAccess` | Droit d'accès unitaire porté par un billet, consommé **indépendamment** au scan. `access_type` (enum `ticket_type`) : `open_ticket` / `session_standard` / `session_dining`. `session_id`, `event_id`, `valid_date`, `is_scanned`/`scanned_at` **par accès**. |

### Relations

```
Event 1──n Session 1──n TicketAccess
Product 1──n ProductComponent
Reservation 1──n ReservationItem n──1 Product
Reservation 1──n Ticket 1──n TicketAccess
Reservation 1──n PaymentTransaction
ReservationItem 1──n Ticket (item ancre)
```

### Refonte « 1 billet par personne » (2026-10-05)

- **Avant** : 1 billet = 1 personne × 1 composant (un pass musée+théâtre =
  2 QR/personne).
- **Après** : 1 billet = 1 personne portant **N accès** consommés
  indépendamment — le même QR passe au musée ET au théâtre.
- `_emit_tickets()` (reservation_service) : fusion gloutonne — une personne
  se rattache au premier billet de même (catégorie, profil gratuité) n'ayant
  pas déjà un accès de même clé `(access_type, event_id, session_id)`.
  **Limite connue** : deux achats distincts de même catégorie peuvent fusionner
  alors qu'ils visaient deux personnes (les pass restent la voie recommandée).
- Migration `a1c9e4f7b2d3` **sans backfill** → les billets pré-migration sont
  refusés au scan.

### Migrations Alembic (ordre)

`14fb7bdea1ce` init + check capacity → `5e59dc6519d3` prix par catégorie sur
Event (supersédé par products) → `d38f51a62b90` catalogue produits →
`e5a91c7f3d28` gratuité → `c7d31e08a4f6` groupes → `f2c4a8e91b07` paiements →
`e8a2f50b1c39` réservation expirée → `a1c9e4f7b2d3` billet multi-accès →
`b7e2a1f09c34` refonte billets → `d4b8c2e61a07` `events.description`
(affiche des productions) → `e6b3f1a84c2d` `products.is_addon` →
`f5a8c1d93e04` idempotence des paiements (`idempotency_key` unique +
`response` JSONB — non destructive, NULL pour l'historique).

---

## 5. Règles métier

### Anti-surbooking (Point 13)

- `booked_seats` est incrémenté **à la création** de la commande (avant
  paiement), sous `SELECT ... FOR UPDATE` — les sessions sont verrouillées
  **triées par id** (anti-deadlock).
- Contrainte SQL `booked_seats <= max_capacity` en filet de sécurité.
- Une commande `pending` tient la jauge ; la purge la libère.
- **Démontré sous concurrence** (2026-10-06, `test_concurrency.py`) :
  30 requêtes simultanées sur jauge 5 → 5 acceptées, 25 rejets 400,
  `booked_seats = 5`, zéro dépassement.

### Cycle de vie d'une réservation

```
pending ──(solde = 0 via payments, ou total = 0 €)──> confirmed  → billets émis
   ├──(annulation guichet, pending seulement)──> cancelled  → sièges restitués,
   │                                                          paiements conservés
   └──(TTL 15 min sans solde, purge 60 s)──> expired       → sièges restitués
```

- **Émission différée** (DFC n°7) : les billets ne sont émis que lorsque
  `amount_due` atteint 0 → `confirmed`. Séance supprimée entre-temps → `409`.
- **Fenêtre de vente des séances** : une séance dont le début +
  `LATE_TOLERANCE` (30 min, partagée avec le contrôle d'accès) est passé
  ne peut plus être vendue → `400`, quel que soit le canal. Une séance
  commencée mais encore dans la tolérance reste vendable — cohérent avec
  le scan qui l'accepterait encore. La même propriété `Session.is_expired`
  (modèle) est exposée par `GET /events` (`SessionRead.is_expired`) : le
  POS grise les séances expirées sans dupliquer la règle.
- Panier à 0 € (DFC n°6) : confirmation immédiate sans encaissement.
- TTL panier : 15 min (`PENDING_TTL`) ; purge périodique toutes les 60 s dans
  le lifespan FastAPI + endpoint `POST /admin/reservations/purge`.

### Tarification (DFC n°5)

- Prix **recalculés côté serveur** depuis le catalogue `products` — jamais
  depuis le client.
- Modificateur **haute saison** appliqué au calcul, jamais stocké :
  `+2 €` adulte/réduit, `+1 €` enfant, `+5 €` forfait famille. Déclenché si la
  date de référence (`visit_date` ou jour de la 1ʳᵉ séance) tombe dans une
  `SeasonalPeriod`.
- `computed_price` et `season_modifier` figés par item (traçabilité).
- **Produits add-on** (`is_addon`, ex. `extra_show`) : chaque personne
  add-on doit être couverte par un produit non-add-on accordant le même
  droit dans la même commande, sinon **400**. Sans cette règle,
  « musée + séance supp. » (17 €) court-circuiterait le Pass (20 €) et
  `extra_show` seul le billet théâtre plein tarif.
- **Cohérence de grille** : `pass_2_shows` reste strictement sous
  `pass_1_show + extra_show` par catégorie (24/16/18 < 25/16,50/19 €) —
  le pass groupé est toujours la meilleure offre.

### Gratuités (DFC n°6)

`free_profile` sur l'item → billet émis à 0 €, catégorie physique conservée
(la jauge compte) :

| Profil | Billette émise | Contrôle |
|---|---|---|
| `under_4` | child | « vérifier l'âge » |
| `disability` | catégorie de l'item (adult/child requis) | « vérifier la carte » |
| `pmr_companion` | adult | « accompagnateur PMR » |

Accompagnateur PMR : **max 1 par porteur `disability`** dans la même commande.
`free_profile` interdit sur `family` et `group`.

### Canaux de vente & paiements (DFC n°7)

| | Web (en ligne) | POS (guichet / taverne) |
|---|---|---|
| Moyens | `cb` uniquement | `cb`, `cash`, `ancv`, `check` |
| Paiement | unique, montant total exact | multi-tranches cumulées |
| Rendu | — | espèces : oui ; **ANCV : jamais** (excédent perdu, `amount` ≠ `applied_amount`) |
| Chèque | non | réservé aux commandes contenant groupe/scolaire |

### Idempotence des paiements (2026-10-09)

`POST /reservations/{id}/payments` exige `idempotency_key` (UUID, dans
le corps JSON — validé par Pydantic ; pas d'en-tête dédié) identifiant
**l'opération logique** d'encaissement :

- **clé nouvelle** → traitement normal, `201` ;
- **même clé + même contenu** (réservation, moyen, montant) → rejeu :
  `200` + corps **identique** au premier appel (snapshot `response`
  JSONB restitué verbatim — solde historique, tickets initiaux, même
  si la commande a été annulée ou soldée depuis) ;
- **même clé + contenu différent** (autre montant, autre moyen, autre
  réservation) → `409 Conflict` ;
- **échec avant enregistrement** (validation 4xx, rollback, crash) →
  la clé n'est pas consommée : le retry est une nouvelle tentative
  légitime, pas un conflit.

Mécanique dans `add_payment` : lookup de la clé avant le verrou
(chemin rapide), `SELECT ... FOR UPDATE` sur la réservation, **second
lookup sous le verrou** (un concurrent de même clé a pu commiter
entre-temps — son rejeu prime sur la garde « déjà soldée »), puis
imputation : **INSERT contrôlé de la tranche dès le flush explicite,
avant l'émission des billets** — sans cela, les lectures de
`_emit_tickets` déclencheraient un autoflush hors du périmètre de
récupération et la collision remonterait en 500 au lieu de 409 (cas
d'une commande à séance soldée par l'encaissement — corrigé
2026-10-09). Émission + **snapshot sérialisé dans la même transaction**
(`flush` avant `model_dump` pour disposer des ids/`created_at` ; les
accès émis portent leur relation `session` peuplée — les champs
`session_start`/`session_event_title` du JSONB sont identiques à
ceux d'une relecture `GET`). La contrainte
`uq_payment_transactions_idempotency_key` reste la garantie ultime :
une course entre deux réservations sur la même clé provoque une
`IntegrityError` → rollback → relecture → rejeu ou 409 selon
l'empreinte. **Démontré sous concurrence** (`test_concurrency.py` :
8 paiements simultanés même clé → 1×201 + 7×200, 1 transaction ;
même clé sur 2 réservations → 201 + 409, y compris sur commandes à
séance soldées).

**Cycle de vie client** : clé `crypto.randomUUID()` créée au
déclenchement d'une nouvelle opération, conservée tant que l'issue est
incertaine (réseau, timeout, 5xx), libérée après succès ou rejet 4xx
certain. Sur les **deux** canaux, l'opération en attente (réservation +
clé + moyen + montant, rien de sensible) est persistée en
`sessionStorage` (`pending-payment.ts` / `pos-pending.ts`, testés
vitest) et les paramètres de l'opération sont **figés** tant qu'elle
n'est pas résolue. Règle cardinale : **un échec de lecture n'est jamais
assimilé à un abandon** — si `GET /reservations/{id}` échoue ou ne
permet pas d'établir l'état, l'opération est conservée et tout nouvel
achat est bloqué (`previousOutcome`/`posResolution` →
`"unknown"`/`"blocked"`, liste blanche des statuts — toute valeur non
reconnue est incertaine). Les flux « GET + décision » sont extraits
(`checkPreviousOutcome` / `resolveStoredPending`, fetch injecté) pour
être testés vitest sur les pannes réelles : erreur réseau, 5xx, corps
JSON mal formé, statut non-string ou non reconnu.

Billetterie web : la reprise rejoue le paiement sur **la même
réservation** sans recréer de panier ni revalider le formulaire — qui
est gelé (`fieldset disabled`) avec le montant réel de l'opération, pas
l'estimation. Une opération en attente sur un autre produit est résolue
par `GET /reservations/{id}` avant écrasement (confirmée → `/succes`,
commande morte → abandonnée à la purge, indéterminée → blocage).
**« Abandonner » est purement local** (oubli de la clé `sessionStorage`,
aucune mutation serveur) — un éventuel encaissement resterait tracé sur
la réservation ; l'abandon d'un `pending` est sans risque car le canal
web n'admet que la CB au montant exact : une réservation web `pending`
ne peut porter aucun encaissement (invariant prouvé par
`test_canal_web_ne_peut_pas_etre_partiellement_paye`). À la caisse, un
`pending` porteur d'acomptes n'est jamais « abandonné » mais **repris**
avec la même clé (`posResolution → resume`).

Caisse : verrou synchrone `paymentInFlight` (ref — le state `busy`
laisse passer un double clic dans le même batch React) ; tant qu'une
tranche est incertaine, moyen/montant figés, « Modifier la commande »,
« Nouvelle vente » et `submitOrder` bloqués, bouton « Réessayer
l'encaissement ». Au rechargement, la commande est restaurée via GET
(`pending` → reprise même clé ; `confirmed` → commande restaurée avec
billets ; annulée/expirée → clé libérée, encaissements éventuels
signalés pour remboursement manuel ; état indéterminé → caisse bloquée
avec bouton « Revérifier »).

### Groupes

- Seuil officiel : **minimum 8 personnes** (`MIN_GROUP_SIZE = 8`),
  `group_size` obligatoire sur produit `kind = group`, interdit ailleurs.
- Tarif : `price_adult` du produit = prix par personne.

### Bornes d'entrée (montants et quantités)

Une commande couvre au plus **120 personnes**
(`MAX_PERSONS_PER_RESERVATION` — repères : jauge de séance seedée ~80,
« capacité de 120 » de la spec OBJECTIFS §17) et **120 lignes**
(`MAX_ITEMS_PER_RESERVATION` — les interfaces émettent **1 ligne par
personne individuelle**, la borne lignes ne peut donc pas être
inférieure au plafond de personnes). Une tranche d'encaissement est
plafonnée à **50 000 €**
(`MAX_PAYMENT_AMOUNT`, sous la capacité `Numeric(10,2)` =
99 999 999,99 € — dépassement PostgreSQL impossible).
`customer_email` est déjà borné à 254 caractères par `EmailStr`
(RFC 5321) — sous la colonne `String(320)`.

**Comptage des personnes** : seules les lignes de produits de base
comptent — un individuel = 1, une famille = 4 + `extra_children`, un
groupe = `group_size`. Les produits `is_addon` (séance
supplémentaire…) **ne comptent pas** : la règle de couverture impose
déjà qu'ils portent des accès pour des personnes couvertes par un
produit de base de la même commande — ce sont des accès
supplémentaires, pas des personnes en plus. La capacité des séances
reste décomptée par accès réel (`_session_slots`), indépendamment.

Champ hors borne → **422** (Pydantic) ; total de personnes dépassé en
agrégat par des lignes individuellement valides → **400** en service,
comme « capacité insuffisante ». `session_ids` reste borné par la
règle métier (nombre exact de slots). Sans ces bornes, une valeur
extrême débordait la colonne PostgreSQL (500 évitable) ou déclenchait
une création massive de billets sur les produits sans jauge — seul
vecteur réel, les produits à séance étant déjà bornés par la jauge.

### Pass « Journée »

Tout produit contenant `museum_day` + séance(s) impose `visit_date` = jour
civil de chaque séance (Europe/Paris) — vérifié à la réservation.

---

## 6. Contrôle d'accès (scan)

**Endpoint** : `POST /tickets/{ticket_id}/scan` — corps = **exactement un**
poste (`event_id` = entrée journée/musée, `session_id` = porte de séance ;
validation `model_validator`).

**Règles** (ticket_service.py) :

- `open_ticket` : valable le jour civil `valid_date` (Europe/Paris), sans
  contrainte d'heure.
- `session_*` : valable le jour de la séance, jusqu'à `start_time + 30 min`
  (`LATE_TOLERANCE`).
- **Usage unique par accès** (`is_scanned`) — un même QR peut donc être
  présenté à plusieurs postes (conséquence voulue du modèle polymorphique).
- Verrou `SELECT ... FOR UPDATE` sur les accès → deux douchettes simultanées
  se sérialisent, la seconde voit l'accès consommé (anti double-scan).
  **Démontré** (`test_concurrency.py`) : 8 scans concurrents du même accès
  → 1 seul accepté, 7 rejets 400.
- `control_warning` remonté pour les profils gratuits (via l'item ancre).
- Réponse = accès validé (`access_label`) + liste complète des accès du billet.

**Résidus connus** : le scan ne vérifie pas `reservation.status` (à durcir
si remboursement). Billets émis avant la migration `a1c9e4f7b2d3` refusés.

---

## 7. API — référence des endpoints

Base : `http://localhost:8000` — docs auto : `/docs` (Swagger).

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/health` | `{"status":"ok","project":"Musée des Pirates"}` |
| GET | `/events?date=YYYY-MM-DD` | Événements actifs + séances. Avec `date` : filtre journée civile Europe/Paris (`contains_eager`). |
| GET | `/products` | Catalogue produits actifs + composants. |
| GET | `/seasonal/check?date=YYYY-MM-DD` | `{date, high_season}` — modificateur DFC n°5. |
| POST | `/reservations` | Crée le panier : `{customer_email, channel, items[]}`. Item : `product_code`, `category`, `visit_date`, `session_id`/`session_ids`, `extra_children`, `group_size`, `free_profile`. → `201 ReservationRead` (pending, sièges réservés). |
| GET | `/reservations/{id}` | Détail : items, tickets+accesses, payments, `paid_amount`, `amount_due`. |
| POST | `/reservations/{id}/payments` | Tranche d'encaissement `{method, amount, idempotency_key}` → `201 PaymentResult` (`change_due`, `amount_due`, `reservation_status`, `tickets` si soldé). Rejeu même clé+même contenu → `200` + snapshot identique ; même clé + contenu différent → `409` (§5 Idempotence). |
| POST | `/reservations/{id}/cancel` | Annule une commande `pending` (guichet) — sièges restitués, paiements conservés. |
| POST | `/tickets/{ticket_id}/scan` | Contrôle d'accès `{event_id}` XOR `{session_id}` → `TicketScanResponse`. |
| POST | `/admin/reservations/purge` | Expire les `pending` > 15 min, restitue la jauge. |

---

## 8. Frontend

Next.js 16 / App Router, TypeScript, Tailwind 4, shadcn/ui. Fetches côté
serveur (`cache: "no-store"`) sur `NEXT_PUBLIC_API_URL`.

| Route | Fichier | Rôle |
|---|---|---|
| `/` | `app/page.tsx` | Accueil « Musée des Pirates » + CTA réservation. |
| `/reserver` | `(client)/reserver/page.tsx` | Server Component : catalogue produits en cartes, séances du jour, badge haute saison, sold-out. `?date=` pilote le jour. |
| | `date-picker.tsx` | Calendrier shadcn + popover + date-fns (fr) → pousse `?date=`. |
| | `reservation-dialog.tsx` | Client : compteurs par tarif + profils gratuits, choix de séance(s), séance supplémentaire `extra_show` fusionnée, estimation live (re-calculée serveur), POST reservation + paiement CB simulé **idempotent** (`idempotency_key`, reprise `sessionStorage` via `pending-payment.ts`, formulaire figé tant qu'une opération est incertaine — échec de vérification = blocage, jamais abandon), redirect `/succes`. |
| `/succes` | `(client)/succes/page.tsx` | Confirmation : lignes, total, référence, puis **cartes-billets** (`TicketCard`) + bouton « Imprimer les billets ». |
| `/scanner` | `(admin)/scanner/page.tsx` | Client : sélecteur de poste (entrée musée / séances du jour), `@yudiel/react-qr-scanner`, verdict vert/rouge, warning justificatif, droits restants. |
| `/caisse` | `(admin)/caisse/page.tsx` + `pos-terminal.tsx` + `cart.ts` | Terminal POS guichet : **ligne = produit × N personnes** (compteurs par tarif, profils gratuits inclus), composition miroir à l'ajout (uniquement des droits non encore couverts — `copiableCounts`), **optimisation automatique « Pass 1 Spectacle »** quand musée + séance à composition strictement identique coexistent (`strictPassHint` → lignes consommées supprimées, badge « Optimisé », toast d'économie ; cas partiels/ambigus = suggestion explicite `passSuggestion`), absorption des lignes simples au clic direct sur le Pass, séance supplémentaire par ligne (`addExtraShow`), multi-paiements **idempotents** avec rendu (clé par tranche persistée `sessionStorage` via `pos-pending.ts`, verrou synchrone anti double-clic, paramètres figés + blocage nouvelle vente tant qu'une tranche est incertaine, restauration de la commande après rechargement via GET), annulation « Modifier la commande », émission/impression des cartes-billets. Logique panier extraite dans `cart.ts` (purs fonctions, testées par `cart.test.ts` — vitest). |

**Billet carte (CR80, 85,6 × 54 mm)** — composant partagé
`components/ticket-card.tsx` : QR (`ticket-qr.tsx`, ~23 mm, jeton =
`ticket.id`), catégorie, accès réservés avec libellés et dates
(`session_event_title` / `session_start` / `valid_date`), référence commande
courte (8 premiers hex de `reservation.id`, format `XXXX-XXXX`). Utilisé par
`/succes` et `/caisse`. Impression = `window.print()` ; les pages hôtes
masquent tout sauf les cartes via `print:hidden` (Tailwind), rendu couleur
fidèle forcé par `print-color-adjust` dans `globals.css`. Pagination native
du flux (pas de positionnement absolu) → les commandes nombreuses
(groupes) s'impriment sur plusieurs pages.

Conventions front : Server Components par défaut, `"use client"` minimal ;
formatage `Intl` en `fr-FR` / `Europe/Paris` ; types API recopiés localement.

---

## 9. Scripts & tests

```bash
# Base de données
docker compose up -d                          # PostgreSQL :5432

# Backend (depuis backend/, venv activé)
./venv/Scripts/python.exe scripts/seed_db.py      # seed idempotent
uvicorn app.main:app --reload                     # API :8000

# Backend — redémarrage propre (PowerShell) : tuer l'écouteur par PORT
# puis relancer détaché. Ne pas filtrer les processus par nom : le
# worker réel est un enfant spawn_main sans « uvicorn » en ligne de
# commande (voir §12 — gotcha).
$p = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
Stop-Process -Id $p -Force
Start-Process -FilePath ".\venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--reload" `
  -WorkingDirectory "C:\Users\pierr\dev\musee\backend" -WindowStyle Hidden
./venv/Scripts/python.exe scripts/test_booking.py # suite E2E
./venv/Scripts/python.exe scripts/test_concurrency.py  # preuve concurrence (M1)
./venv/Scripts/ruff.exe check .                   # lint backend (config : pyproject.toml)
alembic revision --autogenerate -m "..."          # nouvelle migration
alembic upgrade head
alembic check                                     # dérive modèles↔migrations

# Tests métier pytest — base dédiée musee_test (à créer une fois) :
psql postgresql://postgres:password@localhost:5432/postgres \
  -c "CREATE DATABASE musee_test"
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/musee_test \
  alembic upgrade head
pytest                       # 138 tests métier — isolation : transaction
                             # rollbackée par test, jamais de seed nécessaire
pytest --cov=app --cov-report=term-missing --cov-report=xml
                             # coverage (plancher 65 % — pyproject.toml)

# Frontend (depuis frontend/)
npm run dev    # :3000
npm run lint   # eslint
npm test       # vitest — logique panier caisse (cart.test.ts) +
               # reprise paiement web (pending-payment.test.ts : flux
               # GET+décision sous panne injectée) + reprise POS
               # (pos-pending.test.ts : idem au rechargement caisse) +
               # formatage des erreurs API (utils.test.ts :
               # apiErrorDetail — detail texte vs tableau Pydantic 422)
npm test -- --coverage   # + coverage v8 (plancher 80 %, vitest.config.ts)
npx tsc --noEmit
```

`test_booking.py` couvre : panier mixte + paiement CB, haute saison,
cohérence visit_date, scans (musée/séance/double-scan/autre jour),
surbooking séquentiel, multi-paiements POS (cash+ANCV, chèque réservé
groupes), **idempotence E2E** (rejeu même clé → `200` + snapshot
identique, même clé + montant/méthode divergents → `409`, **rejeu de
la clé d'un acompte après annulation → `200` + snapshot de l'opération
initiale, aucune duplication ni changement de statut** — le scénario
« réponse perdue puis commande annulée » du guichet, test 11b-bis,
et le test 12 re-vérifie `len(payments) == 1`), seuil groupe ≥ 8, `extra_show`
fusionné, purge des paniers
expirés, fenêtre de vente des séances (tolérance vendue+scannée, expirée
refusée), **annulation guichet** (restitution de jauge prouvée
fonctionnellement sur séance cap-1, encaissements conservés, rejets
400/404), **lecture GET** : `/products` (catalogue actif + composants),
`/seasonal/check` (BS/HS cohérents avec le seed) et
`/reservations/{id}` (graphe complet relu + 404) — smoke HTTP qui
garantit la sérialisation des `response_model` sans lazy-loading
(`MissingGreenlet`), **bornes d'entrée** (test 13 : champs hors borne
→ 422 HTTP — groupe de 121, 121 lignes, email > 254 car., tranche
> 50 000 €, montant débordant `Numeric` — 11 billets individuels
acceptés, agrégat > 120 personnes → 400, borne 120 exacte acceptée,
12 personnes + 12 séances supplémentaires acceptées sans double
comptage).
**Depuis 2026-10-08, chaque appel est une assertion bloquante**
(helper `expect`) : les rejets autrefois affichés sans vérification et les
gardes `if status == 201:` qui sautaient les assertions en cas d'échec ont
été éliminés — une régression métier fait échouer la suite (prouvé par
régression temporaire sur le refus ANCV/web). Jours de test **relatifs** :
`HIGH_DAY`=J+45 couvert par « HS test » ; `LOW_DAY` est **choisi au seed**
hors de toute `SeasonalPeriod` existante et hors de la fenêtre « HS test »
(reproductible toute l'année, y compris en juillet-août). Les séances
« du jour » du seed sont bornées au jour civil Paris (`session_today`) :
`now±Xh` pouvait basculer sur le jour voisin entre 23h et 0h20 locales,
rendant la séance invendable/non scannable.

`test_concurrency.py` (M1 + idempotence 2026-10-09) démontre sous
concurrence réelle (threads + barrière, requêtes simultanées) :
30 réservations concurrentes sur jauge 5 → exactement 5 × 201,
`booked_seats == 5` ; 8 scans concurrents du même accès → exactement
1 × 200 ; **8 paiements concurrents portant la même clé d'idempotence
sur une même commande → 1 × 201 + 7 × 200, un seul `payment.id`, une
seule transaction en base** ; **même clé sur deux réservations
distinctes → 201 + 409, un seul encaissement** (l'index unique tranche
la course que le FOR UPDATE ne sérialise pas) ; **même clé, contenus
différents en concurrence → 201 + 409, une seule empreinte honorée** ;
**2 tranches ANCV concurrentes proches du solde → la seconde plafonnée
au reste (excédent perdu, DFC n°7), total imputé exact** ; **4 tranches
cash concurrentes à clés distinctes → 4 enregistrements, solde exact** ;
**même clé sur 2 réservations à séance soldées → 201 + 409, un seul
encaissement, perdant sans état partiel puis soldable avec une clé
neuve** ; le corps du 201 (snapshot `response` figé en base) et la
relecture GET portent les mêmes `session_start`/`session_event_title`
renseignés. Ce scénario prouve le **contrat** de la course — la branche
qui produit le 409 (second lookup sous le verrou ou récupération après
`IntegrityError`) dépend du timing réel et n'est pas garantie à chaque
run ; les deux branches sont exercées de façon **déterministe** par
`test_payments.py` (commit concurrent injecté depuis une seconde
connexion). Seed idempotent, relançable à volonté.

**Suite métier pytest** (2026-10-10) : `backend/tests/` — 138 tests en
~21 s contre PostgreSQL réel (`musee_test`, créée à part, jamais de
données de dev). Isolation : chaque test tourne dans une transaction
externe rollbackée (`join_transaction_mode="create_savepoint"` — les
`commit()` internes des services libèrent un savepoint, le rollback final
tout annule). `conftest.py` fournit `db`, `catalog` (mini-catalogue
basse saison) et `high_season` ; `factories.py` les helpers de création.
`test_pricing.py` : modificateurs tarifaires et bornes de saison.
`test_reservation_rules.py` : matrice de validation de `create_reservation`
(20 rejets auparavant ni testés ni exercés : `free_profile` sur famille/
groupe, `disability` sans catégorie, PMR sans porteur, add-on sans droit
de base, séance expirée, capacité…). `test_reservation_flow.py` : flux
complet pending → payé → billets (fusion gloutonne par catégorie),
annulation (restitution de jauge, encaissements conservés, rejets).
`test_scan.py` : contrôle d'accès `scan_ticket` — 404, mauvais poste
(musée↔séance, mauvais événement), double scan avec message daté,
journée civile musée, fenêtre séance ±30 min **avec borne exacte**
(horloge figée à 14h00 Paris via `monkeypatch` sur `datetime` du module —
déterministe, zéro dépendance à l'heure réelle ; `start_time` déplacé
après émission réelle des billets), consommation indépendante des accès
d'un même QR, `control_warning` des 3 profils de gratuité. Branches
défensives documentées non testées : « sans séance associée »
(inatteignable sous FK), fallback « déjà utilisé » sans date, libellé
dîner-spectacle (aucun produit ne l'émet). `test_payments.py` :
garde-fous d'encaissement (404, commande soldée/expirée, chèque réservé
aux groupes dans les deux sens, CB/chèque au-delà du solde, ANCV
partiel au POS), intégrité « commande → paiement » — produit modifié
ou séance supprimée entre-temps → **409** — et **idempotence** (18 tests :
rejeu même clé sans doublon, snapshot historique restitué après un
paiement ultérieur ou une annulation, rejeu après expiration avec
encaissement toujours tracé (`payments` + `applied_amount` inchangés),
409 sur montant/méthode/
réservation divergents, multi-paiements à clés distinctes préservés,
clé non consommée par un échec 4xx, rejeu web sans double émission,
atomicité — un échec en cours de transaction ne laisse aucun état
partiel, snapshot relu depuis la colonne JSONB après `expire_all`,
aucune duplication de billets en base au rejeu, invariant « canal web
⇒ aucun encaissement partiel possible » qui fonde l'abandon local,
**snapshot figé cohérent avec la relecture GET** — `session_start`/
`session_event_title` renseignés, rejeu fidèle — et **branches de
récupération de course exercées** par injection d'un commit concurrent
depuis une seconde connexion : `IntegrityError` au flush (**régression
AUDIT-001 — échec prouvé sur l'ancien code**) et clé commitée pendant
le `FOR UPDATE` (couverture d'une branche déjà correcte — le test passe
aussi avant la correction) → 409 d'empreinte, perdant sans état
partiel) ; `test_reservation_rules.py`
couvre en plus catégorie manquante, incohérence `visit_date`/séance du
Pass et catalogue corrompu (produit sans droit, composant musée sans
événement, événement inactif, groupe sans tarif) ; `test_pricing.py`
couvre les tarifs famille et groupe, basse et haute saison.
`test_input_bounds.py` : bornes d'entrée — montant à la borne (50 000 €
accepté), juste au-dessus et valeur extrême rejetés en `ValidationError`
(422 en HTTP), `group_size`/`extra_children` au plafond et au-delà,
dépassement de la capacité `Integer` rejeté avant insertion, 121 lignes
refusées / 120 acceptées / 11 billets individuels créés, email valide
de 254 car. accepté et 255 rejeté **pour sa longueur** (syntaxe valide
dans les deux cas — la règle de longueur est isolée), plafond agrégé
de 120 personnes vérifié en service (400) et borne incluse (groupe de
120 créé), **comptage des personnes sans double comptage des add-ons**
(12 billets + 12 séances supp. = 12 personnes ; 60+60 lignes au double
plafond ; add-on **groupe** de 60 couvert par un groupe de 70 — cas où
le double comptage dépasserait réellement le plafond, régression
prouvée par suppression de la garde) et add-ons ne masquant pas un
dépassement des lignes de base.
`test_purge.py` : TTL 15 min sous horloge figée — `pending` expiré
libère la jauge, `pending` récent / `confirmed` / `cancelled` anciens
conservés, sélectivité sur séance partagée, restitution multi-items et
multi-séances, séance supprimée sans crash, **borne exacte** du cutoff
(`<` strict paramétré ±1 s).
Coverage `pytest-cov` — `app/` **82 %**, `app/services/` **~99,5 %**
(pricing 100 %, reservation_service 99 % — 2 lignes défensives
documentées : garde « billets déjà émis » (175) et `raise` quand
l'`IntegrityError` ne relue aucune opération gagnante (857) ; les
branches de récupération de course sont elles exercées — ticket_service
98 % ; les `api/*` à 0 % sont exercées par l'E2E dans un autre
processus) ; plancher `fail_under=65`. Coverage frontend via
`@vitest/coverage-v8` — baseline **88 %** (cart.ts 87 %, pending-payment.ts 95 %, plancher 80 %
dans `vitest.config.ts`). Rapports (`.coverage`, `coverage.xml`,
`htmlcov/`, `coverage/`) ignorés par git.

**CI (M2)** : `.github/workflows/ci.yml` — déclenchée sur push `main` et PR.
Job **backend** : service Postgres 15 (`postgres:15-alpine`),
`alembic upgrade head` + `seed_db.py` sur `musee_db`, création +
migration de `musee_test`, `alembic check` (dérive modèles↔migrations),
`pytest --cov` (plancher 65 %, rapport `coverage.xml` conservé en
artefact), uvicorn démarré en fond + attente `/health`, puis
`test_booking.py` et `test_concurrency.py` ; lint `ruff
check .` (config `backend/pyproject.toml` : règles E/F/W/I/B/UP/RUF/ASYNC/
DTZ/FURB, `Depends` FastAPI déclaré immutable-calls, E501 off, migrations
générées exclues, scripts de test dispensés de DTZ/ASYNC210/RUF001,
tests/ de DTZ — fixtures à jours relatifs).
Job **frontend** : `npm ci`, `eslint`, `vitest --coverage` (plancher
80 %), `next typegen` +
`tsc --noEmit`, puis **`next build`** (les pages qui fetch sont dynamiques
`no-store` — aucun appel API au build ; `NEXT_PUBLIC_API_URL` posée car
inlinée au build dans les composants clients). Robustesse : groupe
`concurrency` (run obsolète annulé par ref) et `timeout-minutes` 15/10.

**Protection de `main`** (2026-10-08) : force-push et suppression de la
branche bloqués, y compris pour les admins. Pas de PR obligatoire ni de
status check requis — workflow solo : les pushes directs restent possibles
et la CI rejoue les preuves à chaque push.

**Cycle de vie des fixtures** (2026-10-06) : `test_booking.py` et
`test_concurrency.py` activent leurs objets de test au seed (`is_active`)
et les **désactivent en fin de run** (`finally` → `cleanup()`) — le
catalogue vu par `/products` et `/events` (donc `/reserver`, `/caisse`,
`/scanner`) reste propre entre les runs. `test_concurrency.py` réaligne
aussi le composant `museum_day` de `concurrency_museum` sur son événement
dédié : `test_booking.py` re-pointe indifféremment tous les composants
musée vers « Musée des Pirates », chaque script restaure donc ses propres
invariants au seed.

**Catalogue seedé** (basse saison) : `museum_entry` 12/8/9 €,
`theater_show` 10/7/8 €, `extra_show` 5/3,50/4 € (**add-on** : séance
supplémentaire, exige un billet/pass à séance dans la commande),
`pass_1_show` 20/13/15 €, `pass_2_shows` 24/16/18 €, `family_museum`
35 € (+6 €/enfant sup.), `family_pass_1_show` 58 € (+10 €/enfant sup.). Haute saison seedée :
2026-07-01 → 2026-08-31. Théâtre du Kraken : **quatre productions** en
carte — « **À l'Abordage !** » à **10h30**, « **L'École des Pirates** » à
**11h45**, « **Les Conjurés** » à **15h00** et « **Le Repaire de
Barbe-Froide** » à **16h30** (2 le matin, 2 l'après-midi), jauge 80,
7 jours glissants. Chaque pièce est un événement
`theater` distinct avec sa `description` (le seed renomme via la clé
`former` au lieu de créer des doublons). L'événement salle historique
« Théâtre du Kraken » est désactivé (`is_active=false`) ; ses séances
existantes ont été rattachées à la pièce correspondant à leur horaire.

**Test physique** validé : scanner sur Android via `adb reverse tcp:3000` +
`tcp:8000`.

---

## 10. Socle documentaire

- **Cahier des charges — 13 points** : feuille de route **immuable** ; les
  backlogs/DFC s'y ajoutent sans le modifier. Points nommés : **n°10**
  (billets contrôlés indépendamment, distinction valide/utilisé/annulé/
  inexistant/autre séance/autre date), **n°12** (« une architecture
  commune » — verbatim : *« Principe central : NE PAS multiplier les
  logiques de réservation. Le site public (:3000), la caisse et
  éventuellement d'autres interfaces utilisent les mêmes règles via un
  MOTEUR MÉTIER unique. »*) et **n°13** (concurrence/surbooking).
- **Document directeur** : `OBJECTIFS.md` (version opérationnelle qui fait
  foi, transcrite du fichier Word
  `Musee_des_Pirates_Objectifs_Professionnalisation.docx`) ; le pilotage
  associé est `PILOTAGE.md`.
- **DFC** (Documents Fondateurs Complémentaires) :
  - n°1 — modèle opérationnel (flux continu / séquencé / ponctuel, synergies,
    Pass « Journée » / « Capitaine »).
  - n°2 — logique spatio-temporelle et restauration.
  - n°3 — dualité des espaces scéniques (→ nomenclature « le Complexe »).
  - n°4 — non documenté ici (à compléter).
  - n°5 — grille tarifaire officielle (→ Chantier A).
  - n°6 — gratuité et panier à 0 €.
  - n°7 — canaux de vente et moyens de paiement (→ Chantier B).
- **Note d'implémentation technique n°1** — traduction backend des DFC 2 et 3 :
  bimodalité du plan de salle (Chantier C), architecture *Strategy* :
  **Gradin** (théâtre de jour : rangées × sièges, contiguïté stricte, sièges
  tampons dynamiques, FOR UPDATE) vs **Banquet** (dîner-spectacle du soir :
  tables autour de l'action). `menu_choice` optionnel.

---

## 11. État d'avancement

### Livré

- ✅ Backlog initial épuisé (2026-10-04) : scan physique validé, `?date=`
  Europe/Paris, tarification serveur, modale de réservation, page `/succes`.
- ✅ **Chantier B** (2026-10-05) : `PaymentTransaction`, multi-paiements POS,
  émission différée des billets, écran `/caisse`, annulation `pending`,
  purge des paniers (`expired`, TTL 15 min, boucle 60 s).
- ✅ **Refonte « 1 billet par personne »** (2026-10-05) : `ticket_accesses`,
  fusion gloutonne, scanner contextuel, `extra_show` fusionné sur le même QR.
- ✅ Règle groupes ≥ 8 ; gratuités DFC n°6 ; haute saison DFC n°5.
- ✅ Correctifs post-revue externe : FOR UPDATE sur accès, `len(session_ids)`
  strict, filtre `is_scanned`, 409 différé, 400 `museum_day`.
- ✅ **Programmation théâtre** (2026-10-06, enrichie 2026-10-08) : 4
  productions distinctes au Théâtre du Kraken — « À l'Abordage ! »
  (10h30), « L'École des Pirates » (11h45), « Les Conjurés » (15h00) et
  « Le Repaire de Barbe-Froide » (16h30) — `events.description`, titre
  de la pièce affiché en billetterie (`/reserver`), modale, caisse,
  postes du scanner, verdict de scan (`access_label`) et accès des
  billets (`session_event_title`).
- ✅ `test_booking.py` résilient au ménage du catalogue : réactive son
  événement « Théâtre (test) » et re-pointe tous les composants musée
  vers « Musée des Pirates » (suite E2E à nouveau verte).
- ✅ **Fluidification caisse** (2026-10-06) : panier à compteurs par tarif
  (une ligne peut porter 2A + 1E), clic produit = +1 adulte si déjà
  présent, sinon reprise de la composition du panier ; contrôles capacité
  séance et PMR par ligne ; bannière « Convertir en Pass 1 Spectacle »
  avec économie calculée quand musée + séance coexistent à l'unité.
- ✅ **Fenêtre de vente des séances** (2026-10-06) : refus serveur (400)
  si `start_time + 30 min` est dépassé — la tolérance `LATE_TOLERANCE`
  vit désormais sur le modèle `Session` (source unique vente + contrôle +
  API). `Session.is_expired` est exposée par `GET /events` : le POS
  désactive les séances expirées dans le sélecteur (« terminée ») sans
  réimplémenter la règle. Cas testés : vendue et scannée dans la
  tolérance, refusée une fois expirée, flag vérifié sur 4 séances.
- ✅ **Fixtures de test auto-désactivées** (2026-10-06) : teardown
  `cleanup()` dans `test_booking.py` et `test_concurrency.py` — les
  produits/événements « (test) » et `concurrency_*` ne polluent plus
  `/reserver`, `/caisse` ni `/scanner` entre les runs ; M1 vérifié
  reproductible sur runs consécutifs.
- ✅ **Billets format carte** (2026-10-06) : `TicketCard` CR80 partagé
  `/succes` + `/caisse` — QR + catégorie + accès datés + référence courte ;
  impression navigateur avec masquage du reste de page (`print:hidden`).
- ✅ **Grille tarifaire cohérente** (2026-10-06) : `Product.is_addon`
  (migration `e6b3f1a84c2d`) + règle serveur de couverture des droits —
  `extra_show` ne peut plus être vendu sans billet/pass à séance dans la
  même commande (combos dominants musée+supp., famille+supp., add-on
  seul rejetés). `pass_2_shows` repricé 26/18/21 → **24/16/18 €** pour
  rester sous pass_1+extra. Le seed resynchronise désormais label, prix,
  kind et `is_addon` des produits existants (source de vérité de la
  grille). Front : add-ons masqués dans la grille `/reserver` (proposés
  uniquement en option des produits à séance), `addExtraShow` du POS ne
  miroite que les lignes à séance.
- ✅ **Optimisation automatique du panier caisse** (2026-10-07) : ajout
  Musée + Théâtre à composition **strictement identique** → conversion
  immédiate en Pass 1 Spectacle (lignes consommées supprimées — jamais de
  doublon « entrée + pass » facturé), toast d'économie + badge
  « Optimisé » sur la ligne ; clic direct sur le produit Pass **absorbe**
  les lignes simples correspondantes au lieu de se superposer ; cas
  partiels ou ambigus → suggestion explicite inchangée (jamais de
  transformation silencieuse). Correctif du bug de consommation : le
  budget `toMove` était partagé entre les deux familles de lignes — la
  famille théâtre n'était jamais décrémentée et restait au panier. La
  composition miroir ne recopie plus les droits déjà couverts (un pass au
  panier n'est pas recompté). Logique panier extraite dans `cart.ts`
  (fonctions pures) + 15 tests `cart.test.ts` (vitest, `npm test`).
- ✅ **Fiabilisation tests & CI** (2026-10-08) : `test_booking.py` —
  tous les rejets et toutes les créations sont des assertions bloquantes
  (helper `expect`), test 11 « annulation » (restitution de jauge,
  encaissements conservés, 400/404), `LOW_DAY` choisi dynamiquement hors
  périodes saisonnières. CI : `next build` ajouté, `concurrency` +
  `timeout-minutes`. `main` protégé contre force-push/suppression.
  Preuve de régression temporaire : suite rouge sur refus ANCV/web
  neutralisé, verte après revert (run CI 37746023295).
- ✅ **pytest + coverage** (2026-10-08) : `backend/tests/` — 49 tests
  métier contre `musee_test` (PostgreSQL réel, isolation par transaction
  rollbackée) : tarification, matrice de validation de
  `create_reservation` (20 règles jusque-là non testées), flux
  réservation/paiement/billets/annulation dont fusion gloutonne.
  Coverage : `pytest-cov` backend (baseline 70 % `app/`, ~73 %
  `app/services/`, plancher 65 %) et `@vitest/coverage-v8` frontend
  (baseline 87 %, plancher 80 %). CI : `alembic check`, étape pytest+cov,
  artefact `coverage-backend`, `vitest --coverage`.
- ✅ **Tests scan `ticket_service`** (2026-10-08) : `test_scan.py` —
  18 tests (mauvais poste, double scan daté, journée civile musée,
  fenêtre séance avec borne exacte des 30 min sous horloge figée,
  consommation indépendante des accès, `control_warning` des 3
  gratuités). `ticket_service.py` : 0 % → 98 % ; `app/` : 70 % → 77 %.
- ✅ **Tests encaissement `add_payment`** (2026-10-08) :
  `test_payments.py` (11) + extensions rules/pricing/flow (11) —
  garde-fous d'état (404, soldée, expirée), chèque groupe (les deux
  sens), dépassements de solde, ANCV partiel, 409 produit modifié /
  séance supprimée entre commande et paiement, annulation sans séance.
  `reservation_service.py` : 86 % → 93 % ; `services/` : ~88 % → ~94 % ;
  `app/` : 77 % → 79 %. Reste : la purge (mini-lot dédié) et la garde
  d'idempotence d'émission (défensive, documentée).
- ✅ **Tests purge** (2026-10-08) : `test_purge.py` — 11 tests sous
  horloge figée (`created_at` positionné relativement au cutoff) :
  expiration/libération du `pending`, conservation des récents et des
  commandes `confirmed`/`cancelled`, sélectivité multi-commandes sur
  une séance, restitution multi-items/multi-séances, séance supprimée,
  borne exacte `created_at == cutoff` (paramétrée ±1 s).
  `reservation_service.py` : 93 % → **99 %** ; `services/` : ~94 % →
  **~99,5 %** ; `app/` : 79 % → 81 %. Il ne reste dans `services/` que
  2 lignes défensives documentées (garde d'idempotence d'émission,
  « accès sans séance » inatteignable sous FK).
- ✅ **Smoke tests HTTP de lecture** (2026-10-08) : `test_booking.py`
  test 12 — les 3 derniers GET non exercés (`/products`,
  `/seasonal/check`, `/reservations/{id}`) sont désormais assertés en
  E2E : statut, données seedées attendues, sérialisation complète des
  `response_model` (détecterait un `MissingGreenlet`), 404 propre.
  Toute la couche HTTP est maintenant exercée par l'E2E — pas de suite
  ASGI jugée nécessaire à ce stade.
- ✅ **Protocole « fonctionnalité + filet de tests »** (2026-10-08) :
  règle permanente dans `AGENTS.md` — analyse d'impact avant code,
  niveau de test choisi selon le risque (pytest / E2E / concurrence /
  vitest), coverage en plancher jamais en objectif, aucune
  modification métier pour faire passer un test, clôture = tests +
  régression + CI verts.
- ✅ **CI minimale (M2)** (2026-10-07, issue GitHub #3) : workflow
  `.github/workflows/ci.yml` — backend (Postgres service,
  alembic + seed + uvicorn + suites E2E/concurrence, ruff) et frontend
  (eslint + vitest + `next typegen` + tsc). **Statut : démontré** — run
  vert sur `main`
  ([37576091825](https://github.com/pierremusee/museedespirates/actions/runs/37576091825))
  après correction du typecheck CI (types de routes Next générés par
  `next typegen`, absents sans build). Adoption de ruff au passage :
  enums migrés `str, enum.Enum` → `enum.StrEnum` (Python 3.11), idiome
  `Depends` déclaré, ~30 autofix (imports, `Union` → `|`, `Decimal`).
- ✅ **Idempotence des paiements** (2026-10-09) : `idempotency_key`
  UUID obligatoire sur `POST /reservations/{id}/payments`, unique en
  base (`uq_payment_transactions_idempotency_key`) ; snapshot
  `response` JSONB du `PaymentResult` enregistré dans la même
  transaction que l'encaissement et les billets. Rejeu même clé +
  même contenu → `200` + corps identique ; contenu divergent → `409` ;
  échec avant enregistrement → clé non consommée. Double lookup de la
  clé (avant et **sous** le `FOR UPDATE`) — sans le second, un
  concurrent de même clé tombait sur « déjà soldée » au lieu de rejouer
  (cas découvert et corrigé via `test_concurrency.py`). Filet
  `IntegrityError` → rollback → relecture → rejeu/409 pour la course
  inter-réservations. Front : verrou synchrone caisse + cycle de vie
  de clé par tranche persisté `sessionStorage` (`pos-pending.ts`),
  paramètres figés et nouvelle vente bloquée tant qu'une tranche est
  incertaine, restauration de la commande après rechargement ; reprise
  web `sessionStorage` sans recréer de panier (`pending-payment.ts`),
  formulaire gelé, échec de vérification ou statut non reconnu =
  blocage (jamais abandon). 118 tests pytest + rejeu E2E + concurrence
  démontrée (8 scénarios, dont course de clé sur commandes à séance
  soldées) ; vitest (64 tests) couvre les décisions de
  contrôle du flux **et** le flux « GET + décision » sous pannes
  injectées (réseau, 5xx, JSON mal formé, statut non reconnu) —
  composants React non testés (pas de harness), limites en §12.
- ✅ **Audit idempotence — corrections ciblées** (2026-10-09, PR #4) :
  collision de clé sur commande à séance soldée — l'`IntegrityError`
  pouvait surgir d'un autoflush pendant `_emit_tickets`, hors du
  périmètre de récupération (500 au lieu de 409) → INSERT contrôlé du
  paiement dès le flush explicite, avant l'émission ; snapshot
  `response` privé des champs de séance — `TicketAccess` créé avec
  `session_id` seul, relation `session` non peuplée → `session_start`/
  `session_event_title` figés à `null` dans le JSONB et propagés au
  rejeu → relation peuplée à l'émission (séance et événement déjà
  chargés, aucune requête en plus). Les deux branches de récupération
  (IntegrityError au flush, clé commitée pendant le `FOR UPDATE`) sont
  exercées par injection d'un commit concurrent — déterministe ; le
  scénario 8 de `test_concurrency.py` prouve E2E le **contrat** de la
  course (201 + 409, un seul encaissement, perdant intègre et
  soldable) sans garantir la branche empruntée à chaque run.
- ✅ **Audit — bornes d'entrée** (2026-10-09, AUDIT-008) : montants et
  quantités non bornés → dépassements `Numeric(10,2)`/`Integer`
  évitables (500) et création massive de billets possible sur les
  produits sans jauge (`customer_email` était déjà borné à 254 car.
  par `EmailStr` < colonne 320). Bornes métier décidées :
  **120 personnes** par commande (spec §17, jauge seedée ~80),
  **120 lignes** (les interfaces émettent 1 ligne/personne — la
  première version à 10 lignes bloquait 11 billets individuels),
  **50 000 €** par tranche. Champs hors borne → 422 ; agrégat de
  personnes > 120 → 400 en service. Revue indépendante (2026-10-10) :
  **comptage des add-ons corrigé** — une séance supplémentaire porte
  des accès pour des personnes déjà couvertes, elle ne double-compte
  plus (le modèle permet un add-on groupe/famille : test dédié rendant
  le double comptage observable, sinon inatteignable avec des add-ons
  1 personne/ligne sous la borne de 120 lignes) ; **erreurs 422** —
  `toast.error(body.detail)` recevait un tableau Pydantic → crash de
  rendu React (« Objects are not valid as a React child ») sur la
  caisse et le site, remplacé par `apiErrorDetail` (`lib/utils.ts`,
  6 tests vitest : texte, tableau aplatit `loc`+`msg`, repli) ;
  incohérence doc email corrigée (> 320 → la borne réelle est 254,
  tests valides à 254/255). `test_input_bounds.py` : 20 tests ;
  `test_booking.py` test 13 : preuve HTTP (dont 11 lignes acceptées).
  Limites restantes : borne par champ, pas de borne temporelle ni de
  rate-limiting (hors périmètre M5 sécurité).

### Backlog (feuille de route non figée)

- **Phase préliminaire** — conception de l'expérience visiteur (narratif
  avant code).
- **Chantier A** — consolidation métier (grille tarifaire/paiements/tarifs
  spéciaux — largement entamé via DFC n°5–7).
- **Chantier C** — plan de salle : matrice de sièges du Kraken, bimodalité
  Gradin/Banquet (Note n°1) — **non implémenté** (pas de modèle siège).
- **Chantier D** — tableau de bord admin : création de dates/séances via
  interface (seul l'endpoint de purge existe côté admin API).

### Boîte à idées (futures, PAS des règles)

Cross-selling billetterie ↔ Taverne ; événementiel bi-mensuel au théâtre ;
séances/visites en anglais ; vente B2B (scolaires, CE, TO) avec devis/acompte ;
POS 100 % modulaire (configuration-driven UI) ; page d'accueil « coupe-file »
live (spectacle imminent + places restantes, QR sur affiches physiques) ;
module SAV/remboursements ; gestion de stock restaurant ; confirmation
d'achat (billet PDF téléchargeable — partiellement couvert par
l'impression navigateur des `TicketCard`, lien unique de consultation,
Apple/Google Wallet).

---

## 12. Dette technique & résidus connus

- Doublons d'événements de test en base (`Musée (test)` ×5,
  `Théâtre (test)` ×5, `Concurrence *` ×2) — désactivés
  (`is_active=false`), **non supprimés** (choix non destructif). Depuis
  2026-10-06 les scripts de test désactivent leurs fixtures en fin de run
  (`cleanup()`) et les réactivent au seed : le catalogue reste propre sans
  intervention manuelle. Limite assumée : un `kill -9` en plein run laisse
  les fixtures actives jusqu'au run suivant.
- `SeasonalPeriod` « HS test » : purgée puis recréée à chaque run de
  `test_booking.py` (fenêtre recalée autour de `HIGH_DAY`) — une seule
  ligne subsiste, la vraie haute saison n'est pas touchée.
- Le scan ne vérifie pas `reservation.status` — à durcir si annulation/
  remboursement post-confirmation est ajouté.
- `booked_seats` = personnes ≠ `COUNT(tickets)` après fusion gloutonne.
- Limite `_emit_tickets` : deux achats distincts de même catégorie peuvent
  fusionner sur un même billet.
- `TicketScanRequest` : pas d'authentification sur les endpoints (dev only,
  CORS ouvert).
- `POST /reservations` n'est pas idempotent : un double-submit crée
  deux `pending` tenant la jauge — le retry navigateur est couvert
  côté paiement, pas côté création de panier (phase ultérieure si
  besoin réel).
- La purge peut expirer un `pending` **partiellement payé** : les
  encaissements restent tracés mais la commande meurt — remboursement
  manuel (connu DFC n°7, à traiter avec la machine à états M4).
- Pas de tests de **composants React** (pas de harness jsdom/testing-
  library) : la répression des états incertains est prouvée au niveau
  des décisions de flux extraites (`checkPreviousOutcome`,
  `resolveStoredPending`, fetch injecté) — le câblage JSX (fieldset
  disabled, bannière caisse, gardes `submitOrder`/`reset`) n'est pas
  exercé automatiquement.
- ESLint signale un artefact `coverage/block-navigation.js` généré
  par `vitest --coverage` local (warning, non bloquant, hors git).
- Gotcha uvicorn : sous Windows, `--reload` crée une chaîne reloader →
  worker → `spawn_main` ; tuer le parent laisse le petit-fils orphelin
  **qui continue de servir l'ancien code** sur :8000 (constaté : vente
  d'une séance expirée encore acceptée après le patch). Le filtre par
  nom ne le voit pas — identifier l'écouteur par le port
  (`Get-NetTCPConnection -LocalPort 8000`), kill par PID, relance
  détachée (voir §9). Symptôme type : l'API répond mais ignore le code
  frais → suspecter un vieux worker avant de déboguer.
- Gotcha Windows : une stratégie de contrôle d'application (WDAC) a bloqué
  `_greenlet.pyd` du venv → tout endpoint DB en 500. Résolu par
  `pip install --force-reinstall --no-cache-dir greenlet` (binaire frais,
  non issu du cache).
- Canal en ligne volontairement **partiel** : la Taverne (flux libre) ne
  passe pas par la réservation en ligne.
- Impression des billets = impression **navigateur** (papier A4) — pas
  d'intégration imprimante carte/thermique ; le format CR80 est prêt si un
  pilote dédié arrive.

---

## 13. Conventions de travail & maintenance de ce document

### Nomenclature

- « **le Complexe** » = l'ensemble Musée + Théâtre + Restaurant **uniquement**.
- Moteur métier **unique** (`backend/app/services/`) partagé site public et
  caisse — pas de logique dupliquée.

### Patrons imposés

- Opérations **non destructives / réversibles** (désactivation > suppression,
  seeds idempotents) — valable pour le catalogue/référentiel ; en dev, les
  réservations/billets/paiements sont **jetables** (purge/reset libres).
- Prix toujours calculés **côté serveur**.
- Toute jauge passe par `SELECT ... FOR UPDATE` (trié par id).
- Validation sur **matériel réel** privilégiée (scanner Android USB).
- Séparation stricte : idées (boîte à idées) ≠ règles ≠ backlog.
- Enums PostgreSQL stockés en `str` via `values_callable` — migrations
  Alembic à générer par `--autogenerate` depuis `backend/`.
- **Un script de test laisse le catalogue comme il l'a trouvé** : fixtures
  activées au seed, désactivées en fin de run (`finally`/`cleanup()`),
  jamais de suppression ; chaque script restaure ses propres invariants au
  seed (ex. composants repointés par un autre script).

### Règle de mise à jour

> **À chaque session de travail qui modifie le code, le schéma, les règles
> métier ou le backlog :**
> 1. Mettre à jour la/les sections concernées de ce fichier et la date
>    d'en-tête.
> 2. **Si un milestone ou la position de maturité a changé** : mettre à jour
>    `PILOTAGE.md` (et fermer l'issue GitHub associée via la PR).
> 3. Optionnel : enregistrer une courte conclusion dans la mémoire Honcho
>    (peer `user-default-dev`) préfixée `[Musée des Pirates — état projet
>    AAAA-MM-JJ]` — couche de contexte facultative, jamais source de vérité.
>
> Voir `AGENTS.md` à la racine — la règle y est rappelée pour tout agent.

### Ce que Honcho ne retient pas verbatim

Texte intégral des 13 points (seuls n°10, n°12 et n°13 ressortent), DFC n°4,
corps détaillé des DFC n°2–3 — **ce fichier fait foi** pour ces contenus une
fois complétés. Honcho accumule sans remplacer (doublons constatés) : ne
jamais s'y fier sans recouper avec le dépôt.
