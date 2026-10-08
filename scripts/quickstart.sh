#!/usr/bin/env bash
# Chemins d'essai de RAGEngine, une commande chacun (ADR-071).
#
#   scripts/quickstart.sh demo   # sans RAGFlow ni GPU : explorer l'API et l'interface (aucune vraie recherche)
#   scripts/quickstart.sh cpu    # vraie chaîne (RAGFlow, BGE-M3, reranker), sans GPU NVIDIA — lent, gourmand en RAM
#   scripts/quickstart.sh gpu    # vraie chaîne, avec GPU NVIDIA (image TEI Ada `89-1.9`)
#   scripts/quickstart.sh stop demo|cpu|gpu    # arrête les conteneurs, garde les données
#
# Option : --yes (ne pas demander confirmation quand la mémoire disponible paraît juste).
#
# Idempotent : relancé, il ne recrée que ce dont la configuration a changé et ne
# réémet pas une RAGFLOW_API_KEY encore valide. N'affiche aucun secret : la clé
# RAGFlow émise est écrite dans le fichier d'environnement local, jamais à l'écran.
# Jamais `down -v`, jamais `--remove-orphans` : les fichiers compose partagent un
# nom de projet (docker/docker-compose.gpu.yml, en-tête).
#
# Prérequis : Docker Engine + Compose v2 ; `uv` pour cpu et gpu (amorce de RAGFlow) ;
# pour gpu, le NVIDIA Container Toolkit.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

ASSUME_YES=0
args=()
for arg in "$@"; do
  if [ "$arg" = "--yes" ]; then ASSUME_YES=1; else args+=("$arg"); fi
done
[ "${#args[@]}" -ge 1 ] || usage
ACTION="${args[0]}"

case "$ACTION" in
  demo | cpu | gpu) MODE="$ACTION" ;;
  stop) MODE="${args[1]:-}"; [[ "$MODE" =~ ^(demo|cpu|gpu)$ ]] || usage ;;
  *) usage ;;
esac

command -v docker >/dev/null || { echo "✗ docker introuvable" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "✗ Docker Compose v2 requis (\`docker compose\`)" >&2; exit 1; }

if [ "$MODE" = demo ]; then ENV_FILE=.env.demo; else ENV_FILE=.env; fi

compose() { docker compose --env-file "$ENV_FILE" "$@"; }
core() { compose -f docker/docker-compose.yml "$@"; }
value_of() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' || true; }
value_or() { local v; v="$(value_of "$1")"; echo "${v:-$2}"; }

# ─── Arrêt ───────────────────────────────────────────────────────────────────
if [ "$ACTION" = stop ]; then
  [ -f "$ENV_FILE" ] || { echo "✗ $ENV_FILE absent : rien à arrêter" >&2; exit 1; }
  compose -f docker/docker-compose.traefik.yml stop
  if [ "$MODE" != demo ]; then
    compose -f docker/docker-compose.ragflow.yml stop
    if [ "$MODE" = gpu ]; then compose -f docker/docker-compose.gpu.yml stop; else compose -f docker/docker-compose.cpu.yml stop; fi
  fi
  core --profile workers stop
  echo "OK — conteneurs arrêtés, données conservées"
  exit 0
fi

# ─── Utilitaires ─────────────────────────────────────────────────────────────
random_hex() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Remplace (ou ajoute) une variable sans jamais afficher sa valeur.
set_var() {
  local name="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -vE "^$name=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$name" "$value" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

needs_value() {
  local v
  v="$(value_of "$1")"
  [ -z "$v" ] || [[ "$v" == change-me* ]]
}

available_ram_gb() { awk '/^MemAvailable:/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null || echo 0; }

# $1 = Go conseillés, $2 = description de ce qui les consomme.
check_ram() {
  local need="$1" have
  have="$(available_ram_gb)"
  echo "→ Mémoire : ~${need} Go libres conseillés ($2) ; disponible ici : ${have} Go"
  if [ "$have" -lt "$need" ] && [ "$ASSUME_YES" -ne 1 ]; then
    if [ -t 0 ]; then
      read -r -p "  Mémoire disponible inférieure au conseil. Continuer quand même ? [o/N] " reply
      [[ "$reply" =~ ^[oOyY] ]] || { echo "Abandon." >&2; exit 1; }
    else
      echo "✗ Mémoire disponible inférieure au conseil ; relancer avec --yes pour passer outre." >&2
      exit 1
    fi
  fi
}

# Attend qu'un conteneur soit `healthy` (ou `running` s'il n'a pas de healthcheck).
wait_healthy() {
  local name="$1" timeout="$2" waited=0 state
  while [ "$waited" -lt "$timeout" ]; do
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo absent)"
    case "$state" in healthy | running) return 0 ;; esac
    sleep 5; waited=$((waited + 5))
  done
  echo "✗ $name n'est pas prêt après ${timeout}s (état : ${state:-?}) — voir \`docker logs $name\`" >&2
  return 1
}

http_code() { curl -s -o /dev/null -w '%{http_code}' "$@" || true; }

# ─── Fichier d'environnement ─────────────────────────────────────────────────
if [ "$MODE" = demo ]; then
  if [ ! -f "$ENV_FILE" ]; then
    umask 077
    cat > "$ENV_FILE" <<ENVEOF
# Généré par scripts/quickstart.sh demo — démonstration locale, sans RAGFlow ni GPU (ADR-071).
RAGENGINE_ENV_FILE=.env.demo
RAGENGINE_ENV=development
RAGENGINE_PROJECT_NAME=ragengine-demo
INGESTION_ENGINE=fake-shared
RAGFLOW_POLL_INTERVAL_SECONDS=2
RAGENGINE_SECRET_KEY=$(random_hex)
POSTGRES_PASSWORD=$(random_hex)
ENVEOF
    echo "→ $ENV_FILE créé (secrets générés, non affichés)"
  fi
else
  if [ ! -f "$ENV_FILE" ]; then
    cp .env.example "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "→ $ENV_FILE créé depuis .env.example"
  fi
  project="$(value_or RAGENGINE_PROJECT_NAME ragengine)"
  needs_value RAGENGINE_SECRET_KEY && { set_var RAGENGINE_SECRET_KEY "$(random_hex)"; echo "→ généré : RAGENGINE_SECRET_KEY"; }
  needs_value RAGFLOW_BOOTSTRAP_PASSWORD && { set_var RAGFLOW_BOOTSTRAP_PASSWORD "$(random_hex)"; echo "→ généré : RAGFLOW_BOOTSTRAP_PASSWORD"; }
  # Le mot de passe PostgreSQL ne change jamais si un volume existe déjà : l'ancien resterait attendu.
  if needs_value POSTGRES_PASSWORD && ! docker volume inspect "${project}_postgres_data" >/dev/null 2>&1; then
    set_var POSTGRES_PASSWORD "$(random_hex)"; echo "→ généré : POSTGRES_PASSWORD"
  fi
  if [ "$(value_or INGESTION_ENGINE ragflow)" != ragflow ]; then
    echo "✗ INGESTION_ENGINE n'est pas « ragflow » dans $ENV_FILE : ce chemin utilise le vrai moteur" >&2
    exit 1
  fi
fi

TRAEFIK_PORT="$(value_or RAGENGINE_TRAEFIK_HOST_PORT 8090)"
API_PORT="$(value_or RAGENGINE_API_PORT 8000)"
PROJECT="$(value_or RAGENGINE_PROJECT_NAME ragengine)"
RAGFLOW_PROJECT="$(value_or RAGFLOW_PROJECT_NAME ragflow)"

# ─── Démonstration ───────────────────────────────────────────────────────────
if [ "$MODE" = demo ]; then
  check_ram 3 "PostgreSQL, Redis, API, worker, frontend"
  echo "→ Cœur : PostgreSQL, Redis, API"
  core up -d --build --wait --wait-timeout 300 postgres redis api
  # Après l'API : elle applique les migrations, le worker ne doit pas courir contre elle sur une base vide.
  echo "→ Worker de la file par défaut"
  core --profile workers up -d worker-default
  echo "→ Interface (Traefik + frontend ; premier build : quelques minutes)"
  compose -f docker/docker-compose.traefik.yml up -d --build --wait --wait-timeout 600

  code="$(http_code "http://127.0.0.1:${TRAEFIK_PORT}/api/v1/health")"
  [ "$code" = 200 ] || { echo "✗ /api/v1/health répond $code via Traefik" >&2; exit 1; }
  echo
  echo "OK — démonstration prête : http://127.0.0.1:${TRAEFIK_PORT}   (API : http://127.0.0.1:${API_PORT}/api/v1/docs)"
  echo "  Crée un compte, une base de connaissances, dépose un fichier TEXTE (.txt, UTF-8)."
  echo "  ⚠ Moteur factice : recherche lexicale seulement — aucune qualité de recherche réelle,"
  echo "    aucun OCR, aucun embedding. Pour la vraie chaîne : scripts/quickstart.sh cpu|gpu."
  echo "  Arrêt : scripts/quickstart.sh stop demo"
  exit 0
fi

# ─── Vraie chaîne (cpu / gpu) ────────────────────────────────────────────────
if [ "$MODE" = gpu ]; then
  command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 \
    || { echo "✗ GPU NVIDIA introuvable (\`nvidia-smi\`). Utiliser \`scripts/quickstart.sh cpu\` ?" >&2; exit 1; }
  docker info 2>/dev/null | grep -qi nvidia \
    || echo "⚠ Aucun runtime nvidia déclaré dans Docker : installer le NVIDIA Container Toolkit si le démarrage échoue."
  check_ram 12 "cœur, RAGFlow, Elasticsearch ≤ 4 Go ; modèles en VRAM"
else
  check_ram 16 "cœur, RAGFlow, Elasticsearch ≤ 4 Go, + les deux modèles TEI en RAM (~4,5 Go de poids) — estimation, non mesurée"
  echo "  Variante CPU : non testée de bout en bout sur matériel sans GPU (ADR-071). Ingestion lente."
fi
command -v uv >/dev/null || { echo "✗ uv requis pour l'amorce de RAGFlow : https://docs.astral.sh/uv/" >&2; exit 1; }

echo "→ 1/6 Cœur : PostgreSQL, Redis, API, workers"
core up -d --build --wait --wait-timeout 300 postgres redis api
core --profile workers up -d worker-default worker-beat

if [ "$MODE" = gpu ]; then
  echo "→ 2/6 Inférence GPU (TEI) et worker GPU"
  compose -f docker/docker-compose.gpu.yml up -d --build
else
  echo "→ 2/6 Inférence CPU (TEI ${RAGENGINE_TEI_CPU_TAG:-cpu-1.9})"
  compose -f docker/docker-compose.cpu.yml up -d
fi

echo "→ 3/6 RAGFlow"
compose -f docker/docker-compose.ragflow.yml up -d

echo "→ 4/6 Attente de l'inférence (premier démarrage : téléchargement des poids, 30 min max)"
wait_healthy "${PROJECT}-inference-embedding" 1800
wait_healthy "${PROJECT}-inference-reranker" 1800
echo "→ Attente de RAGFlow (10 min max)"
wait_healthy "${RAGFLOW_PROJECT}-cpu" 600
base_url="http://127.0.0.1:$(value_or RAGENGINE_RAGFLOW_HOST_PORT 9390)"

echo "→ 5/6 Amorce de RAGFlow"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if ! uv run --project backend python scripts/bootstrap_ragflow.py --base-url "$base_url" > "$log" 2>&1; then
  grep -v "RAGFLOW_API_KEY=" "$log" | tail -n 20 >&2
  exit 1
fi
grep -v "RAGFLOW_API_KEY=" "$log" | grep "→" || true
new_key="$(grep -oE 'RAGFLOW_API_KEY=[^[:space:]]+' "$log" | tail -n1 | cut -d= -f2- || true)"
if [ -n "$new_key" ]; then
  set_var RAGFLOW_API_KEY "$new_key"
  echo "→ RAGFLOW_API_KEY émise et écrite dans $ENV_FILE ; redémarrage de l'API et des workers"
  core --profile workers up -d --force-recreate api worker-default worker-beat
  if [ "$MODE" = gpu ]; then compose -f docker/docker-compose.gpu.yml up -d --force-recreate worker-gpu; fi
fi

echo "→ 6/6 Interface, puis vérification"
compose -f docker/docker-compose.traefik.yml up -d --build

for name in postgres redis api worker-default worker-beat inference-embedding inference-reranker; do
  wait_healthy "${PROJECT}-${name}" 300
done
if [ "$MODE" = gpu ]; then wait_healthy "${PROJECT}-worker-gpu" 300; fi
for name in es mysql minio valkey cpu; do
  wait_healthy "${RAGFLOW_PROJECT}-${name}" 300
done
wait_healthy "${PROJECT}-traefik" 120

fail=0
check() { # $1 = libellé, $2 = code attendu, $3... = arguments curl
  local label="$1" expected="$2" code
  shift 2
  code="$(http_code "$@")"
  if [ "$code" = "$expected" ]; then echo "  ✓ $label"; else echo "  ✗ $label (HTTP $code, attendu $expected)" >&2; fail=1; fi
}
check "API /health" 200 "http://127.0.0.1:${API_PORT}/api/v1/health"
check "Interface via Traefik" 200 "http://127.0.0.1:${TRAEFIK_PORT}/"
check "API via Traefik" 200 "http://127.0.0.1:${TRAEFIK_PORT}/api/v1/health"
check "TEI embedding (/embed)" 200 -X POST -H 'Content-Type: application/json' \
  -d '{"inputs":"bonjour"}' "http://127.0.0.1:$(value_or RAGENGINE_INFERENCE_EMBEDDING_HOST_PORT 9997)/embed"
check "TEI reranker (/rerank)" 200 -X POST -H 'Content-Type: application/json' \
  -d '{"query":"bonjour","texts":["salut","table"]}' "http://127.0.0.1:$(value_or RAGENGINE_INFERENCE_RERANKER_HOST_PORT 9998)/rerank"
[ "$fail" -eq 0 ] || exit 1

echo
echo "OK — chaîne complète prête : http://127.0.0.1:${TRAEFIK_PORT}"
echo "  Crée un compte, une base de connaissances, dépose un document."
echo "  Arrêt : scripts/quickstart.sh stop ${MODE}"
