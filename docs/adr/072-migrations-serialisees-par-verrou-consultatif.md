# ADR-072 — Migrations de base sérialisées par un verrou consultatif PostgreSQL

**Date :** 2026-10-05 · **Statut :** accepté · **Prolonge :** ADR-010 (Alembic au démarrage)

## Contexte

`backend/docker-entrypoint.sh` lance `alembic upgrade head` avant le process applicatif, et
**tous** les conteneurs de l'image `ragengine/api` passent par lui : `api`, `worker-default`,
`worker-beat` (`docker/docker-compose.yml`) et `worker-gpu` (`docker/docker-compose.gpu.yml`).
Sur une base vide, ou lors d'un déploiement qui apporte une migration, quatre `upgrade`
s'exécutent en même temps. Reproduit sur le PostgreSQL de dev avec quatre processus lâchés
ensemble : trois échouent (`UniqueViolationError … pg_type_typname_nsp_index`, type créé deux
fois), et leurs conteneurs, en `restart: unless-stopped`, repartent en boucle jusqu'à ce que le
premier ait fini. Rien ne corrompait la base (le DDL est transactionnel), mais un déploiement
pouvait laisser des conteneurs en crash loop et `deploy-prod.sh` conclure à tort à un échec.

## Décision

Un **verrou consultatif PostgreSQL** (`pg_advisory_xact_lock`, clé fixe `MIGRATION_LOCK_ID`)
est pris dans `app/db/migrations/env.py`, **dans** la transaction des migrations et avant la
lecture de la version courante. Le premier processus migre ; les suivants attendent, relisent la
version, la trouvent à `head` et ne font rien. Le verrou est lié à la transaction : relâché au
commit ou au rollback, jamais retenu par un processus tué.

**Alternative écartée : un service de migration à usage unique** (`depends_on: condition:
service_completed_successfully`). Plus net sur le papier (« un seul responsable »), mais
`worker-gpu` vit dans un autre fichier Compose, qui ne peut pas référencer un service du premier
dans `depends_on` (même contrainte que celle déjà documentée dans son en-tête). Il faudrait
dupliquer le service ou imposer un ordre de lancement entre fichiers, et changer la topologie de
production (`deploy-prod.sh`, `start-prod.sh`) pour un défaut que le verrou règle sans y toucher.
Le verrou protège aussi tout lancement isolé d'un seul service.

## Conséquences

- Aucun changement de topologie ni de fichier Compose : `docker compose config` est identique
  avant et après, et `deploy-prod.sh` / `start-prod.sh` fonctionnent tels quels. Prochain
  déploiement : `bash scripts/deploy-prod.sh` suffit (l'image est reconstruite).
- Un processus attend tant que la migration en cours n'est pas terminée ; une migration
  anormalement longue retient donc les démarrages des autres conteneurs (leurs `start_period`
  sont de 15 à 30 s : une migration lourde appellera un `depends_on` sur un service de migration).
- `backend/tests/db/test_concurrent_migrations.py` lance quatre `alembic upgrade head` en
  processus séparés sur une base vierge (trois manches), puis sur une base en retard d'une
  révision : rouge sans le verrou (3 échecs sur 4), vert avec.
