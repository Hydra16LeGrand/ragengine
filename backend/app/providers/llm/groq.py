"""Adaptateur `LLMPort` vers l'API Chat Completions de Groq (SDK officiel `groq`).

Groq vise la compatibilité avec l'API Chat Completions d'OpenAI ; cet adaptateur
suit donc `openai.py` de très près. Deux écarts, vérifiés contre le SDK `groq`
installé (schémas Pydantic générés depuis son spec OpenAPI) et confirmés par un
appel réel (`llama-3.3-70b-versatile`, complétion et streaming) :

- **Le rôle `"system"` reste un message.** Comme OpenAI, contrairement à
  Anthropic. Les `ChatMessage` sont transmis tels quels, dans l'ordre reçu.
- **Aucun paramètre d'échantillonnage n'est envoyé.** Même philosophie que les
  deux adaptateurs existants : `temperature`/`top_p` ne sont pas exposés par le
  port et cet adaptateur ne réintroduit pas de valeur maison.
- **Pas de champ `refusal` dédié.** Contrairement à OpenAI,
  `ChatCompletionMessage` (non-stream) et `ChoiceDelta` (stream) du SDK `groq`
  n'exposent aucun attribut `refusal` — confirmé par introspection du SDK
  installé (`groq.types.chat.chat_completion_message`,
  `groq.types.chat.chat_completion_chunk`) et par un appel réel.
- **`content_filter` n'existe, côté typage, qu'en streaming.** Le littéral
  `finish_reason` de `Choice` (réponse complète, `chat_completion.py`) est
  `Literal["stop", "length", "tool_calls", "function_call"]` — `"content_filter"`
  en est absent, contrairement à celui de `Choice` en streaming
  (`chat_completion_chunk.py`), qui l'inclut. Ce n'est pas une supposition :
  mypy rejette la comparaison `choice.finish_reason == "content_filter"` sur la
  réponse non-streaming comme structurellement impossible
  (`comparison-overlap`), ce qui confirme l'introspection faite à la main.
  `complete()` ne vérifie donc `content_filter` nulle part ; un contenu manquant
  y est signalé par l'erreur générique de fin de méthode, faute de signal de
  refus dédié côté Groq pour ce mode. `stream()` le vérifie, lui, puisque le
  type l'y autorise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from typing import Final

import groq
from groq import AsyncGroq
from groq.types.chat import ChatCompletionMessageParam

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.base import (
    ChatMessage,
    LLMCapabilities,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
)

# Un seul modèle suffit à ce livrable, pour les mêmes raisons qu'`openai.py` :
# Groq n'est pas le fournisseur par défaut de RAGEngine, et chaque entrée
# ajoutée est une affirmation à maintenir.
#
# `llama-3.3-70b-versatile` est le modèle confirmé par l'exemple réel fourni
# (playground Groq) et par l'appel empirique effectué lors de l'écriture de cet
# adaptateur. Fenêtre de contexte de 131 072 tokens : valeur documentée par
# Groq (console.groq.com/docs/model/llama-3.3-70b-versatile), pas une
# supposition.
GROQ_CATALOG: Final[tuple[LLMCapabilities, ...]] = (
    LLMCapabilities(
        provider_id="llama-3.3-70b-versatile",
        vendor="groq",
        context_window=131_072,
        supports_streaming=True,
    ),
)


@contextmanager
def _translated_errors(provider_id: str) -> Iterator[None]:
    """Traduit les exceptions du SDK en erreurs de domaine (plus spécifique d'abord).

    Même ordre que pour Anthropic/OpenAI : `RateLimitError` dérive de
    `APIStatusError` dans le SDK `groq` (vérifié par introspection de
    `groq.RateLimitError.__mro__`), l'inverser masquerait le 429.
    """
    try:
        yield
    except groq.RateLimitError as exc:
        message = f"Quota Groq épuisé pour le modèle « {provider_id} »."
        raise LLMRateLimitError(message) from exc
    except groq.APIStatusError as exc:
        message = (
            f"L'API Groq a répondu {exc.status_code} pour le modèle « {provider_id} » : "
            f"{exc.message}"
        )
        raise LLMError(message) from exc
    except groq.APIConnectionError as exc:
        message = f"L'API Groq est injoignable pour le modèle « {provider_id} »."
        raise LLMError(message) from exc


def _to_message_params(messages: Sequence[ChatMessage]) -> list[ChatCompletionMessageParam]:
    """Projette la conversation — rôle `"system"` inclus — dans le format du SDK."""
    params: list[ChatCompletionMessageParam] = []
    for message in messages:
        role = message.role
        if role == "system":
            params.append({"role": "system", "content": message.content})
        elif role == "user":
            params.append({"role": "user", "content": message.content})
        else:
            params.append({"role": "assistant", "content": message.content})
    return params


def _refusal_error(provider_id: str, detail: str | None) -> LLMRefusalError:
    message = f"Le modèle « {provider_id} » a refusé de répondre à cette requête."
    return LLMRefusalError(
        message if detail is None else f"{message} Motif : {detail}",
        extensions={"provider_id": provider_id, "vendor": "groq"},
    )


class GroqLLMAdapter:
    """Implémentation de `LLMPort` adossée au client asynchrone `AsyncGroq`."""

    def __init__(self, *, capabilities: LLMCapabilities, client: AsyncGroq) -> None:
        self.capabilities = capabilities
        self._client = client

    @classmethod
    def from_settings(cls, capabilities: LLMCapabilities, settings: Settings) -> GroqLLMAdapter:
        """Construit l'adaptateur à partir de la clé API de `Settings`."""
        if settings.groq_api_key is None:
            message = f"Le modèle « {capabilities.provider_id} » requiert GROQ_API_KEY."
            raise ValidationError(message)
        client = AsyncGroq(api_key=settings.groq_api_key.get_secret_value())
        return cls(capabilities=capabilities, client=client)

    @classmethod
    def from_api_key(cls, capabilities: LLMCapabilities, api_key: str) -> GroqLLMAdapter:
        """Construit l'adaptateur depuis une clé fournie par l'appelant (clé LLM personnelle)."""
        return cls(capabilities=capabilities, client=AsyncGroq(api_key=api_key))

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Renvoie la réponse complète du modèle."""
        provider_id = self.capabilities.provider_id

        with _translated_errors(provider_id):
            response = await self._client.chat.completions.create(
                model=provider_id,
                messages=_to_message_params(messages),
                # `max_completion_tokens` : même paramètre que l'adaptateur OpenAI,
                # Groq suit la même convention (`max_tokens` est déprécié côté API).
                max_completion_tokens=max_tokens,
            )

        if not response.choices:
            raise LLMError(f"Le modèle « {provider_id} » n'a renvoyé aucune complétion.")

        # Ni `refusal` ni `content_filter` ne sont vérifiables ici (cf. docstring
        # du module) : un contenu manquant est signalé génériquement.
        content = response.choices[0].message.content
        if content is None:
            raise LLMError(f"Le modèle « {provider_id} » n'a renvoyé aucun texte.")
        return content

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        """Renvoie un itérateur asynchrone sur les fragments de texte générés."""
        return self._stream(messages, max_tokens=max_tokens)

    async def _stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        provider_id = self.capabilities.provider_id

        with _translated_errors(provider_id):
            chunks = await self._client.chat.completions.create(
                model=provider_id,
                messages=_to_message_params(messages),
                max_completion_tokens=max_tokens,
                stream=True,
            )
            async for chunk in chunks:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason == "content_filter":
                    raise _refusal_error(provider_id, "content_filter")
                if choice.delta.content:
                    yield choice.delta.content
