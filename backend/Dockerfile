# syntax=docker/dockerfile:1
#
# Image de la façade FastAPI RAGEngine.
#
# CONTEXTE DE BUILD : la RACINE du dépôt — `docker/docker-compose.yml` déclare
# `context: ..` et `dockerfile: backend/Dockerfile`. Tous les chemins COPY sont
# donc préfixés par `backend/`.

# --------------------------------------------------------------------------- #
# Étape 1 — construction de l'environnement Python (dépendances + paquet `app`)
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/opt/venv

WORKDIR /src

RUN uv venv "$VIRTUAL_ENV"

# Le paquet est installé depuis ses sources : hatchling lit la version dans
# `app/__init__.py`, d'où la copie du code avant l'installation.
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app

RUN uv pip install --no-cache .

# --------------------------------------------------------------------------- #
# Étape 2 — image d'exécution
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Permet à un bind mount de développement (`./backend/app:/app/app`) de
    # prendre le pas sur le paquet installé, pour le rechargement à chaud.
    PYTHONPATH=/app

RUN groupadd --system --gid 1001 ragengine \
    && useradd --system --uid 1001 --gid ragengine \
    --create-home --home-dir /home/ragengine ragengine \
    && mkdir -p /app \
    && chown ragengine:ragengine /app \
    # Racine du stockage local des documents (ADR-014, RAGENGINE_STORAGE_ROOT_DIR
    # par défaut). Un volume Docker nommé monté pour la première fois sur un
    # chemin existant dans l'image en hérite le contenu ET la propriété : sans
    # ce dossier pré-créé ici, le point de montage appartiendrait à root et
    # l'utilisateur applicatif ne pourrait jamais y écrire.
    && mkdir -p /data/documents \
    && chown ragengine:ragengine /data/documents \
    # Même raisonnement pour le fichier `shelve` de `worker-beat`
    # (`docker/docker-compose.yml`, volume nommé `celery_beat_data`) : sans ce
    # dossier pré-créé, le point de montage appartient à root et le scheduler,
    # exécuté sous `ragengine`, ne peut jamais écrire son schedule persisté
    # (`PermissionError`, `worker-beat` en crash loop — trouvé en campagne E2E).
    && mkdir -p /var/lib/celery-beat \
    && chown ragengine:ragengine /var/lib/celery-beat

COPY --from=builder --chown=ragengine:ragengine /opt/venv /opt/venv

WORKDIR /app

# Alembic charge les scripts de migration par CHEMIN DE FICHIER, pas par import
# Python (`script_location = %(here)s/app/db/migrations` dans alembic.ini) :
# ces fichiers doivent exister tels quels sur disque à côté de `alembic.ini`,
# ce qu'une installation par `pip install .` ne garantit pas pour du contenu
# hors paquet Python strict. On les copie donc ici, au même titre que `app/`
# tire parti du `PYTHONPATH=/app` déjà en place pour le rechargement à chaud :
# la même copie sert les deux usages, sans dupliquer la logique.
COPY --chown=ragengine:ragengine backend/app ./app
COPY --chown=ragengine:ragengine backend/alembic.ini ./alembic.ini
COPY --chown=ragengine:ragengine --chmod=755 backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER ragengine

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import httpx; httpx.get('http://127.0.0.1:8000/api/v1/health').raise_for_status()"]

ENTRYPOINT ["docker-entrypoint.sh"]
# IP cliente réelle derrière Traefik + cloudflared (ADR-052) : uvicorn n'honore
# `X-Forwarded-For`/`X-Forwarded-Proto` que venant du sous-réseau Docker de ce
# conteneur, passerelle exclue, calculé au démarrage (le sous-réseau est
# attribué par Docker). Détection en échec ⇒ le conteneur ne démarre pas.
# `FORWARDED_ALLOW_IPS` n'est donc jamais lu, et `*` n'est jamais utilisé.
CMD ["sh", "-c", "set -eu; trusted=\"$(python -m app.core.trusted_proxies)\"; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$trusted\""]
