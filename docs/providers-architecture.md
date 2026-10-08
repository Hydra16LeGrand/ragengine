# Provider architecture — v1 (Phase 2)

Normative reference of the `backend/app/providers/` layer: ports, capability descriptors, the factory/registry mechanism, and the contract of `GET /api/v1/providers`. Complements `docs/domain-model.md` without duplicating it. Conforms to CONTRIBUTING.md §3.1.

## 0. Cross-cutting principles

- A **port** is a Python interface (`typing.Protocol`, structural) in `app/providers/<domain>/base.py`. Services and routers import only the port, never a concrete adapter.
- Each port carries an immutable **capability descriptor** (Pydantic, `frozen=True`): what the implementation can do, not how. Services can inspect the capabilities (e.g. supported languages) without knowing the adapter.
- A **factory** resolves a model identifier (`str`) to a port instance, through a **registry**: a `Mapping[str, ProviderRegistration]` built when the module is loaded (no dynamic discovery, no plugin — the list of supported providers is a deliberate choice made in code, consistent with "no over-engineering").
- **Availability ≠ reachability.** The registry declares a provider `available` if it has the configuration it needs (API key present, URL set) — never through a network call when the catalogue is built. A provider with `available=true` can still fail at run time; guaranteeing otherwise is not the catalogue's job.
- No port raises a raw SDK exception: every adapter translates the third party's errors into `RAGEngineError` (or a subclass) before they reach the service.

## 1. `EmbeddingPort` — `app/providers/embeddings/`

```python
class EmbeddingCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider_id: str          # resolution key in the registry, e.g. "bge-m3"
    dimensions: int
    max_batch_size: int
    max_input_tokens: int
    languages: tuple[str, ...] | None   # None = unrestricted multilingual
    is_local: bool            # True = served by the GPU inference service (Phase 3)

class EmbeddingPort(Protocol):
    capabilities: EmbeddingCapabilities

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

`embed_documents` and `embed_query` are **distinct** from the start: several model families (BGE included) expect an asymmetric query/passage treatment (a different instruction prefix). A symmetric adapter implements both by calling the same internal method — that is free; the opposite (adding asymmetry afterwards) is not, because it changes the port signature and every caller.

### Expected adapters
- `local.py` — `LocalEmbeddingAdapter`: HTTP client to `settings.embedding_inference_base_url`, OpenAI-compatible contract (`POST /v1/embeddings`). **Written in Phase 2 in anticipation, confirmed exact in Phase 3 against a real Xinference instance (ADR-011)**: no correction was needed. The inference service is now Text Embeddings Inference (ADR-051), which serves this route next to its native `/embed`.
- `openai.py` — `OpenAIEmbeddingAdapter`: real, `text-embedding-3-large` by default, uses `settings.openai_api_key`.
- `fake.py` — `FakeEmbeddingAdapter`: deterministic (text hash → fixed-dimension vector), for the service orchestration tests.

## 2. `RerankerPort` — `app/providers/rerankers/`

```python
class RerankerCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider_id: str
    max_documents: int
    languages: tuple[str, ...] | None
    is_local: bool

class RerankResult(BaseModel):
    index: int      # position in the input `documents` list
    score: float

class RerankerPort(Protocol):
    capabilities: RerankerCapabilities

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]: ...
```

### Expected adapters
- `local.py` — `LocalRerankerAdapter`: HTTP client to `settings.reranker_inference_base_url`, **native TEI** contract (`POST /rerank`, `{query, texts, raw_scores}` → bare array `[{index, score}, ...]`), since Xinference was replaced by Text Embeddings Inference (ADR-051). This document used to describe it as `POST /v1/rerank` (Cohere/Jina dialect) until that correction: exact under Xinference (Phase 3, ADR-011), obsolete since the TEI migration — that route does not exist on the TEI side, which only exposes `/rerank`.
- `passthrough.py` — `PassthroughReranker`: returns the documents in their input order with a constant score. It is the default implementation when reranking is disabled for a KB (`reranker_model_id IS NULL`) — Null Object pattern, not a conditional branch in the services.
- `fake.py` — for tests (deterministic score based on the position).

## 3. `LLMPort` — `app/providers/llm/`

```python
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class LLMCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider_id: str
    vendor: Literal["anthropic", "openai", "groq"]
    context_window: int
    supports_streaming: bool

class LLMPort(Protocol):
    capabilities: LLMCapabilities

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> str: ...

    def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]: ...
```

`stream` exists from now on: the Playground (Phase 7) is a chat, and designing the port without streaming then adding it after the fact would break every caller already written. An adapter that does not support streaming (no such case is planned here, but the clause exists for a future provider) signals it through `capabilities.supports_streaming = False`; the calling service decides, the port does not guess.

**No `temperature` in the port** — a deliberate decision, not an oversight. The current Claude models (Opus 5, Sonnet 5, Fable 5) **reject** this parameter (HTTP 400): behaviour is steered through the prompt and the reasoning effort, not through sampling. Exposing `temperature` in a cross-vendor port would have produced an interface that breaks at run time on the default provider. An adapter that needs it (OpenAI, if applicable) handles it internally with its own default value — it is not a concept shared between providers.

### Expected adapters
- `anthropic.py` — `AnthropicLLMAdapter`: real, official `anthropic` SDK (`AsyncAnthropic`, `await client.messages.create(...)`; streaming through `client.messages.stream(...)` + `stream.text_stream`), uses `settings.anthropic_api_key`. **Model catalogue to register** (exact IDs, no date suffix): `claude-opus-5` (default — maximum quality), `claude-sonnet-5` (cost/quality balance), `claude-haiku-4-5` (fast/cheap, useful for evaluation generation in Phase 8). Do not invent another ID; do not use a dated ID.
- `openai.py` — `OpenAILLMAdapter`: real, official `openai` SDK, uses `settings.openai_api_key`.
- `groq.py` — `GroqLLMAdapter`: real, official `groq` SDK (`AsyncGroq`, Chat Completions API almost identical to OpenAI — `"system"` role as a message, `max_completion_tokens`). Unlike OpenAI, the SDK exposes **no** dedicated `refusal` field (neither on `message`, nor on `delta` when streaming); only the `content_filter` stop reason signals filtered content. Catalogue: `llama-3.3-70b-versatile` (131,072-token window, documented by Groq), uses `settings.groq_api_key`.
- `fake.py` — deterministic response (echo of the last message), for tests.

## 4. `ObservabilityPort` — `app/providers/observability/`

The port exposes a **domain-shaped** API, not a 1:1 wrapper of OpenTelemetry: services must know nothing about spans or traces.

```python
class ObservabilityPort(Protocol):
    def observe_ingestion_step(
        self, *, job_id: UUID, state: JobState, **attributes: JsonValue
    ) -> AbstractContextManager[None]: ...

    def observe_retrieval(
        self, *, knowledge_base_id: UUID, query: str, **attributes: JsonValue
    ) -> AbstractContextManager[None]: ...

    def record_event(self, name: str, **attributes: JsonValue) -> None: ...
```

### Expected adapters
- `noop.py` — `NoOpObservability`: every method is a strict no-op (empty context managers, `record_event` ignored). **It is the default implementation** (CONTRIBUTING.md §3.4) — never an `if settings.observability_enabled` branch scattered in a service.
- `langfuse.py` — `LangfuseObservability`: real, through the standard OpenTelemetry SDK (`opentelemetry-sdk`) exporting to Langfuse's OTLP endpoint (`settings.langfuse_host`), authenticated with `settings.langfuse_public_key`/`langfuse_secret_key`. **Asynchronous export, never blocking** (CONTRIBUTING.md §4 of the product brief): an export failure must never make the request that triggered it fail — it catches and logs the error, it does not propagate it.
- `fake.py` — records the calls in memory (a list), so that tests verify THAT observability was called without depending on a real collector.

## 5. `IngestionEnginePort`

The ingestion engine port (`app/providers/engine/base.py`) was **deliberately not defined in Phase 2**: designing it before auditing RAGFlow's real HTTP API (planned for Phase 4) would have frozen an interface on assumptions. It is now defined and implemented by `providers/engine/ragflow/`, with a registry (`providers/engine/registry.py`) and an in-memory fake (`providers/engine/fake.py`). Its normative reference is `docs/ingestion-engine-architecture.md`, and `docs/extending.md` §2 explains how to add an engine.

## 6. Registry and factories

One module per domain, e.g. `app/providers/embeddings/registry.py`:

```python
@dataclass(frozen=True)
class ProviderRegistration:
    capabilities: EmbeddingCapabilities
    build: Callable[[Settings], EmbeddingPort]
    is_available: Callable[[Settings], bool]

_REGISTRY: Final[Mapping[str, ProviderRegistration]] = {
    "bge-m3": ProviderRegistration(..., build=..., is_available=lambda s: True),
    "text-embedding-3-large": ProviderRegistration(
        ..., build=OpenAIEmbeddingAdapter.from_settings,
        is_available=lambda s: s.openai_api_key is not None,
    ),
}

def resolve_embedding_provider(model_id: str, settings: Settings) -> EmbeddingPort:
    registration = _REGISTRY.get(model_id)
    if registration is None or not registration.is_available(settings):
        raise ValidationError(f"Modèle d'embedding « {model_id} » indisponible.")
    return registration.build(settings)

def list_embedding_providers(settings: Settings) -> list[EmbeddingProviderStatus]: ...
```

The same scheme applies to `rerankers/registry.py` and `llm/registry.py` (the LLM registration also carries a `label` and a `build_with_key`, which builds the adapter from a personal key of the user, see `docs/api-guide.md` §4.3). `observability/` has **no** multiple-choice registry: it is a binary switch (Langfuse configured or not), resolved directly by a `get_observability(settings) -> ObservabilityPort` factory in `observability/factory.py`.

The **default** `provider_id` of a new KB comes from `Settings` (`EMBEDDING_MODEL_ID`, `RERANKER_MODEL_ID`, declared since Phase 0); the `POST /api/v1/knowledge-bases` payload can override it (CONTRIBUTING.md §1: configurable through the API payload).

## 7. `GET /api/v1/providers`

```json
{
  "embeddings": [
    {"provider_id": "bge-m3", "is_local": true, "dimensions": 1024, "languages": null, "available": true},
    {"provider_id": "text-embedding-3-large", "is_local": false, "dimensions": 3072, "languages": null, "available": false}
  ],
  "rerankers": [ ... ],
  "llm": [ ... ],
  "observability": {"provider_id": "langfuse", "available": false}
}
```

`available` reflects the configuration (§0), not a network ping. Read-only route, accessible without authentication (it is a capability catalogue, not user data) — consistent with the possibility for a signed-out visitor to compose a configuration before creating an account.

## 8. Integration with `KnowledgeBaseService` (Phase 1, already delivered)

`KnowledgeBaseService.create` (`backend/app/services/knowledge_bases.py`) used to accept `embedding_model_id`/`reranker_model_id` as free strings, without validation. This phase added a check against the registries (`resolve_embedding_provider`/`resolve_reranker_provider`, confirming only existence and availability — **not** instantiating the adapter at creation, which would open a connection for nothing). An unknown or unavailable `model_id` raises `ValidationError` → 422, before the database write. It is a targeted modification of a file already verified in Phase 1: it was made in the main window, not delegated, so as not to reopen a stabilized service to an agent that did not write it.
