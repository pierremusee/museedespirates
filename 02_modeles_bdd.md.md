# MISSION D'INITIALISATION : MUSÉE DES PIRATES
**Destinataire :** Agent Devin (via Hermes)
**Outil de persistance :** Mémoire Honcho

## 1. CONTEXTE GLOBAL (À mémoriser dans Honcho)
Le projet "Musée des Pirates" est une plateforme web complète gérant la billetterie et le contrôle d'accès d'un complexe culturel fictif. 
Le défi technique central est la gestion de la concurrence et du surbooking en temps réel lors de l'achat de billets (notamment pour des événements à jauge stricte comme le Théâtre du Kraken). L'architecture est API-first.

## 2. STACK TECHNIQUE IMPOSÉE
- **Langage :** Python 3.11+
- **Framework Web :** FastAPI
- **Base de données :** PostgreSQL (communication asynchrone)
- **ORM & Validation :** SQLAlchemy 2.0 (async) + Pydantic v2
- **Migrations :** Alembic

## 3. PLAN D'ACTION STRICT POUR DEVIN
Exécute les étapes suivantes de manière séquentielle. Ne passe pas à l'étape suivante tant que la précédente n'est pas validée.

### Étape A : Environnement et Dépendances
1. Crée un répertoire racine `backend`.
2. Initialise un environnement virtuel Python (`venv`).
3. Crée un fichier `requirements.txt` contenant impérativement : `fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `pydantic`, `pydantic-settings`, et `alembic`.
4. Installe les dépendances.

### Étape B : Architecture des dossiers
Dans le répertoire `backend`, génère la structure suivante :
- `app/`
  - `api/` (endpoints FastAPI)
  - `core/` (configuration, sécurité)
  - `db/` (connexion base de données et sessions)
  - `models/` (modèles SQLAlchemy)
  - `schemas/` (modèles Pydantic)
  - `services/` (logique métier)

### Étape C : Point d'entrée et Test
1. Crée le fichier `app/main.py`.
2. Configure une instance basique de FastAPI.
3. Ajoute un endpoint `GET /health` retournant `{"status": "ok", "project": "Musée des Pirates"}`.
4. Teste le lancement du serveur avec Uvicorn.

### Étape D : Initialisation des Migrations
1. Initialise Alembic (commande `alembic init alembic`) à la racine du backend.
2. Configure brièvement le fichier `alembic.ini` et `env.py` pour qu'ils soient prêts à recevoir les futurs modèles asynchrones de SQLAlchemy.

## 4. RAPPORT DE FIN DE MISSION
Une fois les 4 étapes terminées, mets à jour la mémoire Honcho avec l'état actuel de l'infrastructure et affiche un rapport de succès dans le terminal confirmant que l'environnement est prêt pour la création des tables de la base de données.