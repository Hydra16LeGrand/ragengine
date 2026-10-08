# Ingestion engine architecture — v1 (Phase 4)

Normative reference of `IngestionEnginePort` and of its RAGFlow adapter. Based on a **real empirical audit** (RAGFlow v0.26.4, actually deployed and queried — not on the documentation alone). Complements `docs/domain-model.md` (states, permissions) and `docs/providers-architecture.md` (sibling ports) without duplicating them.

## Summary (go down into a section only if you touch it)

- **`run` is the only discrete state RAGFlow exposes.** `chunking`/`embedding`/`indexing`
  remain our abstraction, projected by a heuristic over `progress_msg` (§3) — never read
  directly, never a contract.
- **Business errors come back as HTTP 200**; the real status is the `code` field of the JSON
  body (§2). Only `100/105/106` are replayable (`EngineTransientError`).
- **A document can stay `RUNNING` indefinitely** (observed RAGFlow bug): RAGEngine must protect
  itself, an anti-stall watchdog is mandatory (§5, `RAGFLOW_STUCK_JOB_TIMEOUT_SECONDS`).
- **`ensure_dataset`, never `create_dataset`**: a duplicated name is silently renamed by
  RAGFlow. The name sent is the `KnowledgeBase` UUID, never its readable name (§2).
- **Enabling reranking changes the scale of the combined score**: it can empty the results under
  `similarity_threshold` without an error (§0, fact 5) — settled in Phase 12 (ADR-026): effective
  reranking + non-zero threshold is refused (422) unless `acknowledge_threshold_risk=true` is explicit.
- **12 values of `chunk_method`, never `general`** (§4); the precheck → profile mapping is fixed
  and implemented (`app/ingestion/chunking_profile.py`, ADR-014). `layout_recognize` now follows
  `is_likely_scanned` by default (`Plain Text` on a clean text layer, `DeepDOC` on a probable scan),
  corrected by the per-document-type routing of Phase 11 (ADR-023 §3).
- **Deployment**: pin a RAGFlow release tag (never `main`); only `ragflow-cpu` joins the two
  Docker networks (§6).
- **An endpoint listing the chunks of a document exists** (`GET /datasets/{id}/documents/
  {doc}/chunks`, verified in Phase 8, absent from the initial audit — §8): evaluation generation
  uses it as its source of passages, not a `search()` scoped to the document (ADR-017).

## 0. What the audit established, and why it shapes the port

Five verified facts determine the shape of the port; each one contradicts a reasonable assumption that could have been frozen without an audit.

1. **RAGFlow accepts an external embedding/rerank provider through its HTTP API, without going through the web interface.** `PUT /api/v1/providers` + `POST /api/v1/providers/HuggingFace/instances` (two instances, one per TEI container — ADR-051), once per deployment, points RAGFlow to **our** TEI containers (`docs/adr/051`). This is what makes ADR-001 genuinely practicable: RAGFlow remains the single engine, but the models remain ours.
2. **The parsing pipeline is atomic from the point of view of states.** RAGFlow exposes only one discrete state field (`run`: `UNSTART|RUNNING|CANCEL|DONE|FAIL`). Chunking, embedding and indexing do exist but **only as free-text lines** in `progress_msg`, non-contractual (they vary with `chunk_method`). `chunking → embedding → indexing` therefore remain **our abstraction** (already announced by `docs/domain-model.md` §3) — projected by a heuristic, never read directly.
3. **Business errors come back as HTTP 200.** The real status lives in the `code` field of the JSON body. An adapter that relies on `raise_for_status()` detects no RAGFlow error at all.
4. **A document can stay stuck indefinitely in `RUNNING` without ever reaching a terminal state** (observed RAGFlow bug: an unhandled exception before the internal tasks are created leaves the document with nothing to make it progress). No RAGFlow mechanism recovers from it on its own.
5. **Enabling reranking changes the scale of the combined similarity score**, which can then fall under `similarity_threshold` and empty the results without an error.

These five facts are not implementation details: they are what determine that a **watchdog worker** is needed on the RAGEngine side (fact 4), that **the port cannot expose a reliable fine-grained state** (fact 2), and that **enabling reranking is a choice that has a price elsewhere** (fact 5).

## 1. Bootstrap — outside the port, once per deployment

Registering the HuggingFace provider (fact 1) is **not** an operation of the port: it is a deployment bootstrap step, like the Alembic migration (ADR-010). It lives in `scripts/bootstrap_ragflow.py` (Phase 4, agent), idempotent (checks existence before creating), called by `scripts/bootstrap.sh` after `docker-compose.ragflow.yml` starts. It produces the RAGFlow account, its API key (stored in `.env` under `RAGFLOW_API_KEY`, declared since Phase 0), and the registration of the `HuggingFace` provider with its two TEI instances (ADR-051).

The RAGFlow service account password is RSA-encrypted on the client side (`conf/public.pem` provided by the RAGFlow image) — the bootstrap script encapsulates this detail, `IngestionEnginePort` never knows about it.

## 2. `IngestionEnginePort` — `app/providers/engine/base.py`

```python
class EngineDatasetRef(BaseModel):
    """External identifier of a RAGFlow dataset, opaque to the caller."""
    model_config = ConfigDict(frozen=True)
    external_id: str

class EngineDocumentRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    external_id: str

class ChunkingProfile(BaseModel):
    """Translation of a precheck decision into RAGFlow parameters (§4)."""
    model_config = ConfigDict(frozen=True)
    chunk_method: str | None   # one of the 12 real values, see §4 — never "general";
                               # None = impose none, the engine deduces it from the file type
    parser_config: dict[str, JsonValue] = Field(default_factory=dict)

class EngineParseStatus(BaseModel):
    """Projection of the RAGFlow state — best-effort, see §3. NOT a fine-grained source of truth."""
    model_config = ConfigDict(frozen=True)
    run: Literal["unstart", "running", "cancel", "done", "fail"]
    inferred_phase: JobState | None   # chunking/embedding/indexing/None if undeterminable
    progress: float                    # [-1.0, 1.0] as is — NOT a percentage
    last_message: str | None           # last line of `progress_msg`
    chunk_count: int
    error: str | None                  # non-empty only if run == "fail"

class EngineSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    chunk_id: str
    document_external_id: str
    content: str
    similarity: float
    vector_similarity: float
    term_similarity: float
    page_start: int | None = None      # first page covered (1-based)
    page_end: int | None = None        # last page covered

class IngestionEnginePort(Protocol):
    async def ensure_dataset(
        self, *, ref: EngineDatasetRef | None, name_seed: str,
        embedding_provider_id: str, reranker_provider_id: str | None,
    ) -> EngineDatasetRef: ...

    async def upload_document(
        self, dataset: EngineDatasetRef, *, filename: str, content: bytes
    ) -> EngineDocumentRef: ...

    async def start_parsing(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef, profile: ChunkingProfile
    ) -> None: ...

    async def get_parse_status(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> EngineParseStatus: ...

    async def cancel_parsing(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> None: ...

    async def search(
        self, datasets: Sequence[EngineDatasetRef], *, query: str, top_k: int,
        rerank_provider_id: str | None, similarity_threshold: float | None,
        document_refs: Sequence[EngineDocumentRef] | None = None,
        rerank_candidate_pool_size: int | None = None,
    ) -> list[EngineSearchResult]: ...

    async def delete_document(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> None: ...

    async def delete_dataset(self, ref: EngineDatasetRef) -> None: ...

    async def list_document_chunks(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef,
        *, page: int = 1, page_size: int,
    ) -> EngineDocumentChunksPage: ...
```

Design points that come directly from the audit:

- **`ensure_dataset`, not `create_dataset`.** A duplicated dataset name is not an error on the RAGFlow side — it is silently renamed (observed fact: `audit-kb` → `audit-kb-renamed(1)`). Creating a RAGFlow dataset by readable name would produce duplicates at every new attempt after a failure. The RAGFlow name sent is therefore derived from **the `KnowledgeBase` UUID**, never from its human name (unique by construction, no prior check needed); `ensure_dataset` reuses `ref` if it is already known (`KnowledgeBase.ragflow_dataset_external_id`, Phase 1) and only creates otherwise.
- **`get_parse_status` is explicitly best-effort.** `inferred_phase` can be `None`: no calling code must treat it as a guarantee. The RAGFlow adapter implements the heuristic of §3; a future engine that exposed a real fine-grained state would only have to fill `inferred_phase` more precisely, without changing the port.
- **`similarity_threshold: float | None` on `search`** — not a hidden default in the adapter. Settled in Phase 12 (ADR-026, `RetrievalService.search`): rather than silently lowering this threshold when `rerank_provider_id` is effective (fact 5), the service **refuses** the combination (422) unless explicitly acknowledged (`acknowledge_threshold_risk=true`) — an invisible default in the adapter, or an automatic readjustment of the threshold, would both have reproduced the trap in another form.
- **`document_refs` and `rerank_candidate_pool_size` on `search`.** `document_refs` restricts the search to a subset of documents (a filter natively supported by RAGFlow); `None` filters nothing, an empty sequence returns `[]` without calling the engine. `rerank_candidate_pool_size` caps the pool of candidates submitted to the reranker (distinct from `top_k`, which bounds the output): without a cap, too large a batch of candidates of real length can exceed the reranker's VRAM budget (incident confirmed on 2026-09-06, CONTRIBUTING.md §2).
- **`delete_document` is not idempotent, on purpose.** An already removed document makes the engine answer its `102` ("not found or forbidden"), translated into `EngineError`. Making it silent in the adapter would also hide a real permissions defect, which RAGFlow makes indistinguishable from an absence; it is up to the caller to decide what a permanent error means for it.
- **Errors.** The RAGFlow adapter systematically translates the body's `code` field (never the HTTP status alone) into `EngineError` (subclass of `RAGEngineError`) or a more precise subclass when the meaning is clear (`EngineValidationError` for `code=101`, `EngineTransientError` for `code in {100, 105, 106}` — only those are eligible for Celery retry). `code=102` ("not found or forbidden", the two being indistinguishable on the RAGFlow side, verified fact) becomes a generic `EngineError`: RAGEngine already knows the existence of its own KBs/documents from its own database, so the RAGFlow ambiguity never leaks to the user.

## 3. State projection — a documented heuristic, not a contract

`chunking → embedding → indexing` are projected from `progress_msg` by a search for markers, **observed as common to RAGFlow's generic pipeline** (after the parser-specific step, hence independent of `chunk_method`):

| Marker observed in `progress_msg` | `inferred_phase` |
|---|---|
| (none of the following markers seen yet) | `JobState.CHUNKING` |
| `"Embedding chunks"` seen, `"Indexing done"` not yet | `JobState.EMBEDDING` |
| `"Indexing done"` seen | `JobState.INDEXING` |

`run == "done"` ⇒ `JobState.COMPLETED` without ambiguity, whatever `inferred_phase`. If no marker matches (new RAGFlow version, unexpected message), `inferred_phase = None` and the polling service (§5) merely logs `last_message` without a phase transition — **it must never guess**.

**Consequence measured in Phase 4, not to be treated as an edge case**: on a small document, no poll ever lands on `embedding` or `indexing`. The sequence actually observed is `chunking` (several seconds) then directly `done` — the intermediate markers exist in the log but scroll by between two polls. The polling tasks must therefore treat `run == "done"` as satisfying **all** the remaining phase conditions, and `run_indexing` must know how to write its two transitions (`embedding → indexing → completed`) during a single execution. This is not a shortcut: they are the two transitions that belong to it, written one after the other, without any state being skipped.

`progress` (float `[-1.0, 1.0]`, `-1.0` = failure, noisy while queued — verified fact) does **not** feed `IngestionJob.progress_percent` directly: too unstable for a displayed gauge. It serves only as a secondary liveness signal (see watchdog, §5).

## 4. `chunk_method` — real catalogue and precheck → profile mapping

Real values, confirmed by RAGFlow's own rejection message (**12, not 13, and never
`general`**):

`naive, book, email, laws, manual, one, paper, picture, presentation, qa, table, tag`

⚠️ **Empirical correction (Phase 11, 2026-08-06)**: this list previously counted `resume` as the 13th value. Tested with `PUT` and `PATCH`, including a case variant (`Resume`): RAGFlow systematically rejects it — `{"code":102,"message":"Field: <chunk_method> - Message: <`chunk_method` resume doesn't exist>"}`. The validation mechanism itself works (`chunk_method=paper` was accepted on the same document): `resume` simply never existed on this version of RAGFlow. See established fact 1 of the project plan.

Initial mapping (Phase 4), to be refined with use:

| Precheck decision (`docs/domain-model.md` §2, `Document.precheck_decision`) | `chunk_method` | Notes |
|---|---|---|
| No particular option, document with a clean text layer (`is_likely_scanned = false`) | `naive` + `parser_config.layout_recognize = "Plain Text"` | **Routing by document type (ADR-023, Phase 11 Step 5)**: measured on the 29-document corpus (established fact 10 of the project plan), `Plain Text` beats `DeepDOC` on any document whose precheck detected no scan — fewer case errors, less column interleaving, better coverage of the source text. New default, in place of the former empty `parser_config`. |
| No particular option, probably scanned document (`is_likely_scanned = true`) | `naive` + `parser_config.layout_recognize = "DeepDOC"` | Full analysis, set explicitly rather than inherited from the engine default — more robust if that default changes on the RAGFlow side. |
| "Optimize for financial tables", document = **spreadsheet** | `table` | One chunk = one data row (verified fact). **Two measured product reservations**: (1) the tabular parser infers types and can corrupt short ambiguous text values (e.g. `"T1"` reinterpreted as a date) — seen in the audit; (2) it **loses the sheet label** of an `.xlsx`, where `naive` repeats it at the end of every row (`… ——Budget fictif`, produced by `deepdoc/parser/excel_parser.py`) — on a multi-sheet workbook, two rows of different sheets become indistinguishable (Phase 15 Step 2, documents 08 and 52). **No engine parameter allows re-injecting it**: `rag/app/table.py::chunk()` reads only `table_column_mode`/`table_column_roles` (column roles) from `parser_config`, and `Excel.__call__` stacks one `DataFrame` per sheet without ever carrying its name to the chunk. Nothing is wired to remedy this — doing so would mean pre-processing the workbook before handing it to the engine, i.e. reimplementing its building block (CONTRIBUTING.md §3.3). The workaround is a **user choice**: unchecking the option falls back to `naive`, which keeps the label at the price of one chunk per batch of rows. `.csv` is not concerned (no sheets, no label lost). |
| "Optimize for financial tables", document = **PDF** | `chunk_method` unchanged (`naive`) + `parser_config.layout_recognize = "DeepDOC"` | **Empirical correction, Phase 4 end-to-end test**: `chunk_method=table` on a PDF makes the job fail — RAGFlow answers `file type not supported yet(excel, text, csv supported)` (verbatim message, `run="fail"`). The initial mapping assumed `table` was valid for any document; that is true only for a spreadsheet. On a PDF, DeepDOC (`naive`) already recognizes the tables in its layout analysis — it is the same lever as "heavy OCR". Implemented in `app/ingestion/chunking_profile.py::profile_for_decision`, which receives the format detected at the precheck to arbitrate. |
| "Analyze the document layout" (`heavy_ocr`, pre-ADR-031 label: "Enable heavy OCR") | `chunk_method` unchanged + `parser_config.layout_recognize = "DeepDOC"` | RAGFlow has only one local visual extraction engine; "heavy OCR" translates into a `parser_config` parameter, not a different `chunk_method`. **Parameter confirmed in Phase 4 against the real instance**: `layout_recognize` accepts `"DeepDOC"` (full layout analysis + OCR) and `"Plain Text"` (short-circuit); the other accepted values designate external parsers (MinerU, PaddleOCR, SoMark) that require a declared provider, of which RAGEngine configures none. The option therefore **imposes** `DeepDOC` instead of inheriting the dataset setting. Beware: RAGFlow accepts any string at validation and only fails at parsing time. **Keeps priority** over the new default driven by `is_likely_scanned` (ADR-023 §3): when checked, it forces `DeepDOC` even on a document judged clean by the precheck. **Since ADR-031 (2026-08-11), the interface offers it checked on any PDF** — mechanics unchanged, only the proposal and the label move; the API schema default remains `false`. |
| Document = **image only** (`.png`/`.jpg`/…) | `chunk_method = None`, no `parser_config` | **Empirical correction, end-to-end test (2026-08)**: the initial mapping targeted an explicit `chunk_method=picture`; in reality RAGFlow assigns `picture` **itself** at upload (`POST /datasets/{id}/documents`) and then refuses any `PATCH` carrying a `chunk_method` on a visual document — `Not supported yet!`, whatever the value sent, `picture` itself included. Setting `layout_recognize=DeepDOC` on top changed nothing in the result (same chunks, same score). The only profile that succeeds is therefore a `PATCH` **skipped entirely** (`chunking_profile.py::NO_CHUNK_METHOD`, `RagflowEngineAdapter.start_parsing` only makes the call if `profile_payload` is non-empty) — the document is then OCR'd and indexed normally. |
| Document = **slideshow** (`.pptx`/`.ppt`/`.pages`) | `chunk_method = None`, no `parser_config` | **Empirical correction, Phase 15 Step 3 (2026-08-26)**: the guard that refuses any `chunk_method` does not concern visual documents only — `api/utils/validation_utils.py::validate_chunk_method` (the two-argument function, not the homonymous Pydantic validator) tests `doc.type == FileType.VISUAL **or** re.search(r"\.(ppt\|pptx\|pages)$", doc.name)`, and the check fires on the mere **presence** of the key (`if "chunk_method" in req`, `api/apps/services/document_api_service.py`), not on its value. Production was setting `naive`: measured on corpus document 31, the `.pptx` failed at chunking before any chunk, whereas setting nothing yields 4 clean chunks with the `presentation` the engine assigns itself (`FileService.get_parser`). The `parser_config` is empty too and this is not a shortcut: `rag/app/presentation.py::chunk()` reads `layout_recognize` only on its `.pdf` branch, never on a `.pptx`. The precheck now recognizes this family (`PrecheckFormat.PRESENTATION`) so it can route it, without claiming to describe the file. |

`picture` (automatically assigned by the engine to images, never explicitly by RAGEngine — see above), `presentation` (same on a `.pptx`), `book`, `tag`, `email`, `one` remain available in `ChunkingProfile` without being exposed: do not add options to the decision screen without having tested them against the real instance. `resume` does not exist on this instance (above): do not reintroduce it without rechecking.

**A fine selection, Phase 15** (Step 3, concluded by **ADR-044** — retained pairs, measured exclusions, recorded debts) — the only place where the user names a template themselves:

| Precheck decision | Effect | Notes |
|---|---|---|
| "Chunking method" (`chunk_method_override`) | **replaces** the `chunk_method` produced by the mapping above, and nothing else | **Closed catalogue** (`app/domain/schemas/document.py::AdvancedChunkMethod`): `naive`, `laws`, `paper`, `manual`, `qa` — a measured subset of the engine's templates, each exclusion motivated by a number (Phase 15, Step 2). `email` in particular is ruled out on a **measured defect**, not for lack of measurement: set on a PDF it returns `run=done` while indexing the raw bytes of the file (2.2% source coverage, first chunk `%PDF-1.4 …`) — an apparent success that indexes binary. `layout_recognize` is **never** touched: the two settings remain combinable, and a scan stays routed `DeepDOC`. No effect on an image or a slideshow (the engine refuses any `chunk_method` there): ignored rather than fatal. Default `null` ⇒ automatic routing unchanged, the v1 contract does not move. **Per-document filtering lives on the interface side** (`frontend/src/api/precheck.ts::advancedChunkMethodsFor`): `laws`/`paper`/`manual` are only offered on a non-scanned PDF **without** a multi-column page (measured: `laws` collapses to 19.2% degenerate chunks on a multi-column layout), `qa` never on a PDF (measured: "Unable to recognize Q&A structure.", 0 chunks) but on Markdown/text. The backend, for its part, accepts the whole catalogue: an explicit advanced option is not a suggestion, and the API contract cannot depend on a precheck report that the caller has not read. Verified end to end against the real stack on document 11: `naive` 2 chunks, `laws` 9, `paper` 9 — exactly the Step 2 measurements. |

**An additive option, Phase 13** (internal Phase 13 impact study, candidate 4) — set in `parser_config`, **independently** of the `chunk_method`/`layout_recognize` mapping above, never mixed with its decision logic (`chunking_profile.py::_additive_parser_config`):

| Precheck decision | `parser_config` set | Notes |
|---|---|---|
| "Refine search granularity" (`use_parent_child`) | `parent_child.use_parent_child = true` **and** `ext.children_delimiter = "\n"` | Parent/child chunking: indexes small child chunks for precise matching, aggregates up to the parent chunk (more context) at search time — an engine mechanism already unconditionally active on the retrieval side (`retrieval_by_children`, guaranteed no-op if no chunk has a `mom_id`). **Engine bug discovered and worked around (end-to-end test, Phase 13)**: `PATCH /datasets/{id}/documents/{doc}` persists `parser_config` **raw**, never flattening it as `get_parser_config()` does on the *dataset* path — setting only `parent_child.use_parent_child=true` is therefore a silent no-op, `rag/app/naive.py::chunk()` reads only the flat key `children_delimiter`. Verified workaround (mother chunks marked `available: false`, short child chunks found, search indeed returning the parent): route the same value through `parser_config.ext` (a free field of `ParserConfig`), which the document path merges as is before persisting. Opt-in, unchecked by default: nothing in the precheck report allows guessing whether the user wants it. |

**GraphRAG `method="ner"` (candidate 1) is deliberately not exposed** — verified against the real instance (`infiniflow/ragflow:v0.26.4`): `api/db/services/task_service.py::queue_tasks` (document path, the one `RagflowEngineAdapter.start_parsing` triggers) never queues a `task_type="graphrag"` task; only `queue_per_doc_raptor_task` exists for RAPTOR, with no GraphRAG equivalent. The only confirmed trigger that actually queues a `graphrag` task is the dataset-wide endpoint `POST /datasets/{id}/index?type=graph` — explicitly ruled out (silently overwrites `Knowledgebase.parser_config`, per the internal Phase 13 impact study). `enable_graph_ner` was implemented, tested end to end (confirmed without effect: no task queued, no `knowledge_graph_kwd` document created in Elasticsearch) then removed from the catalogue — closed catalogue, an option with no verified effect is never exposed (§4, `chunking_profile.py` module).

This option is short-circuited on an **image** just like the rest of the profile (§ above), out of caution rather than because of a verified engine constraint on this precise point: `validate_chunk_method` guards only on the presence of `chunk_method` in the request (not on `parser_config` alone), so a `parser_config`-only `PATCH` on a visual document could technically go through — but with no notion of child/parent chunks on a single-block OCR, sending it would make no sense anyway; empty profile on both sides, like the other options on this format.

## 5. Anti-stall watchdog — imposed by fact 4

A document can stay `RUNNING`, `progress=0.0`, `progress_msg=""`, indefinitely (observed RAGFlow bug, not a generic robustness hypothesis). RAGEngine **must** protect itself:

- The polling (Celery task, Phase 3 already built on `run_embedding`/`run_indexing`, adapted in Phase 4 to query `get_parse_status` in a loop rather than doing stub work) carries a configurable guard delay (`RAGFLOW_STUCK_JOB_TIMEOUT_SECONDS`, in `Settings`/`.env.example`) measured from the last observed change of `progress`/`progress_msg` — not from the start of the job (a real voluminous document can legitimately take a long time, the audit only tested one-page documents).
- Beyond the delay, the adapter calls `cancel_parsing` (`POST .../documents/stop`, verified working — the document cleanly moves to `CANCEL`), then the RAGEngine job transitions to `failed` with an explicit message ("The ingestion engine has not progressed for Xs"), not to `cancelled` (ADR: a stall is not a cancellation requested by the user, see `docs/domain-model.md` §1 for the distinction).

## 6. Deployment — two traps lived through in the audit, not to be reproduced

`docker/docker-compose.ragflow.yml` (Phase 4, agent) relies on RAGFlow's **official** Docker files (cloned, never retyped by hand — the audit showed that YAML fidelity matters here), with two mandatory corrections discovered in the audit:

1. **Pin a release tag** (`v0.26.4` or later at the time of implementation), **never `main`**. The repository bind-mounts `docker/entrypoint.sh` into the image: the one on `main` references a migration script absent from the corresponding published image, which produces a silent restart loop (`exit 127`) with no clear relation to the real cause.
2. **The network collision affects RAGFlow's internal dependencies (`redis` first), not RAGFlow itself.** The audit first attached the whole RAGFlow stack to the `ragengine` network: RAGFlow's internal `redis` service has the same name as RAGEngine's, the two step on each other (RAGFlow tries to authenticate against RAGEngine's Redis, which has no password, and dies). The audit worked around it by isolating all of RAGFlow behind a dedicated TCP relay for the access to Xinference.
   **Design retained for the real deployment, simpler**: only the application container (`ragflow-cpu`, the one serving the HTTP API) joins **both networks** — its own (where `mysql`, `redis`, `minio`, `es01` live, invisible to RAGEngine, no collision since RAGEngine never names them) and `ragengine` (external, already created by the core stack). No TCP relay is then needed in either direction: `ragflow-cpu` natively resolves `inference-embedding:80`/`inference-reranker:80` on `ragengine` (TEI, ADR-051), and the RAGEngine API/workers just as natively resolve `ragflow-cpu:9380`. `RAGFLOW_BASE_URL=http://ragflow-cpu:9380` (already in place in `.env.example`, to be renamed if the service takes another name).

Slim/CPU variant with no embedded model (confirmed: it is the only one distributed since v0.22, the optional TEI profile remains disabled). Ports shifted like the rest of the stack (policy already in place, CONTRIBUTING.md/`docker-compose.yml`) to avoid any collision with the RAGEngine API (8000), PostgreSQL (5433), Redis (6380) and TEI (9997/9998).

## 7. What the audit did not cover — to verify before relying on it

Recorded so that it is not mistakenly presented as established in Phase 4: the **Infinity** doc engine (only Elasticsearch was tested), large multi-page documents (the whole audit concerns one-page documents), real concurrency at scale, the alternative endpoint `POST /api/v1/datasets/{id}/documents/parse` and the `POST /api/v1/documents/ingest` mode (they exist in the RAGFlow code, never called), the `SCHEDULE` state (never observed), the survival of the network proxy (§7) across a restart.

## 8. Listing the chunks of a document — verified in Phase 8, extended in Phase 13

`IngestionEnginePort.list_document_chunks` (Phase 8, evaluation generation, ADR-017; extended in Phase 13, consultation of the chunks from the interface) relies on `GET /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks`, verified against the real instance (outside the scope of the Phase 4 audit, which covered only ingestion and retrieval):

- Same `code`/`data` envelope as the rest of the API (fact 3, §0): `{"code": 0, "data": {"chunks": [...], "doc": {...}, "total": n}}`, HTTP 200 including on a business error.
- `data.chunks[].id`, `.content`, `.positions`, `.important_keywords`, `.questions` and `.available` have all been consumed since Phase 13 (`EngineChunk`) — only `docnm_kwd`, `image_id`, `tag_kwd`, `document_id`, `dataset_id` remain without an identified use, ignored by the parsing model. `data.doc.chunk_method`/`.chunk_count`/`.token_count` feed `EngineDocumentChunksMeta`, rendered at the top of the chunks panel on the interface side.
- Unknown document or outside the tenant ⇒ `code=102` ("You don't own the document …"), the same "not found/forbidden" ambiguity as everywhere else (§2), translated into a generic `EngineError`.
- Pagination really exposed by the port since Phase 13 (`page`/`page_size`, `EngineDocumentChunksPage.total` carries the count over all pages). Evaluation generation (ADR-017) and the quality guard continue to read only `page=1`: a single passage/batch is enough for their use, nothing to do with a limit of the port.
