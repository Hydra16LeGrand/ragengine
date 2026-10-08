"""Implémentation déterministe de `LLMPort`, réservée aux tests.

Aucun appel réseau : la réponse est l'écho du dernier message utilisateur. Le
streaming ne se contente pas de renvoyer la réponse en un bloc — il la découpe,
afin que les tests exercent réellement le chemin d'agrégation des fragments
plutôt qu'un cas dégénéré à un seul morceau.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Final

from app.core.errors import ValidationError
from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMVendor

ECHO_PREFIX: Final = "echo: "
"""Préfixe de la réponse simulée — rend l'écho reconnaissable dans les assertions."""

STREAM_CHUNK_SIZE: Final = 8
"""Taille des fragments émis par `stream`, en caractères."""

FAKE_PROVIDER_ID: Final = "fake-llm"


class FakeLLMAdapter:
    """Fournisseur de génération simulé, sans dépendance externe."""

    def __init__(
        self,
        *,
        provider_id: str = FAKE_PROVIDER_ID,
        vendor: LLMVendor = "anthropic",
        context_window: int = 200_000,
    ) -> None:
        self.capabilities = LLMCapabilities(
            provider_id=provider_id,
            vendor=vendor,
            context_window=context_window,
            supports_streaming=True,
        )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Renvoie l'écho du dernier message utilisateur.

        `max_tokens` est ignoré : le fake ne tokenise pas, et tronquer sur un
        nombre de caractères donnerait une illusion de fidélité au contrat réel.
        """
        return self._echo(messages)

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        """Renvoie l'écho découpé en plusieurs fragments."""
        return self._stream(messages, max_tokens=max_tokens)

    async def _stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        answer = self._echo(messages)
        for start in range(0, len(answer), STREAM_CHUNK_SIZE):
            yield answer[start : start + STREAM_CHUNK_SIZE]

    @staticmethod
    def _echo(messages: Sequence[ChatMessage]) -> str:
        last_user_message = next(
            (message for message in reversed(messages) if message.role == "user"), None
        )
        if last_user_message is None:
            raise ValidationError("La conversation ne contient aucun message utilisateur.")
        return f"{ECHO_PREFIX}{last_user_message.content}"
