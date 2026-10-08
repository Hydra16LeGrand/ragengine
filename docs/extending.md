# Extending RAGEngine

RAGEngine has three extension points, from the narrowest to the widest. Pick the smallest one that
does the job.

| You want to… | Extension point | Touches |
|---|---|---|
| Add an embedding, reranker, LLM or observability backend | **Provider** (§1) | one adapter file, one registry entry |
| Plug another ingestion/retrieval engine in place of RAGFlow | **Ingestion engine adapter** (§2) | one adapter, one registry entry, one setting |
| Add routes, settings defaults, Celery tasks or maintenance steps without editing the core | **Plugin** (§3) | a folder under `backend/app/plugins/` |

The architecture is hexagonal: the core only knows *ports* (`Protocol` classes in
`backend/app/providers/<domain>/base.py`). Concrete adapters are the only modules allowed to import
third-party SDKs, and a registry resolves a configured identifier to an adapter. The detailed
reference for the ports is [`providers-architecture.md`](providers-architecture.md); this
page is the short, practical version.

**Discovery is explicit, on purpose.** Providers and engines are listed in code (a dictionary built
at import time), not discovered through Python `entry_points` or by scanning the filesystem. A page
of documentation and a worked example are enough for a handful of backends, and an explicit list
keeps what a deployment can run readable, reviewable and testable in one place
([ADR-070](adr/070-fabrique-du-moteur-adaptateur-factice-et-rapport-de-precheck.md)). Only plugins
(§3) are discovered, because they are whole folders that a deployment adds or removes.

## 1. A provider (embedding, reranker, LLM, observability)

Each domain (`embeddings/`, `rerankers/`, `llm/`, `observability/`) has the same three parts: a
port, one adapter file per backend, and a registry. Adding an external (API-based) backend means
three small edits and no change to the port or to any service.

`backend/app/providers/llm/registry.py` shows the shape. Every adapter is described by a
registration, and the registry is a plain mapping built when the module is imported:

```python
@dataclass(frozen=True)
class LLMProviderRegistration:
    capabilities: LLMCapabilities
    build: Callable[[Settings], LLMPort]
    build_with_key: Callable[[LLMCapabilities, str], LLMPort]
    is_available: Callable[[Settings], bool]
    label: str


def _groq_registration(capabilities: LLMCapabilities) -> LLMProviderRegistration:
    return LLMProviderRegistration(
        capabilities=capabilities,
        build=lambda settings: GroqLLMAdapter.from_settings(capabilities, settings),
        build_with_key=GroqLLMAdapter.from_api_key,
        is_available=_has_groq_key,
        label=_LABELS[capabilities.provider_id],
    )


_REGISTRY: Final[Mapping[str, LLMProviderRegistration]] = {
    **{c.provider_id: _anthropic_registration(c) for c in ANTHROPIC_CATALOG},
    **{c.provider_id: _openai_registration(c) for c in OPENAI_CATALOG},
    **{c.provider_id: _groq_registration(c) for c in GROQ_CATALOG},
}
```

### Invariants every adapter keeps

Checked on the adapters already written (`backend/app/providers/llm/`, `backend/app/providers/observability/`):

- **No raw SDK exception leaves an adapter.** Each provider translates its errors into `RAGEngineError` (or a domain subclass) *before* they reach a service.
- **Availability ≠ reachability.** `is_available(settings)` only looks at configuration (key present, URL set) — never at the network. `GET /api/v1/providers` builds its catalog with no latency and no external dependency.
- **Building an adapter is free.** No network client is opened before the first real call (the HTTP client is created lazily). Resolving a provider to validate a configuration must allocate no resource.
- **No sampling parameter on the LLM port** (`temperature`, `top_p`, `top_k`): the current Claude models reject them (HTTP 400), and a cross-provider port that breaks at run time on the default provider is not a port. An adapter that needs one handles it internally, never through the port.
- **The registry is a declaration in code, not a discovery.** The list of supported providers is a deliberate choice (a dict built at import) — no plugin introspection.

Each domain also has a descriptor of capabilities next to its port (e.g. `LLMCapabilities`, a frozen Pydantic model: what the implementation can do, never how), and a deterministic `fake.py` next to the real adapters, for service tests without external dependency. `observability/` is the exception to the registry: a binary switch (`get_observability(settings)` returns `LangfuseObservability` only if the host **and** both keys are set, otherwise the `NoOpObservability` Null Object; a partial configuration degrades to no-op). It is memoized by identity of the `settings` object, because `LangfuseObservability.from_settings` starts an export thread on construction — a new implementation that opens a background resource must reproduce that precaution.

### Checklist: add an external provider (example: Mistral as an LLM)

1. **Check the official SDK** (no home-made HTTP call if an SDK exists) and confirm, by introspection or a real call, the points that vary between providers: is the `system` role a message or a root parameter? How is a refusal / content filtering signalled? Which exception derives from which (catch order)? Do not assume by analogy with an existing adapter — `groq.py` documents precisely where Groq diverges from OpenAI and why; reproduce that as a method, not as a result.
2. **Create `backend/app/providers/llm/mistral.py`**:
   - `MISTRAL_CATALOG: Final[tuple[LLMCapabilities, ...]]` — exact model IDs checked against the Mistral documentation, never a dated or guessed ID.
   - `_translated_errors(provider_id)` — a context manager that translates the SDK exceptions into `LLMError`/`LLMRateLimitError`/`LLMRefusalError` (catch the most specific first: `RateLimitError` before `APIStatusError`, from which it derives), **including the third party's detailed message** in `detail` (`anthropic.py`/`groq.py` style: `f"L'API Mistral a répondu {code} : {message}"`, never just the code).
   - `MistralLLMAdapter` — `capabilities`, `from_settings(capabilities, settings)` (raises `ValidationError` if `settings.mistral_api_key` is missing), `complete(...)`, `stream(...)`. No sampling parameter on the port signature.
3. **Extend `Settings`** (`backend/app/core/config.py`): `mistral_api_key: SecretStr | None = Field(default=None, validation_alias="MISTRAL_API_KEY")`, exactly following the pattern of the three existing keys.
4. **Document the variable** in `.env.example`, in the "external LLMs" section, with a comment in the style of the neighbouring entries.
5. **Register it in `registry.py`**: a `_has_mistral_key` function, a `_mistral_registration(capabilities)` function, and merge `MISTRAL_CATALOG` into `_REGISTRY` — same shape as the three existing blocks, reorder nothing else. `is_available` must only look at configuration, never at the network.
6. **Extend `LLMVendor`** (`Literal["anthropic", "openai", "groq"]` in `llm/base.py`) with `"mistral"` — this is a port change (the vendors are enumerated in the type), so check it with strict mypy before committing.
7. **Tests** (`backend/tests/providers/llm/test_mistral.py`, mirror of `test_groq.py`/`test_anthropic.py`): construction from `Settings` (missing key → `ValidationError`), translation of every SDK error family (mock the client, never a real network call), `complete` and `stream` on a simulated response, refusal detection if the provider exposes one. Also extend `test_registry.py` (the new `provider_id` appears in `list_llm_providers`, `resolve_llm_provider` resolves it if the key is present and fails otherwise).
8. **No change on the services or routers side**: `GET /api/v1/providers` already lists the registries dynamically, and `RetrievalService.chat` resolves `llm_provider_id` through `resolve_llm_provider` without knowing the list of providers.

The same scheme applies to `embeddings/` and `rerankers/` for an **external** provider (e.g. a new API-based embeddings provider, in the manner of `embeddings/openai.py`): unchanged port, one adapter file, one registry entry, one configuration key. Tests mirror `app/` in `backend/tests/providers/<domain>/test_<provider>.py` (pytest + httpx); a feature without a test is not finished (CONTRIBUTING.md §6).

### A local (GPU) provider is a different story

Adding a **local** model (served by the GPU, like `bge-m3`/`bge-reranker-v2-m3` today) is **not** a simple registry extension. Non-negotiable constraints (CONTRIBUTING.md §2/§3), to validate before any code:

- **No GPU task runs in the FastAPI process.** The adapter stays a light HTTP client (see `embeddings/local.py`, `LocalEmbeddingAdapter`) — the model itself lives in the dedicated inference service (`docker/docker-compose.gpu.yml`, TEI), never loaded by the facade nor by a Celery worker outside the `gpu` queue.
- **VRAM footprint documented explicitly**, before merging: a real measurement on the target hardware (not a product-sheet estimate), and verification that the sum of the co-resident models stays under the 10 GB budget (a 2 GB margin on the 12 GB of the RTX 4080 Laptop) — see the header of `docker-compose.gpu.yml`, which documents the measured footprint of the two current models (4.66 GiB) as a style reference.
- **One model per container** (TEI has no notion of replica to tune, unlike Xinference: one container, one model, a fixed weight in VRAM — ADR-051): VRAM is bounded by the number of containers, not by an application option on the RAGEngine side.
- **Batch size configurable through the environment, with a conservative default** (see `EMBEDDING_BATCH_SIZE` in `.env.example`) — never a constant hard-coded in the adapter.
- This category of change touches a shared compose file (co-resident VRAM budget): treat it like the modification of a pipeline with a single owner of end-to-end consistency (CONTRIBUTING.md §9), not like an isolated registry extension delegated without coordination.

## 2. An ingestion engine adapter

RAGFlow is the production engine for ingestion, indexing and hybrid retrieval. It is consumed only
through `IngestionEnginePort` (`backend/app/providers/engine/base.py`), implemented by
`providers/engine/ragflow/`. The port has nine operations — `ensure_dataset`, `upload_document`,
`start_parsing`, `get_parse_status`, `cancel_parsing`, `search`, `delete_document`,
`delete_dataset`, `list_document_chunks` — and a small set of errors (`EngineError`,
`EngineValidationError`, `EngineTransientError`; only the last is retried by Celery).

The engine is chosen by one setting, and resolved by a registry in
`backend/app/providers/engine/registry.py`:

```python
_REGISTRY: Final[Mapping[str, EngineRegistration]] = {
    RAGFLOW_ENGINE_ID: EngineRegistration(
        build=RagflowEngineAdapter.from_settings,
        is_available=lambda settings: settings.ragflow_api_key is not None,
        label="RAGFlow",
        production_grade=True,
    ),
    FAKE_ENGINE_ID: EngineRegistration(
        build=lambda settings: shared_fake_engine(),
        is_available=lambda settings: True,
        label="In-memory fake engine (tests and demonstrations)",
        production_grade=False,
    ),
}
```

`INGESTION_ENGINE` (default `ragflow`) selects the entry; services and Celery tasks only ever call
`resolve_ingestion_engine(settings)` and get back an `IngestionEnginePort`.

### The reference adapter: the fake engine

`backend/app/providers/engine/fake.py` is a complete, deterministic, in-memory implementation of
the whole port: every method, every parse state (`unstart`, `running`, `done`, `fail`, `cancel`),
every error. It doubles as the single test double of the suite and as a worked example. Its first
half reads as a plain adapter:

```python
class FakeIngestionEngine:
    async def ensure_dataset(self, *, ref, name_seed, embedding_provider_id, reranker_provider_id):
        """Reuses `ref`, else creates the dataset of `name_seed` — idempotent on that name."""

    async def start_parsing(self, dataset, document, profile):
        doc = self._document(dataset, document)
        doc.run, doc.polls = "running", 0
        try:
            doc.pending = split_into_chunks(doc.content.decode("utf-8"))
        except UnicodeDecodeError:
            doc.pending_error = NOT_TEXT_MESSAGE

    async def search(self, datasets, *, query, top_k, rerank_provider_id, similarity_threshold,
                     document_refs=None, rerank_candidate_pool_size=None):
        """Lexical search: score = share of the query words found in the chunk."""
```

With `INGESTION_ENGINE=fake` the whole API runs without RAGFlow or a GPU: a document goes through
the pipeline up to `completed` and a search finds the text that was uploaded
(`backend/tests/test_fake_engine_end_to_end.py`). Know its limits:

- **It is not a production engine.** No semantic search (the dense score is always `0.0`), no OCR
  (a file that is not UTF-8 text fails to parse), no reranking, no persistence. `Settings` refuses
  `INGESTION_ENGINE=fake` when `RAGENGINE_ENV` is `staging` or `production`, and
  `GET /api/v1/providers` announces it with `engine.is_fake = true`.
- **Single process.** Its state lives in the process: an API and a Celery worker started as two
  processes do not share it. Tests and single-process demonstrations are its use.

In tests, ask the fake for a behaviour with an option rather than writing a new double:

```python
engine = FakeIngestionEngine(strict=False, statuses=[DONE], chunks=[EngineChunk(...)])
engine.failures["delete_document"] = EngineTransientError("engine unreachable")
engine.search_results = [...]            # scripted results instead of a lexical search
assert len(engine.search_calls) == 1     # what the service asked
```

### Adding a real engine

1. Implement `IngestionEnginePort` in `backend/app/providers/engine/<name>/`. Translate every
   failure of the engine into `EngineError` / `EngineValidationError` / `EngineTransientError`.
2. Declare the identifier in the `Literal` of `Settings.ingestion_engine` and register it in
   `registry.py` with `production_grade=True`.
3. Prove the contract: run the same scenarios as `backend/tests/providers/engine/test_fake.py`
   against your adapter (a container or a recorded server), and keep a fake for the unit tests of
   the services.

Be aware of the project rule: RAGFlow is the single production engine
([ADR-001](adr/001-ragflow-moteur-unique.md)); a second production engine is a product decision,
not a refactoring.

## 3. A plugin

A plugin is a Python package under `backend/app/plugins/<name>/` that exposes an attribute named
`plugin`, an instance of `app.core.plugins.Plugin`. The core discovers every sub-package of
`app.plugins` the first time `get_plugins()` is called, never names a plugin, and works without any
([ADR-069](adr/069-open-core-greffons-et-schema.md)). A plugin depends on the core, never the other
way round.

`backend/app/core/plugins.py` lists the extension points; each one has a complete, safe default
when no plugin plugs into it:

| `Plugin` field | What it adds |
|---|---|
| `routers` | Routers mounted under `/api/v1` after the core's |
| `settings_defaults` | Default values for `Settings` fields, applied only when the environment does not set them |
| `task_modules`, `task_routes` | Celery task modules and their queue routing |
| `maintenance_steps` | Categories added to the maintenance sweep |
| `api_key_scopes` | Extra API-key scopes required by the plugin's routes |
| `admits_anonymous` | Lets visitors without an account reach the access matrix |
| `llm_funding_policy`, `llm_quota_rules`, `resource_quotas` | Spending and creation limits |
| `knowledge_base_fields`, `principal_fields` | Extra fields on `KnowledgeBaseRead` and `PrincipalRead` |

### A minimal example: `ping`

`backend/app/plugin_examples/ping/` is a complete plugin — one route and one setting default.
It lives **outside** `app/plugins/`, the only package the core scans, so it is never active unless
you decide so:

```python
# backend/app/plugin_examples/ping/__init__.py
APP_NAME: str = "RAGEngine — plugin example"


def _routers() -> Sequence[APIRouter]:
    from .routes import router  # imported lazily: the module stays light at import time

    return (router,)


plugin = Plugin(
    name="ping",
    routers=_routers,
    settings_defaults={"app_name": APP_NAME},
)
```

```python
# backend/app/plugin_examples/ping/routes.py
router = APIRouter(prefix="/example", tags=["example"])


@router.get("/ping", response_model=Pong)
async def ping(settings: SettingsDep, principal: RegisteredPrincipal) -> Pong:
    del principal  # the dependency is enough: it answers 401 without a login
    return Pong(pong=True, app_name=settings.app_name)
```

To activate it, copy the folder:

```bash
cd backend
cp -r app/plugin_examples/ping app/plugins/ping
```

It uses relative imports so that the copy works unchanged. After a restart,
`GET /api/v1/example/ping` answers `{"pong": true, "app_name": "RAGEngine — plugin example"}` to a
logged-in caller, and the brand is the plugin's value until you set `RAGENGINE_APP_NAME`
(`backend/tests/test_plugin_example.py` proves each step: inactive by default, registered when
activated, discovered once copied, environment still wins).

### Rules to keep

- **Keep `__init__.py` light.** `get_plugins()` is called from low-level modules (schemas,
  dependencies, services): import routers and services inside the factories you give to `Plugin`,
  as above. An import error in a present plugin is never swallowed: a broken plugin stops the
  start-up.
- **`settings_defaults` never overrides the environment.** A variable set in `.env` (even empty)
  wins. The core alone has no spending cap and no imposed model; a plugin supplies its own through
  this field.
- **Tasks.** List the module in `task_modules` and give each task a queue in `task_routes`, in the
  same shape as the core's `TASK_ROUTES` (`backend/app/workers/celery_app.py`). Routing is
  explicit: CPU work (I/O, orchestration, polling the engine) goes to the `default` queue, and only
  work that really needs the GPU goes to the `gpu` queue, which runs with `concurrency=1`:

  ```python
  TASK_ROUTES: Final[dict[str, dict[str, str]]] = {
      PRECHECK_TASK: {"queue": DEFAULT_QUEUE},
      CHUNKING_TASK: {"queue": DEFAULT_QUEUE},
      # ...
  }
  ```

  A plugin's `Plugin(task_modules=("app.plugins.mine.tasks",), task_routes={...})` is merged into
  this table by `all_task_routes()`.

- **One owner for single-valued points.** `llm_funding_policy` and `resource_quotas` can be provided
  by at most one plugin; two providers make the start-up fail with the names of both.
- **Test the core without your plugin.** `plugins_overridden(())` builds the application with no
  plugin, which is how `backend/tests/test_core_without_plugins.py` proves the core stands alone.
