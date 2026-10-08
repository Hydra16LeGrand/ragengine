"""Tests du port `LLMPort` : descripteur de capacités et découpage du prompt système."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.providers.llm.base import ChatMessage, LLMCapabilities, split_system_prompt

CAPABILITIES = LLMCapabilities(
    provider_id="modele-test",
    vendor="anthropic",
    context_window=1_000,
    supports_streaming=True,
)


def test_capabilities_are_frozen() -> None:
    """Un descripteur de capacités est immuable (§0) : nul ne le mute en cours de route."""
    with pytest.raises(PydanticValidationError):
        CAPABILITIES.context_window = 42


def test_split_system_prompt_separates_roles_and_preserves_order() -> None:
    """Les tours système sortent de la conversation ; l'ordre du reste est conservé."""
    system, conversation = split_system_prompt(
        [
            ChatMessage(role="system", content="Règle 1."),
            ChatMessage(role="user", content="Bonjour"),
            ChatMessage(role="system", content="Règle 2."),
            ChatMessage(role="assistant", content="Salut"),
        ]
    )

    assert system == "Règle 1.\n\nRègle 2."
    assert [(message.role, message.content) for message in conversation] == [
        ("user", "Bonjour"),
        ("assistant", "Salut"),
    ]


def test_split_system_prompt_returns_none_without_system_turn() -> None:
    """Sans tour système, l'adaptateur doit pouvoir omettre le paramètre plutôt qu'envoyer ""."""
    system, conversation = split_system_prompt([ChatMessage(role="user", content="Bonjour")])

    assert system is None
    assert len(conversation) == 1
