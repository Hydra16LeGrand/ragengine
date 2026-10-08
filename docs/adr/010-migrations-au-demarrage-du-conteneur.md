# ADR-010 — Les migrations Alembic s'appliquent au démarrage du conteneur

**Date :** 2026-07-26 · **Statut :** accepté

## Contexte
En vérifiant la Phase 1 en conditions réelles (conteneur buildé, requêtes HTTP réelles plutôt que la seule suite pytest), l'API échouait sur toute route touchant la base : `relation "users" does not exist`. Cause : `backend/Dockerfile` construisait une image qui installait le paquet `app` via `pip install .`, mais n'appliquait jamais les migrations. Le problème est plus profond qu'un oubli de commande : Alembic charge ses scripts de migration (`env.py`, `versions/*.py`) par **chemin de fichier** (`script_location = %(here)s/app/db/migrations`), pas par import Python — rien ne garantissait que ces fichiers survivent sur disque à une installation par `pip install .`, qui ne traite que le paquet Python au sens strict.

## Décision
`backend/docker-entrypoint.sh` exécute `alembic upgrade head` avant `exec`-er la commande du conteneur (`uvicorn`). Le Dockerfile copie désormais explicitement `app/` et `alembic.ini` dans l'image d'exécution, à l'emplacement (`/app`) déjà pointé par `PYTHONPATH` — la même copie sert le rechargement à chaud en développement (bind mount, mécanisme préexistant) et la présence garantie des scripts de migration.

Vérifié en conditions réelles : conteneur neuf contre une base PostgreSQL vide → migration visible dans les logs → `POST /api/v1/users` réussit immédiatement (HTTP 201).

## Conséquences
- Tout déploiement de la stack cœur applique automatiquement le schéma courant : `docker compose up` suffit, `./scripts/bootstrap.sh` n'a rien à faire de plus.
- Un déploiement à plusieurs répliques de l'API appliquerait la migration en double au démarrage simultané de chaque réplique : Alembic est idempotent sur une base déjà à jour (`upgrade head` sur `head` ne fait rien), donc sans risque de corruption, mais ce n'est pas le design final. La Phase 9 (durcissement) devra séparer la migration du démarrage applicatif (job dédié, ou verrou consultatif PostgreSQL) avant tout déploiement multi-réplique.
- Toute nouvelle révision Alembic est donc appliquée au prochain redémarrage de l'API, sans étape manuelle — y compris en Phase 4+ quand le schéma évoluera.
