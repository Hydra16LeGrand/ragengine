"""Port `IngestionEngine` : garanties du contrat, indépendantes de tout moteur.

Ce qui est éprouvé ici tient au port lui-même — catalogue des templates de
chunking, hiérarchie d'erreurs, immuabilité des références. La traduction des
réponses RAGFlow est éprouvée dans `test_ragflow_client.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import RAGEngineError
from app.domain.enums import JobState
from app.providers.engine.base import (
    CHUNK_METHODS,
    DEFAULT_CHUNK_METHOD,
    ChunkingProfile,
    EngineDatasetRef,
    EngineDocumentRef,
    EngineError,
    EngineParseStatus,
    EngineTransientError,
    EngineValidationError,
    IngestionEnginePort,
)
from app.providers.engine.ragflow.client import RagflowEngineAdapter


def test_the_chunk_method_catalogue_matches_the_engine() -> None:
    """Treize valeurs réelles, et `general` n'en fait pas partie.

    Le nombre compte : la documentation de RAGFlow en annonce douze, son propre
    message de rejet en énumère treize (`resume` est absent de la doc).
    """
    assert len(CHUNK_METHODS) == 13
    assert "resume" in CHUNK_METHODS
    assert "general" not in CHUNK_METHODS
    assert DEFAULT_CHUNK_METHOD in CHUNK_METHODS


@pytest.mark.parametrize("chunk_method", sorted(CHUNK_METHODS))
def test_every_catalogued_chunk_method_is_accepted(chunk_method: str) -> None:
    """Aucune valeur réelle n'est refusée par erreur."""
    assert ChunkingProfile(chunk_method=chunk_method).chunk_method == chunk_method


@pytest.mark.parametrize("chunk_method", ["general", "General", "", "auto", "naive "])
def test_an_uncatalogued_chunk_method_is_refused_locally(chunk_method: str) -> None:
    """Refus immédiat plutôt qu'un `code=101` en HTTP 200, bien plus coûteux à lire."""
    with pytest.raises(PydanticValidationError):
        ChunkingProfile(chunk_method=chunk_method)


def test_a_profile_defaults_to_naive_without_parser_config() -> None:
    """Le défaut est explicite et vide : rien n'est imposé au parseur en douce."""
    profile = ChunkingProfile()

    assert profile.chunk_method == "naive"
    assert profile.parser_config == {}


def test_the_error_hierarchy_hangs_under_ragengine_error() -> None:
    """Le domaine n'attrape qu'`EngineError` ; les handlers RFC 9457 voient une `RAGEngineError`."""
    assert issubclass(EngineError, RAGEngineError)
    assert issubclass(EngineValidationError, EngineError)
    assert issubclass(EngineTransientError, EngineError)


def test_each_error_carries_its_own_problem_status() -> None:
    """502 pour une faute du tiers, 422 pour un argument, 503 pour un incident passager."""
    assert EngineError.status == 502
    assert EngineValidationError.status == 422
    assert EngineTransientError.status == 503


def test_a_transient_error_stays_distinguishable_from_a_permanent_one() -> None:
    """C'est cette distinction, et elle seule, qui pilote le retry Celery."""
    assert isinstance(EngineTransientError("x"), EngineError)
    assert not isinstance(EngineError("x"), EngineTransientError)
    assert not isinstance(EngineValidationError("x"), EngineTransientError)


@pytest.mark.parametrize(
    ("run", "expected"),
    [("unstart", False), ("running", False), ("done", True), ("fail", True), ("cancel", True)],
)
def test_terminal_states_are_the_three_the_engine_never_leaves(run: str, expected: bool) -> None:
    """La veille anti-blocage (ADR-012) n'a de sens que sur les états non terminaux."""
    status = EngineParseStatus(
        run=run,  # type: ignore[arg-type]
        inferred_phase=None,
        progress=0.0,
        last_message=None,
        chunk_count=0,
        error=None,
    )

    assert status.is_terminal is expected


def test_a_status_outside_the_documented_progress_range_is_refused() -> None:
    """`progress` vit dans [-1, 1] : le contrat du port l'affirme, donc il le vérifie."""
    with pytest.raises(PydanticValidationError):
        EngineParseStatus(
            run="running",
            inferred_phase=JobState.CHUNKING,
            progress=1.5,
            last_message=None,
            chunk_count=0,
            error=None,
        )


def test_references_are_frozen() -> None:
    """Un service peut conserver une référence sans craindre qu'elle mute sous lui."""
    dataset = EngineDatasetRef(external_id="abc")
    document = EngineDocumentRef(external_id="def")

    with pytest.raises(PydanticValidationError):
        dataset.external_id = "autre"  # type: ignore[misc]
    with pytest.raises(PydanticValidationError):
        document.external_id = "autre"  # type: ignore[misc]


def test_the_ragflow_adapter_satisfies_the_port() -> None:
    """Vérification structurelle : c'est mypy qui fait foi, ceci en est le filet."""
    adapter = RagflowEngineAdapter(base_url="http://ragflow.invalid", api_key="k")

    assert isinstance(adapter, IngestionEnginePort)
