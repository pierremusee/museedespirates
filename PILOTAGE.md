# PILOTAGE — Musée des Pirates

> Document volontairement court et stable. Mis à jour **uniquement** quand un
> milestone avance, se clôt ou que la position de maturité change — jamais à
> chaque session de code.
>
> - Cible complète : `OBJECTIFS.md` (niveaux de maturité §20, scénarios §17)
> - État technique réel : `DOCUMENTATION.md`
> - Exécution : issues GitHub (1 par milestone), PR, CI

## Vocabulaire de preuve (anti-illusion)

Toute affirmation de maturité utilise l'un de ces statuts, dans l'ordre
croissant d'exigence :

| Statut | Signification |
|---|---|
| `prévu` | Intention documentée, rien d'implémenté |
| `implémenté` | Le code existe |
| `exercé` | Le chemin est appelé par un test, sans assertion dessus |
| `testé` | Un test avec assertion couvre le comportement |
| `démontré` | Prouvé dans les conditions visées (concurrence réelle, charge…) |
| `reproductible` | Une commande suffit à rejouer la preuve |

Règle : écrire une documentation n'est pas une preuve ; une CI verte ne prouve
que les tests présents ; une simulation n'est pas une expérience réelle.

## Position actuelle — évaluation du 2026-10-09

| Niveau (OBJECTIFS.md §20) | Statut |
|---|---|
| 1 — Fonctionnel | **Atteint** : parcours web complets, E2E verte, scan validé physiquement |
| 2 — Robuste | **Entamé** : règles métier testées ; concurrence **démontrée** (M1, 2026-10-06) ; preuves **rejouées en CI** (M2, 2026-10-07) ; paiements sans machine à états |
| 3 — Professionnel | Non atteint : sécurité absente, observabilité quasi nulle (CI minimale en place depuis M2) |
| 4 — Production simulée | Non atteint |
| 5 — Référence portfolio | Non atteint : pas de README racine, un tiers ne peut pas installer seul |

## Milestone courant

**M1 — démontrer la concurrence : terminé et validé (2026-10-06).**

Preuve : `backend/scripts/test_concurrency.py` — 30 requêtes concurrentes
sur jauge 5 → exactement 5 × 201, `booked_seats == 5`, zéro dépassement ;
8 scans concurrents du même accès → 1 seul × 200. Commande :
`./venv/Scripts/python.exe scripts/test_concurrency.py` (depuis `backend/`).
L'assertion manquante du test 5 (`test_booking.py`) a été ajoutée.
Statut du mécanisme anti-surbooking : **démontré + reproductible**.

**M4 — entamé (2026-10-09)** : volet « idempotence des paiements »
livré et démontré — `idempotency_key` obligatoire, unique en base,
snapshot `response` JSONB, rejeu 200 / conflit 409, concurrence
démontrée par `test_concurrency.py` (1×201 + 7×200 même clé ;
201 + 409 sur courses inter-réservations, y compris commandes à
séance soldées). Reste à faire dans M4 :
machine à états des paiements et échec/timeout simulables.

**M2 — CI minimale : terminée et validée (2026-10-07).**

Preuve : run GitHub Actions vert sur `main` —
[run 37576091825](https://github.com/pierremusee/museedespirates/actions/runs/37576091825)
(job backend 50 s : Postgres 15 + alembic + seed + uvicorn +
`test_booking.py` + `test_concurrency.py` + ruff ; job frontend 31 s :
eslint + vitest 15 tests + `next typegen` + `tsc --noEmit`). Premier run
37575952028 en échec (typecheck sans `.next/types`) corrigé par
`next typegen` — itération démontrée. Statut : **démontré +
reproductible** (chaque push/PR rejoue les preuves M1 et les tests).
Issue #3 clôturée. Prochain milestone : M3 (README + install tiers).

<details><summary>Critères de M1 (tous remplis)</summary>

- [x] N concurrent sur capacité K → K succès, zéro dépassement
- [x] Scans simultanés du même accès → 1 seul accepté
- [x] Assertion ajoutée au test séquentiel existant
- [x] Reproductible en une commande (3 runs consécutifs verts)

</details>

## Prochains milestones (ordre indicatif)

| # | Milestone | Écart traité |
|---|---|---|
| M2 | CI minimale (GitHub Actions : Postgres + suite de tests + lint front/back) | Reproductibilité des preuves — §11, §15 |
| M3 | README racine + fiabilisation de l'install pour un tiers | Niveau 5, §18 |
| M4 | Paiements : machine à états + idempotence + échec/timeout simulables | §6, §17 « Double paiement » |
| M5 | Sécurité minimale : auth par rôle sur endpoints admin/scanner, CORS restreint | §10, Niveau 3 |

Le backlog détaillé des chantiers (A–D) et la boîte à idées restent dans
`DOCUMENTATION.md` §11 — ils ne sont pas dupliqués ici.

## Écarts et risques principaux

1. ~~Anti-surbooking non prouvé~~ → démontré par M1 (2026-10-06).
2. ~~Aucune reproductibilité automatisée~~ → démontrée par M2
   (2026-10-07, run CI vert sur `main`).
3. Pas de README racine → un tiers ne peut pas installer le projet.
4. Sécurité absente (aucune auth, CORS ouvert) — assumé « dev only », mais
   bloque le Niveau 3.
