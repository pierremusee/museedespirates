# MISSION DE MODÉLISATION : MUSÉE DES PIRATES
**Destinataire :** Agent Devin (via Hermes)
**Outil de persistance :** Mémoire Honcho

## 1. OBJECTIF DE LA MISSION
Concevoir l'architecture de la base de données PostgreSQL en utilisant l'ORM SQLAlchemy 2.0 (mode asynchrone). 
Ce schéma doit impérativement supporter le verrouillage transactionnel pour éviter tout surbooking, gérer les réservations de groupe et préparer le terrain pour le contrôle d'accès par QR code.

## 2. SCHÉMA RELATIONNEL STRICT

### A. Modèle `Event` (Catalogue du musée)
- **id** : UUID (Primary Key, généré par défaut)
- **title** : String (Not Null)
- **event_type** : Enum (`permanent_exhibition`, `theater`, `guided_tour`)
- **base_price** : Decimal (Not Null, précision tarifaire)
- **is_active** : Boolean (Default True)

### B. Modèle `Session` (Moteur anti-surbooking)
- **id** : UUID (Primary Key, généré par défaut)
- **event_id** : UUID (Foreign Key -> `events.id`)
- **start_time** : DateTime (Not Null, timezone aware)
- **max_capacity** : Integer (Not Null)
- **booked_seats** : Integer (Default 0, utilisé pour bloquer la concurrence)

### C. Modèle `Reservation` (Panier client global)
- **id** : UUID (Primary Key, généré par défaut)
- **customer_email** : String (Not Null, format validé par Pydantic plus tard)
- **total_price** : Decimal (Not Null)
- **status** : Enum (`pending`, `confirmed`, `cancelled`)
- **created_at** : DateTime (Default Now, timezone aware)

### D. Modèle `Ticket` (Génération QR Code et Accès)
- **id** : UUID (Primary Key, sert de jeton unique pour le QR Code)
- **reservation_id** : UUID (Foreign Key -> `reservations.id`)
- **session_id** : UUID (Foreign Key -> `sessions.id`)
- **ticket_category** : Enum (`adult`, `child`, `group`, `school`)
- **is_scanned** : Boolean (Default False)
- **scanned_at** : DateTime (Nullable)

## 3. PLAN D'ACTION POUR DEVIN
1. **Création des modèles :** Dans le dossier `app/models/`, rédige les classes SQLAlchemy correspondant aux quatre tables décrites ci-dessus, en respectant la syntaxe SQLAlchemy 2.0 (utilisation de `Mapped` et `mapped_column`).
2. **Relations :** Configure les relations (relationships) entre `Event` et `Session`, et entre `Reservation`, `Session` et `Ticket`.
3. **Migration :** Génère la première migration avec Alembic via la commande `alembic revision --autogenerate -m "Initialisation des tables métiers"`.
4. **Mise à jour Honcho :** Enregistre ce schéma relationnel dans la mémoire de Honcho pour que le futur agent front-end connaisse la structure exacte des données.
5. **Rapport :** Affiche un message de succès confirmant la création des modèles et de la migration.