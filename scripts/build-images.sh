#!/usr/bin/env bash
# Construit ragengine/api et ragengine/frontend, tagués à la fois avec un
# identifiant reproductible (git rev-parse --short HEAD, +"-dirty" si l'arbre
# de travail n'est pas propre) et avec `dev` (tag flottant, confort local).
# L'image TEI officielle (ghcr.io/huggingface/text-embeddings-inference) est
# tirée directement par `docker compose`, jamais construite ici (ADR-051 —
# `inference.Dockerfile`, qui servait Xinference, est supprimé).
#
# Usage : ./scripts/build-images.sh
#   VITE_APP_NAME="Ma Marque" ./scripts/build-images.sh   # marque de la SPA (défaut : RAGEngine)
#
# Pour un déploiement reproductible/revenable, épingler le tag produit ici
# plutôt que d'utiliser `dev` : RAGENGINE_IMAGE_TAG=<tag> docker compose ...
# (ou le renseigner dans .env). Revenir en arrière = repointer sur un tag
# précédent, à condition que l'image correspondante existe encore localement
# ou dans un registre — ce script ne pousse vers aucun registre (pas de CI/CD
# à ce jour, dette séparée).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  SHA="${SHA}-dirty"
fi

echo "→ Tag reproductible : $SHA"

docker build -f "$ROOT/backend/Dockerfile" \
  -t "ragengine/api:$SHA" -t "ragengine/api:dev" \
  "$ROOT"

docker build -f "$ROOT/frontend/Dockerfile" \
  --build-arg "VITE_APP_NAME=${VITE_APP_NAME:-}" \
  -t "ragengine/frontend:$SHA" -t "ragengine/frontend:dev" \
  "$ROOT/frontend"

echo "→ Construites : ragengine/api:$SHA, ragengine/frontend:$SHA (+ tag dev)."
echo "  Pour déployer ce build : RAGENGINE_IMAGE_TAG=$SHA docker compose -f docker/docker-compose.yml ... up -d"
