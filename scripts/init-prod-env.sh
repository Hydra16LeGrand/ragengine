#!/usr/bin/env bash
# Prépare `.env.prod.local` (ignoré par git) à partir du gabarit `.env.prod`.
#
# Usage, depuis la racine du dépôt, par l'OPÉRATEUR (jamais par un agent) :
#   bash scripts/init-prod-env.sh
#
# - Génère les secrets absents, vides ou encore à `change-me` ; n'écrase JAMAIS
#   une valeur déjà renseignée : relancé après le premier démarrage, il ne fait
#   pas tourner les mots de passe de bases déjà initialisées (ce qui couperait
#   l'accès aux volumes existants).
# - Recopie les clés LLM depuis `.env` si elles manquent dans `.env.prod.local`.
# - N'affiche aucune valeur : seulement les NOMS des variables traitées.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$ROOT/.env.prod"
TARGET="$ROOT/.env.prod.local"
DEV_ENV="$ROOT/.env"

[ -f "$TARGET" ] || cp "$TEMPLATE" "$TARGET"
chmod 600 "$TARGET"

# Valeur courante d'une variable dans la cible (dernière occurrence non commentée).
current() { grep -E "^$1=" "$TARGET" | tail -n1 | cut -d= -f2- || true; }

# Remplace (ou ajoute) une variable, sans jamais afficher sa valeur.
set_var() {
  local name="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -vE "^$name=" "$TARGET" > "$tmp" || true
  printf '%s=%s\n' "$name" "$value" >> "$tmp"
  cat "$tmp" > "$TARGET"
  rm -f "$tmp"
}

needs_value() {
  local value
  value="$(current "$1")"
  [ -z "$value" ] || [[ "$value" == change-me* ]]
}

set_var RAGENGINE_ENV_FILE .env.prod.local

if needs_value RAGENGINE_SECRET_KEY; then
  set_var RAGENGINE_SECRET_KEY "$(openssl rand -hex 32)"
  echo "généré : RAGENGINE_SECRET_KEY"
fi

for name in POSTGRES_PASSWORD RAGFLOW_MYSQL_PASSWORD RAGFLOW_ELASTIC_PASSWORD \
  RAGFLOW_MINIO_PASSWORD RAGFLOW_REDIS_PASSWORD RAGFLOW_BOOTSTRAP_PASSWORD; do
  if needs_value "$name"; then
    set_var "$name" "$(openssl rand -hex 24)"
    echo "généré : $name"
  fi
done

if [ -f "$DEV_ENV" ]; then
  for name in ANTHROPIC_API_KEY OPENAI_API_KEY GROQ_API_KEY GEMINI_API_KEY; do
    value="$(grep -E "^$name=" "$DEV_ENV" | tail -n1 | cut -d= -f2- || true)"
    if [ -n "$value" ] && [ -z "$(current "$name")" ]; then
      set_var "$name" "$value"
      echo "recopié depuis .env : $name"
    fi
  done
fi

if [ -z "$(current CLOUDFLARE_TUNNEL_TOKEN)" ]; then
  echo "à renseigner à la main : CLOUDFLARE_TUNNEL_TOKEN (jeton du tunnel Cloudflare)"
fi
echo "OK — $TARGET"
