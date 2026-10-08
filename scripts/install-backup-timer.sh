#!/usr/bin/env bash
# Installe la planification systemd --user de la sauvegarde RAGEngine
# production (scripts/backup-postgres.sh + scripts/backup-documents.sh contre
# .env.prod.local), tous les jours à 3h, avec rattrapage d'une exécution
# manquée (Persistent=true — voir scripts/systemd/ragengine-backup.timer et
# docs/adr/067-sauvegarde-postgres-et-documents.md).
#
# Idempotent et non destructif :
#   - relancé sans rien avoir changé, il ne fait rien (message "déjà à jour") ;
#   - si un fichier cible existe et DIFFÈRE de ce que ce dépôt porte, il refuse
#     et affiche le diff plutôt que d'écraser silencieusement — relancer avec
#     --force pour appliquer quand même.
#
# Usage :
#   ./scripts/install-backup-timer.sh [--force]
#
# Désinstallation (manuelle, volontairement non scriptée pour rester un geste
# explicite) :
#   systemctl --user disable --now ragengine-backup.timer
#   rm ~/.config/systemd/user/ragengine-backup.service ~/.config/systemd/user/ragengine-backup.timer
#   systemctl --user daemon-reload
set -euo pipefail

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT/scripts/systemd"
DEST_DIR="$HOME/.config/systemd/user"

if [[ ! -f "$ROOT/.env.prod.local" ]]; then
  echo "Avertissement : $ROOT/.env.prod.local absent — le timer s'installera quand même," >&2
  echo "mais chaque exécution échouera tant que ce fichier n'existe pas (scripts/init-prod-env.sh)." >&2
fi

mkdir -p "$DEST_DIR"

install_unit() {
  local name="$1"
  local src="$SRC_DIR/$name"
  local dest="$DEST_DIR/$name"
  local rendered
  rendered="$(sed "s#__REPO_ROOT__#$ROOT#g" "$src")"

  if [[ -f "$dest" ]]; then
    if diff -q <(printf '%s' "$rendered") "$dest" >/dev/null 2>&1; then
      echo "→ $name déjà à jour, rien à faire."
      return 0
    fi
    if [[ "$FORCE" -ne 1 ]]; then
      echo "Erreur : $dest existe déjà et diffère de ce que ce dépôt installerait." >&2
      echo "Diff (existant → proposé) :" >&2
      diff -u "$dest" <(printf '%s' "$rendered") >&2 || true
      echo "Relancer avec --force pour écraser, si c'est bien voulu." >&2
      return 1
    fi
    echo "→ $name : écrasement demandé (--force)."
  else
    echo "→ Installation de $name."
  fi
  printf '%s' "$rendered" > "$dest"
}

install_unit ragengine-backup.service
install_unit ragengine-backup.timer

systemctl --user daemon-reload
systemctl --user enable --now ragengine-backup.timer

echo
echo "→ Statut :"
systemctl --user status ragengine-backup.timer --no-pager || true
echo
echo "→ Prochaine exécution :"
systemctl --user list-timers ragengine-backup.timer --no-pager || true
echo
echo "Vérifier après coup :"
echo "  systemctl --user status ragengine-backup.service"
echo "  journalctl --user -u ragengine-backup.service -n 50"
echo "  tail -n 20 \${BACKUP_DIR:-\$HOME/Sauvegardes/ragengine}/backup.log"
