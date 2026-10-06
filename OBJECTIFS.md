# Musée des Pirates — Objectifs de professionnalisation et de performance

> **Source de vérité de la cible.** Ce fichier est la transcription
> opérationnelle du document directeur
> `Musee_des_Pirates_Objectifs_Professionnalisation.docx` (original humain,
> figé à sa version du 2026-10-06). Toute évolution de la cible se fait ici ;
> le DOCX n'est pas réédité à chaque changement.
> Dernière synchronisation : 2026-10-06.

## 1. Finalité du document

Ce document définit le niveau cible du projet Musée des Pirates. L'objectif
n'est pas de construire le plus grand nombre possible de fonctionnalités, mais
de reproduire avec un niveau de rigueur maximal les exigences d'un véritable
système métier de billetterie et de gestion d'un établissement culturel, tout
en restant dans un environnement fictif et sans exploitation commerciale
réelle.

Le projet doit pouvoir être présenté comme un système conçu selon une logique
professionnelle : architecture cohérente, règles métier explicites, sécurité,
résilience, tests, observabilité, documentation et procédures d'exploitation.

## 2. Principe directeur

Chaque décision de conception doit être évaluée comme si le système devait
être repris demain par une équipe professionnelle. Une fonctionnalité n'est
considérée comme terminée que lorsqu'elle est fonctionnelle, sécurisée,
testée, documentée et intégrée proprement au reste du système.

Le projet reste volontairement hors production réelle : aucun vrai paiement,
aucune exploitation commerciale et aucune dépendance à de vraies données
visiteurs ne sont nécessaires pour atteindre cette cible technique.

## 3. Architecture et qualité du code

- Architecture clairement séparée entre interface, API, logique métier,
  persistance et infrastructure.
- Responsabilités des services clairement définies ; absence de logique
  métier critique dispersée dans l'interface.
- Modèle de données cohérent, normalisé lorsque pertinent, avec contraintes
  SQL explicites.
- Transactions correctement délimitées et cohérence transactionnelle
  garantie.
- Migrations versionnées, reproductibles et documentées.
- Configuration externalisée ; aucun secret dans le dépôt.
- Gestion uniforme des erreurs et des réponses API.
- Validation systématique côté serveur ; le client ne constitue jamais une
  source d'autorité.
- Code typé, lisible, testable et soumis à linting/formatage.
- Dette technique identifiée, classée et traitée explicitement.
- Interfaces et contrats API documentés et stables.

## 4. Modèle métier et billetterie

- Calendrier d'ouverture, fermetures exceptionnelles, saisons et périodes
  tarifaires.
- Gestion des espaces, capacités, séances et créneaux.
- Tarification centralisée et recalculée côté serveur.
- Tarifs adultes, enfants, gratuités, PMR et accompagnateurs avec règles
  explicites.
- Billets individuels et billets comportant plusieurs droits d'accès.
- Pass, options, suppléments et produits combinés avec règles de
  compatibilité.
- Réservations temporaires avec expiration et libération automatique des
  jauges.
- Réservations web et caisse utilisant le même moteur métier.
- Groupes, scolaires, partenaires, invitations et autres catégories métier
  pertinentes.
- Annulations, reports, remboursements, avoirs et changements de séance.
- Traçabilité des changements importants.

## 5. Concurrence et anti-surbooking

C'est un domaine prioritaire. Le système doit être conçu pour rester cohérent
lorsque plusieurs utilisateurs ou canaux agissent simultanément.

- Verrouillage transactionnel approprié sur les ressources critiques.
- Contraintes de base de données empêchant les états impossibles.
- Gestion des deadlocks et ordre cohérent de verrouillage.
- Idempotence des opérations sensibles.
- Protection contre les doubles clics et les requêtes répétées.
- Tests de concurrence automatisés.
- Tests de saturation des dernières places disponibles.
- Démonstration mesurable de l'absence de surbooking dans les scénarios
  testés.

## 6. Paiements simulés mais réalistes

- Machine à états explicite pour les paiements.
- Succès, refus, expiration, interruption, timeout et réponse perdue.
- Idempotence des tentatives de paiement.
- Paiements multiples et ventilation par moyen de paiement.
- Remboursements totaux et partiels.
- Avoirs et corrections contrôlées.
- Journal des transactions et rapprochement avec les réservations.
- Séparation stricte entre simulation de paiement et logique de réservation.

## 7. Caisse et opérations

- Interface adaptée à un usage rapide par un agent.
- Gestion des espèces, rendu de monnaie et clôture de caisse.
- Multi-paiements.
- Ouverture et fermeture de caisse.
- Historique des opérations.
- Annulations et corrections soumises à autorisation.
- Rapport de caisse.
- Différenciation claire des droits entre caisse, administration et
  supervision.

## 8. Contrôle d'accès

- QR codes uniques et vérifiables côté serveur.
- Contrôle contextuel par jour, séance ou événement.
- Protection contre le double scan concurrent.
- Gestion des billets comportant plusieurs accès.
- Réponses scanner rapides et compréhensibles.
- Gestion des billets invalides, déjà utilisés, expirés ou annulés.
- Mode dégradé/offline étudié et documenté, même s'il reste simulé.
- Journalisation des scans et événements de contrôle.

## 9. Administration

- Création et modification des événements et séances.
- Gestion des capacités, horaires, tarifs et produits.
- Annulation ou déplacement d'une séance.
- Recherche et consultation des réservations.
- Gestion des remboursements et avoirs.
- Gestion des utilisateurs et rôles.
- Historique/audit des actions administratives.
- Exports et rapports opérationnels.
- Interface permettant au maximum d'éviter les modifications manuelles de la
  base.

## 10. Sécurité

- Authentification robuste.
- Autorisation par rôles et permissions.
- Principe du moindre privilège.
- Protection des endpoints administratifs et scanner.
- Validation et sanitation des entrées.
- Protection contre injections, XSS, abus d'API et autres vulnérabilités
  pertinentes.
- Rate limiting sur les opérations exposées.
- Secrets et clés hors dépôt.
- Logs ne contenant pas inutilement de données sensibles.
- CORS et politiques réseau configurés explicitement pour l'environnement.
- Audit de sécurité périodique.
- Threat model documenté pour les scénarios importants.

## 11. Tests et preuve de qualité

- Tests unitaires des règles métier critiques.
- Tests d'intégration API + base de données.
- Tests end-to-end des parcours essentiels.
- Tests de régression automatisés.
- Tests de concurrence et de charge.
- Tests de paiements et d'idempotence.
- Tests de scan simultané.
- Tests des expirations et purges.
- Tests des scénarios d'erreur.
- Pipeline CI exécutant automatiquement les contrôles.
- Rapports de tests reproductibles.

## 12. Performance

- Mesurer les performances au lieu de les supposer.
- Définir des objectifs de latence pour les opérations critiques.
- Optimiser les requêtes SQL et indexer selon les usages réels.
- Éviter les N+1 queries.
- Limiter les payloads inutiles.
- Mettre en cache uniquement lorsque cela apporte un bénéfice démontré.
- Tester le comportement sous charge.
- Identifier les goulots d'étranglement avant optimisation.
- Documenter les résultats des tests de performance.

## 13. Observabilité et exploitation

- Logs structurés et exploitables.
- Correlation/request IDs pour suivre une opération de bout en bout.
- Métriques techniques et métier.
- Suivi des erreurs.
- Health checks pertinents.
- Tableau de supervision.
- Alertes sur les anomalies importantes.
- Suivi des réservations en attente, paiements, jauges, scans et erreurs.
- Procédures documentées pour les incidents courants.

## 14. Sauvegarde, reprise et résilience

- Sauvegardes automatisées simulées ou réelles dans l'environnement de
  développement.
- Procédure de restauration documentée.
- Tests réguliers de restauration.
- Scénarios de perte de connexion à la base.
- Scénarios d'arrêt/redémarrage des services.
- Gestion des opérations interrompues.
- Définition de comportements sûrs en cas de panne.
- Documentation d'un plan de reprise après incident.

## 15. Déploiement et chaîne de livraison

- Environnements distincts développement, staging et démonstration/production
  simulée.
- Dockerisation reproductible.
- CI/CD.
- Migrations contrôlées.
- Variables de configuration par environnement.
- Builds reproductibles.
- Vérifications automatiques avant déploiement.
- Procédure de retour arrière documentée.
- Versionnement clair des releases.

## 16. Simulateur d'incidents

Le projet doit pouvoir démontrer sa robustesse sans dépendre d'un incident
réel.

- Paiement indisponible.
- Paiement débité mais réponse perdue.
- Base de données indisponible ou lente.
- Réseau de caisse interrompu.
- Scanner indisponible.
- Séance annulée.
- Séance déplacée.
- Pic massif de connexions.
- Expiration simultanée d'un grand nombre de réservations.
- Restauration depuis une sauvegarde.

## 17. Scénarios de démonstration de haut niveau

| Scénario | Résultat attendu |
|---|---|
| Dernières places | 500 demandes concurrentes pour une capacité de 120 ; aucune vente au-delà de la capacité. |
| Double paiement | Deux tentatives identiques ; une seule transaction logique. |
| Double scan | Deux scanners sur le même accès ; un seul contrôle accepté. |
| Annulation de séance | Blocage des ventes, identification des billets, calcul des solutions de remboursement/report et traçabilité. |
| Restauration | Simulation d'une perte puis restauration ; vérification de la cohérence métier. |
| Pic de trafic | Montée en charge progressive et mesure des latences, erreurs et saturation. |

## 18. Documentation professionnelle

- README permettant à un tiers d'installer et comprendre le projet.
- Documentation d'architecture.
- Documentation du modèle métier.
- Documentation des règles de tarification.
- Documentation des API.
- Documentation sécurité.
- Documentation concurrence et anti-surbooking.
- Documentation paiements.
- Documentation déploiement.
- Documentation sauvegarde/restauration.
- Documentation incidents.
- Décisions d'architecture importantes consignées.
- Dette technique connue et backlog maintenu.

## 19. RGPD et données

Même dans un projet fictif, les données doivent être traitées comme si elles
étaient sensibles.

- Minimisation des données.
- Séparation des données nécessaires des données accessoires.
- Durées de conservation définies.
- Suppression/anonymisation étudiée.
- Exports et droits simulables.
- Journalisation maîtrisée.
- Aucune donnée personnelle réelle nécessaire pour les démonstrations.

## 20. Critères de maturité finale

| Niveau | Critère |
|---|---|
| Niveau 1 — Fonctionnel | Les parcours principaux fonctionnent. |
| Niveau 2 — Robuste | Les règles métier, transactions et erreurs critiques sont maîtrisées. |
| Niveau 3 — Professionnel | Sécurité, tests, observabilité, déploiement et documentation sont structurés. |
| Niveau 4 — Production simulée | Charge, incidents, restauration, supervision et procédures sont démontrables. |
| Niveau 5 — Référence portfolio | Un tiers peut comprendre, déployer, tester et auditer le système sans dépendre du créateur. |

## 21. Règle de pilotage du projet

Le projet ne doit pas être piloté uniquement par une liste de
fonctionnalités. Chaque chantier doit contribuer à la cible globale. Les
feuilles de route doivent définir des objectifs intermédiaires mesurables,
des critères d'acceptation et des preuves de fonctionnement.

Avant de considérer une fonctionnalité comme terminée, vérifier :
fonctionnement, règles métier, concurrence, sécurité, erreurs, tests,
performance, observabilité, documentation et intégration.

## 22. Limites assumées

Le projet ne prétend pas être une solution commerciale certifiée ni une
expérience d'exploitation réelle. Il ne doit pas prétendre avoir subi des
contraintes qu'il n'a pas réellement rencontrées. La valeur du projet repose
sur la qualité de sa conception, de sa simulation, de ses tests et de sa
documentation.

## 23. Définition de la réussite

La cible finale est atteinte lorsque le Musée des Pirates peut être présenté
comme un système métier complet, cohérent et professionnellement conçu, dont
les choix techniques sont justifiés, les risques principaux testés, les
incidents simulables, les performances mesurables et l'exploitation
documentée — tout en restant un projet fictif et non commercial.
