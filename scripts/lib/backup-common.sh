#!/usr/bin/env bash
# Fonctions partagées par scripts/backup-postgres.sh et scripts/backup-documents.sh
# (ADR-067). Pas de point d'entrée ici : ce fichier est fait pour être `source`,
# jamais exécuté directement.
#
# Trois responsabilités communes aux deux scripts de sauvegarde :
#   - lecture d'un fichier d'environnement (--env-file) sans jamais exécuter
#     son contenu (pas de `source`/`eval` sur un fichier de secrets) ;
#   - garde-fou disque, avant toute écriture (incident ADR-050 : un dossier de
#     sauvegarde qui remplit le disque serait une régression, pas une protection) ;
#   - rétention 14 quotidiennes / 8 hebdomadaires, et journalisation à une ligne
#     par exécution dans un fichier `backup.log` commun au dossier de destination.

# --- Lecture d'un fichier d'environnement, sans sourcing -------------------
#
# Analyse ligne à ligne (KEY=VALUE), comme le fait `docker compose --env-file` —
# jamais un `source`/`eval` du fichier, qui exécuterait n'importe quel contenu
# shell qu'il porterait. Une variable déjà présente dans l'environnement du
# process (export explicite avant l'appel) garde la priorité sur le fichier.
#
# N'affiche JAMAIS la valeur lue (aucun echo/log d'une ligne de ce fichier) :
# c'est par ce fichier que transitent les mots de passe de base de données.
backup_load_env_file() {
  local file="$1" line key value
  if [[ ! -f "$file" ]]; then
    echo "Erreur : fichier d'environnement introuvable : $file" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      if [[ "$value" =~ ^\"(.*)\"$ ]]; then
        value="${BASH_REMATCH[1]}"
      elif [[ "$value" =~ ^\'(.*)\'$ ]]; then
        value="${BASH_REMATCH[1]}"
      fi
      # ${!key+x} : vrai si la variable est déjà définie (même vide) dans l'environnement.
      if [[ -z "${!key+x}" ]]; then
        printf -v "$key" '%s' "$value"
        export "$key"
      fi
    fi
  done < "$file"
}

# --- Journalisation ----------------------------------------------------------
#
# Une ligne horodatée (UTC, ISO 8601) par appel, ajoutée à $1. Format
# `CLE=valeur` pour rester grep-able (`grep ECHEC backup.log`) sans jamais
# porter de secret (aucun mot de passe ni contenu de fichier .env dedans).
backup_log() {
  local log_file="$1"
  shift
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$log_file"
}

# --- Garde-fou disque ---------------------------------------------------------
#
# Espace disponible (Mio) sur le système de fichiers portant $1. `df --output`
# est GNU coreutils (présent sur toutes les cibles connues de ce dépôt) —
# jamais de dépendance ajoutée pour ça.
backup_free_mib() {
  df --output=avail -BM "$1" 2>/dev/null | tail -n1 | tr -dc '0-9'
}

# backup_check_disk_guard <répertoire> <seuil_mio> <log_file> <étiquette>
#
# Retourne 1 (et journalise + affiche sur stderr) si l'espace libre passe sous
# le seuil — c'est à l'appelant de considérer ça comme un échec bruyant
# (`exit 1`), jamais un fichier partiel silencieusement laissé derrière.
backup_check_disk_guard() {
  local dir="$1" threshold_mib="$2" log_file="$3" label="$4" available_mib
  available_mib="$(backup_free_mib "$dir")"
  if [[ -z "$available_mib" ]]; then
    echo "Erreur : impossible de déterminer l'espace disque disponible pour $dir." >&2
    backup_log "$log_file" "ECHEC composant=$label motif=espace_disque_indetermine repertoire=$dir"
    return 1
  fi
  if (( available_mib < threshold_mib )); then
    echo "Erreur : espace disque insuffisant pour $dir (${available_mib} Mio disponibles, seuil ${threshold_mib} Mio) — sauvegarde annulée (cf. ADR-050, un disque plein n'est pas une protection)." >&2
    backup_log "$log_file" "ECHEC composant=$label motif=disque_insuffisant disponible_mio=$available_mib seuil_mio=$threshold_mib"
    return 1
  fi
  return 0
}

# --- Rétention 14 quotidiennes / 8 hebdomadaires ------------------------------
#
# backup_apply_retention <répertoire> <préfixe_fichier> <extension> <n_quotidien> <n_hebdo> <log_file>
#
# Politique GFS simplifiée : les `n_quotidien` fichiers les plus récents (par
# nom — l'horodatage `YYYYmmddThhmmssZ` est trié lexicographiquement dans le
# même ordre que chronologiquement) sont conservés tels quels. Au-delà, un seul
# fichier par semaine ISO (le plus récent de cette semaine, grâce au tri) est
# conservé, jusqu'à `n_hebdo` semaines distinctes ; tout le reste est purgé.
# Le préfixe (nom du conteneur ou du volume) isole dev et prod même s'ils
# partagent le même répertoire de destination.
backup_apply_retention() {
  local dir="$1" prefix="$2" extension="$3" daily="$4" weekly="$5" log_file="$6"
  local -a files
  mapfile -t files < <(find "$dir" -maxdepth 1 -type f -name "${prefix}-*.${extension}" | sort -r)

  local total=${#files[@]}
  if (( total <= daily )); then
    return 0
  fi

  local -A kept_weeks=()
  local weekly_kept=0 index=0
  local file base ts week

  for file in "${files[@]}"; do
    if (( index < daily )); then
      index=$((index + 1))
      continue
    fi
    base="$(basename "$file")"
    ts="${base#"${prefix}"-}"
    ts="${ts%%T*}"
    week="$(date -u -d "$ts" +%G-%V 2>/dev/null || true)"
    if [[ -n "$week" ]] && [[ -z "${kept_weeks[$week]:-}" ]] && (( weekly_kept < weekly )); then
      kept_weeks[$week]=1
      weekly_kept=$((weekly_kept + 1))
      index=$((index + 1))
      continue
    fi
    rm -f "$file"
    backup_log "$log_file" "PURGE fichier=$base"
    index=$((index + 1))
  done
}
