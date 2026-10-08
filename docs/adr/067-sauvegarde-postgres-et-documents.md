# ADR-067 — Sauvegarde PostgreSQL applicatif et fichiers d'origine des documents

**Date :** 2026-09-21 · **Statut :** accepté

## Résumé

Le script `scripts/backup-postgres.sh` existait depuis la Phase F mais n'était installé nulle
part : aucune sauvegarde ne tournait avant cette ADR (l'audit sécurité 2026-09 (interne), constat 10
de la liste de contrôle). Décision : sauvegarder **PostgreSQL applicatif** (source de vérité,
CONTRIBUTING.md §3 point 5) **et** les **fichiers d'origine des documents** (volume Docker
`documents_data`, sinon une restauration référence des fichiers absents) ; laisser **RAGFlow**
(MySQL/Elasticsearch/MinIO) hors périmètre car reconstructible par réindexation depuis ces deux
éléments — mais sans outillage de réindexation automatique aujourd'hui, donc un risque résiduel
assumé, pas résolu. Destination par défaut `~/Sauvegardes/ragengine` (paramétrable
`BACKUP_DIR`), rétention 14 quotidiennes + 8 hebdomadaires, garde-fou disque à 2 Gio libres,
planifié par un minuteur systemd `--user` (`Persistent=true`, rattrape une exécution manquée sur
portable éteint la nuit).

## Contexte

La production (`rag.example.com`, projets `ragengine-prod`/`ragflow-prod`) tourne sur cette machine.
Un incident antérieur (ADR-050) a rempli le disque hôte à 138 Go de journaux Docker : une
sauvegarde qui remplirait le disque à son tour serait une régression, pas une protection — d'où le
garde-fou disque et la rétention automatique, non négociables ici.

## Décision

### Ce qui est sauvegardé

1. **PostgreSQL applicatif** (`scripts/backup-postgres.sh`) — bases de connaissance, documents
   (métadonnées), jobs, utilisateurs, clés API, configs de providers. C'est la seule source de
   vérité de l'état applicatif (CONTRIBUTING.md §3 point 5) ; sans elle, rien d'autre n'a de sens à
   restaurer. `pg_dump --format=custom`, à chaud, vérifié après écriture par
   `pg_restore --list` **exécuté dans le conteneur qui a produit le dump** (pas depuis l'hôte, dont
   la version majeure de `pg_restore` peut diverger).
2. **Fichiers d'origine des documents** (`scripts/backup-documents.sh`, nouveau) — le volume
   Docker nommé `${RAGENGINE_PROJECT_NAME}_documents_data`, adressé par empreinte SHA-256 par
   `LocalFilesystemStorageAdapter` (`backend/app/providers/storage/local.py`, ADR-014). Sans cette
   sauvegarde, restaurer le seul Postgres donnerait des `Document.storage_uri` qui ne pointent plus
   sur rien : la base semblerait cohérente, chaque téléchargement/precheck échouerait
   (`StorageError`). Archivé par `tar` via un conteneur `alpine` jetable qui monte le volume en
   lecture seule (jamais d'accès direct à `/var/lib/docker/volumes`, chemin hôte non garanti selon
   le driver de stockage Docker), vérifié après écriture par `tar tzf`.

### Ce qui n'est pas sauvegardé, et pourquoi

- **RAGFlow (MySQL, Elasticsearch, MinIO)** — hors périmètre. Justification : RAGFlow est appelé
  avec les octets originaux du document (`IngestionEnginePort.upload_document`,
  `backend/app/workers/tasks/ingestion.py:730`) et le template de chunking choisi au precheck (déjà
  en Postgres) ; son état interne est donc, en principe, **reconstructible** en recréant les
  datasets et en rejouant l'upload pour chaque `Document` de Postgres avec sa décision de precheck.
  **Réserve honnête** : aucun script de réindexation en masse n'existe aujourd'hui pour effectuer
  ce rejeu — le rendre reconstructible *en principe* n'est pas la même chose que pouvoir le
  reconstruire *en pratique* en une commande. C'est un risque résiduel assumé, pas résolu par cette
  ADR ; noté comme dette de suivi (RAGFlow non couvert par un script de bascule de dataset depuis
  Postgres+stockage).
- **Redis** (cœur RAGEngine et Valkey RAGFlow) — état éphémère (files Celery, rate-limiting,
  sessions courtes) : aucun état qui ne soit pas soit reconstruit au vol, soit déjà dupliqué en
  Postgres (les sessions web, elles, vivent en Postgres — `db/models.py`, table `sessions`).
- **Secrets** (`RAGENGINE_SECRET_KEY`, mots de passe de bases, `CLOUDFLARE_TUNNEL_TOKEN`…) —
  **jamais** sauvegardés par ces scripts, qui les lisent seulement pour se connecter
  (`backup_load_env_file`, jamais affichés, jamais copiés dans une sauvegarde). Leur continuité
  relève d'un gestionnaire de secrets séparé, hors périmètre.

### Destination, rétention, garde-fou

- **Destination paramétrable** (`BACKUP_DIR`, défaut `~/Sauvegardes/ragengine`) — hors du dépôt
  (contrairement à l'ancien défaut `./backups`), pour pouvoir être repointée plus tard vers un
  dossier synchronisé (Nextcloud, Dropbox, `rclone mount`…) sans toucher au code : aucun client de
  synchronisation n'est installé sur cette machine à ce jour (vérifié), donc aucun n'est configuré
  ici — seul le paramètre existe. Repointer :
  `BACKUP_DIR=~/Nextcloud/ragengine-backups ./scripts/backup-postgres.sh .env.prod.local` (idem
  pour `backup-documents.sh`), ou l'exporter dans l'environnement du minuteur systemd
  (`systemctl --user edit ragengine-backup.service`, section `[Service]`, `Environment=BACKUP_DIR=…`).
- **Rétention** : 14 sauvegardes quotidiennes conservées telles quelles, puis 1 sauvegarde par
  semaine ISO conservée jusqu'à 8 semaines, le reste purgé automatiquement après chaque exécution
  réussie (`scripts/lib/backup-common.sh:backup_apply_retention`). Le nom de fichier est préfixé
  par le conteneur/volume (pas seulement par nom de base), ce qui isole dev et prod même s'ils
  partagent `BACKUP_DIR` — `POSTGRES_DB` vaut `ragengine` dans les deux environnements.
- **Garde-fou disque** : sous 2 Gio libres sur le système de fichiers de `BACKUP_DIR`
  (`BACKUP_MIN_FREE_MIB`, défaut 2048), la sauvegarde ne s'exécute pas et le signale (code de
  sortie non nul, ligne `ECHEC` dans `backup.log`) — jamais un remplissage silencieux (ADR-050).
- **Journalisation** : une ligne par exécution dans `$BACKUP_DIR/backup.log` (`OK`/`ECHEC`/`PURGE`),
  jamais de secret dedans ; visible aussi par `journalctl --user -u ragengine-backup.service` via
  le minuteur systemd.

### Planification

Minuteur systemd `--user` (`scripts/systemd/ragengine-backup.{service,timer}`,
`scripts/install-backup-timer.sh`) plutôt que cron : `Persistent=true` rattrape une exécution
manquée (portable éteint/en veille à l'heure planifiée) au prochain démarrage de l'instance
`systemd --user`, ce qu'une ligne cron nocturne ne fait jamais sur une machine parfois éteinte —
exactement le cas d'usage nommé dans le brief de cette ADR. `loginctl enable-linger` activé pour
que l'instance utilisateur (et donc le minuteur) tourne même hors session graphique active.
Déclenchement quotidien à 3h, `RandomizedDelaySec=300`.

## Conséquences

- Une perte totale des conteneurs prod est restaurable pour l'état applicatif (Postgres) et les
  fichiers d'origine, testé réellement (restauration dans une base jetable sur le Postgres de dev,
  comptage de lignes comparé, base jetable supprimée ensuite — voir
  `docs/runbook-deploiement.md`).
- RAGFlow (MySQL/ES/MinIO) reste un point de reconstruction manuel non outillé : après une
  restauration de Postgres + documents, les bases de connaissance existent en métadonnées mais ne
  sont plus indexées tant qu'une réingestion n'est pas rejouée document par document. Dette de
  suivi explicite, pas un angle mort caché.
- `scripts/backup-postgres.sh` reste utilisable tel quel en dev (aucun argument, mêmes défauts
  qu'avant) ; l'usage prod ajoute un seul argument positionnel, le fichier d'environnement.
