"""Tests de `FakeObservability` : mémoire des appels, exploitable par d'autres suites."""

from uuid import uuid4

import pytest

from app.domain.enums import JobState
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.fake import FakeObservability


def test_fake_satisfies_the_port() -> None:
    """Le fake est structurellement un `ObservabilityPort` et se substitue au no-op."""
    assert isinstance(FakeObservability(), ObservabilityPort)


def test_calls_are_recorded_in_order_with_their_attributes() -> None:
    """Les tests futurs vérifient QUE l'observabilité a été sollicitée, et avec quoi."""
    observability = FakeObservability()
    job_id = uuid4()
    knowledge_base_id = uuid4()

    with observability.observe_ingestion_step(job_id=job_id, state=JobState.CHUNKING, size=3):
        pass
    with observability.observe_retrieval(knowledge_base_id=knowledge_base_id, query="rapport"):
        pass
    observability.record_event("precheck.accepted", document_id="doc-1")

    assert observability.kinds() == ["ingestion_step", "retrieval", "event"]
    assert observability.calls[0].attributes == {
        "job_id": str(job_id),
        "state": JobState.CHUNKING.value,
        "size": 3,
    }
    assert observability.calls[1].attributes["query"] == "rapport"
    assert observability.calls[2].name == "precheck.accepted"


def _fail_inside_retrieval(observability: FakeObservability) -> None:
    """Bloc observé qui échoue, pour distinguer « sollicité » de « terminé »."""
    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
        raise RuntimeError("panne métier")


def test_an_interrupted_block_is_recorded_but_not_completed() -> None:
    """Un bloc qui échoue reste visible dans `calls` sans apparaître dans `completed`."""
    observability = FakeObservability()

    with pytest.raises(RuntimeError, match="panne métier"):
        _fail_inside_retrieval(observability)

    assert observability.kinds() == ["retrieval"]
    assert observability.completed == []


def test_each_instance_starts_empty() -> None:
    """Les listes sont propres à l'instance : pas d'état partagé entre tests."""
    assert FakeObservability().calls == []
    assert FakeObservability().completed == []
