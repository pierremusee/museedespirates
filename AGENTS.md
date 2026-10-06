# AGENTS.md — Musée des Pirates

## Références projet

- `OBJECTIFS.md` — la cible de professionnalisation (source de vérité ;
  transcrite du document Word directeur, quasi immuable).
- `PILOTAGE.md` — position actuelle, milestone courant, critères de sortie
  et preuves. Court par construction.
- `DOCUMENTATION.md` — la **documentation vivante du projet** (architecture,
  modèle de données, règles métier, endpoints, backlog, dette).
  La lire avant toute intervention.

## Règle de documentation continue (obligatoire)

À **chaque** modification du code, du schéma de données, des règles métier
ou du backlog :

1. Mettre à jour les sections concernées de `DOCUMENTATION.md` et la date
   d'en-tête (« Dernière mise à jour »).
2. Si un milestone avance ou se clôt, ou si la position de maturité change :
   mettre à jour `PILOTAGE.md` et clôturer l'issue GitHub associée via la PR
   (1 issue par milestone, jamais par tâche).
3. Facultatif : enregistrer une courte conclusion dans la mémoire Honcho
   (via `ask-hermes`, peer `user-default-dev`) en préfixant par
   `[Musée des Pirates — état projet AAAA-MM-JJ]`. Honcho n'est jamais une
   source de vérité : le dépôt doit rester autosuffisant si Honcho est
   indisponible.

## Anti-illusion

Un objectif n'est jamais atteint parce qu'il est documenté. Les statuts de
`PILOTAGE.md` (prévu / implémenté / exercé / testé / démontré /
reproductible) doivent refléter des preuves identifiables (test, commande,
PR), pas des intentions.

## Mode analyse d'idée

Déclencheur : Pierre expose explicitement une nouvelle idée produit ou
fonctionnelle (formulation du type « j'ai une idée »).

Règles du mode :

- Aucun fichier modifié, aucun code écrit — laisser l'idée s'exposer
  librement, sans exiger de spécification technique.
- Consulter `DOCUMENTATION.md` et le code réel avant de conclure ; la
  mémoire Honcho n'est utilisée que si elle apporte un contexte pertinent.
- Analyser : valeur produit, valeur métier, valeur démonstrative portfolio,
  existant, dépendances, implications (architecture/données/API/frontend),
  risques, risque d'enfermement ou de complication d'évolutions futures
  **plausibles**, coût/complexité, pistes de simplification.
- Ne pas transformer l'idée en tâche, milestone ou backlog sans accord.
- Proposer le plus petit périmètre permettant de tester l'idée ; comparer
  brièvement les architectures possibles et recommander la plus simple qui
  préserve les évolutions plausibles.
- Ne pas créer d'abstraction « au cas où » : identifier uniquement les
  contraintes futures plausibles réellement affectées par l'idée.
- Verdict obligatoire parmi : « Faisable maintenant » / « À préparer » /
  « À garder » / « À éviter », avec justification.
- « À garder » aboutit, si Pierre le confirme, à une ligne dans la boîte à
  idées de `DOCUMENTATION.md` — jamais à un milestone automatique.
- L'implémentation ne démarre que sur accord explicite de Pierre.

## Conventions du projet

- Nomenclature : « **le Complexe** » désigne uniquement l'ensemble
  Musée + Théâtre + Restaurant.
- Moteur métier unique dans `backend/app/services/` — aucune règle dupliquée
  entre le site public et la caisse.
- Prix recalculés côté serveur, jamais depuis le client.
- Toute jauge : `SELECT ... FOR UPDATE` (sessions triées par id, anti-deadlock).
- Opérations non destructives : désactivation (`is_active=false`) plutôt que
  suppression ; seeds idempotents.
- **Phase développement : données jetables.** Les réservations, billets et
  paiements créés en dev sont sans valeur — purge, reset ou suppression
  libres et sans confirmation si ça simplifie le travail. La convention
  non destructive vise le catalogue/référentiel (events, products,
  sessions), pas les données d'usage.
- Le cahier des charges en 13 points est **immuable** : les DFC et backlogs
  s'y ajoutent sans le modifier.
- Backend : voir `frontend/AGENTS.md` pour les règles Next.js 16 (breaking
  changes — lire `node_modules/next/dist/docs/` avant d'écrire du code
  frontend).
