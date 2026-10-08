"""Adaptateur `LLMPort` vers l'API Messages d'Anthropic (SDK officiel).

Trois particularités de l'API Claude sont absorbées ici, pour qu'aucun service
n'ait à les connaître :

1. **Le prompt système n'est pas un message.** Contrairement à OpenAI, Claude le
   reçoit dans un paramètre racine `system=`. Les `ChatMessage` de rôle
   `"system"` sont donc extraits de la séquence et concaténés.
2. **Aucun paramètre d'échantillonnage n'est envoyé.** `temperature`, `top_p` et
   `top_k` sont rejetés par les modèles enregistrés ici (HTTP 400). La requête
   reste minimale : `model`, `max_tokens`, `system`, `messages` — le
   comportement de raisonnement par défaut convient à ce livrable.
3. **Un refus est un succès HTTP.** `stop_reason == "refusal"` renvoie un 200
   dont `content` peut être vide ; il est détecté *avant* la lecture du contenu,
   sinon l'extraction du premier bloc de texte lèverait un `StopIteration`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from typing import Final

import anthropic
from anthropic import AsyncAnthropic, omit
from anthropic.types import MessageParam

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.base import (
    ChatMessage,
    LLMCapabilities,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    split_system_prompt,
)

ANTHROPIC_CATALOG: Final[tuple[LLMCapabilities, ...]] = (
    # IDs exacts, sans suffixe de date (docs/providers-architecture.md §3).
    LLMCapabilities(
        provider_id="claude-opus-5",
        vendor="anthropic",
        context_window=1_000_000,
        supports_streaming=True,
    ),
    LLMCapabilities(
        provider_id="claude-sonnet-5",
        vendor="anthropic",
        context_window=1_000_000,
        supports_streaming=True,
    ),
    LLMCapabilities(
        # Seul modèle du catalogue à 200 k : utile pour la génération
        # d'évaluation (Phase 8), où le coût prime sur la fenêtre.
        provider_id="claude-haiku-4-5",
        vendor="anthropic",
        context_window=200_000,
        supports_streaming=True,
    ),
)

DEFAULT_ANTHROPIC_PROVIDER_ID: Final = "claude-opus-5"
"""Modèle par défaut du catalogue : qualité maximale."""


@contextmanager
def _translated_errors(provider_id: str) -> Iterator[None]:
    """Traduit les exceptions du SDK en erreurs de domaine.

    Ordre du plus spécifique au plus général : `RateLimitError` dérive de
    `APIStatusError`, l'inverser masquerait le 429.
    """
    try:
        yield
    except anthropic.RateLimitError as exc:
        message = f"Quota Anthropic épuisé pour le modèle « {provider_id} »."
        raise LLMRateLimitError(message) from exc
    except anthropic.APIStatusError as exc:
        message = (
            f"L'API Anthropic a répondu {exc.status_code} pour le modèle « {provider_id} » : "
            f"{exc.message}"
        )
        raise LLMError(message) from exc
    except anthropic.APIConnectionError as exc:
        message = f"L'API Anthropic est injoignable pour le modèle « {provider_id} »."
        raise LLMError(message) from exc


def _to_message_params(conversation: Sequence[ChatMessage]) -> list[MessageParam]:
    """Projette les tours `user`/`assistant` dans le format attendu par le SDK."""
    params: list[MessageParam] = []
    for message in conversation:
        role = message.role
        if role == "system":  # pragma: no cover - retiré en amont par split_system_prompt
            continue
        params.append({"role": role, "content": message.content})
    return params


def _refusal_error(provider_id: str) -> LLMRefusalError:
    return LLMRefusalError(
        f"Le modèle « {provider_id} » a refusé de répondre à cette requête.",
        extensions={"provider_id": provider_id, "vendor": "anthropic"},
    )


class AnthropicLLMAdapter:
    """Implémentation de `LLMPort` adossée au client asynchrone `AsyncAnthropic`."""

    def __init__(self, *, capabilities: LLMCapabilities, client: AsyncAnthropic) -> None:
        self.capabilities = capabilities
        self._client = client

    @classmethod
    def from_settings(
        cls, capabilities: LLMCapabilities, settings: Settings
    ) -> AnthropicLLMAdapter:
        """Construit l'adaptateur à partir de la clé API de `Settings`."""
        if settings.anthropic_api_key is None:
            message = f"Le modèle « {capabilities.provider_id} » requiert ANTHROPIC_API_KEY."
            raise ValidationError(message)
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        return cls(capabilities=capabilities, client=client)

    @classmethod
    def from_api_key(cls, capabilities: LLMCapabilities, api_key: str) -> AnthropicLLMAdapter:
        """Construit l'adaptateur à partir d'une clé fournie par l'appelant.

        Chemin des clés LLM personnelles (`UserLlmKeyService.resolve_key`) : pas
        de lecture de `Settings`, la clé a déjà été déchiffrée par l'appelant.
        """
        return cls(capabilities=capabilities, client=AsyncAnthropic(api_key=api_key))

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Renvoie la réponse complète du modèle."""
        system, conversation = split_system_prompt(messages)
        provider_id = self.capabilities.provider_id

        with _translated_errors(provider_id):
            response = await self._client.messages.create(
                model=provider_id,
                max_tokens=max_tokens,
                system=system if system is not None else omit,
                messages=_to_message_params(conversation),
            )

        # Vérifié avant toute lecture de `content` : sur un refus la liste peut
        # être vide et l'extraction du premier bloc échouerait.
        if response.stop_reason == "refusal":
            raise _refusal_error(provider_id)

        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            message = f"Le modèle « {provider_id} » n'a renvoyé aucun bloc de texte."
            raise LLMError(message)
        return text

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        """Renvoie un itérateur asynchrone sur les fragments de texte générés."""
        return self._stream(messages, max_tokens=max_tokens)

    async def _stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        system, conversation = split_system_prompt(messages)
        provider_id = self.capabilities.provider_id

        with _translated_errors(provider_id):
            async with self._client.messages.stream(
                model=provider_id,
                max_tokens=max_tokens,
                system=system if system is not None else omit,
                messages=_to_message_params(conversation),
            ) as stream:
                async for chunk in stream.text_stream:
                    yield chunk

                # Un refus produit un flux vide : sans cette vérification,
                # l'appelant recevrait une réponse silencieusement tronquée.
                final_message = await stream.get_final_message()
                if final_message.stop_reason == "refusal":
                    raise _refusal_error(provider_id)
