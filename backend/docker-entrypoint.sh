#!/bin/sh
# Applique les migrations avant de démarrer le process applicatif.
#
# Tous les conteneurs de l'image (api, worker-default, worker-beat, worker-gpu) passent
# par ici et lancent donc chacun l'upgrade. Ils sont sérialisés par un verrou consultatif
# PostgreSQL pris dans la transaction des migrations (`app/db/migrations/env.py`, ADR-072) :
# le premier migre, les suivants attendent puis constatent que la base est à `head`.
# Un déploiement multi-réplique n'a donc rien d'autre à orchestrer.
set -eu

alembic upgrade head
exec "$@"
