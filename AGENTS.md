# AGENTS.md — Musée des Pirates

## Référence projet

`DOCUMENTATION.md` à la racine est la **documentation vivante du projet**
(architecture, modèle de données, règles métier, endpoints, backlog, dette).
La lire avant toute intervention.

## Règle de documentation continue (obligatoire)

À **chaque** modification du code, du schéma de données, des règles métier
ou du backlog :

1. Mettre à jour les sections concernées de `DOCUMENTATION.md` et la date
   d'en-tête (« Dernière mise à jour »).
2. Enregistrer la conclusion dans la mémoire Honcho (via `ask-hermes`, peer
   `user-default-dev`) en préfixant par
   `[Musée des Pirates — état projet AAAA-MM-JJ]`.

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
