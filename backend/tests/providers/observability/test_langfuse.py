"""Tests de `LangfuseObservability`.

Le test central de ce module est `test_export_failure_never_reaches_the_caller` :
il vérifie directement l'exigence CONTRIBUTING.md §3.4 — l'absence de configuration
**ou** la défaillance de la brique d'observabilité ne doit jamais faire échouer
la requête qui l'a déclenchée.

Aucun collecteur OTLP n'est joint : soit le traceur est simulé, soit le
`SpanProcessor` est remplacé par un double qui échoue à la demande.
"""

import base64
import json
import logging
from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from app.core.config import Settings
from app.domain.enums import JobState
from app.providers.observability import langfuse as langfuse_module
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.langfuse import (
    INGESTION_SPAN_NAME,
    RETRIEVAL_SPAN_NAME,
    LangfuseObservability,
    basic_auth_header,
    otlp_endpoint,
)

LOGGER_NAME = "app.providers.observability.langfuse"


class _ExplodingProcessor(SpanProcessor):
    """Processeur dont l'export échoue de façon synchrone, à la clôture du span.

    `SynchronousMultiSpanProcessor.on_end` ne capture rien : l'exception remonte
    donc jusqu'à `span.end()`, c'est-à-dire jusque dans l'adaptateur. C'est
    exactement le scénario que l'adaptateur doit absorber.
    """

    def on_end(self, span: ReadableSpan) -> None:
        message = "collecteur OTLP injoignable"
        raise ConnectionError(message)


class _ExplodingTracer:
    """Traceur dont l'ouverture de span échoue systématiquement."""

    def start_span(self, name: str, **kwargs: object) -> object:
        message = f"traceur indisponible pour {name}"
        raise RuntimeError(message)


def _in_memory_observability() -> tuple[LangfuseObservability, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return LangfuseObservability(provider.get_tracer("test")), exporter


def _exploding_observability() -> LangfuseObservability:
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(_ExplodingProcessor())
    return LangfuseObservability(provider.get_tracer("test"))


BUSINESS_FAILURE = "panne métier"


def _fail_inside_ingestion_span(observability: LangfuseObservability) -> None:
    """Bloc observé qui échoue : l'erreur métier doit traverser l'observabilité."""
    with observability.observe_ingestion_step(job_id=uuid4(), state=JobState.FAILED):
        raise RuntimeError(BUSINESS_FAILURE)


def _fail_inside_retrieval_span(observability: LangfuseObservability) -> None:
    """Même scénario côté retrieval."""
    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
        raise RuntimeError(BUSINESS_FAILURE)


def test_adapter_satisfies_the_port() -> None:
    """L'adaptateur réel est structurellement un `ObservabilityPort`."""
    observability, _ = _in_memory_observability()

    assert isinstance(observability, ObservabilityPort)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("https://cloud.langfuse.com", "https://cloud.langfuse.com/api/public/otel/v1/traces"),
        ("https://langfuse.interne/", "https://langfuse.interne/api/public/otel/v1/traces"),
    ],
    ids=["sans-slash-final", "avec-slash-final"],
)
def test_otlp_endpoint_is_built_from_the_host(host: str, expected: str) -> None:
    """L'endpoint OTLP dérive de `LANGFUSE_HOST` — aucune URL en dur ailleurs."""
    assert otlp_endpoint(host) == expected


def test_basic_auth_header_encodes_the_key_pair() -> None:
    """L'authentification retenue est HTTP Basic `public_key:secret_key`."""
    header = basic_auth_header("pk-lf-1", "sk-lf-2")

    scheme, _, value = header.partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(value).decode() == "pk-lf-1:sk-lf-2"


def test_from_settings_wires_the_endpoint_and_the_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'exporteur reçoit l'URL construite depuis les settings et l'en-tête Basic."""
    recorded: dict[str, object] = {}

    def _fake_exporter(*, endpoint: str, headers: Mapping[str, str]) -> object:
        recorded["endpoint"] = endpoint
        recorded["headers"] = dict(headers)
        return SimpleNamespace()

    processors: list[object] = []

    def _fake_batch_processor(exporter: object) -> SpanProcessor:
        processors.append(exporter)
        return cast(SpanProcessor, SimpleNamespace(on_start=None, on_end=None))

    monkeypatch.setattr(langfuse_module, "OTLPSpanExporter", _fake_exporter)
    monkeypatch.setattr(langfuse_module, "BatchSpanProcessor", _fake_batch_processor)
    monkeypatch.setattr(TracerProvider, "add_span_processor", lambda self, processor: None)
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.interne.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    LangfuseObservability.from_settings(Settings())

    assert recorded["endpoint"] == "https://langfuse.interne.test/api/public/otel/v1/traces"
    assert recorded["headers"] == {"Authorization": basic_auth_header("pk-lf-test", "sk-lf-test")}
    # L'export doit être asynchrone : c'est le `BatchSpanProcessor` qui bufferise.
    assert len(processors) == 1


def test_from_settings_omits_the_header_when_keys_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans clés, l'adaptateur se construit quand même — sans en-tête d'authentification."""
    recorded: dict[str, object] = {}

    def _fake_exporter(*, endpoint: str, headers: Mapping[str, str]) -> object:
        recorded["headers"] = dict(headers)
        return SimpleNamespace()

    monkeypatch.setattr(langfuse_module, "OTLPSpanExporter", _fake_exporter)
    monkeypatch.setattr(langfuse_module, "BatchSpanProcessor", lambda exporter: exporter)
    monkeypatch.setattr(TracerProvider, "add_span_processor", lambda self, processor: None)
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.interne.test")

    LangfuseObservability.from_settings(Settings())

    assert recorded["headers"] == {}


def test_spans_carry_the_domain_attributes() -> None:
    """Le port est façonné par le domaine : job, état, base de connaissances, requête."""
    observability, exporter = _in_memory_observability()
    job_id = uuid4()
    knowledge_base_id = uuid4()

    with observability.observe_ingestion_step(
        job_id=job_id, state=JobState.CHUNKING, chunk_count=12
    ):
        pass
    with observability.observe_retrieval(
        knowledge_base_id=knowledge_base_id, query="rapport annuel", top_k=5
    ):
        pass

    spans = {span.name: span for span in exporter.get_finished_spans()}
    ingestion = spans[INGESTION_SPAN_NAME].attributes or {}
    retrieval = spans[RETRIEVAL_SPAN_NAME].attributes or {}

    assert ingestion["job.id"] == str(job_id)
    assert ingestion["job.state"] == JobState.CHUNKING.value
    assert ingestion["chunk_count"] == 12
    assert retrieval["knowledge_base.id"] == str(knowledge_base_id)
    assert retrieval["retrieval.query"] == "rapport annuel"
    assert retrieval["top_k"] == 5


def test_nested_attributes_are_serialised_and_nulls_dropped() -> None:
    """OpenTelemetry n'accepte pas les structures imbriquées : elles sont sérialisées."""
    observability, exporter = _in_memory_observability()

    with observability.observe_retrieval(
        knowledge_base_id=uuid4(),
        query="q",
        filters={"lang": "fr", "year": 2026},
        cursor=None,
    ):
        pass

    attributes = exporter.get_finished_spans()[0].attributes or {}
    assert json.loads(cast(str, attributes["filters"])) == {"lang": "fr", "year": 2026}
    assert "cursor" not in attributes, "une valeur nulle est omise, pas convertie en « None »"


def test_nested_spans_are_parented() -> None:
    """Le span est activé dans le contexte courant : les appels imbriqués s'emboîtent."""
    observability, exporter = _in_memory_observability()

    # `with A, B:` entre dans A puis dans B : l'imbrication testée est bien réelle.
    with (
        observability.observe_ingestion_step(job_id=uuid4(), state=JobState.INDEXING),
        observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"),
    ):
        pass

    spans = {span.name: span for span in exporter.get_finished_spans()}
    child = spans[RETRIEVAL_SPAN_NAME]
    parent_context = spans[INGESTION_SPAN_NAME].get_span_context()

    assert child.parent is not None
    assert child.parent.span_id == parent_context.span_id


def test_export_failure_never_reaches_the_caller(caplog: pytest.LogCaptureFixture) -> None:
    """Test central (CONTRIBUTING.md §3.4) : un échec d'export est absorbé et logué.

    Le processeur simule un collecteur injoignable dont l'erreur remonte de façon
    synchrone jusqu'à `span.end()`. Le bloc observé doit s'exécuter intégralement
    et l'appelant ne doit rien voir d'autre qu'un avertissement dans les logs.
    """
    observability = _exploding_observability()
    executed: list[str] = []
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with observability.observe_ingestion_step(job_id=uuid4(), state=JobState.EMBEDDING):
        executed.append("étape d'ingestion")
    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
        executed.append("retrieval")
    observability.record_event("precheck.accepted", document_id="doc-1")

    assert executed == ["étape d'ingestion", "retrieval"]
    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert records, "l'échec doit être tracé dans les logs, pas avalé en silence"
    assert all(record.levelno == logging.WARNING for record in records)


def test_tracer_failure_never_reaches_the_caller(caplog: pytest.LogCaptureFixture) -> None:
    """Même garantie lorsque c'est l'ouverture du span qui échoue."""
    observability = LangfuseObservability(cast(Tracer, _ExplodingTracer()))
    executed: list[str] = []
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
        executed.append("retrieval")
    observability.record_event("job.failed", job_id="job-1")

    assert executed == ["retrieval"]
    assert [record.name for record in caplog.records if record.name == LOGGER_NAME]


def test_the_observed_exception_still_propagates() -> None:
    """L'observabilité n'avale pas l'erreur métier — elle la marque et la relaie."""
    observability, exporter = _in_memory_observability()

    with pytest.raises(RuntimeError, match=BUSINESS_FAILURE):
        _fail_inside_ingestion_span(observability)

    span = exporter.get_finished_spans()[0]
    assert span.status.is_ok is False
    assert span.events, "l'exception doit être enregistrée sur le span"


def test_the_observed_exception_propagates_even_when_tracing_fails() -> None:
    """Cumul des deux échecs : celui du traçage est absorbé, celui du métier passe."""
    observability = _exploding_observability()

    with pytest.raises(RuntimeError, match=BUSINESS_FAILURE):
        _fail_inside_retrieval_span(observability)


def test_record_event_attaches_to_the_surrounding_span() -> None:
    """Dans un span actif, l'événement s'y rattache plutôt que d'en créer un nouveau."""
    observability, exporter = _in_memory_observability()

    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
        observability.record_event("reranker.skipped", reason="disabled")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert [event.name for event in spans[0].events] == ["reranker.skipped"]


def test_record_event_outside_any_span_emits_a_standalone_span() -> None:
    """Hors de tout span, l'événement reste exportable : un span instantané le porte."""
    observability, exporter = _in_memory_observability()

    observability.record_event("job.queued", job_id="job-1")

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["job.queued"]
    assert (spans[0].attributes or {})["job_id"] == "job-1"
