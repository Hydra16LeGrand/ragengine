"""Adaptateur `LLMPort` vers l'API Chat Completions d'OpenAI (SDK officiel).

Différences de traitement par rapport à l'adaptateur Anthropic :

- **Le rôle `"system"` reste un message.** OpenAI attend le prompt système comme
  premier élément de `messages`, pas dans un paramètre racine. Les `ChatMessage`
  sont donc transmis tels quels, dans l'ordre reçu.
- **Aucun paramètre d'échantillonnage n'est envoyé.** Le port n'expose pas de
  `temperature` (`docs/providers-architecture.md` §3) et cet adaptateur ne
  réintroduit pas de valeur maison : il s'en remet au défaut du fournisseur, la
  seule valeur qui reste juste quand OpenAI fait évoluer ses modèles.
- **Un refus est explicite.** Le champ `message.refusal` et le motif d'arrêt
  `content_filter` sont vérifiés avant la lecture du contenu, qui peut être
  `None` dans ces deux cas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from typing import Final

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.base import (
    ChatMessage,
    LLMCapabilities,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
)

# Un seul modèle suffit à ce livrable : le catalogue OpenAI n'est pas le
# fournisseur par défaut de RAGEngine (CONTRIBUTING.md §3.1 place Anthropic en tête),
# et chaque entrée ajoutée est une affirmation à maintenir.
#
# `gpt-4.1` est retenu plutôt qu'un identifiant plus récent parce qu'il est
# *vérifiable localement* : il figure dans le littéral `ChatModel` du SDK
# `openai` installé (`openai.types.chat_model.ChatModel`), ce qui exclut
# l'invention d'un ID. Il offre par ailleurs une très large fenêtre de contexte,
# cohérente avec des prompts RAG longs.
OPENAI_CATALOG: Final[tuple[LLMCapabilities, ...]] = (
    LLMCapabilities(
        provider_id="gpt-4.1",
        vendor="openai",
        # OpenAI documente une fenêtre d'environ un million de tokens pour ce
        # modèle. On retient 1 000 000 comme borne basse sûre — la valeur exacte
        # publiée (1 047 576) est à vérifier avant tout usage de comptage strict.
        context_window=1_000_000,
        supports_streaming=True,
    ),
)


@contextmanager
def _translated_errors(provider_id: str) -> Iterator[None]:
    """Traduit les exceptions du SDK en erreurs de domaine (plus spécifique d'abord)."""
    try:
        yield
    except openai.RateLimitError as exc:
        message = f"Quota OpenAI épuisé pour le modèle « {provider_id} »."
        raise LLMRateLimitError(message) from exc
    except openai.APIStatusError as exc:
        message = (
            f"L'API OpenAI a répondu {exc.status_code} pour le modèle « {provider_id} » : "
            f"{exc.message}"
        )
        raise LLMError(message) from exc
    except openai.APIConnectionError as exc:
        message = f"L'API OpenAI est injoignable pour le modèle « {provider_id} »."
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
        extensions={"provider_id": provider_id, "vendor": "openai"},
    )


class OpenAILLMAdapter:
    """Implémentation de `LLMPort` adossée au client asynchrone `AsyncOpenAI`."""

    def __init__(self, *, capabilities: LLMCapabilities, client: AsyncOpenAI) -> None:
        self.capabilities = capabilities
        self._client = client

    @classmethod
    def from_settings(cls, capabilities: LLMCapabilities, settings: Settings) -> OpenAILLMAdapter:
        """Construit l'adaptateur à partir de la clé API de `Settings`."""
        if settings.openai_api_key is None:
            message = f"Le modèle « {capabilities.provider_id} » requiert OPENAI_API_KEY."
            raise ValidationError(message)
        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        return cls(capabilities=capabilities, client=client)

    @classmethod
    def from_api_key(cls, capabilities: LLMCapabilities, api_key: str) -> OpenAILLMAdapter:
        """Construit l'adaptateur depuis une clé fournie par l'appelant (clé LLM personnelle)."""
        return cls(capabilities=capabilities, client=AsyncOpenAI(api_key=api_key))

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Renvoie la réponse complète du modèle."""
        provider_id = self.capabilities.provider_id

        with _translated_errors(provider_id):
            response = await self._client.chat.completions.create(
                model=provider_id,
                messages=_to_message_params(messages),
                # `max_completion_tokens` remplace `max_tokens`, déprécié côté
                # OpenAI ; c'est la même borne exprimée dans le paramètre courant.
                max_completion_tokens=max_tokens,
            )

        if not response.choices:
            raise LLMError(f"Le modèle « {provider_id} » n'a renvoyé aucune complétion.")

        choice = response.choices[0]
        if choice.message.refusal is not None:
            raise _refusal_error(provider_id, choice.message.refusal)
        if choice.finish_reason == "content_filter":
            raise _refusal_error(provider_id, "content_filter")

        content = choice.message.content
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
                if choice.delta.refusal is not None:
                    raise _refusal_error(provider_id, choice.delta.refusal)
                if choice.delta.content:
                    yield choice.delta.content
