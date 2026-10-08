#!/usr/bin/env bash
# Amorçage de la stack cœur RAGEngine (api, worker, redis, postgres).
# Usage : ./scripts/bootstrap.sh [up|down|logs|ps]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/docker/docker-compose.yml")
ACTION="${1:-up}"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "→ .env absent : création depuis .env.example"
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "  ⚠ Renseigne RAGENGINE_SECRET_KEY, POSTGRES_PASSWORD et tes clés LLM dans .env"
fi

case "$ACTION" in
  up)
    "${COMPOSE[@]}" up -d --build
    echo "→ Stack démarrée. Santé de l'API : http://localhost:${RAGENGINE_API_PORT:-8000}/api/v1/health"
    ;;
  down) "${COMPOSE[@]}" down ;;
  logs) "${COMPOSE[@]}" logs -f ;;
  ps)   "${COMPOSE[@]}" ps ;;
  *)    echo "Action inconnue : $ACTION (attendu : up|down|logs|ps)" >&2; exit 1 ;;
esac
