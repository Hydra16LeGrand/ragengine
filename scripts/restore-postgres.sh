#!/usr/bin/env bash
# Restaure un dump produit par scripts/backup-postgres.sh (--format=custom).
# Opération destructive par nature (écrase la base cible) : toujours confirmée
# explicitement, jamais silencieuse.
#
# Usage :
#   ./scripts/restore-postgres.sh <fichier.dump> [base_cible]
#
# base_cible est optionnel, défaut : POSTGRES_DB (env) ou "ragengine". Utile
# pour restaurer dans une base jetable de vérification sans toucher la base
# applicative réelle : ./scripts/restore-postgres.sh backups/xxx.dump restore_test
#
# Variables d'environnement (mêmes défauts que backup-postgres.sh) :
#   POSTGRES_CONTAINER, POSTGRES_USER, POSTGRES_PASSWORD
set -euo pipefail

DUMP_FILE="${1:?Usage: $0 <fichier.dump> [base_cible]}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-ragengine-postgres}"
POSTGRES_USER="${POSTGRES_USER:-ragengine}"
TARGET_DB="${2:-${POSTGRES_DB:-ragengine}}"

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "Erreur : fichier de sauvegarde introuvable : $DUMP_FILE" >&2
  exit 1
fi

if ! docker inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  echo "Erreur : conteneur '$POSTGRES_CONTAINER' introuvable. Variable POSTGRES_CONTAINER à ajuster ?" >&2
  exit 1
fi

DB_PASSWORD="${POSTGRES_PASSWORD:-$(docker exec "$POSTGRES_CONTAINER" printenv POSTGRES_PASSWORD 2>/dev/null || true)}"

echo "⚠  Ceci va ÉCRASER le contenu de la base '$TARGET_DB' (conteneur $POSTGRES_CONTAINER)"
echo "   avec le contenu de : $DUMP_FILE"
read -r -p "Confirmer la restauration ? Tapez 'oui' pour continuer : " CONFIRMATION
if [[ "$CONFIRMATION" != "oui" ]]; then
  echo "Annulé."
  exit 1
fi

echo "→ Copie du dump dans le conteneur…"
docker cp "$DUMP_FILE" "$POSTGRES_CONTAINER:/tmp/restore.dump"

# pg_restore --dbname exige une base existante pour s'y connecter — la créer
# si besoin (cas d'une base jetable de vérification qui n'existe pas encore).
DB_EXISTS="$(docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
  psql --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
  -c "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'")"
if [[ "$DB_EXISTS" != "1" ]]; then
  echo "→ Base '$TARGET_DB' inexistante, création…"
  docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
    createdb --username "$POSTGRES_USER" "$TARGET_DB"
fi

echo "→ Restauration (pg_restore --clean --if-exists) sur '$TARGET_DB'…"
docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
  pg_restore --clean --if-exists --no-owner --username "$POSTGRES_USER" \
  --dbname "$TARGET_DB" /tmp/restore.dump

docker exec "$POSTGRES_CONTAINER" rm -f /tmp/restore.dump

echo "→ Restauration terminée sur '$TARGET_DB'."
