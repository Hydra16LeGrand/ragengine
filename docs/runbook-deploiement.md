# Deployment runbook

Short procedures for the day-to-day operations once the stack is in production. The deployment topology itself is described in the root `README.md` (production section).

## Restart a specific service

No need to restart everything — target the service:

```bash
docker compose -f docker/docker-compose.yml [-f docker/docker-compose.gpu.yml] \
  restart api
```

Replace `api` with the service concerned (`worker-default`, `worker-gpu`, `worker-beat`, `postgres`, `redis`…). Only add `docker-compose.gpu.yml`/`.ragflow.yml`/`.observability.yml` if the targeted service depends on it.

## Roll back after a failed deployment

`scripts/build-images.sh` tags each image both with a reproducible SHA (`git rev-parse --short HEAD`, `+"-dirty"` if the tree was not clean) and with `dev` (floating tag). There is no remote registry to date — the rollback only works if the image of the previous SHA still exists locally (`docker image ls ragengine/api`).

```bash
# 1. Find the previous SHA (the one before the failed deployment)
git log --oneline -n 5

# 2. Point back to that SHA and redeploy
RAGENGINE_IMAGE_TAG=<previous-sha> docker compose -f docker/docker-compose.yml \
  [-f docker/docker-compose.gpu.yml] [-f docker/docker-compose.traefik.yml] \
  up -d api worker-default worker-gpu worker-beat [frontend traefik]
```

If the image of the previous SHA no longer exists locally, there is no rollback possible without rebuilding from that commit (`git checkout <previous-sha> -- . && ./scripts/build-images.sh`, expecting the floating `dev` tag to be overwritten) — as long as no image registry exists (a separate debt), that is the only way.

## Back up / restore PostgreSQL and the documents (ADR-067)

Two distinct backups, to be made together: the application PostgreSQL (metadata, source of truth) **and** the original files of the documents (volume `documents_data`) — restoring one without the other leaves `Document.storage_uri` values pointing at nothing. RAGFlow (MySQL/ES/MinIO) is outside the scope of these scripts; see ADR-067 for the justification and the reservation that goes with it.

```bash
# Backup (hot, does not disturb the service) — dev, defaults unchanged:
./scripts/backup-postgres.sh
./scripts/backup-documents.sh

# Prod — a single positional argument, the environment file already used
# for docker compose --env-file (never displayed, never copied into the backup):
./scripts/backup-postgres.sh .env.prod.local
./scripts/backup-documents.sh .env.prod.local
# → ~/Sauvegardes/ragengine/<container-or-volume>-<UTC-timestamp>.{dump,tar.gz}
# (BACKUP_DIR is configurable — see ADR-067 to point it at a synchronized folder)
```

Each backup is verified after being written (`pg_restore --list` / `tar tzf`) and fails loudly (non-zero exit code, `ECHEC` line in `$BACKUP_DIR/backup.log`) if the integrity, the disk space (`BACKUP_MIN_FREE_MIB`, default 2048 MiB) or the target container/volume are not up to it. Automatic retention after every successful backup: 14 daily + 8 weekly (`BACKUP_RETENTION_DAILY`/`BACKUP_RETENTION_WEEKLY`), the rest purged.

### Restore — tested procedure

**Never restore directly onto the production database without being sure of the file.** Always check first in a throwaway database:

```bash
# 1. Restore the dump into a throwaway database, on ANY reachable PostgreSQL
#    (the dev one is fine for a simple content check):
POSTGRES_CONTAINER=ragengine-postgres ./scripts/restore-postgres.sh \
  ~/Sauvegardes/ragengine/ragengine-prod-postgres-<timestamp>.dump verification_du_jour

# 2. Count a few rows and compare with the expected total (e.g. organizations, documents):
docker exec ragengine-postgres psql -U ragengine -d verification_du_jour \
  -c "SELECT count(*) FROM organizations;"

# 3. Once satisfied, drop the throwaway database:
docker exec ragengine-postgres psql -U ragengine -d postgres \
  -c "DROP DATABASE verification_du_jour;"
```

Real restore (overwrites the target database — explicit confirmation `oui` required; the script's prompt is in French):

```bash
# On the PROD PostgreSQL itself, only after the verification step above:
POSTGRES_CONTAINER=ragengine-prod-postgres POSTGRES_PASSWORD=<prod-password> \
  ./scripts/restore-postgres.sh ~/Sauvegardes/ragengine/ragengine-prod-postgres-<timestamp>.dump ragengine
```

Original files of the documents (total loss of the `documents_data` volume):

```bash
docker volume create ragengine-prod_documents_data   # if the volume no longer exists at all
docker run --rm \
  -v ragengine-prod_documents_data:/data \
  -v ~/Sauvegardes/ragengine:/backup:ro \
  alpine tar xzf /backup/ragengine-prod_documents_data-<timestamp>.tar.gz -C /data
```

Actually tested (2026-09-21): backup of the real production database, restore into a throwaway database of the dev PostgreSQL, row counts compared table by table (5/5, including the 4 `organizations` identical by UUID and content), throwaway database dropped. Details in ADR-067.

### Scheduling

A `--user` systemd timer (not cron — `Persistent=true` catches up a missed run on a laptop that is off/asleep, which a nightly cron line never does):

```bash
./scripts/install-backup-timer.sh          # idempotent installation, never a silent overwrite
systemctl --user status ragengine-backup.timer
journalctl --user -u ragengine-backup.service -n 50
```

Uninstall:

```bash
systemctl --user disable --now ragengine-backup.timer
rm ~/.config/systemd/user/ragengine-backup.service ~/.config/systemd/user/ragengine-backup.timer
systemctl --user daemon-reload
```

## First platform administrator

`docker exec ragengine-api python -m app.cli.promote_admin <email>` (ADR-048, addendum; `ragengine-prod-api` in production). To be done once, early, after the sign-up of the real account that will administer the platform.
