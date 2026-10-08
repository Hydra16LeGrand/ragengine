"""Tests de l'`OpenAILLMAdapter` — aucun appel réseau, le client SDK est simulé.

Le pendant du test Anthropic : ici le rôle `"system"` doit **rester** un message
de la liste `messages`, puisque l'API Chat Completions l'attend ainsi. Les
assertions portent sur l'argument effectivement passé à `create`.
"""

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import cast

import httpx
import openai
import pytest
from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.base import (
    ChatMessage,
    LLMError,
    LLMPort,
    LLMRateLimitError,
    LLMRefusalError,
)
from app.providers.llm.openai import OPENAI_CATALOG, OpenAILLMAdapter

CAPABILITIES = OPENAI_CATALOG[0]


def _completion(
    *,
    content: str | None = "Réponse.",
    refusal: str | None = None,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    """Réponse minimale de Chat Completions, réduite aux champs lus par l'adaptateur."""
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _chunk(
    *, content: str | None = None, refusal: str | None = None, finish_reason: str | None = None
) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


class _FakeCompletions:
    """Double du namespace `client.chat.completions` : mémorise les arguments reçus."""

    def __init__(
        self,
        *,
        completion: SimpleNamespace | None = None,
        chunks: Sequence[SimpleNamespace] = (),
        error: Exception | None = None,
    ) -> None:
        self._completion = completion if completion is not None else _completion()
        self._chunks = chunks
        self._error = error
        self.kwargs: dict[str, object] = {}

    async def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        if kwargs.get("stream"):
            return self._stream()
        return self._completion

    async def _stream(self) -> AsyncIterator[SimpleNamespace]:
        for chunk in self._chunks:
            yield chunk


def _adapter(completions: _FakeCompletions) -> OpenAILLMAdapter:
    client = cast(AsyncOpenAI, SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    return OpenAILLMAdapter(capabilities=CAPABILITIES, client=client)


def _http_error(exc_type: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return exc_type("boom", response=httpx.Response(status, request=request), body=None)


def test_adapter_satisfies_the_port() -> None:
    """L'adaptateur est structurellement un `LLMPort`."""
    assert isinstance(_adapter(_FakeCompletions()), LLMPort)


async def test_complete_keeps_the_system_turn_inside_messages() -> None:
    """Contrairement à Claude, OpenAI reçoit le prompt système comme message."""
    fake = _FakeCompletions()

    answer = await _adapter(fake).complete(
        [
            ChatMessage(role="system", content="Tu réponds en français."),
            ChatMessage(role="user", content="Bonjour"),
            ChatMessage(role="assistant", content="Bonjour !"),
        ],
        max_tokens=256,
    )

    assert answer == "Réponse."
    assert fake.kwargs["messages"] == [
        {"role": "system", "content": "Tu réponds en français."},
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Bonjour !"},
    ]
    assert fake.kwargs["model"] == CAPABILITIES.provider_id
    assert fake.kwargs["max_completion_tokens"] == 256
    assert "system" not in fake.kwargs, "`system=` est propre à l'API Anthropic"


async def test_complete_sends_no_sampling_parameter() -> None:
    """Le port n'expose pas `temperature` et l'adaptateur n'en réintroduit pas."""
    fake = _FakeCompletions()

    await _adapter(fake).complete([ChatMessage(role="user", content="Bonjour")], max_tokens=64)

    assert set(fake.kwargs) == {"model", "messages", "max_completion_tokens"}


async def test_complete_raises_a_domain_error_on_refusal() -> None:
    """Le champ `refusal` est vérifié avant la lecture du contenu, qui est `None`."""
    fake = _FakeCompletions(completion=_completion(content=None, refusal="Politique interne."))

    with pytest.raises(LLMRefusalError) as excinfo:
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)

    assert excinfo.value.extensions["vendor"] == "openai"


async def test_complete_raises_a_domain_error_on_content_filter() -> None:
    """Un arrêt sur `content_filter` est traité comme un refus explicite."""
    fake = _FakeCompletions(completion=_completion(content=None, finish_reason="content_filter"))

    with pytest.raises(LLMRefusalError):
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)


async def test_complete_raises_when_no_choice_is_returned() -> None:
    """Une réponse sans complétion est une erreur de domaine, pas un `IndexError`."""
    fake = _FakeCompletions(completion=SimpleNamespace(choices=[]))

    with pytest.raises(LLMError):
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)


async def test_stream_yields_every_delta_and_keeps_the_system_turn() -> None:
    """Le streaming rend les fragments non vides et applique la même projection."""
    fake = _FakeCompletions(
        chunks=(
            _chunk(content="Bon"),
            _chunk(content=None),
            _chunk(content="jour"),
            _chunk(content="", finish_reason="stop"),
        )
    )

    chunks = [
        chunk
        async for chunk in _adapter(fake).stream(
            [
                ChatMessage(role="system", content="Sois bref."),
                ChatMessage(role="user", content="Salut"),
            ],
            max_tokens=128,
        )
    ]

    assert chunks == ["Bon", "jour"]
    assert fake.kwargs["messages"][0] == {"role": "system", "content": "Sois bref."}
    assert fake.kwargs["stream"] is True


async def test_stream_raises_a_domain_error_on_content_filter() -> None:
    """Un flux interrompu par le filtre de contenu est signalé, pas tronqué en silence."""
    fake = _FakeCompletions(chunks=(_chunk(content="Bon"), _chunk(finish_reason="content_filter")))

    with pytest.raises(LLMRefusalError):
        async for _ in _adapter(fake).stream(
            [ChatMessage(role="user", content="…")], max_tokens=64
        ):
            pass


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (_http_error(openai.RateLimitError, 429), LLMRateLimitError),
        (_http_error(openai.APIStatusError, 500), LLMError),
        (
            openai.APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            ),
            LLMError,
        ),
    ],
    ids=["rate-limit", "status", "connection"],
)
async def test_sdk_errors_are_translated_to_domain_errors(
    sdk_error: Exception, expected: type[LLMError]
) -> None:
    """Aucune exception SDK brute ne franchit le port (§0 du document d'architecture)."""
    fake = _FakeCompletions(error=sdk_error)

    with pytest.raises(expected) as excinfo:
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)

    assert not isinstance(excinfo.value, openai.APIError)
    assert excinfo.value.__cause__ is sdk_error


def test_from_settings_requires_the_api_key() -> None:
    """Sans `OPENAI_API_KEY`, la construction échoue en erreur de domaine (422)."""
    with pytest.raises(ValidationError):
        OpenAILLMAdapter.from_settings(CAPABILITIES, Settings())


def test_from_settings_builds_an_adapter_carrying_its_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avec la clé, l'adaptateur est construit et porte les capacités demandées."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    adapter = OpenAILLMAdapter.from_settings(CAPABILITIES, Settings())

    assert adapter.capabilities == CAPABILITIES
    assert isinstance(adapter, LLMPort)


def test_catalog_declares_a_single_verifiable_model() -> None:
    """Le catalogue OpenAI reste volontairement minimal, avec un ID vérifiable."""
    assert [capabilities.provider_id for capabilities in OPENAI_CATALOG] == ["gpt-4.1"]
    assert CAPABILITIES.vendor == "openai"
    assert CAPABILITIES.supports_streaming is True
