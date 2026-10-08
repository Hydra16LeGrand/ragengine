# RAGEngine domain model — v1

Normative reference of Phase 1. Every implementation (SQLAlchemy models, Pydantic schemas, routers) conforms to it. If it contradicts an implementation hunch, this document prevails; deviations are discussed in an ADR.

## 0. Cross-cutting conventions

- **Identifiers**: `UUID`, column `id`, primary key, `server_default=gen_random_uuid()` (native to PostgreSQL 13+, no extension). Never an auto-incremented integer exposed. Exception: `KnowledgeBaseMember` has no `id` column — the `(knowledge_base_id, user_id)` pair **is** the primary key, which by construction forbids two concurrent roles for the same pair.
- **Timestamps**: `TIMESTAMPTZ` in UTC, `created_at` / `updated_at` on every table (server defaults, `onupdate` for `updated_at`).
- **Naming**: plural snake_case tables (`knowledge_bases`), snake_case columns. Enumerations are named PostgreSQL types.
- **Deletion**: physical deletion with cascade for `KnowledgeBase` and `Document`. Real accounts are never physically deleted — `is_active = false`. A `User` is physically deleted only by the purge of expired guests (§4); that deletion then cascades to everything the user owns (`ON DELETE CASCADE` from `users` to sessions, API keys, owned bases, memberships, and transitively the documents/jobs of the owned bases). **Point of attention for any future administration feature**: an endpoint that physically deleted a real account would silently erase all of its knowledge bases through this same cascade — deleting a real account must remain `is_active = false`, never a `DELETE`.
- **Pagination**: by cursor, never by offset. Cursor = base64url of `(created_at, id)`, ordering `created_at DESC, id DESC`. Request `?limit=<1..100, default 50>&cursor=<opaque>`. Response: `{"data": [...], "next_cursor": "..." | null}`.
- **External references**: every key belonging to a third-party system is suffixed `_external_id` (e.g. `ragflow_dataset_external_id`), nullable, never used as an internal join key.

---

## 1. Identity and access

### `User`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `email` | text, nullable | lower-cased by the service; functional unique index on `lower(email)`. No `citext`: we avoid depending on an extension |
| `password_hash` | text, nullable | argon2id; `NULL` for a guest |
| `display_name` | text | |
| `role` | enum `UserRole` | `admin` \| `member` — **platform** role |
| `is_guest` | bool | `true` = ephemeral space (ADR-009) |
| `is_active` | bool | logical deactivation |
| `expires_at` | timestamptz, nullable | non-`NULL` for guests only |
| `last_login_at` | timestamptz, nullable | |

Constraints: a guest has `email IS NULL AND password_hash IS NULL AND expires_at IS NOT NULL`; a real account has the opposite. To be expressed as a `CheckConstraint`, not only in Python.

**A guest is an ephemeral `User`**, not a parallel entity (ADR-009). Elevation to a real account is an update of the row (sets `email`/`password_hash`, `is_guest = false`, `expires_at = NULL`): all the work already produced is kept without any data migration.

### `KnowledgeBaseMember`

Association table carrying the role **per knowledge base**.

| Column | Type | Notes |
|---|---|---|
| `knowledge_base_id` | UUID FK → `knowledge_bases` | composite PK |
| `user_id` | UUID FK → `users` | composite PK |
| `role` | enum `MemberRole` | `owner` \| `editor` \| `viewer` |

The creator of a KB is its `owner`. A KB always has at least one `owner`: an **application-level** invariant, not expressible as a simple SQL `CHECK` (it concerns a set of rows, not one row). No code removes a member yet; the future service that implements it MUST refuse to remove the last `owner` of a base — no workaround accepted (trigger, reconciliation task).

### `Organization` (ADR-022)

The tenant: ownership boundary of knowledge bases, future carrier of the quotas (Axis 2) and billing (Axis 3) of the Premium backlog (ADR-021).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `name` | text | |
| `slug` | text | unique **globally** — unlike a KB slug, which is unique only per owner |

Every account receives a personal organization on creation — real account (`UserService.register`) **and** guest (`SessionService.open_guest_space`) — without which it could not create any base (`knowledge_bases.organization_id` is required). `KnowledgeBase.owner_id` **does not change**: the organization is a dimension orthogonal to the owner, not a replacement.

Deletion is refused (`409`) while the organization still owns knowledge bases (`knowledge_bases.organization_id` carries `ON DELETE RESTRICT`): no multi-KB physical cascade from the deletion of a tenant, unlike the `User` → `KnowledgeBase` cascade already in place.

### `OrganizationMember` (ADR-022)

Association table carrying the role **per organization**, **N:M** `User` ↔ `Organization` relation from the start: an account can be invited into several organizations without a new structural migration.

| Column | Type | Notes |
|---|---|---|
| `organization_id` | UUID FK → `organizations` | composite PK |
| `user_id` | UUID FK → `users` | composite PK |
| `role` | enum `OrganizationRole` | `owner` \| `member` — only two levels, an intermediate `admin` will be added by a future migration if a real need appears |

The creator of an organization is its `owner`. An organization always has at least one `owner`: same application-level invariant as `KnowledgeBaseMember`, carried by `OrganizationService` (removal and demotion of the last `owner` refused, `409`).

Three distinct roles now coexist in the system and must not be confused: `UserRole` (platform, RAGEngine admin), `OrganizationRole` (tenant, owner/member), `MemberRole` (per knowledge base, owner/editor/viewer).

### `ApiKey`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | owner |
| `name` | text | readable label |
| `prefix` | text, indexed | first 12 characters, for display and support |
| `key_hash` | text, unique, indexed | **SHA-256** of the token (high entropy ⇒ no slow KDF needed) |
| `scopes` | text[] | default `{"*"}` — fine-grained enforcement is in Phase 9, the column exists from now on to avoid a migration |
| `last_used_at` | timestamptz, nullable | |
| `expires_at` | timestamptz, nullable | |
| `revoked_at` | timestamptz, nullable | revocation is non-destructive |

Token format: `rag_<env>_<43 base64url characters>`. **It is displayed only once**, at creation. Guests cannot create a key.

### `UserLlmKey`

Table `user_llm_keys`: the personal generation key of a user, one provider at a time (`UNIQUE (user_id, provider)`, `provider` restricted by a `CHECK` to `anthropic` \| `openai` \| `groq`). `encrypted_key` holds the API key encrypted (Fernet, `app.core.security.encrypt_secret`), never in plaintext; no route returns it. Cascade deletion with the `User`.

### `Session`

Interface sessions, opaque tokens in the database (immediate revocation, unlike a self-contained JWT).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | |
| `token_hash` | text, unique, indexed | SHA-256 of the session token |
| `expires_at` | timestamptz | |
| `revoked_at` | timestamptz, nullable | |
| `user_agent`, `ip_address` | text, nullable | traceability |

Transport: `HttpOnly` cookie, `SameSite=Lax`, `Secure` outside development. The Vite dev server proxies `/api`, so requests are same-origin and the cookie works without any particular configuration.

---

## 2. Knowledge

### `KnowledgeBase`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `slug` | text | unique **per owner**, derived from the name |
| `name`, `description` | text | |
| `owner_id` | UUID FK → `users` | |
| `organization_id` | UUID FK → `organizations`, indexed, `ON DELETE RESTRICT` | owning tenant (ADR-022) — dimension orthogonal to `owner_id`, not a replacement; resolved by `KnowledgeBaseService.create` (default organization if the principal has only one, otherwise an explicit `organization_id` is required) |
| `visibility` | enum `Visibility` | `private` \| `public` — `public` allows reading and the Playground to guests (ADR-005) |
| `chatbot_enabled`, `chatbot_slug`, `chatbot_llm_provider_id` | bool, text (unique), text — nullable | publication as a public chatbot (ADR-046), independent of `visibility`; columns of the shared schema, used by the hosted-platform plugin only (ADR-069) |
| `embedding_model_id` | text | denormalized from the provider config, to stay queryable |
| `reranker_model_id` | text, nullable | |
| `provider_config` | JSONB | serialization of the `ProviderConfig` value object (fine options, thresholds) |
| `observability_enabled` | bool | default `false` — optional building block (Phase 8) |
| `evaluation_enabled` | bool | default `false` — optional building block (Phase 8) |
| `ragflow_dataset_external_id` | text, nullable | filled in Phase 4 |
| `document_count` | int | denormalized counter, maintained by the ingestion service |

`ProviderConfig` is **not** a table: it is a Pydantic value object serialized into `provider_config`. The two structuring choices (embedding, reranker) are duplicated into columns so they can be filtered in SQL. A deliberate deviation from the wording of the plan, motivated in ADR-009.

### `Document`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `knowledge_base_id` | UUID FK, indexed | |
| `filename`, `content_type` | text | |
| `size_bytes` | bigint | |
| `checksum_sha256` | text, indexed | idempotence: the same file re-uploaded into the same KB is detected |
| `storage_uri` | text | location of the original file |
| `status` | enum `DocumentStatus` | `pending` \| `processing` \| `awaiting_review` \| `indexed` \| `failed` |
| `page_count` | int, nullable | |
| `detected_languages` | text[] | ISO 639-1 codes, filled at the precheck (ADR-003) |
| `theme` | text, nullable | theme label entered by the user to organize a base by subject — never inferred (unlike `detected_languages`); set at ingestion or afterwards (`PATCH /documents/{id}`); filterable at retrieval |
| `precheck_report` | JSONB, nullable | detected tables, scanned pages, figures, languages |
| `precheck_decision` | JSONB, nullable | user choice (table optimization, `heavy_ocr` layout analysis, parent/child granularity) |
| `current_job_id` | UUID FK → `ingestion_jobs`, nullable | latest job |
| `ragflow_document_external_id` | text, nullable | filled in Phase 4 |
| `has_quality_warning` | bool | denormalized projection of `IngestionJob.warning != NULL` for the current job (ADR-024); written in the same transaction as the transition to `completed`, by the same single writer; never affects `status` |
| `chunk_count` | int, nullable | total number of chunks indexed on the engine side, same writer as `has_quality_warning` (ADR-024); `NULL` for a document ingested before this column, or if the computation failed (never blocking) |

`Document.status` is a **denormalized projection** of the state of the current job, meant to list without a join. It has a **single writer**: the job state-transition service. No other code writes it — this is the rule that prevents two competing sources of truth.

---

## 3. Ingestion

### `IngestionJob`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `document_id` | UUID FK, indexed | |
| `knowledge_base_id` | UUID FK, indexed | denormalized, to list the jobs of a KB |
| `state` | enum `JobState` | see state machine |
| `progress_percent` | smallint | 0–100 |
| `attempts` | int | default 0 |
| `celery_task_id` | text, nullable | |
| `error` | JSONB, nullable | `{code, message, retryable}` |
| `warning` | JSONB, nullable | `{code, message, metrics}` of the quality guard (ADR-024) — written only at the final transition to `completed` of the indexing step; `NULL`: nothing suspicious detected |
| `started_at`, `finished_at` | timestamptz, nullable | |
| `engine_progress`, `engine_last_message`, `engine_progress_observed_at` | float, text, timestamptz — nullable | liveness witnesses of the engine (anti-stall watch, ADR-012): the last *different* observation reported by the engine (`engine_progress` is the raw value in [-1, 1], NOT a percentage); the guard delay is measured from it, never from the start of the job |

### State machine

```
queued ──▶ prechecking ──▶ awaiting_review ──▶ chunking ──▶ embedding ──▶ indexing ──▶ completed
                                   │
                                   └── (decision timeout) ──▶ chunking  [default decision]

any non-terminal state ──▶ failed
queued | awaiting_review ──▶ cancelled
failed ──▶ queued | awaiting_review | chunking | embedding   (manual retry, ADR-020)
```

Terminal states: `completed`, `failed`, `cancelled` (`failed` is left only by the manual retry, `POST /api/v1/jobs/{job_id}/retry`, never automatically). `Document.status` is a projection of `IngestionJob.state`, written in the same transaction as the transition: `queued→pending`, `prechecking|chunking|embedding|indexing→processing`, `awaiting_review→awaiting_review`, `completed→indexed`, `failed→failed`, `cancelled→pending` (a cancelled job leaves the document ready to be ingested again).

Mandatory rules:
- Transitions go **exclusively** through a dedicated service that validates the legality of the jump and writes `Document.status` in the same transaction. An illegal transition raises `ConflictError` → HTTP 409 (CONTRIBUTING.md §4).
- `cancelled` is an addition to the plan's initial list: a job that cannot be cancelled is a usage defect, and adding an enumeration value later costs a migration plus frontend changes.
- `chunking`, `embedding` and `indexing` are **our** abstraction. Phase 4 will project onto them the real progress reported by RAGFlow, which does not necessarily split its pipeline this way.

### `IngestionJobEvent`

Journal of transitions: `id`, `job_id` (indexed FK), `state`, `message` (text, nullable), `created_at`. Feeds the detailed progress timeline of the interface (CONTRIBUTING.md §7) and is the audit trail. Written by the same transition service, never elsewhere.

---

## 4. Permission matrix

| Action | guest | member | admin |
|---|---|---|---|
| Read a `public` KB / search it | ✓ | ✓ | ✓ |
| Read a `private` KB | ✗ | KB member | ✓ |
| Create a KB | ✓ (1, ephemeral, quotas) | ✓ | ✓ |
| Ingest a document | ✓ (quotas) | `owner`/`editor` | ✓ |
| Decide the precheck | ✓ (their documents) | `owner`/`editor` | ✓ |
| Edit / delete a KB | ✗ | `owner` | ✓ |
| Manage the members of a KB | ✗ | `owner` | ✓ |
| Create an organization (beyond the auto-provisioned personal one) | ✗ (ADR-005, reserved to real accounts) | ✓ | ✓ |
| Read an organization / list or read its members | ✗ | organization member | ✓ |
| Edit / delete an organization, manage its members | ✗ | organization `owner` | ✓ |
| Create / revoke an API key | ✗ | ✓ | ✓ |
| Manage users | ✗ | ✗ | ✓ |

Guest quotas, **driven by configuration** (never hard-coded): `GUEST_MAX_KNOWLEDGE_BASES` (1), `GUEST_MAX_DOCUMENTS` (3), `GUEST_MAX_UPLOAD_BYTES` (20 MB in total), `GUEST_SESSION_TTL_HOURS` (24). Exceeding one ⇒ `403` with a `ProblemDetail` of type `/problems/guest-quota-exceeded` carrying the reached quota and its limit as an extension — this is what the interface uses to offer elevation to an account. The guest mode and the user administration are provided by the hosted-platform plugin (ADR-069); the open core keeps the schema (`is_guest`, the `admin` role) but serves neither.

**Organizations (ADR-022)**: unlike a KB, there is **no** notion of a public organization — a principal that is not a member systematically receives `404`, never `403` nor `401`, anonymous included. Reading the member directory is open to any member (`OrganizationRole.MEMBER`), unlike a KB where only the `owner` manages members: the composition of a team is normally visible to its members, whereas per-KB confidentiality follows a different logic (isolating private bases between teams).

Purge of expired guests: purge service written in Phase 1, **scheduled** on Celery beat in Phase 3.

---

## 5. REST surface v1

Conforms to CONTRIBUTING.md §4: plural, kebab-case, no verb in the paths. Routes marked (plugin) are served by the hosted-platform plugin (ADR-069), not by the open core.

**Identity**
- `POST /api/v1/users` — sign-up (public)
- `GET /api/v1/users` · `PATCH /api/v1/users/{user_id}` — administration (`admin` role) (plugin)
- `POST /api/v1/sessions` — sign-in (email + password) → cookie
- `POST /api/v1/guest-sessions` — opens an ephemeral guest space → cookie (plugin)
- `DELETE /api/v1/sessions/current` — sign-out
- `GET /api/v1/me` — current principal: role, `is_guest`, consumed quotas
- `PUT /api/v1/me/credentials` — guest → account elevation, **keeping the data** (plugin)
- `GET`/`POST /api/v1/api-keys` · `DELETE /api/v1/api-keys/{api_key_id}` — revocation

**Organizations (ADR-022)**
- `POST`/`GET /api/v1/organizations` · `GET`/`PATCH`/`DELETE /api/v1/organizations/{organization_id}` — creation reserved to a real account (ADR-005), edit/delete reserved to the `owner`
- `GET`/`PUT`/`DELETE /api/v1/organizations/{organization_id}/members/{user_id}` — read open to any member, mutation reserved to the `owner`

**Knowledge**
- `POST`/`GET /api/v1/knowledge-bases` · `GET`/`PATCH`/`DELETE /api/v1/knowledge-bases/{kb_id}`
- `GET`/`PUT`/`DELETE /api/v1/knowledge-bases/{kb_id}/members/{user_id}`
- `GET /api/v1/knowledge-bases/{kb_id}/documents`
- `POST /api/v1/knowledge-bases/{kb_id}/documents/ingest` — upload → **202** + job; the precheck starts automatically, there is no separate `POST` to trigger it
- `GET`/`PATCH`/`DELETE /api/v1/documents/{document_id}` · `GET /api/v1/documents/{document_id}/content` · `GET /api/v1/documents/{document_id}/chunks`
- `PUT /api/v1/documents/{document_id}/precheck/decision` — human-in-the-loop routing
- `GET /api/v1/jobs/{job_id}` · `GET /api/v1/knowledge-bases/{kb_id}/jobs`
- `DELETE /api/v1/jobs/{job_id}` — cancellation (→ `cancelled`) · `POST /api/v1/jobs/{job_id}/retry` — manual retry of a `failed` job

Codes: `202` for any accepted asynchronous processing, `409` on an illegal state transition, `403` on an exceeded guest quota, `404` on a resource that does not exist **or** is invisible to the principal (never reveal the existence of a private KB through a 403).

---

## 6. Authentication — principal resolution

A single middleware resolves the principal in this order:
1. `Authorization: Bearer <API key>` header → SHA-256 lookup on `key_hash`, rejected if revoked or expired, `last_used_at` updated.
2. Session cookie → lookup by `token_hash`, rejected if expired or revoked.
3. Neither → **anonymous** principal (can read public KBs; every persistent action answers `401` with a `ProblemDetail` of type `/problems/authentication-required`, which triggers the elevation flow on the interface side).

The principal exposes `user`, `role`, `is_guest`, `auth_method`. The FastAPI dependencies `require_authenticated`, `require_role(...)` and `require_kb_permission(...)` are the only points where the rules of §4 are enforced — never access control scattered in a router.
