"""Port `LLMPort` : contrat de génération partagé par tous les fournisseurs.

Signatures normatives : `docs/providers-architecture.md` §3. Les services et les
routers importent uniquement ce module, jamais un adaptateur concret.

**Aucun paramètre d'échantillonnage** (`temperature`, `top_p`, `top_k`) n'est
exposé : les modèles Claude actuels les rejettent (HTTP 400), et un port
cross-fournisseur qui casse à l'exécution sur le fournisseur par défaut n'est pas
un port. Un adaptateur qui a besoin d'échantillonner le gère en interne.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from http import HTTPStatus
from typing import ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.core.errors import RAGEngineError

LLMVendor = Literal["anthropic", "openai", "groq"]
"""Fournisseurs de génération supportés (CONTRIBUTING.md §2 : jamais de LLM local)."""


class ChatMessage(BaseModel):
    """Tour de conversation soumis au modèle.

    Le rôle `"system"` est une abstraction du port, pas un format de transport :
    Anthropic attend le prompt système dans un paramètre racine séparé tandis
    qu'OpenAI l'attend comme message. Chaque adaptateur fait la traduction.
    """

    role: Literal["system", "user", "assistant"]
    content: str


class LLMCapabilities(BaseModel):
    """Descripteur immuable de ce qu'une implémentation sait faire."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    vendor: LLMVendor
    context_window: int
    supports_streaming: bool


@runtime_checkable
class LLMPort(Protocol):
    """Interface structurelle d'un fournisseur de génération.

    `stream` existe dès la v1 : le Playground (Phase 7) est un chat, et ajouter
    le streaming après coup casserait tout appelant déjà écrit. Un adaptateur
    qui ne le supporterait pas le déclare via `capabilities.supports_streaming`.
    """

    capabilities: LLMCapabilities

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Renvoie la réponse complète du modèle, en une seule fois."""
        ...

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        """Renvoie un itérateur asynchrone sur les fragments de texte générés."""
        ...


class LLMError(RAGEngineError):
    """Échec d'un fournisseur de génération tiers.

    Aucun adaptateur ne laisse fuiter une exception SDK brute
    (`docs/providers-architecture.md` §0) : tout ce qui remonte d'un SDK est
    traduit ici, `502` traduisant la défaillance d'un service en amont.
    """

    status: ClassVar[int] = HTTPStatus.BAD_GATEWAY
    title: ClassVar[str] = "Bad Gateway"
    type: ClassVar[str] = "/problems/llm-error"


class LLMRateLimitError(LLMError):
    """Quota du fournisseur épuisé — l'appelant peut réessayer plus tard."""

    status: ClassVar[int] = HTTPStatus.TOO_MANY_REQUESTS
    title: ClassVar[str] = "Too Many Requests"
    type: ClassVar[str] = "/problems/llm-rate-limited"


class LLMRefusalError(LLMError):
    """Le fournisseur a refusé la requête (classificateur de sécurité).

    Ce n'est pas une panne : la réponse HTTP est un succès, seul le contenu
    manque. Le refus est donc un état terminal explicite — ni repli sur un autre
    modèle ni nouvelle tentative, hors périmètre de cette phase.
    """

    status: ClassVar[int] = HTTPStatus.UNPROCESSABLE_ENTITY
    title: ClassVar[str] = "Unprocessable Entity"
    type: ClassVar[str] = "/problems/llm-refusal"


def split_system_prompt(messages: Sequence[ChatMessage]) -> tuple[str | None, list[ChatMessage]]:
    """Sépare les tours de rôle `"system"` du reste de la conversation.

    Les contenus système sont concaténés dans l'ordre reçu (séparés par une ligne
    vide) ; les tours `user`/`assistant` conservent leur ordre. Utilisé par les
    adaptateurs dont l'API attend le prompt système hors de la liste de messages.
    """
    system_parts = [message.content for message in messages if message.role == "system"]
    conversation = [message for message in messages if message.role != "system"]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, conversation
