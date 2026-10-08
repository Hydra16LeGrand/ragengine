#!/usr/bin/env bash
# Sauvegarde des fichiers d'origine des documents téléversés (ADR-014,
# ADR-067) : le volume Docker nommé `${RAGENGINE_PROJECT_NAME}_documents_data`,
# adressé par empreinte SHA-256 par `LocalFilesystemStorageAdapter`
# (backend/app/providers/storage/local.py). Sans cette sauvegarde, restaurer
# le seul Postgres applicatif donnerait des lignes `Document.storage_uri` qui
# ne pointent plus sur rien — la base redeviendrait cohérente en apparence,
# mais chaque téléchargement/precheck échouerait (StorageError).
#
# Le volume est archivé tel quel (`tar`) via un conteneur jetable qui le monte
# en lecture seule — jamais un accès direct à /var/lib/docker/volumes (chemin
# hôte non garanti selon le driver de stockage Docker), jamais d'arrêt du
# service (le montage read-only suffit ; pas d'écriture concurrente attendue
# pendant une fenêtre de sauvegarde nocturne, et un fichier en cours d'upload
# atomique — backend/app/providers/storage/local.py `_write_atomically` — n'existe sous son nom
# final qu'une fois complet).
#
# Usage :
#   ./scripts/backup-documents.sh                     # dev, défauts inchangés
#   ./scripts/backup-documents.sh .env.prod.local      # prod
#
# Variables d'environnement (mêmes défauts/priorités que backup-postgres.sh,
# scripts/lib/backup-common.sh) :
#   RAGENGINE_PROJECT_NAME   Nom de projet Compose (défaut : ragengine). Sert à dériver
#                             DOCUMENTS_VOLUME si celui-ci n'est pas fourni explicitement.
#   DOCUMENTS_VOLUME          Volume Docker à archiver (défaut : ${RAGENGINE_PROJECT_NAME}_documents_data).
#   BACKUP_DIR                Répertoire de sortie (défaut : ~/Sauvegardes/ragengine — même
#                              dossier que backup-postgres.sh, fichiers distingués par préfixe).
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
DOCUMENTS_VOLUME="${DOCUMENTS_VOLUME:-${RAGENGINE_PROJECT_NAME}_documents_data}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/Sauvegardes/ragengine}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-14}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-8}"
BACKUP_MIN_FREE_MIB="${BACKUP_MIN_FREE_MIB:-2048}"

mkdir -p "$BACKUP_DIR"
LOG_FILE="$BACKUP_DIR/backup.log"

if ! backup_check_disk_guard "$BACKUP_DIR" "$BACKUP_MIN_FREE_MIB" "$LOG_FILE" "documents:$DOCUMENTS_VOLUME"; then
  exit 1
fi

if ! docker volume inspect "$DOCUMENTS_VOLUME" >/dev/null 2>&1; then
  echo "Erreur : volume '$DOCUMENTS_VOLUME' introuvable. Variable DOCUMENTS_VOLUME ou fichier d'environnement à ajuster ?" >&2
  backup_log "$LOG_FILE" "ECHEC composant=documents:$DOCUMENTS_VOLUME motif=volume_introuvable"
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/${DOCUMENTS_VOLUME}-${TIMESTAMP}.tar.gz"
START_EPOCH="$(date +%s)"

echo "→ Sauvegarde du volume '$DOCUMENTS_VOLUME' vers $OUT_FILE"

if ! docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "${DOCUMENTS_VOLUME}:/data:ro" \
  -v "${BACKUP_DIR}:/backup" \
  alpine:latest \
  tar czf "/backup/$(basename "$OUT_FILE")" -C /data .; then
  rm -f "$OUT_FILE"
  echo "Erreur : archivage tar échoué pour le volume '$DOCUMENTS_VOLUME'." >&2
  backup_log "$LOG_FILE" "ECHEC composant=documents:$DOCUMENTS_VOLUME motif=tar"
  exit 1
fi

# Vérification d'intégrité : l'archive doit être relisible en entier, pas
# seulement avoir été écrite sans erreur de code retour.
if ! tar tzf "$OUT_FILE" >/dev/null 2>&1; then
  rm -f "$OUT_FILE"
  echo "Erreur : vérification d'intégrité (tar tzf) échouée pour $OUT_FILE — fichier supprimé." >&2
  backup_log "$LOG_FILE" "ECHEC composant=documents:$DOCUMENTS_VOLUME motif=integrite_tar_tzf"
  exit 1
fi

SIZE_BYTES="$(stat -c%s "$OUT_FILE")"
SIZE_HUMAN="$(du -h "$OUT_FILE" | cut -f1)"
DURATION="$(( $(date +%s) - START_EPOCH ))"

echo "→ Sauvegarde terminée et vérifiée : $OUT_FILE ($SIZE_HUMAN, ${DURATION}s)"
backup_log "$LOG_FILE" "OK composant=documents:$DOCUMENTS_VOLUME fichier=$(basename "$OUT_FILE") taille_octets=$SIZE_BYTES duree_s=$DURATION"

backup_apply_retention "$BACKUP_DIR" "$DOCUMENTS_VOLUME" "tar.gz" \
  "$BACKUP_RETENTION_DAILY" "$BACKUP_RETENTION_WEEKLY" "$LOG_FILE"
