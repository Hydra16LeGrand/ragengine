#!/usr/bin/env bash
# Sauvegarde à chaud du Postgres applicatif (métadonnées RAGEngine — bases de
# connaissance, documents, jobs, utilisateurs, clés API : CONTRIBUTING.md §3 point 5,
# jamais d'état applicatif dans RAGFlow seul). Ne touche pas au Postgres
# RAGFlow/Elasticsearch, hors périmètre applicatif.
#
# `pg_dump --format=custom` : compressé nativement, restaurable sélectivement
# via pg_restore, format binaire recommandé par la documentation Postgres pour
# tout usage hors simple relecture humaine — préféré ici à `--format=plain`.
#
# Usage :
#   ./scripts/backup-postgres.sh                     # dev, défauts inchangés
#   ./scripts/backup-postgres.sh .env.prod.local      # prod — lit ses paramètres
#                                                      # de connexion dans ce fichier
#
# Le fichier d'environnement (positionnel, optionnel) est celui déjà utilisé
# pour `docker compose --env-file` (le plan de déploiement interne) — jamais sourcé
# (scripts/lib/backup-common.sh:backup_load_env_file), jamais affiché.
#
# Variables d'environnement (toutes optionnelles ; une variable déjà exportée
# avant l'appel garde priorité sur le fichier d'environnement ci-dessus) :
#   RAGENGINE_PROJECT_NAME  Nom de projet Compose (défaut : ragengine — dev). Sert à dériver
#                            POSTGRES_CONTAINER si celui-ci n'est pas fourni explicitement.
#   POSTGRES_CONTAINER      Nom du conteneur Postgres (défaut : ${RAGENGINE_PROJECT_NAME}-postgres,
#                            donc ragengine-postgres en dev, ragengine-prod-postgres en prod).
#   POSTGRES_DB              Base à sauvegarder (défaut : ragengine).
#   POSTGRES_USER             Utilisateur Postgres (défaut : ragengine).
#   POSTGRES_PASSWORD         Mot de passe (défaut : relu dans l'environnement du conteneur).
#   BACKUP_DIR                Répertoire de sortie (défaut : ~/Sauvegardes/ragengine — un
#                              dossier hors du dépôt, à repointer plus tard vers un dossier
#                              synchronisé : voir docs/adr/067-sauvegarde-postgres-et-documents.md).
#   BACKUP_RETENTION_DAILY    Nombre de sauvegardes quotidiennes conservées (défaut : 14).
#   BACKUP_RETENTION_WEEKLY   Nombre de sauvegardes hebdomadaires conservées au-delà (défaut : 8).
#   BACKUP_MIN_FREE_MIB       Seuil d'espace libre en Mio sous lequel la sauvegarde est
#                              annulée plutôt que de remplir le disque (défaut : 2048).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/backup-common.sh
source "$ROOT/scripts/lib/backup-common.sh"

ENV_FILE="${1:-}"
if [[ -n "$ENV_FILE" ]]; then
  backup_load_env_file "$ENV_FILE"
fi

RAGENGINE_PROJECT_NAME="${RAGENGINE_PROJECT_NAME:-ragengine}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-${RAGENGINE_PROJECT_NAME}-postgres}"
POSTGRES_DB="${POSTGRES_DB:-ragengine}"
POSTGRES_USER="${POSTGRES_USER:-ragengine}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/Sauvegardes/ragengine}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-14}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-8}"
BACKUP_MIN_FREE_MIB="${BACKUP_MIN_FREE_MIB:-2048}"

mkdir -p "$BACKUP_DIR"
LOG_FILE="$BACKUP_DIR/backup.log"

if ! backup_check_disk_guard "$BACKUP_DIR" "$BACKUP_MIN_FREE_MIB" "$LOG_FILE" "postgres:$POSTGRES_CONTAINER"; then
  exit 1
fi

if ! docker inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  echo "Erreur : conteneur '$POSTGRES_CONTAINER' introuvable. Variable POSTGRES_CONTAINER ou fichier d'environnement à ajuster ?" >&2
  backup_log "$LOG_FILE" "ECHEC composant=postgres:$POSTGRES_CONTAINER motif=conteneur_introuvable"
  exit 1
fi

# Le mot de passe vit dans l'environnement du conteneur (posé par docker-compose
# depuis .env) — on le lit là plutôt que d'exiger que l'opérateur le ressaisisse
# ou l'exporte séparément sur l'hôte.
DB_PASSWORD="${POSTGRES_PASSWORD:-$(docker exec "$POSTGRES_CONTAINER" printenv POSTGRES_PASSWORD 2>/dev/null || true)}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# Préfixe par nom de conteneur, pas par seul nom de base : dev et prod
# partagent le même POSTGRES_DB ("ragengine"), et peuvent partager le même
# BACKUP_DIR — le nom de conteneur les distingue sans ambiguïté.
OUT_FILE="$BACKUP_DIR/${POSTGRES_CONTAINER}-${TIMESTAMP}.dump"
START_EPOCH="$(date +%s)"

echo "→ Sauvegarde de '$POSTGRES_DB' (conteneur $POSTGRES_CONTAINER) vers $OUT_FILE"

if ! docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
  pg_dump --format=custom --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  > "$OUT_FILE"; then
  rm -f "$OUT_FILE"
  echo "Erreur : pg_dump a échoué pour '$POSTGRES_DB' (conteneur $POSTGRES_CONTAINER)." >&2
  backup_log "$LOG_FILE" "ECHEC composant=postgres:$POSTGRES_CONTAINER motif=pg_dump"
  exit 1
fi

# Vérification d'intégrité : le TOC doit être relisible par le même moteur que
# celui qui a produit le dump (docker cp + pg_restore --list *dans* le
# conteneur, plutôt qu'un pg_restore de l'hôte qui pourrait être d'une version
# majeure différente). Échec bruyant et fichier retiré plutôt qu'une sauvegarde
# silencieusement corrompue conservée.
VERIFY_REMOTE_PATH="/tmp/backup-verify-${TIMESTAMP}.dump"
docker cp "$OUT_FILE" "${POSTGRES_CONTAINER}:${VERIFY_REMOTE_PATH}"
if ! docker exec "$POSTGRES_CONTAINER" pg_restore --list "$VERIFY_REMOTE_PATH" >/dev/null 2>&1; then
  docker exec "$POSTGRES_CONTAINER" rm -f "$VERIFY_REMOTE_PATH" >/dev/null 2>&1 || true
  rm -f "$OUT_FILE"
  echo "Erreur : vérification d'intégrité (pg_restore --list) échouée pour $OUT_FILE — fichier supprimé." >&2
  backup_log "$LOG_FILE" "ECHEC composant=postgres:$POSTGRES_CONTAINER motif=integrite_pg_restore_list"
  exit 1
fi
docker exec "$POSTGRES_CONTAINER" rm -f "$VERIFY_REMOTE_PATH"

SIZE_BYTES="$(stat -c%s "$OUT_FILE")"
SIZE_HUMAN="$(du -h "$OUT_FILE" | cut -f1)"
DURATION="$(( $(date +%s) - START_EPOCH ))"

echo "→ Sauvegarde terminée et vérifiée : $OUT_FILE ($SIZE_HUMAN, ${DURATION}s)"
backup_log "$LOG_FILE" "OK composant=postgres:$POSTGRES_CONTAINER fichier=$(basename "$OUT_FILE") taille_octets=$SIZE_BYTES duree_s=$DURATION"

backup_apply_retention "$BACKUP_DIR" "$POSTGRES_CONTAINER" "dump" \
  "$BACKUP_RETENTION_DAILY" "$BACKUP_RETENTION_WEEKLY" "$LOG_FILE"
