#!/usr/bin/env bash
# Déploie la PRODUCTION en une commande : construction des images, épinglage du
# tag, recréation des services, puis vérifications réelles.
#
# Usage, depuis la racine du dépôt :
#   bash scripts/deploy-prod.sh              # construit puis déploie
#   bash scripts/deploy-prod.sh --skip-build # redéploie l'image déjà épinglée
#
# Refuse de partir si des fichiers SUIVIS sont modifiés : on ne déploie que du
# code commité (les fichiers non suivis du dépôt sont ignorés, ils ne partent
# pas dans les images).
#
# Ne touche jamais à PostgreSQL, Redis, RAGFlow ni aux conteneurs TEI : ils ne
# sont pas recréés, seuls l'API, les workers, le frontend, Traefik et le tunnel
# le sont. Jamais `down`, jamais `-v`, jamais `--remove-orphans` (cette option a
# déjà supprimé tout le cœur une fois : les fichiers compose partagent le nom de
# projet, cf. en-tête de docker/docker-compose.gpu.yml).
#
# N'affiche aucun secret : le fichier d'environnement n'est lu que pour en
# extraire des valeurs non sensibles (port hôte, présence du jeton de tunnel).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE="${ENV_FILE:-.env.prod.local}"
PUBLIC_HOST="${PUBLIC_HOST:-rag.example.com}"
SKIP_BUILD=0
[ "${1:-}" = "--skip-build" ] && SKIP_BUILD=1

[ -f "$ENV_FILE" ] || { echo "✗ $ENV_FILE absent : lancer d'abord scripts/init-prod-env.sh" >&2; exit 1; }

dirty="$(git status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
  echo "✗ Des fichiers suivis sont modifiés. Commite-les d'abord :" >&2
  echo "$dirty" >&2
  exit 1
fi

compose() { docker compose --env-file "$ENV_FILE" "$@"; }
value_of() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true; }

# ── 1. Images ────────────────────────────────────────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "→ Construction des images"
  tag="$(./scripts/build-images.sh | sed -n 's/^→ Tag reproductible : //p' | tail -n1)"
  [ -n "$tag" ] || { echo "✗ tag de build introuvable" >&2; exit 1; }
  tmp="$(mktemp)"
  grep -vE '^RAGENGINE_IMAGE_TAG=' "$ENV_FILE" > "$tmp" || true
  printf 'RAGENGINE_IMAGE_TAG=%s\n' "$tag" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  echo "→ Tag épinglé dans $ENV_FILE : $tag"
else
  tag="$(value_of RAGENGINE_IMAGE_TAG)"
  echo "→ Construction ignorée ; tag déjà épinglé : ${tag:-<absent>}"
fi

# ── 2. Recréation des services ───────────────────────────────────────────────
echo "→ API et workers"
compose -f docker/docker-compose.yml --profile workers up -d
echo "→ Worker GPU"
compose -f docker/docker-compose.gpu.yml up -d worker-gpu
echo "→ Traefik et frontend"
compose -f docker/docker-compose.traefik.yml up -d
if [ -n "$(value_of CLOUDFLARE_TUNNEL_TOKEN)" ]; then
  echo "→ Tunnel Cloudflare"
  compose -f docker/docker-compose.traefik.yml --profile tunnel up -d
else
  echo "→ Tunnel ignoré : CLOUDFLARE_TUNNEL_TOKEN absent de $ENV_FILE"
fi

# ── 3. Santé ─────────────────────────────────────────────────────────────────
project="$(value_of RAGENGINE_PROJECT_NAME)"
project="${project:-ragengine}"
echo "→ Attente de l'état healthy (5 min max)"
for _ in $(seq 1 60); do
  unhealthy="$(docker ps --filter "name=^/${project}-" \
    --format '{{.Names}}\t{{.Status}}' | grep -E "starting|unhealthy" || true)"
  [ -z "$unhealthy" ] && break
  sleep 5
done
if [ -n "$unhealthy" ]; then
  echo "✗ Conteneurs encore instables :" >&2
  echo "$unhealthy" >&2
  echo "  Journaux : docker logs <nom> --tail 50" >&2
  exit 1
fi

# ── 4. Vérifications réelles ─────────────────────────────────────────────────
port="$(value_of RAGENGINE_TRAEFIK_HOST_PORT)"
port="${port:?RAGENGINE_TRAEFIK_HOST_PORT manquant dans $ENV_FILE}"
base="http://127.0.0.1:${port}"

code_front="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $PUBLIC_HOST" "$base/")"
code_api="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $PUBLIC_HOST" "$base/api/v1/health")"
headers="$(curl -sI -H "Host: $PUBLIC_HOST" "$base/" | tr 'A-Z' 'a-z')"
exposed="$(docker ps --format '{{.Names}}\t{{.Ports}}' | grep '0.0.0.0:' || true)"

echo
echo "Frontend      : $code_front"
echo "API /health   : $code_api"
for h in x-frame-options content-security-policy strict-transport-security; do
  case "$headers" in *"$h"*) echo "En-tête $h : présent";; *) echo "En-tête $h : ABSENT";; esac
done
if [ -n "$exposed" ]; then
  echo "✗ Ports publiés hors boucle locale :" >&2
  echo "$exposed" >&2
  exit 1
fi
echo "Ports publiés : tous en 127.0.0.1"

[ "$code_front" = "200" ] && [ "$code_api" = "200" ] || {
  echo "✗ Le déploiement répond mal : voir les codes ci-dessus." >&2
  exit 1
}

docker ps --format '{{.Names}}\t{{.Status}}' | grep -E "^${project}-" || true
echo
echo "OK — déployé en $tag sur https://$PUBLIC_HOST"
echo "Rappel : un changement de nom de cookie (ADR-064) déconnecte toutes les sessions."
