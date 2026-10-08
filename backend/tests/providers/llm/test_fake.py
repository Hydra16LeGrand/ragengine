"""Tests du `FakeLLMAdapter` : contrat du port respecté, sans réseau."""

import pytest

from app.core.errors import ValidationError
from app.providers.llm.base import ChatMessage, LLMPort
from app.providers.llm.fake import ECHO_PREFIX, FakeLLMAdapter


def _conversation() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="Tu es concis."),
        ChatMessage(role="user", content="Bonjour"),
        ChatMessage(role="assistant", content="Bonjour !"),
        ChatMessage(role="user", content="Résume ce document en une phrase, s'il te plaît."),
    ]


def test_fake_adapter_satisfies_the_port() -> None:
    """Le fake est structurellement un `LLMPort` : les services peuvent le substituer."""
    assert isinstance(FakeLLMAdapter(), LLMPort)


async def test_complete_echoes_the_last_user_message() -> None:
    """La réponse est déterministe : l'écho du dernier tour utilisateur."""
    answer = await FakeLLMAdapter().complete(_conversation(), max_tokens=128)

    assert answer == f"{ECHO_PREFIX}Résume ce document en une phrase, s'il te plaît."


async def test_stream_yields_several_chunks_that_rebuild_the_answer() -> None:
    """Le streaming produit plusieurs morceaux ; leur concaténation est la réponse."""
    adapter = FakeLLMAdapter()
    messages = _conversation()

    chunks = [chunk async for chunk in adapter.stream(messages, max_tokens=128)]

    assert len(chunks) > 1, "un seul morceau n'exercerait pas le chemin de streaming"
    assert "".join(chunks) == await adapter.complete(messages, max_tokens=128)
    assert all(chunks), "aucun morceau vide ne doit être émis"


async def test_complete_requires_a_user_message() -> None:
    """Une conversation sans tour utilisateur est une erreur de domaine, pas un crash."""
    with pytest.raises(ValidationError):
        await FakeLLMAdapter().complete(
            [ChatMessage(role="system", content="Tu es concis.")], max_tokens=64
        )


def test_capabilities_declare_streaming_support() -> None:
    """Le descripteur de capacités reflète ce que l'implémentation sait faire."""
    capabilities = FakeLLMAdapter().capabilities

    assert capabilities.supports_streaming is True
    assert capabilities.vendor == "anthropic"
