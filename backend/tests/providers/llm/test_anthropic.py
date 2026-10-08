"""Tests de l'`AnthropicLLMAdapter` — aucun appel réseau, le client SDK est simulé.

Les assertions portent sur **l'argument effectivement passé au SDK** (et pas
seulement sur l'absence de crash) : c'est la seule façon de verrouiller la
traduction du rôle `"system"` vers le paramètre racine `system=` de l'API Claude.
"""

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import cast

import anthropic
import httpx
import pytest
from anthropic import AsyncAnthropic, omit
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.anthropic import ANTHROPIC_CATALOG, AnthropicLLMAdapter
from app.providers.llm.base import (
    ChatMessage,
    LLMError,
    LLMPort,
    LLMRateLimitError,
    LLMRefusalError,
)

CAPABILITIES = ANTHROPIC_CATALOG[0]


def _message(*, text: str | None = "Bonjour.", stop_reason: str = "end_turn") -> SimpleNamespace:
    """Réponse minimale de l'API Messages, réduite aux champs que lit l'adaptateur."""
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class _FakeStream:
    def __init__(self, chunks: Sequence[str], final_message: SimpleNamespace) -> None:
        self._chunks = chunks
        self._final_message = final_message

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk

    async def get_final_message(self) -> SimpleNamespace:
        return self._final_message


class _FakeStreamManager:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream

    async def __aenter__(self) -> _FakeStream:
        return self._stream

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeMessages:
    """Double du namespace `client.messages` : mémorise les arguments reçus."""

    def __init__(
        self,
        *,
        response: SimpleNamespace | None = None,
        chunks: Sequence[str] = (),
        final_message: SimpleNamespace | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response if response is not None else _message()
        self._chunks = chunks
        self._final_message = final_message if final_message is not None else _message()
        self._error = error
        self.create_kwargs: dict[str, object] = {}
        self.stream_kwargs: dict[str, object] = {}

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.create_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response

    def stream(self, **kwargs: object) -> _FakeStreamManager:
        self.stream_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return _FakeStreamManager(_FakeStream(self._chunks, self._final_message))


def _adapter(messages: _FakeMessages) -> AnthropicLLMAdapter:
    client = cast(AsyncAnthropic, SimpleNamespace(messages=messages))
    return AnthropicLLMAdapter(capabilities=CAPABILITIES, client=client)


def _http_error(exc_type: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return exc_type("boom", response=httpx.Response(status, request=request), body=None)


def test_adapter_satisfies_the_port() -> None:
    """L'adaptateur est structurellement un `LLMPort`."""
    assert isinstance(_adapter(_FakeMessages()), LLMPort)


async def test_complete_moves_system_turns_to_the_system_parameter() -> None:
    """Les tours `"system"` quittent `messages` pour le paramètre racine `system=`."""
    fake = _FakeMessages(response=_message(text="Réponse."))

    answer = await _adapter(fake).complete(
        [
            ChatMessage(role="system", content="Tu réponds en français."),
            ChatMessage(role="user", content="Bonjour"),
            ChatMessage(role="assistant", content="Bonjour !"),
            ChatMessage(role="user", content="Continue."),
        ],
        max_tokens=256,
    )

    assert answer == "Réponse."
    assert fake.create_kwargs["system"] == "Tu réponds en français."
    assert fake.create_kwargs["messages"] == [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Bonjour !"},
        {"role": "user", "content": "Continue."},
    ]
    assert fake.create_kwargs["model"] == CAPABILITIES.provider_id
    assert fake.create_kwargs["max_tokens"] == 256


async def test_complete_concatenates_several_system_turns() -> None:
    """Plusieurs tours système sont fusionnés dans l'ordre reçu."""
    fake = _FakeMessages()

    await _adapter(fake).complete(
        [
            ChatMessage(role="system", content="Règle 1."),
            ChatMessage(role="user", content="Bonjour"),
            ChatMessage(role="system", content="Règle 2."),
        ],
        max_tokens=64,
    )

    assert fake.create_kwargs["system"] == "Règle 1.\n\nRègle 2."
    assert fake.create_kwargs["messages"] == [{"role": "user", "content": "Bonjour"}]


async def test_complete_omits_the_system_parameter_when_there_is_none() -> None:
    """Sans tour système, le paramètre n'est pas envoyé (sentinelle `omit` du SDK)."""
    fake = _FakeMessages()

    await _adapter(fake).complete([ChatMessage(role="user", content="Bonjour")], max_tokens=64)

    assert fake.create_kwargs["system"] is omit


async def test_complete_never_sends_sampling_parameters() -> None:
    """`temperature`/`top_p`/`top_k` sont rejetés (400) par ces modèles : jamais envoyés."""
    fake = _FakeMessages()

    await _adapter(fake).complete([ChatMessage(role="user", content="Bonjour")], max_tokens=64)

    assert set(fake.create_kwargs) == {"model", "max_tokens", "system", "messages"}


async def test_complete_raises_a_domain_error_on_refusal() -> None:
    """Un refus (`stop_reason == "refusal"`, contenu vide) est une erreur explicite.

    Sans cette vérification préalable, l'extraction du premier bloc de texte
    lèverait un `StopIteration` non géré.
    """
    fake = _FakeMessages(response=_message(text=None, stop_reason="refusal"))

    with pytest.raises(LLMRefusalError) as excinfo:
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)

    assert excinfo.value.extensions["provider_id"] == CAPABILITIES.provider_id


async def test_complete_raises_when_the_response_has_no_text_block() -> None:
    """Une réponse sans bloc de texte est une erreur de domaine, pas un `StopIteration`."""
    fake = _FakeMessages(response=_message(text=None))

    with pytest.raises(LLMError):
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)


async def test_stream_moves_system_turns_and_yields_every_chunk() -> None:
    """Le streaming applique la même traduction du rôle système et rend tous les morceaux."""
    fake = _FakeMessages(chunks=("Bon", "jour", " !"))

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

    assert chunks == ["Bon", "jour", " !"]
    assert fake.stream_kwargs["system"] == "Sois bref."
    assert fake.stream_kwargs["messages"] == [{"role": "user", "content": "Salut"}]
    assert set(fake.stream_kwargs) == {"model", "max_tokens", "system", "messages"}


async def test_stream_raises_a_domain_error_on_refusal() -> None:
    """Un refus en streaming produit un flux vide : il doit être signalé, pas ignoré."""
    fake = _FakeMessages(chunks=(), final_message=_message(text=None, stop_reason="refusal"))

    with pytest.raises(LLMRefusalError):
        async for _ in _adapter(fake).stream(
            [ChatMessage(role="user", content="…")], max_tokens=64
        ):
            pass


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (_http_error(anthropic.RateLimitError, 429), LLMRateLimitError),
        (_http_error(anthropic.APIStatusError, 500), LLMError),
        (
            anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
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
    fake = _FakeMessages(error=sdk_error)

    with pytest.raises(expected) as excinfo:
        await _adapter(fake).complete([ChatMessage(role="user", content="…")], max_tokens=64)

    assert not isinstance(excinfo.value, anthropic.APIError)
    assert excinfo.value.__cause__ is sdk_error


async def test_stream_translates_sdk_errors_too() -> None:
    """Le chemin de streaming partage la même traduction d'erreurs."""
    fake = _FakeMessages(error=_http_error(anthropic.RateLimitError, 429))

    with pytest.raises(LLMRateLimitError):
        async for _ in _adapter(fake).stream(
            [ChatMessage(role="user", content="…")], max_tokens=64
        ):
            pass


def test_from_settings_requires_the_api_key() -> None:
    """Sans `ANTHROPIC_API_KEY`, la construction échoue en erreur de domaine (422)."""
    with pytest.raises(ValidationError):
        AnthropicLLMAdapter.from_settings(CAPABILITIES, Settings())


def test_from_settings_builds_an_adapter_carrying_its_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avec la clé, l'adaptateur est construit et porte les capacités demandées."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = Settings()
    assert settings.anthropic_api_key == SecretStr("sk-ant-test")

    adapter = AnthropicLLMAdapter.from_settings(CAPABILITIES, settings)

    assert adapter.capabilities == CAPABILITIES
    assert isinstance(adapter, LLMPort)


def test_catalog_declares_the_expected_models() -> None:
    """Le catalogue enregistre exactement les trois modèles décidés, sans suffixe de date."""
    by_id = {capabilities.provider_id: capabilities for capabilities in ANTHROPIC_CATALOG}

    assert set(by_id) == {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}
    assert all(capabilities.vendor == "anthropic" for capabilities in ANTHROPIC_CATALOG)
    assert all(capabilities.supports_streaming for capabilities in ANTHROPIC_CATALOG)
    assert by_id["claude-opus-5"].context_window == 1_000_000
    assert by_id["claude-sonnet-5"].context_window == 1_000_000
    assert by_id["claude-haiku-4-5"].context_window == 200_000
