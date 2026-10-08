# API developer guide

How to use the RAGEngine REST API (`/api/v1/…`) from an external consumer (third-party application, integration script). This document explains authentication and the real call order of the two reference flows; it does not duplicate the detail of each endpoint, which the interactive OpenAPI reference already covers exhaustively (§0). Checked against the core OpenAPI (`python -m app.cli.openapi --core`): `backend/tests/test_api_guide.py` fails if this guide cites a route absent from that contract.

**Summary**: two authentication mechanisms coexist — a session cookie (web interface) and an `Authorization: Bearer <key>` API key (developer integrations, reserved for real accounts). Schema A (ingestion) runs `POST .../documents/ingest` → poll `GET /jobs/{job_id}` → `PUT .../precheck/decision` → poll until `completed`; there is **no** separate `POST .../precheck`, the precheck starts automatically at ingestion. Schema B (retrieval) is either `POST /retrieval/search` (plain JSON) or `POST /retrieval/chat` (SSE stream). Every error follows RFC 9457 (`application/problem+json`).

## 0. Exhaustive source of truth

This guide does not claim to list every endpoint, every field or every response code. The complete catalogue, always in sync with the code, is served by the API itself:

- `GET /api/v1/docs-public` — reference documentation (Redoc) for the integration surface (authentication, knowledge bases, documents, jobs, retrieval, providers, evaluations), served **in every environment**. Its raw schema is at `/api/v1/openapi-public.json`, for generating a typed client.
- `GET /api/v1/docs` (interactive Swagger UI), `GET /api/v1/redoc` and `GET /api/v1/openapi.json` (the full schema) are served **in development only**: exposing the whole API surface in production would leak reconnaissance information.

## 1. Authentication

Two mechanisms, resolved in this order by `PrincipalResolver` (`backend/app/services/identity/principal.py`): a valid `Authorization` header takes precedence over the session cookie.

### 1.1. Accounts and sessions (web interface)

```
POST /api/v1/users              # sign-up — {email, password, ...} → account, no session
POST /api/v1/sessions           # sign-in — sets an HttpOnly cookie (SESSION_COOKIE_NAME)
GET  /api/v1/me                 # current principal: identity and role
DELETE /api/v1/sessions/current # sign-out, idempotent
```

### 1.2. API keys (developer integrations)

Reserved for a signed-in account. Issuing a key therefore first requires `POST /users` + `POST /sessions`:

```
POST   /api/v1/api-keys         # {name, scopes?, expires_at?} → plaintext token, ONLY ONCE
GET    /api/v1/api-keys         # paginated list, the token is never shown again (only its prefix is)
DELETE /api/v1/api-keys/{id}    # non-destructive revocation (idempotent)
```

Token format: `rag_<env>_<43 characters>`. The database only stores its SHA-256 digest — the plaintext value in the creation response (`ApiKeyCreated.token`) is **unrecoverable** afterwards, so the client must store it immediately. `scopes` (`ApiKeyCreate.scopes`, default `["*"]`, full access) restricts the key to the listed `<resource>:<action>` scopes, for example `retrieval:search` or `documents:read`; the catalogue is in `backend/app/domain/scopes.py` (ADR-018).

Every request authenticated by key carries:

```
Authorization: Bearer rag_dev_<...>
```

A scheme other than `Bearer`, or an invalid, revoked or expired key, answers `401 Unauthorized`.

## 2. REST convention and error contract

- Prefix `/api/v1/`, plural resources, kebab-case (`knowledge-bases`, `api-keys`).
- Every error — business, Pydantic validation, HTTP, or unexpected — is serialized as `application/problem+json` (RFC 9457) by `app/core/errors.py`, a single envelope for all clients:

  ```json
  {
    "type": "/problems/not-found",
    "title": "Not Found",
    "status": 404,
    "detail": "Base de connaissances introuvable.",
    "instance": "/api/v1/knowledge-bases/…"
  }
  ```

  The `detail` text is written in French.

- Meaningful codes:
  - `202 Accepted` — an asynchronous processing was started (`POST .../documents/ingest`); follow it by polling the job.
  - `409 Conflict` — a state transition was refused (job not cancellable from its current state, demotion of a knowledge base's last `owner`…). The reachable states accompany the response as an extension.
  - `404 Not Found` — including for a resource that exists but is invisible to the principal (a `private` knowledge base the caller is not a member of answers `404`, never `403`, so as not to confirm its existence).
  - `422 Unprocessable Entity` — syntactically correct but semantically invalid request (unknown or unconfigured LLM/embedding model, `rerank=true` without a reranker on the base, effective reranking combined with a non-zero `similarity_threshold` without `acknowledge_threshold_risk`…).
- Cursor-paginated lists (`Page[T]`, `cursor`/`limit` parameters) for documents, jobs, knowledge bases and API keys. Evaluations are not paginated: a run is a manual, costly action, so its volume per base stays small.

## 3. Schema A — Ingestion (real call order)

```
1. POST   /api/v1/knowledge-bases
2. POST   /api/v1/knowledge-bases/{kb_id}/documents/ingest      → 202
3. GET    /api/v1/jobs/{job_id}                                  (poll until awaiting_review)
4. PUT    /api/v1/documents/{document_id}/precheck/decision      (restarts the pipeline)
5. GET    /api/v1/jobs/{job_id}                                  (poll until completed | failed)
```

1. **Create a knowledge base** — `embedding_model_id` is required (real catalogue: `GET /api/v1/providers`, `embeddings` field), `reranker_model_id` is optional. `visibility` (`private` by default, or `public`) governs read/retrieval access without an account.
2. **Ingest a document** — `multipart/form-data`, `file` field (+ optional `filename`, optional `theme`). `content_type`, `size_bytes` and `checksum_sha256` are computed server-side, never accepted from the client. `theme` labels the document to organize it by subject within the base — entered by the user, never inferred; it can be changed afterwards with `PATCH /api/v1/documents/{document_id}` (`{"theme": "…"}`, or `{"theme": null}` to clear it — `filename`, by contrast, cannot be cleared). **Idempotent by checksum**: re-ingesting the same file (same SHA-256) into the same base creates neither an extra document nor an extra job. The response carries `current_job_id`. The preliminary analysis (precheck) starts **automatically** at this call — there is no separate `POST .../precheck` endpoint.
3. **Follow the job** — states `queued → prechecking → awaiting_review → chunking → embedding → indexing → completed` (or `failed`/`cancelled`). The job stops at `awaiting_review`: this is the human intervention point.
4. **Decide the routing** — `optimize_for_tables` (tabular chunking) and `heavy_ocr` (full layout analysis), independent booleans that can be combined. This call moves the job from `awaiting_review` back to `chunking` and runs the rest of the pipeline on its own.
5. **Poll to the end** — `completed` (document indexed and searchable) or `failed` (reason carried by the job).

Useful extras: `GET /api/v1/knowledge-bases/{kb_id}/documents` and `GET /api/v1/knowledge-bases/{kb_id}/jobs` (paginated lists); `DELETE /api/v1/jobs/{job_id}` cancels a job **from `queued` or `awaiting_review` only** (`409` otherwise) and returns the document to `pending`, so it can be ingested again.

## 4. Schema B — Retrieval

Both endpoints are **root** endpoints (no `{kb_id}` in the path): `knowledge_base_id` is a field of the request body.

### 4.1. Search — `POST /api/v1/retrieval/search`

```json
{
  "knowledge_base_id": "…",
  "query": "…",
  "top_k": 10,
  "rerank": null,
  "similarity_threshold": null,
  "acknowledge_threshold_risk": false,
  "document_ids": null,
  "languages": null,
  "content_types": null,
  "themes": null,
  "ingested_after": null,
  "ingested_before": null
}
```

Plain JSON response (`{"results": [...]}`), native RAGFlow hybrid search (dense + BM25).

**Metadata filters** (ADR-045, resolved on the RAGEngine side before the engine call — RAGFlow natively filters only by document subset, never by metadata): `languages` (ISO 639-1 codes, matched against `detected_languages` — ADR-003), `content_types` (exact MIME types, e.g. `application/pdf`), `themes` (`Document.theme` labels, entered by the user, never inferred — matched against the field set at ingestion or via `PATCH /api/v1/documents/{document_id}`), `ingested_after`/`ingested_before` (bounds on the document's creation date, UTC). They are combined with AND between themselves and with `document_ids` if it is also provided. **Semantics distinct from `document_ids`**: these are descriptive filters, not the citation of specific objects — a combination that excludes no document answers an empty list, never an error (unlike a `document_ids` citing an identifier absent from the base, which answers `422`).

`rerank` has a **smart default** (ADR-026): omitted or `null`, the service enables it automatically if the base declares a reranker (`reranker_model_id`), otherwise it stays off — the same behaviour as before for a base without a reranker. Explicit `true`/`false` are always respected as given. `rerank=true` (explicit or resolved by default) on a base with no configured `reranker_model_id` answers `422` — this case can now only happen with an explicit `true`, since default resolution never turns it on by itself.

Combining **effective** reranking (after default resolution) with a non-zero `similarity_threshold` (`!= 0.0`) answers `422` unless `acknowledge_threshold_risk: true` is set — reranking changes the scale of the combined score, and a threshold tuned without a reranker can empty the results once the reranker is active (measured: recall@5 drops from 0.889 to 0.593). A `similarity_threshold` of `null` or `0.0` never triggers this guard, whether reranking is effective or not.

Access: without an account on a `public` base (if the deployment allows it), `viewer` member or higher on a `private` base the caller belongs to; every other case answers `404`.

### 4.2. Generative chat — `POST /api/v1/retrieval/chat`

Same filter fields as the search, plus `messages` (history, which must end with a `user` turn) and `llm_provider_id` (**required** — no implicit fallback; real catalogue: `GET /api/v1/providers`, `llm` field). The response is a `text/event-stream` (SSE) stream, not JSON:

| Event | When | `data` |
|---|---|---|
| `sources` | once, before any text | JSON array of the retrieved passages |
| `token` | one per generated fragment | JSON string (the fragment) |
| `error` | only if the LLM fails **after** the first byte is sent | minimal JSON `ProblemDetail` — then the connection closes, without `done` |
| `done` | once, at the end of a successful stream | `{}` |

Any error *before* the first byte (base not found/invisible, insufficient role, reranking without a reranker, effective reranking + non-zero threshold without `acknowledge_threshold_risk`, `document_ids` outside the base, unknown or unconfigured `llm_provider_id`) answers a regular RFC 9457 `ProblemDetail` — never an SSE event. This lets the client tell unambiguously a refusal of the request from a failure that occurred mid-generation.

### 4.3. Personal LLM key and server key — `PUT`/`DELETE`/`GET /api/v1/me/llm-keys`

A signed-in account can declare its own API key per provider (`anthropic`/`openai`/`groq`): `PUT /me/llm-keys/{provider}` (`{"api_key": "..."}`), `DELETE /me/llm-keys/{provider}`, `GET /me/llm-keys` (provider and timestamps, never the key itself — no route returns it, including right after it is declared).

As soon as a personal key exists for the provider of `llm_provider_id`, `POST /retrieval/chat` **uses it in preference to the server key** — even if no server key is configured for that provider, which makes a model otherwise `available: false` in the catalogue (`GET /providers`) usable for that specific account. Without a declared personal key, the server key (`ANTHROPIC_API_KEY`…) pays for the call, or `422` if it is absent. **By default, no cap and no imposed model**: the requested model is respected. An operator can enable explicit caps (`RAGENGINE_PLATFORM_LLM_USER_DAILY_LIMIT`, `RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT`, `RAGENGINE_PLATFORM_LLM_DAILY_BUDGET_USD`) and an imposed model (`RAGENGINE_PLATFORM_LLM_MODEL_ID`): beyond a cap, `429` (`retry_after_seconds` in the extensions). See `.env.example`.

## 5. Provider catalogue

```
GET /api/v1/providers
```

Without authentication (a capability catalogue, not user data — a visitor must be able to compose a configuration before creating an account). Returns the embeddings, rerankers, LLMs and the observability state actually configured in this environment, each annotated `available` (configuration present, **never** a network ping — a provider with `available=true` can still fail at run time).

## 6. Knowledge bases and permissions

`POST /api/v1/knowledge-bases`, `GET/PATCH/DELETE /api/v1/knowledge-bases/{kb_id}` and member management (`PUT/DELETE /api/v1/knowledge-bases/{kb_id}/members/{user_id}`) follow the role matrix detailed in `docs/domain-model.md` §4 (per-base roles: `viewer`/`editor`/`owner`; platform roles: `member`/`admin`) — not duplicated here. The general rule to remember: a resource invisible to the current principal always answers `404`, never `403`.

## 7. Evaluations (ADR-028)

```
POST /api/v1/knowledge-bases/{kb_id}/evaluations   # editor+ role — consumes a real LLM budget
GET  /api/v1/knowledge-bases/{kb_id}/evaluations
GET  /api/v1/knowledge-bases/{kb_id}/evaluations/{evaluation_id}
GET  /api/v1/evaluations/summary                   # root — cross-KB aggregate, RBAC scope
```

Generates, then scores (retrieval recall + LLM judgement), a sample of reference questions/answers over indexed documents (`sample_size`, default 5). Two paths under the same `POST`, depending on `sample_size`:

- `≤ 20` (`MAX_EVALUATION_SAMPLE_SIZE`): **synchronous**, `201` with the complete run — the call blocks until the run ends, `sample_size` bounds the cost, no Celery job.
- `> 20`: **asynchronous** (ADR-028), `202` with a run `status: "pending"` and no items, executed by a Celery job (`run_async_evaluation`); follow its resolution through `GET .../evaluations/{evaluation_id}` (`status` becomes `completed` or `failed`). Bounded by the deployed ceiling (`Settings.evaluation_async_max_sample_size`, 200 by default) — `422` beyond it.

`422` if evaluation is not enabled on the base (`evaluation_enabled`), if `llm_provider_id` is unknown/unconfigured, or if `sample_size` exceeds the effective ceiling of the chosen path; `409` if no indexed document is available.

`GET /evaluations/summary` answers "is our retrieval good overall": mean recall@k/MRR/judge score, one entry per visible base with at least one evaluation item (same scope as `GET /api/v1/knowledge-bases`), plus an `overall` aggregate recomputed over the union of the items of all those bases — never an average of the per-base entries, so as not to weight each base equally whatever its volume.
