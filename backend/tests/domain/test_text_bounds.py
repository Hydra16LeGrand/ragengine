"""Bornes de taille des textes envoyés au GPU et au LLM (audit sécurité 2026-09, ADR-053).

Ce que ces tests verrouillent : chaque borne rejette **un caractère au-delà** et
accepte la borne elle-même, et le type d'erreur est celui que le frontend
reconnaît (`frontend/src/api/text-limits.ts`) pour afficher un message propre
plutôt que le `detail` générique d'un `422`.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain.schemas import DocumentUpdate, KnowledgeBaseCreate
from app.domain.schemas.common import MAX_PROVIDER_ID_LENGTH, MAX_THEME_LENGTH
from app.domain.schemas.retrieval import (
    MAX_ASSISTANT_TURN_LENGTH,
    MAX_CHAT_MAX_TOKENS,
    MAX_CHAT_MESSAGES,
    MAX_CHAT_TRANSCRIPT_LENGTH,
    MAX_FILTER_DOCUMENT_IDS,
    MAX_FILTER_VALUES,
    MAX_QUERY_LENGTH,
    RetrievalChatRequest,
    RetrievalSearchRequest,
)

KB_ID = str(uuid.uuid4())


def _user(length: int) -> dict[str, str]:
    return {"role": "user", "content": "u" * length}


def _assistant(length: int) -> dict[str, str]:
    return {"role": "assistant", "content": "a" * length}


def _chat(messages: list[dict[str, str]], **overrides: object) -> RetrievalChatRequest:
    payload: dict[str, object] = {
        "knowledge_base_id": KB_ID,
        "llm_provider_id": "claude",
        "messages": messages,
    }
    return RetrievalChatRequest.model_validate(payload | overrides)


def _error_types(excinfo: pytest.ExceptionInfo[ValidationError]) -> set[str]:
    return {error["type"] for error in excinfo.value.errors()}


def _search(query: str) -> RetrievalSearchRequest:
    return RetrievalSearchRequest.model_validate({"knowledge_base_id": KB_ID, "query": query})


def test_the_search_query_is_bounded() -> None:
    _search("q" * MAX_QUERY_LENGTH)

    with pytest.raises(ValidationError) as excinfo:
        _search("q" * (MAX_QUERY_LENGTH + 1))

    assert _error_types(excinfo) == {"string_too_long"}


def test_surrounding_whitespace_does_not_count_against_the_query_bound() -> None:
    """`InputModel` strippe avant de mesurer : un copier-coller aéré n'est pas refusé."""
    request = _search("  " + "q" * MAX_QUERY_LENGTH + "\n\n")

    assert len(request.query) == MAX_QUERY_LENGTH


def test_a_user_turn_is_bounded_like_a_query() -> None:
    """Le dernier tour `user` devient la requête (1er tour, repli) : même borne que `query`."""
    _chat([_user(MAX_QUERY_LENGTH)])

    with pytest.raises(ValidationError) as excinfo:
        _chat([_user(MAX_QUERY_LENGTH + 1)])

    assert _error_types(excinfo) == {"string_too_long"}
    assert excinfo.value.errors()[0]["loc"] == ("messages", 0, "content")


def test_an_assistant_turn_fits_the_longest_answer_the_facade_can_generate() -> None:
    """Rejouer une réponse réelle ne doit jamais être refusé.

    ~4 caractères par token en français : une réponse au plafond fait ~16 400
    caractères, la borne en tolère près du double (7 caractères par token).
    """
    assert MAX_ASSISTANT_TURN_LENGTH >= 7 * MAX_CHAT_MAX_TOKENS
    _chat([_user(10), _assistant(MAX_ASSISTANT_TURN_LENGTH), _user(10)])

    with pytest.raises(ValidationError) as excinfo:
        _chat([_user(10), _assistant(MAX_ASSISTANT_TURN_LENGTH + 1), _user(10)])

    assert _error_types(excinfo) == {"string_too_long"}


def test_the_number_of_turns_is_bounded() -> None:
    _chat([_user(1)] * MAX_CHAT_MESSAGES)

    with pytest.raises(ValidationError) as excinfo:
        _chat([_user(1)] * (MAX_CHAT_MESSAGES + 1))

    assert _error_types(excinfo) == {"too_long"}


def test_the_total_transcript_length_is_bounded() -> None:
    # Deux tours `assistant` au maximum remplissent exactement le budget total.
    assert 2 * MAX_ASSISTANT_TURN_LENGTH == MAX_CHAT_TRANSCRIPT_LENGTH
    _chat(
        [_assistant(MAX_ASSISTANT_TURN_LENGTH), _assistant(MAX_ASSISTANT_TURN_LENGTH - 1), _user(1)]
    )

    with pytest.raises(ValidationError) as excinfo:
        _chat(
            [_assistant(MAX_ASSISTANT_TURN_LENGTH), _assistant(MAX_ASSISTANT_TURN_LENGTH), _user(1)]
        )

    assert _error_types(excinfo) == {"chat_transcript_too_long"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_ids", [str(uuid.uuid4())] * (MAX_FILTER_DOCUMENT_IDS + 1)),
        ("languages", ["fr"] * (MAX_FILTER_VALUES + 1)),
        ("languages", ["x" * 36]),
        ("content_types", ["application/pdf"] * (MAX_FILTER_VALUES + 1)),
        ("content_types", ["x" * 256]),
        ("themes", ["rh"] * (MAX_FILTER_VALUES + 1)),
        ("themes", ["x" * (MAX_THEME_LENGTH + 1)]),
    ],
)
def test_retrieval_filters_are_bounded(field: str, value: list[str]) -> None:
    with pytest.raises(ValidationError):
        RetrievalSearchRequest.model_validate(
            {"knowledge_base_id": KB_ID, "query": "q", field: value}
        )


def test_provider_identifiers_and_theme_are_bounded() -> None:
    with pytest.raises(ValidationError):
        _chat([_user(1)], llm_provider_id="x" * (MAX_PROVIDER_ID_LENGTH + 1))
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(name="Base", embedding_model_id="x" * (MAX_PROVIDER_ID_LENGTH + 1))
    with pytest.raises(ValidationError):
        DocumentUpdate(theme="x" * (MAX_THEME_LENGTH + 1))
