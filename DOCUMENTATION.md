# Musée des Pirates — Documentation du projet

> **Dernière mise à jour : 2026-10-06**
> Ce document est la référence vivante du projet. Il doit être mis à jour à
> chaque évolution (voir §13 — Maintenance). La mémoire Honcho (peer
> `user-default-dev`) est le complément persistant de ce document.

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

**Rôles** : `user-default-dev` = Devin (agent de codage) ; Pierre = validateur
humain final.

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

**Dépôt GitHub** : `github.com/pierremusee/museedespirates` (branche `main`,
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
├── docker-compose.yml          # PostgreSQL 15 + healthcheck
├── .env                        # DATABASE_URL
├── DOCUMENTATION.md            # ← ce fichier
├── 01_initialisation_backend.md.md   # mission fondatrice backend
├── 02_modeles_bdd.md.md              # mission fondatrice modèles
├── gemini-code-1791050970318.md      # mission fondatrice frontend
├── backend/
│   ├── requirements.txt
│   ├── alembic.ini, alembic/         # migrations (9 versions)
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
│   └── scripts/
│       ├── seed_db.py                # catalogue + événements (idempotent)
│       └── test_booking.py           # suite E2E via API HTTP
└── frontend/
    ├── app/
    │   ├── page.tsx                  # accueil
    │   ├── layout.tsx                # Geist + Toaster (sonner)
    │   ├── (client)/reserver/        # page + date-picker + dialog
    │   ├── (client)/succes/          # confirmation + QR codes
    │   └── (admin)/scanner/          # contrôle d'accès QR
    │   └── (admin)/caisse/           # terminal POS (pos-terminal.tsx)
    ├── components/ui/                # shadcn/ui
    └── lib/utils.ts
```

---

## 4. Modèle de données

### Tables et rôle

| Table | Modèle | Rôle |
|---|---|---|
| `events` | `Event` | Entité du Complexe (musée, théâtre…). `event_type` : `permanent_exhibition` / `theater` / `guided_tour`. `is_active` = retrait non destructif du catalogue. |
| `sessions` | `Session` | Séance d'un événement. `max_capacity`, `booked_seats` (compteur anti-surbooking, contrainte `booked_seats <= max_capacity`). `remaining_capacity` exposé en API. |
| `products` | `Product` | Produit vendable. `kind` : `simple` / `pass` / `family` / `group`. Prix de base **basse saison** (`price_adult`, `price_child`, `price_reduced`, `family_base_price`, `extra_child_price`). |
| `product_components` | `ProductComponent` | Contenu d'un produit **par personne**. `component_type` : `museum_day` / `theater_session` / `dining_session` (+ `quantity`, `event_id` pour les accès musée). |
| `seasonal_periods` | `SeasonalPeriod` | Plages « haute saison » déclenchant le modificateur tarifaire (DFC n°5 §4). |
| `reservations` | `Reservation` | Commande/panier. `status` : `pending` / `confirmed` / `cancelled` / `expired`. `sales_channel` : `web` / `pos`. Propriétés `paid_amount`, `amount_due`. |
| `reservation_items` | `ReservationItem` | Ligne de commande. Fige `computed_price`, `season_modifier`, `visit_date`, `session_ids` (JSONB), `group_size`, `free_profile`, `category`, `extra_children`. |
| `payment_transactions` | `PaymentTransaction` | Tranche d'encaissement (DFC n°7). `method` : `cb` / `cash` / `ancv` / `check`. `amount` = nominal remis, `applied_amount` = part imputée (diffèrent pour ANCV excédentaire). `status` : `completed` (+ `refunded`/`cancelled` envisagés). |
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
`b7e2a1f09c34` refonte billets.

---

## 5. Règles métier

### Anti-surbooking (Point 13)

- `booked_seats` est incrémenté **à la création** de la commande (avant
  paiement), sous `SELECT ... FOR UPDATE` — les sessions sont verrouillées
  **triées par id** (anti-deadlock).
- Contrainte SQL `booked_seats <= max_capacity` en filet de sécurité.
- Une commande `pending` tient la jauge ; la purge la libère.

### Cycle de vie d'une réservation

```
pending ──(solde = 0 via payments, ou total = 0 €)──> confirmed  → billets émis
   ├──(annulation guichet, pending seulement)──> cancelled  → sièges restitués,
   │                                                          paiements conservés
   └──(TTL 15 min sans solde, purge 60 s)──> expired       → sièges restitués
```

- **Émission différée** (DFC n°7) : les billets ne sont émis que lorsque
  `amount_due` atteint 0 → `confirmed`. Séance supprimée entre-temps → `409`.
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

### Groupes

- Seuil officiel : **minimum 8 personnes** (`MIN_GROUP_SIZE = 8`),
  `group_size` obligatoire sur produit `kind = group`, interdit ailleurs.
- Tarif : `price_adult` du produit = prix par personne.

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
| POST | `/reservations/{id}/payments` | Tranche d'encaissement `{method, amount}` → `201 PaymentResult` (`change_due`, `amount_due`, `reservation_status`, `tickets` si soldé). |
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
| | `reservation-dialog.tsx` | Client : compteurs par tarif + profils gratuits, choix de séance(s), séance supplémentaire `extra_show` fusionnée, estimation live (re-calculée serveur), POST reservation + paiement CB simulé, redirect `/succes`. |
| `/succes` | `(client)/succes/page.tsx` | Confirmation : lignes, total, référence, **1 QR par billet** (`ticket-qr.tsx` → qrcode.react) + liste des accès. |
| `/scanner` | `(admin)/scanner/page.tsx` | Client : sélecteur de poste (entrée musée / séances du jour), `@yudiel/react-qr-scanner`, verdict vert/rouge, warning justificatif, droits restants. |
| `/caisse` | `(admin)/caisse/page.tsx` + `pos-terminal.tsx` (~1080 lignes) | Terminal POS guichet : panier multi-lignes, tarifs/profils, séance supplémentaire (`addExtraShow`/`personsOf`/`renderLine`), multi-paiements avec rendu, annulation « Modifier la commande », impression QR. |

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
./venv/Scripts/python.exe scripts/test_booking.py # suite E2E
alembic revision --autogenerate -m "..."          # nouvelle migration
alembic upgrade head

# Frontend (depuis frontend/)
npm run dev    # :3000
npm run lint   # eslint
npx tsc --noEmit
```

`test_booking.py` couvre : panier mixte + paiement CB, haute saison,
cohérence visit_date, scans (musée/séance/double-scan/autre jour),
surbooking, multi-paiements POS (cash+ANCV, chèque réservé groupes),
seuil groupe ≥ 8, `extra_show` fusionné, purge des paniers expirés.

**Catalogue seedé** (basse saison) : `museum_entry` 12/8/9 €,
`theater_show` 10/7/8 €, `extra_show` 5/3,50/4 €, `pass_1_show` 20/13/15 €,
`pass_2_shows` 26/18/21 €, `family_museum` 35 € (+6 €/enfant sup.),
`family_pass_1_show` 58 € (+10 €/enfant sup.). Haute saison seedée :
2026-07-01 → 2026-08-31. Théâtre : séances 10h30 et 15h00, jauge 80,
7 jours glissants.

**Test physique** validé : scanner sur Android via `adb reverse tcp:3000` +
`tcp:8000`.

---

## 10. Socle documentaire

- **Cahier des charges — 13 points** : feuille de route **immuable** ; les
  backlogs/DFC s'y ajoutent sans le modifier. Points nommés : **n°10**
  (billets contrôlés indépendamment, distinction valide/utilisé/annulé/
  inexistant/autre séance/autre date) et **n°13** (concurrence/surbooking).
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
live (spectacle imminent + places restantes, QR sur affiches physiques).

---

## 12. Dette technique & résidus connus

- Doublons d'événements de test en base (`Musée (test)`, `Théâtre (test)` ×5) —
  désactivés (`is_active=false`), **non supprimés** (choix non destructif).
- Le scan ne vérifie pas `reservation.status` — à durcir si annulation/
  remboursement post-confirmation est ajouté.
- `booked_seats` = personnes ≠ `COUNT(tickets)` après fusion gloutonne.
- Limite `_emit_tickets` : deux achats distincts de même catégorie peuvent
  fusionner sur un même billet.
- eslint : 4 erreurs pré-existantes dans `app/page.tsx` (apostrophes) — hors
  périmètre.
- `TicketScanRequest` : pas d'authentification sur les endpoints (dev only,
  CORS ouvert).
- Canal en ligne volontairement **partiel** : la Taverne (flux libre) ne
  passe pas par la réservation en ligne.

---

## 13. Conventions de travail & maintenance de ce document

### Nomenclature

- « **le Complexe** » = l'ensemble Musée + Théâtre + Restaurant **uniquement**.
- Moteur métier **unique** (`backend/app/services/`) partagé site public et
  caisse — pas de logique dupliquée.

### Patrons imposés

- Opérations **non destructives / réversibles** (désactivation > suppression,
  seeds idempotents).
- Prix toujours calculés **côté serveur**.
- Toute jauge passe par `SELECT ... FOR UPDATE` (trié par id).
- Validation sur **matériel réel** privilégiée (scanner Android USB).
- Séparation stricte : idées (boîte à idées) ≠ règles ≠ backlog.
- Enums PostgreSQL stockés en `str` via `values_callable` — migrations
  Alembic à générer par `--autogenerate` depuis `backend/`.

### Règle de mise à jour (ce document + Honcho)

> **À chaque session de travail qui modifie le code, le schéma, les règles
> métier ou le backlog :**
> 1. Mettre à jour la/les sections concernées de ce fichier et la date
>    d'en-tête.
> 2. Enregistrer la conclusion dans la mémoire Honcho (peer
>    `user-default-dev`) préfixée `[Musée des Pirates — état projet
>    AAAA-MM-JJ]` pour le regroupement temporel.
>
> Voir `AGENTS.md` à la racine — la règle y est rappelée pour tout agent.

### Ce que Honcho ne retient pas verbatim

Texte intégral des 13 points (seuls n°10 et n°13 ressortent), DFC n°4,
corps détaillé des DFC n°2–3 — **ce fichier fait foi** pour ces contenus une
fois complétés.
