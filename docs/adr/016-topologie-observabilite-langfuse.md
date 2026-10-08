# ADR-016 — Topologie du self-hosting Langfuse (`docker-compose.observability.yml`)

**Date :** 2026-07-29 · **Statut :** accepté

## Contexte

`ObservabilityPort` et son adaptateur Langfuse (OTLP/HTTP) existent depuis une session précédente, mais aucun `docker-compose` ne fait tourner de collecteur : la brique est câblée mais indémarrable. Le self-hosting de Langfuse a beaucoup changé depuis les premières versions (un simple conteneur) : la v3/v4 exige Postgres (métadonnées), ClickHouse (stockage des traces), Redis/Valkey (file d'ingestion) et un stockage S3 (MinIO, buckets `events`/`media`) en plus des deux conteneurs applicatifs `langfuse-web` et `langfuse-worker`. Ce fichier a été récupéré directement (`raw.githubusercontent.com/langfuse/langfuse`, tag `v4.0.0`, identique à `main`), cloné et lu — pas réinventé de mémoire, qui aurait décrit l'ancienne topologie à un seul conteneur.

Deux questions de topologie ne vont pas de soi : (1) partager le Postgres/Redis du cœur RAGEngine ou en isoler des dédiés ; (2) comment les conteneurs API/worker de RAGEngine atteignent le collecteur sans exposer les briques internes de Langfuse au réseau partagé.

## Décision

**Isolation totale**, à l'identique du choix déjà fait pour RAGFlow (ADR-012 §2) : `docker-compose.observability.yml` porte ses propres conteneurs `langfuse-postgres`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-minio`, tous sur un réseau privé `langfuse-internal` non partagé. Seul `langfuse-web` (l'unique point d'entrée OTLP, `POST /api/public/otel/v1/traces`) rejoint en plus le réseau externe `ragengine`, pour être résolu par nom par l'API/les workers — exactement le rôle que joue `ragflow-cpu` pour RAGFlow. Aucune brique interne de Langfuse n'est publiée sur l'hôte (seul `langfuse-web` l'est, en boucle locale) : même politique que `docker-compose.ragflow.yml` (« seuls les ports applicatifs sortent, jamais les dépendances »).

Provisioning sans étape manuelle : `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`LANGFUSE_INIT_PROJECT_SECRET_KEY` (variables d'amorçage de Langfuse, lues uniquement à la création du projet) reprennent **exactement** `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` — déjà définies pour `LangfuseObservability` (`app/core/config.py`). Une seule paire de clés sert donc les deux rôles : celles qui amorcent le projet côté collecteur sont celles que l'exportateur RAGEngine utilise pour s'authentifier. Condition : les renseigner dans `.env` **avant** le tout premier démarrage (volumes vides) — Langfuse ne relit ces variables qu'à la création initiale de l'organisation/projet.

Écart assumé sur l'image MinIO : l'upstream utilise `cgr.dev/chainguard/minio` sans tag public pinnable (image « rolling »). Remplacée par `pgsty/minio:RELEASE.2026-03-25T00-00-00Z` — déjà choisie et en production dans ce dépôt pour `ragflow-minio` (`docker-compose.ragflow.yml`), donc déjà un choix vérifié plutôt qu'une inconnue supplémentaire.

## Conséquences

- `docker compose -f docker/docker-compose.observability.yml up -d` démarre/arrête indépendamment du cœur ; son absence ne modifie aucun comportement (`NoOpObservability`, déjà vrai avant cet ADR).
- Nouvelles variables dans `.env.example`, préfixées `LANGFUSE_STACK_*` pour les distinguer sans ambiguïté des trois variables applicatives déjà existantes (`LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`) qui, elles, ne changent pas.
- Conséquence acceptée du choix « aucun port interne publié » : les URLs de prévisualisation média de Langfuse (`LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT`) ne sont résolvables que depuis l'intérieur du réseau Docker, pas depuis un navigateur sur l'hôte. Sans impact pour RAGEngine : l'usage est le traçage (spans), jamais l'upload de médias vers Langfuse.
- `LANGFUSE_HOST` (variable déjà existante) doit être positionnée à `http://langfuse-web:3000` dans l'environnement des conteneurs `api`/`worker-*` pour activer l'export — ce document ne le fait pas à leur place : Langfuse reste opt-in comme avant.
