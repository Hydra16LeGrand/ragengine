# Contributing to RAGEngine

Thank you for your interest. These are the development rules of the repository. They are
binding for every change, backend or frontend. Section numbers are stable: code comments
and architecture decision records (ADRs) cite them as `CONTRIBUTING.md §N`.

The reference documentation (`docs/`, `.env.example`) is written in English. The ADRs, the
code comments and the docstrings are written in French, as the history of the project. New
contributions may be written in English or French; comments are translated over time, never in
bulk.

## Before you start: the Contributor License Agreement

RAGEngine is distributed under Apache-2.0 with the Commons Clause, and the author also grants
commercial licenses (see [`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md)). To keep that possible
for code you contribute, **every contributor must agree to the [CLA](CLA.md) before a pull request
can be merged**. Add this line to the description of your first pull request:

> I have read and agree to the RAGEngine Contributor License Agreement (CLA.md), version 1.0.

If you contribute on behalf of an employer, make sure you are authorised to do so (see CLA §4).

## 1. Product context

RAGEngine is a sovereign, modular, turnkey enterprise RAG system. It wraps a complete pipeline —
ingestion, interactive precheck (layout analysis), adaptive chunking, hybrid indexing (dense +
BM25), retrieval — behind:

- a **versioned REST API** (FastAPI facade) for developers (API-key authentication);
- a **polished web interface** for users (upload, human-in-the-loop precheck, knowledge-base
  dashboard, retrieval playground).

Two reference workflows: **Workflow A** (ingestion and indexing with human validation of the
precheck) and **Workflow B** (retrieval and exploration through the API or the Playground).

The corpus is mainly **French**, but the system is **multilingual by design**: multilingual
embedding and reranking models (BGE-M3), multilingual OCR, and **automatic language detection of
every document at precheck time** (reported to the user and stored with the indexing metadata). See
ADR-003.

## 2. Hardware constraints — non-negotiable

| Resource | Budget | Exclusive use |
|---|---|---|
| NVIDIA GPU (reference: RTX 4080 Laptop) | **12 GB VRAM** | Visual extraction (OCR/layout), embeddings, local reranking |
| System RAM | 32 GB | Docker services and workers |
| LLM generation | **External** | Third-party APIs (Anthropic, OpenAI, Groq…) — never a local generative LLM |

Derived rules:

- **No GPU work ever runs inside the FastAPI process.** Local models (embedding and reranker) live
  in a **dedicated GPU inference service** (Text Embeddings Inference containers) used both by
  RAGFlow (indexing/retrieval) and by the facade, with bounded concurrency and batch size: that
  service is the VRAM semaphore. Heavy work driven by RAGEngine (precheck, OCR) goes through
  Celery/Redis, queue `gpu` with `concurrency=1`; CPU work (light parsing, I/O, orchestration,
  RAGFlow polling) goes to the `default` queue.
- Local models are loaded once per service (singleton), never per request.
- Any new local model must document its estimated VRAM footprint in its pull request, and the sum
  of co-resident models must stay below 10 GB (2 GB margin).
- Embedding batch sizes are configurable through the environment, with conservative defaults.

## 3. Architecture — structural rules

**Hexagonal architecture (ports and adapters).** The domain core depends on no concrete
technology.

1. **Every replaceable component goes through an interface (port)** in
   `backend/app/providers/<domain>/base.py` (ABC or Protocol), with a configuration-driven
   **factory** (API payload or settings) that resolves the concrete implementation:
   - `engine/`: **RAGFlow (Slim) is THE single ingestion/indexing/retrieval engine** (ADR-001) —
     no competing in-house pipeline. It is still consumed exclusively through its adapter: the
     domain only knows the port. Hybrid storage (dense + BM25) is **delegated to RAGFlow**
     (Elasticsearch or Infinity document engine, chosen at deployment): RAGEngine never plugs a
     vector store directly.
   - `embeddings/`: local multilingual models served on the GPU (default: BGE-M3) and third-party
     APIs — configurable per knowledge base, declared to RAGFlow.
   - `rerankers/`: local multilingual reranker (default: bge-reranker-v2-m3), passthrough.
   - `llm/`: Anthropic, OpenAI, Groq (Playground answer generation, evaluation).
   - `observability/`: Langfuse through OpenTelemetry (can be switched on or off), no-op by
     default.
2. **FastAPI routers contain no business logic**: they validate (Pydantic), delegate to services
   (`app/services/`) and serialise. Services orchestrate ports. Adapters are the only modules that
   import concrete SDKs.
3. **RAGFlow (Slim Docker)** is consumed exclusively through its HTTP API, wrapped in
   `providers/engine/ragflow/`. No other module imports or knows about RAGFlow. RAGFlow is used
   **fully, as designed** (datasets, parsers, chunking templates, native hybrid search): do not
   bypass or re-implement its internal building blocks.
4. **Optional components** (Langfuse, evaluation generation): a missing configuration must never
   make a request fail. Null Object / no-op pattern.
5. Application metadata (knowledge bases, documents, jobs, API keys, provider configurations)
   lives in **PostgreSQL** (SQLAlchemy 2.x + Alembic). Application state never lives in RAGFlow
   alone.

## 4. REST API conventions — strict

- Prefix: `/api/v1/`. Plural, kebab-case resources. No verbs in URLs except explicit non-CRUD
  actions as sub-resources.
- Canonical endpoints:
  - `POST /api/v1/knowledge-bases` · `GET/PATCH/DELETE /api/v1/knowledge-bases/{kb_id}`
  - `POST /api/v1/knowledge-bases/{kb_id}/documents/ingest` (upload → asynchronous job)
  - `PUT /api/v1/documents/{document_id}/precheck/decision` (human-in-the-loop routing; the
    precheck itself starts automatically at ingestion, there is no separate `POST` for it, and its
    report is read from the document, `GET /api/v1/documents/{document_id}`)
  - `GET /api/v1/jobs/{job_id}` (ingestion tracking, states:
    `queued | prechecking | awaiting_review | chunking | embedding | indexing | completed | failed`)
  - `POST /api/v1/retrieval/search` (hybrid search: `top_k`, filters, rerank on/off)
  - `GET /api/v1/providers` (catalogue of available components and their capabilities)
- Errors: RFC 9457 (Problem Details), one envelope. `202` for every accepted asynchronous
  operation, `409` for job state conflicts.
- Authentication (ADR-005): **user accounts and roles (RBAC)**; every action requires signing in
  (there is no guest mode in RAGEngine, ADR-069). Developers use `Authorization: Bearer <api_key>`; keys are
  hashed in the database (never stored in clear) and attached to an account.
- Any breaking change ⇒ a new API version, never a silent mutation of `/v1`.

## 5. Repository layout

```
RAGEngine/
├── README.md · CONTRIBUTING.md · LICENSE · .env.example
├── docker/                  # Orchestration — single source of truth for deployment
│   ├── docker-compose.yml           # core: api, workers, redis, postgres
│   ├── docker-compose.ragflow.yml   # RAGFlow slim + Elasticsearch
│   ├── docker-compose.gpu.yml       # GPU services (TEI, gpu worker)
│   ├── docker-compose.traefik.yml   # reverse proxy + production frontend (+ optional tunnel)
│   └── docker-compose.observability.yml  # Langfuse (optional)
├── backend/
│   ├── pyproject.toml       # uv, Python 3.12
│   ├── app/
│   │   ├── main.py · core/ (config, security, otel, logging)
│   │   ├── api/v1/          # routers only
│   │   ├── domain/          # entities, Pydantic schemas, enums, domain errors
│   │   ├── services/        # use cases / orchestration
│   │   ├── providers/       # ports + adapters (see §3)
│   │   ├── ingestion/       # precheck, chunking profiles, pipeline steps
│   │   ├── workers/         # Celery app, tasks, queue routing
│   │   └── db/              # SQLAlchemy models, Alembic migrations
│   └── tests/               # mirrors app/, pytest
├── frontend/                # React + TypeScript SPA
│   └── src/
│       ├── design/          # tokens (colours, type, spacing), light/dark themes
│       ├── components/      # in-house UI components
│       ├── features/        # knowledge-bases, ingestion, playground, settings…
│       └── api/             # typed client generated from the backend OpenAPI schema
├── scripts/                 # bootstrap, operations, backups
└── docs/adr/                # Architecture Decision Records (mandatory, see §9)
```

## 6. Backend rules (Python)

- Python 3.12, dependencies managed with **uv**, lint and format with **ruff**, strict typing
  checked by **mypy** (no unjustified `Any`).
- Pydantic v2 for every I/O schema. `pydantic-settings` for configuration — **no secret and no URL
  hard-coded**, everything comes from the environment (keep `.env.example` up to date with every
  new variable).
- Async by default in FastAPI; blocking or GPU work goes to a Celery task.
- Structured (JSON) logging through `core/logging.py`; no `print`.
- Tests: pytest + httpx. Concrete adapters are tested against fakes or containers; services
  against mocked ports. A feature without tests is not done.

## 7. Frontend rules

- React + strict TypeScript, Vite. TanStack Query for server state (job polling), API client
  generated from the OpenAPI schema.
- **Own visual identity**: high-end light/dark theme driven by the tokens in `src/design/`.
  Geometric sans-serif typography. No generic "AI template" look: no copy-pasted component kits
  without art direction.
- Careful asynchronous states: animated skeletons during precheck, step-by-step progress during
  indexing (mapped to the job states), designed empty states.
- No UI strings scattered in components: labels are centralised (FR/EN i18n).
- Tests: Vitest + React Testing Library, `X.test.tsx` next to `X.tsx` (ADR-032). A feature without
  tests is not done here either, and the test targets the asynchronous seam — the screen once the
  data has arrived — not only the pure function it calls.

## 8. Git and commits

- [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`,
  `docs:`…). One logical unit per commit; no catch-all commits.
- Pull requests target the default branch, pass CI (lint, types, tests, secret scan) and carry the
  CLA statement (see the top of this document).
- Never commit a secret: `.env` and every `.env.*.local` file stay out of git. `.env.example`,
  `.env.dev` and `.env.prod` are versioned **templates** with placeholders only.

## 9. Working practices

- Any non-trivial architecture decision (library choice, change to a port contract, chunking
  strategy) ⇒ a short ADR in `docs/adr/` (context, decision, consequences). Check
  [`docs/adr/README.md`](docs/adr/README.md) first: the decision may already exist.
- A pipeline or state machine has **one owner of its end-to-end consistency**. When you change
  who triggers what, in which order, at which transition, list every trigger point and test each
  one: a missing dispatch between two "independent" changes is the classic failure.
- Verify behaviour empirically, not only with linters: ordering, races, cross-process contracts and
  the real behaviour of a library are invisible to ruff, mypy and unit tests alone.
- Bounded work: batch processing is done in bounded slices, never in one unbounded burst.
- Prefer what already exists (a dependency, a service) over a new one when it is sufficient.
- Measurement campaigns (chunking quality, retrieval quality) record their results and the
  decision they led to in the ADR that uses them.

## Development setup

See [`README.md`](README.md) for the quick start. Before opening a pull request:

```bash
# backend
cd backend && uv sync --extra dev
uv run ruff check app tests && uv run ruff format --check app tests
uv run mypy app
uv run pytest -q -m "not live_ragflow"   # needs the dev PostgreSQL and Redis containers (README)

# frontend
cd frontend && npm ci --legacy-peer-deps
npm run lint && npx tsc --noEmit && npx vitest run
```
