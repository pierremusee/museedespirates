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

## Position actuelle — évaluation du 2026-10-06

| Niveau (OBJECTIFS.md §20) | Statut |
|---|---|
| 1 — Fonctionnel | **Atteint** : parcours web complets, E2E verte, scan validé physiquement |
| 2 — Robuste | **Entamé** : règles métier testées ; concurrence implémentée mais non démontrée ; paiements sans machine à états |
| 3 — Professionnel | Non atteint : sécurité absente, pas de CI, observabilité quasi nulle |
| 4 — Production simulée | Non atteint |
| 5 — Référence portfolio | Non atteint : pas de README racine, un tiers ne peut pas installer seul |

## Milestone courant — M1 : démontrer la concurrence

**Problème traité** : le claim central du projet (anti-surbooking, Point 13 du
cahier des charges + §5 d'OBJECTIFS.md) est implémenté (`FOR UPDATE` trié,
contrainte `booked_seats <= max_capacity`) mais n'a **aucune preuve
concurrentielle** — le test 5 de `test_booking.py` est séquentiel et sa
seconde requête n'a pas d'assertion.

**Périmètre (volontairement borné)** : démontrer le comportement du mécanisme
existant, pas construire une infrastructure de tests de charge.

**Critères de sortie** :

- Un test lance N requêtes concurrentes sur une séance de capacité K et
  vérifie : exactement K succès, zéro dépassement, `booked_seats == K`.
- Un test lance 2 scans simultanés du même accès → exactement un accepté.
- Le test séquentiel existant (test 5) gagne une assertion sur le rejet.
- La suite est lançable par une commande unique, reproductible.

**Preuve attendue** : sortie de test consignée (dans la PR de clôture).

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

1. Anti-surbooking non prouvé (objet de M1).
2. Aucune reproductibilité automatisée (pas de CI ; la suite E2E exige une
   stack lancée à la main et écrit directement en base).
3. Pas de README racine → un tiers ne peut pas installer le projet.
4. Sécurité absente (aucune auth, CORS ouvert) — assumé « dev only », mais
   bloque le Niveau 3.
5. Doc-drift mineur : `DOCUMENTATION.md` annonce « 9 versions » de
   migrations ; il y en a 12.
