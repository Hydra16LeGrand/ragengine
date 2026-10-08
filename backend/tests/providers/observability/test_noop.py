"""Tests de `NoOpObservability` : l'implémentation par défaut ne fait rien, jamais."""

from uuid import uuid4

from app.domain.enums import JobState
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.noop import NoOpObservability


def test_noop_satisfies_the_port() -> None:
    """Le no-op est structurellement un `ObservabilityPort` (pattern Null Object)."""
    assert isinstance(NoOpObservability(), ObservabilityPort)


def test_observe_ingestion_step_is_an_empty_context_manager() -> None:
    """Le context manager s'ouvre, se ferme, et ne produit aucune valeur."""
    observability = NoOpObservability()

    with observability.observe_ingestion_step(
        job_id=uuid4(), state=JobState.CHUNKING, chunk_count=12
    ) as observed:
        assert observed is None


def test_observe_retrieval_is_an_empty_context_manager() -> None:
    """Idem pour le retrieval, attributs libres compris."""
    observability = NoOpObservability()

    with observability.observe_retrieval(
        knowledge_base_id=uuid4(), query="rapport annuel", top_k=5
    ) as observed:
        assert observed is None


def test_record_event_is_ignored() -> None:
    """L'événement est ignoré silencieusement et ne renvoie rien."""
    assert NoOpObservability().record_event("precheck.accepted", document_id="doc-1") is None


def test_no_method_ever_raises() -> None:
    """Aucune méthode ne lève, même avec des attributs imbriqués ou nuls."""
    observability = NoOpObservability()
    attributes = {"nested": {"a": [1, 2]}, "missing": None}

    with observability.observe_ingestion_step(job_id=uuid4(), state=JobState.FAILED, **attributes):
        pass
    with observability.observe_retrieval(knowledge_base_id=uuid4(), query="", **attributes):
        pass
    observability.record_event("event", **attributes)


def test_exceptions_from_the_observed_block_still_propagate() -> None:
    """Le no-op n'avale pas l'erreur métier du bloc qu'il encadre."""
    observability = NoOpObservability()
    raised = False

    try:
        with observability.observe_retrieval(knowledge_base_id=uuid4(), query="q"):
            raise RuntimeError("panne métier")
    except RuntimeError:
        raised = True

    assert raised
