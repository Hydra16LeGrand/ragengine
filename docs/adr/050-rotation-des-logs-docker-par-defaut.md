# ADR-050 — Rotation de logs par défaut sur tous les conteneurs Docker

**Date :** 2026-09-11 · **Statut :** accepté · **Portée :** les 5 fichiers `docker/docker-compose*.yml`

## Contexte

Incident réel : `langfuse-clickhouse` a rempli le disque hôte à 138 Go (+ 586 Mio sur un second
fichier) en écrivant ses logs en boucle, sans qu'aucune limite ne l'arrête. Cause confirmée :
`json-file`, le driver de logs par défaut de Docker, **n'a aucune borne de taille tant qu'elle n'est
pas explicitement configurée** — aucun des 5 fichiers compose de ce dépôt n'en posait une. Le disque
plein a bloqué bien plus que ce seul conteneur : tout ce qui écrit dans `/tmp` sur l'hôte a cessé de
fonctionner.

## Décision

**Un plafond par défaut sur chaque conteneur, dans chaque fichier compose, via un anchor YAML
répété.** `x-logging: &default-logging` (driver `json-file`, `max-size`/`max-file` tirés de
`RAGENGINE_LOG_MAX_SIZE`/`RAGENGINE_LOG_MAX_FILE`, défauts `10m`/`3` — 30 Mio par conteneur) est
défini en tête de chacun des 5 fichiers (`docker-compose.yml`, `.ragflow.yml`, `.gpu.yml`,
`.observability.yml`, `.traefik.yml`) et référencé (`logging: *default-logging`) par les 21 services
qui portent déjà `restart: unless-stopped`.

**Dupliqué plutôt que centralisé** : les anchors YAML ne traversent pas les fichiers quand ils sont
fusionnés via plusieurs `-f` (chaque fichier est parsé séparément avant fusion) — impossible de
définir l'anchor une seule fois pour les 5 fichiers sans un fichier de base commun que ce dépôt n'a
pas et qu'introduire ici aurait dépassé le périmètre du correctif. La répétition (5 blocs identiques
de 4 lignes) reste plus simple qu'une nouvelle indirection.

**Défaut volontairement large (30 Mio), pas minimal.** Assez pour du débogage courant (relire les
logs récents d'un conteneur en cours d'incident) sans réintroduire le risque : même un conteneur qui
se met à boucler ses logs plafonne désormais à 30 Mio, pas à l'espace disque disponible. Resserrable
par `.env` sans toucher aux fichiers compose.

## Conséquences

- `RAGENGINE_LOG_MAX_SIZE`/`RAGENGINE_LOG_MAX_FILE` documentées dans `.env.example`.
- Chaque conteneur existant doit être recréé (`docker compose up -d --force-recreate`, ou simplement
  reconstruit lors du prochain déploiement) pour que la nouvelle politique de logs s'applique — un
  conteneur déjà démarré ne relit pas sa config `logging:` à chaud.
- Aucun changement de comportement applicatif : uniquement la rétention des logs Docker eux-mêmes.
