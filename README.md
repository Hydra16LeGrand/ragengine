# RAGEngine

A sovereign, self-hosted, turnkey RAG (retrieval-augmented generation) platform for organisations.
RAGEngine wraps a complete pipeline — ingestion, interactive precheck with human validation,
adaptive chunking, hybrid indexing (dense + BM25), reranked retrieval — behind a versioned REST API
for developers and a web interface for end users.

**License: source-available, not open source.** Apache-2.0 with the Commons Clause: free to use,
modify and run, including inside companies and for clients; selling it, or offering it as a paid
hosted service, requires a commercial license. See [License](#license).

## Quick start

Three paths, one command each. Pick by what you want to see and what your machine has.

| | `demo` | `cpu` | `gpu` |
|---|---|---|---|
| Command | `scripts/quickstart.sh demo` | `scripts/quickstart.sh cpu` | `scripts/quickstart.sh gpu` |
| What it starts | PostgreSQL, Redis, API, worker, web interface | the full chain: core, RAGFlow + Elasticsearch, BGE-M3 and bge-reranker-v2-m3 on CPU | the same, models on an NVIDIA GPU |
| What you can do | explore the API and the interface: accounts, knowledge bases, upload, precheck, job tracking, a search | everything, with real parsing, chunking, embeddings, hybrid search and reranking | everything, at full speed |
| Search quality | **none**: a stand-in engine does lexical matching on plain-text files | real | real |
| RAM | ~3 GB free | **~16 GB free recommended** (estimate, not measured) | ~12 GB free, plus 12 GB of VRAM |
| Needs | Docker Compose v2 | Docker Compose v2, [uv](https://docs.astral.sh/uv/) | the same, plus an NVIDIA GPU and the [Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit) |
| First start | a few minutes (builds the images) | long: images plus ~4.4 GiB of model weights | long: the same download |

Each command is idempotent, prints no secret, never deletes data, and ends with the URL to open
(`http://127.0.0.1:8090`). `scripts/quickstart.sh stop demo|cpu|gpu` stops the containers and
keeps the data.

- **`demo` is a tour, not a RAG system.** It runs the stand-in ingestion engine
  (`INGESTION_ENGINE=fake-shared`, [ADR-071](docs/adr/071-chemins-d-essai-sans-ragflow-et-sans-gpu.md)):
  no semantic search, no OCR, no embeddings; only UTF-8 text files are accepted, and search matches
  words. It exists to try the API and the interface in minutes on an ordinary laptop. The engine
  refuses to start in staging or production.
- **`cpu` is not tested end to end.** Its configuration is valid (`docker compose config`) and the
  TEI CPU image is checked against TEI's sources, but it has **not been run on a machine without a
  GPU**. Expect ingestion and reranking to be much slower than on a GPU. RAGFlow and Elasticsearch
  are heavy: Elasticsearch alone is capped at 4 GB.
- **`gpu` is the reference setup** the project is developed and run on.

The manual, step-by-step equivalent of `cpu` and `gpu` is described below.

## What it does

- **Knowledge bases**: create bases, upload documents (PDF, Office, spreadsheets, images, text),
  manage members and roles (RBAC), API keys, organisations.
- **Precheck (human in the loop)**: every document is analysed before indexing — scanned or
  text PDF, column layout, tables, figures, detected languages — and the user confirms or changes
  the proposed processing before anything is chunked.
- **Ingestion pipeline**: asynchronous jobs (`queued → prechecking → awaiting_review → chunking →
  embedding → indexing → completed`) with a quality guard and manual retry of failed jobs.
- **Retrieval**: hybrid search with optional reranking and metadata filters, a Playground with
  multi-turn chat on top of external LLMs, automatic evaluation runs.
- **Multilingual by design**: the reference corpus is French, the models (BGE-M3,
  bge-reranker-v2-m3) and the OCR are multilingual, and every document's languages are detected at
  precheck time.

## What RAGEngine does not include

RAGEngine is the self-hosted core. Three features exist only in the hosted service the author
built on it, and are not part of this repository: **public chatbots** (publishing a knowledge base
behind a public page), **guest mode** (trying the product without an account, with ephemeral spaces
and anonymous quotas) and the **administration console** (platform-wide dashboard and `/admin`
routes). Here every action requires an account; a visitor is sent to the sign-in page. The core
exposes extension points (ADR-069) so that such features can be added as plugins without changing
it.

## Architecture

```
                 ┌──────────────────────────────┐
  browser ──────▶│ Traefik  (frontend + /api)   │◀── optional Cloudflare Tunnel
                 └───────┬───────────────┬──────┘
                         │               │
              ┌──────────▼───┐   ┌───────▼─────────────────────────────┐
              │ React SPA    │   │ FastAPI facade  /api/v1             │
              │ (nginx)      │   │ routers → services → ports/adapters │
              └──────────────┘   └───┬───────────┬───────────┬─────────┘
                                     │           │           │
                         ┌───────────▼──┐  ┌─────▼─────┐  ┌──▼────────────────────┐
                         │ PostgreSQL   │  │ Redis     │  │ External LLM APIs     │
                         │ (metadata)   │  │ (Celery)  │  │ Anthropic/OpenAI/Groq │
                         └──────────────┘  └─────┬─────┘  └───────────────────────┘
                                                 │
                              ┌──────────────────┴────────────────┐
                              │ Celery workers                    │
                              │  queue default (CPU)  queue gpu=1 │
                              └──────────────────┬────────────────┘
                                                 │ HTTP only, through the engine adapter
                     ┌───────────────────────────▼──────────────────────────┐
                     │ RAGFlow (slim, CPU) — parsing, chunking, hybrid search│
                     │ + Elasticsearch, MySQL, MinIO, Valkey (private)       │
                     └───────────────────────────┬──────────────────────────┘
                                                 │
                     ┌───────────────────────────▼──────────────────────────┐
                     │ GPU: Text Embeddings Inference ×2                    │
                     │ BGE-M3 (embeddings) · bge-reranker-v2-m3 (reranking)  │
                     └──────────────────────────────────────────────────────┘
       optional: Langfuse (self-hosted) through OpenTelemetry — no-op when absent
```

- **Hexagonal architecture**: every replaceable component (engine, embeddings, rerankers, LLMs,
  observability, storage) sits behind a port in `backend/app/providers/`, resolved by a factory.
- **RAGFlow is the single ingestion/indexing/retrieval engine**, consumed only through its HTTP API
  by one adapter. RAGEngine adds accounts, permissions, the precheck, job tracking, quotas,
  evaluation and the user interface.
- **No GPU work in the API process**: local models live in dedicated inference containers; heavy
  jobs go through Celery with a `gpu` queue at concurrency 1.
- **No local generative LLM**: answer generation uses external APIs.

Details: [`CONTRIBUTING.md`](CONTRIBUTING.md) (rules), [`docs/adr/`](docs/adr/README.md)
(architecture decisions).

## Hardware requirements

These are the real requirements of the reference machine, not theoretical minimums.

| Resource | Reference | Notes |
|---|---|---|
| NVIDIA GPU | 12 GB VRAM (RTX 4080 Laptop) | Runs the two Text Embeddings Inference (TEI) containers. Measured footprint and budget: ADR-051, `CONTRIBUTING.md` §2. The pinned TEI image tag (`89-1.9`) targets Ada Lovelace GPUs (compute capability 8.9, RTX 40xx); other GPU generations need the matching TEI tag in `docker/docker-compose.gpu.yml`. |
| NVIDIA driver + [Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit) | — | Required to expose the GPU to Docker. |
| RAM | 32 GB | The full stack is API, workers, PostgreSQL, Redis, RAGFlow with Elasticsearch (capped at 4 GB by `RAGFLOW_ES_MEM_LIMIT`), MySQL, MinIO and TEI. The reference machine runs a development and a production environment side by side, which uses most of its 32 GB. |
| Disk | — | Docker images (RAGFlow, Elasticsearch, TEI…), ~4.4 GiB of model weights downloaded on first start, and data volumes. |
| OS | Linux with Docker Engine and Docker Compose v2 | `docker compose`, not `docker-compose`. |

**Without a GPU**: use `scripts/quickstart.sh cpu` (TEI CPU images, `docker/docker-compose.cpu.yml`)
for the real chain, slower and with a lot of RAM (not tested end to end on a machine without a GPU),
or `scripts/quickstart.sh demo` for a lightweight tour with no search quality at all. See
[Quick start](#quick-start).

## Manual setup (development)

This is what `scripts/quickstart.sh cpu|gpu` automates. Prerequisites: Docker Engine with Compose v2; for the GPU stack, the NVIDIA Container Toolkit
(`docker run --rm --gpus all ubuntu nvidia-smi` must work); [uv](https://docs.astral.sh/uv/) for
the backend tooling; Node.js ≥ 20.19 and npm for the frontend dev server.

The Compose files are deliberately separate (each has its own project name) and share the
Docker network `ragengine`, created by the first one. Start them in this order.

### 1. Configure

```bash
cp .env.example .env
```

Set at least: `RAGENGINE_SECRET_KEY` (`change-me` is accepted only with
`RAGENGINE_ENV=development`), `POSTGRES_PASSWORD`, `RAGFLOW_BOOTSTRAP_EMAIL` /
`RAGFLOW_BOOTSTRAP_PASSWORD`, and one LLM key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`GROQ_API_KEY`) for the Playground chat and evaluations. Every variable is documented in
`.env.example`.

### 2. Core: API, PostgreSQL, Redis, CPU worker

```bash
./scripts/bootstrap.sh up
docker compose --env-file .env -f docker/docker-compose.yml --profile workers up -d
```

The second command starts the Celery worker of the `default` queue, without which ingestion never
leaves `queued`.

### 3. GPU inference (embeddings + reranking)

```bash
docker compose --env-file .env -f docker/docker-compose.gpu.yml up -d --build
# no GPU: docker compose --env-file .env -f docker/docker-compose.cpu.yml up -d   (instead, never both)
```

Starts `inference-embedding` (BGE-M3), `inference-reranker` (bge-reranker-v2-m3) and `worker-gpu`.
On first start the weights are downloaded from Hugging Face: wait until both TEI containers are
`healthy`.

### 4. RAGFlow engine

```bash
docker compose --env-file .env -f docker/docker-compose.ragflow.yml up -d
# once ragflow-cpu is healthy (idempotent, safe to re-run):
uv run --project backend python scripts/bootstrap_ragflow.py
```

The bootstrap script creates the RAGFlow service account, prints an API key to copy into
`RAGFLOW_API_KEY` in `.env` (then restart `api` and the workers), and registers the two TEI
instances as model providers.

### 5. Observability (optional)

```bash
docker compose --env-file .env -f docker/docker-compose.observability.yml up -d
```

Fill in the `LANGFUSE_STACK_*` variables before the first start. Without Langfuse, tracing is a
no-op.

### 6. Check and open the interface

```bash
curl http://localhost:8000/api/v1/health
cd frontend && npm ci --legacy-peer-deps && npm run dev     # http://localhost:5173
```

The interactive API reference is at `http://localhost:8000/api/v1/docs` in development; a filtered
developer reference is served in every environment at `/api/v1/docs-public`.

| Service | Host port (127.0.0.1) |
|---|---|
| API | 8000 |
| PostgreSQL | 5433 |
| Redis | 6380 |
| TEI embedding / reranker | 9997 / 9998 |
| RAGFlow API / admin UI | 9390 / 9480 |
| Langfuse (optional) | 3000 |

### Running the tests

```bash
cd backend && uv sync --extra dev
uv run pytest -q -m "not live_ragflow"   # needs the dev PostgreSQL and Redis containers (step 2)
cd ../frontend && npm ci --legacy-peer-deps && npx vitest run
```

Tests marked `live_ragflow` (`backend/tests/services/test_retrieval_live.py`) drive a real RAGFlow +
TEI stack; they fail without one, hence the marker filter above.

## Production

Production runs as a separate set of Compose projects (`ragengine-prod`, `ragflow-prod`) with its
own volumes and host ports, so it can share a machine with a development stack. Traefik serves
the static frontend and routes `/api` to the API on a single host name; every port stays bound to
`127.0.0.1`. Public exposure is up to you: the optional `tunnel` profile of
`docker/docker-compose.traefik.yml` runs a Cloudflare Tunnel (outbound only), or put your own
reverse proxy with TLS in front of Traefik.

```bash
bash scripts/init-prod-env.sh   # .env.prod → .env.prod.local (git-ignored); generates missing secrets
$EDITOR .env.prod.local         # host name in RAGENGINE_CORS_ORIGINS, LLM keys, optional tunnel token
bash scripts/start-prod.sh      # starts the production stack and bootstraps RAGFlow (idempotent)
PUBLIC_HOST=rag.example.com bash scripts/deploy-prod.sh   # later updates: build, pin image tag, redeploy, verify
```

`deploy-prod.sh` refuses to run with modified tracked files and never recreates PostgreSQL, Redis,
RAGFlow or the inference containers. Backups: `scripts/backup-postgres.sh`,
`scripts/backup-documents.sh` and a systemd timer (`scripts/install-backup-timer.sh`, ADR-067).
Day-to-day operations: [`docs/runbook-deploiement.md`](docs/runbook-deploiement.md).
Promote the first platform administrator with
`docker exec ragengine-prod-api python -m app.cli.promote_admin <email>`.

Before exposing a public instance: set a monthly spending cap on every LLM key in the provider
console (application quotas count calls, not money), and monitor availability externally.

## Documentation

The reference documentation is in English; the ADRs, the code comments and the archives are in French (the project's history). Start from [`docs/README.md`](docs/README.md).

- [`docs/api-guide.md`](docs/api-guide.md) — authentication, ingestion and retrieval workflows,
  error format.
- [`docs/extending.md`](docs/extending.md) — **start here to build on RAGEngine**: the three
  extension points (provider, ingestion engine adapter, plugin), each with a worked example, and the
  full checklist for adding a provider.
- [`docs/providers-architecture.md`](docs/providers-architecture.md),
  [`docs/domain-model.md`](docs/domain-model.md),
  [`docs/ingestion-engine-architecture.md`](docs/ingestion-engine-architecture.md) — architecture
  references.
- [`docs/adr/`](docs/adr/README.md) — one architecture decision record per topic.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md). Every contribution requires agreeing to the
[Contributor License Agreement](CLA.md). Report vulnerabilities privately: see
[`SECURITY.md`](SECURITY.md).

## License

RAGEngine is **source-available** under the [Apache License 2.0 with the Commons Clause](LICENSE).
You may use, modify and redistribute it free of charge, for any purpose, including commercial use
inside your own organisation; you may not sell RAGEngine itself, or offer it as a paid hosted
service, without a commercial license from the author. Because of that restriction it is not an
open-source license as defined by the Open Source Initiative.

The author grants an **additional permission**, stated in
[`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md): installing, configuring, integrating or adapting
RAGEngine for a client, for a fee, or embedding it as an internal component of your own product,
does not require a commercial license. Everything else that needs one, with examples in both
directions, is listed there too.

Third-party components keep their own licenses (RAGFlow: Apache-2.0; BGE-M3 and
bge-reranker-v2-m3: MIT; Elasticsearch, pulled as a separate Docker image by RAGFlow's Compose
file: its own license).
