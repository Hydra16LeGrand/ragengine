#!/usr/bin/env bash
# Démarre l'environnement de PRODUCTION (`.env.prod.local`) et amorce RAGFlow prod.
#
# Usage, depuis la racine du dépôt, par l'OPÉRATEUR :
#   bash scripts/start-prod.sh
#
# Idempotent : relancé, il ne recrée que ce dont la configuration a changé et ne
# réémet pas une RAGFLOW_API_KEY encore valide. Ne publie jamais de port hors
# 127.0.0.1 et ne démarre PAS le tunnel (profil `tunnel`, voir la fin).
# N'affiche aucun secret : la clé RAGFlow émise est écrite dans `.env.prod.local`.
#
# Jamais `down`, `-v` ni `--remove-orphans` : les fichiers compose partagent des
# noms de projet (docker/docker-compose.gpu.yml, en-tête).
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env.prod.local
[ -f "$ENV_FILE" ] || { echo "✗ $ENV_FILE absent : lancer d'abord scripts/init-prod-env.sh" >&2; exit 1; }

compose() { docker compose --env-file "$ENV_FILE" "$@"; }
value_of() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true; }

echo "→ Stack cœur (API, PostgreSQL, Redis, workers)"
compose -f docker/docker-compose.yml --profile workers up -d
echo "→ RAGFlow"
compose -f docker/docker-compose.ragflow.yml up -d
echo "→ Inférence GPU (TEI) et worker GPU"
compose -f docker/docker-compose.gpu.yml up -d
echo "→ Traefik et frontend"
compose -f docker/docker-compose.traefik.yml up -d

port="$(value_of RAGENGINE_RAGFLOW_HOST_PORT)"
base_url="http://127.0.0.1:${port:?RAGENGINE_RAGFLOW_HOST_PORT manquant dans $ENV_FILE}"
echo "→ Attente de RAGFlow sur $base_url (10 min max)"
for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$base_url/" || true)"
  [ "$code" != "000" ] && break
  sleep 10
done
[ "$code" != "000" ] || { echo "✗ RAGFlow ne répond pas" >&2; exit 1; }

echo "→ Amorçage de RAGFlow prod"
# Variables de la prod exportées pour primer sur le `.env` de dev que `Settings`
# lit en dur. RAGFLOW_API_KEY vide si absente : la clé de dev ne doit jamais servir.
while IFS= read -r line; do
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"
done < "$ENV_FILE"
export RAGFLOW_API_KEY="${RAGFLOW_API_KEY:-}"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if ! uv run --project backend python scripts/bootstrap_ragflow.py --base-url "$base_url" > "$log" 2>&1; then
  grep -v "RAGFLOW_API_KEY=" "$log" | tail -n 20 >&2
  exit 1
fi
grep -v "RAGFLOW_API_KEY=" "$log" | grep "→" || true

new_key="$(grep -oE 'RAGFLOW_API_KEY=[^[:space:]]+' "$log" | tail -n1 | cut -d= -f2- || true)"
if [ -n "$new_key" ]; then
  tmp="$(mktemp)"
  grep -vE '^RAGFLOW_API_KEY=' "$ENV_FILE" > "$tmp" || true
  printf 'RAGFLOW_API_KEY=%s\n' "$new_key" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  echo "→ RAGFLOW_API_KEY émise et écrite dans $ENV_FILE ; redémarrage de l'API et des workers"
  compose -f docker/docker-compose.yml --profile workers up -d --force-recreate api worker-default worker-beat
  compose -f docker/docker-compose.gpu.yml up -d --force-recreate worker-gpu
fi

echo "→ État"
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E "prod" || true
if [ -n "$(value_of CLOUDFLARE_TUNNEL_TOKEN)" ]; then
  echo "→ Jeton de tunnel présent. Pour ouvrir l'accès public :"
else
  echo "→ Jeton de tunnel absent (CLOUDFLARE_TUNNEL_TOKEN). Une fois renseigné :"
fi
echo "    docker compose --env-file $ENV_FILE -f docker/docker-compose.traefik.yml --profile tunnel up -d"
echo "OK"
