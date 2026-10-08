"""Registre des fournisseurs de génération et factory de résolution.

Schéma normatif : `docs/providers-architecture.md` §6. Le registre est construit
au chargement du module — pas de découverte dynamique, pas de plugin : la liste
des modèles supportés est un choix de code assumé.

`list_llm_providers` ne fait **aucun appel réseau** : `available` reflète la
présence de la configuration nécessaire (§0). Un fournisseur `available=true`
peut malgré tout échouer à l'exécution ; ce n'est pas le rôle du catalogue de le
garantir.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.anthropic import ANTHROPIC_CATALOG, AnthropicLLMAdapter
from app.providers.llm.base import LLMCapabilities, LLMPort, LLMVendor
from app.providers.llm.circuit_breaker import CircuitBreakerLLMAdapter, breaker_for
from app.providers.llm.groq import GROQ_CATALOG, GroqLLMAdapter
from app.providers.llm.openai import OPENAI_CATALOG, OpenAILLMAdapter


class LLMProviderStatus(BaseModel):
    """Ligne du catalogue exposée par `GET /api/v1/providers`."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    label: str
    vendor: LLMVendor
    context_window: int
    supports_streaming: bool
    available: bool


@dataclass(frozen=True)
class LLMProviderRegistration:
    """Entrée du registre : capacités déclarées, constructeur, test de disponibilité."""

    capabilities: LLMCapabilities
    build: Callable[[Settings], LLMPort]
    build_with_key: Callable[[LLMCapabilities, str], LLMPort]
    """Construit l'adaptateur depuis une clé fournie par l'appelant (clé LLM
    personnelle), sans lire `Settings` — chemin des clés utilisateur."""
    is_available: Callable[[Settings], bool]
    label: str
    """Nom lisible (`GET /api/v1/providers`) — voir `LLMProviderStatus.label`."""


_LABELS: Final[Mapping[str, str]] = {
    "claude-opus-5": "Claude Opus 5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "gpt-4.1": "OpenAI — GPT-4.1",
    "llama-3.3-70b-versatile": "Groq — Llama 3.3 70B Versatile",
}
"""Libellés humains, tenus à jour manuellement à chaque entrée de catalogue
ajoutée dans `anthropic.py`/`openai.py`/`groq.py` — pas de dérivation
automatique depuis `provider_id`, qui produirait des noms de modèle inexacts
(`docs/providers-architecture.md` §6)."""


def _has_anthropic_key(settings: Settings) -> bool:
    return settings.anthropic_api_key is not None


def _has_openai_key(settings: Settings) -> bool:
    return settings.openai_api_key is not None


def _has_groq_key(settings: Settings) -> bool:
    return settings.groq_api_key is not None


def _anthropic_registration(capabilities: LLMCapabilities) -> LLMProviderRegistration:
    return LLMProviderRegistration(
        capabilities=capabilities,
        build=lambda settings: AnthropicLLMAdapter.from_settings(capabilities, settings),
        build_with_key=AnthropicLLMAdapter.from_api_key,
        is_available=_has_anthropic_key,
        label=_LABELS[capabilities.provider_id],
    )


def _openai_registration(capabilities: LLMCapabilities) -> LLMProviderRegistration:
    return LLMProviderRegistration(
        capabilities=capabilities,
        build=lambda settings: OpenAILLMAdapter.from_settings(capabilities, settings),
        build_with_key=OpenAILLMAdapter.from_api_key,
        is_available=_has_openai_key,
        label=_LABELS[capabilities.provider_id],
    )


def _groq_registration(capabilities: LLMCapabilities) -> LLMProviderRegistration:
    return LLMProviderRegistration(
        capabilities=capabilities,
        build=lambda settings: GroqLLMAdapter.from_settings(capabilities, settings),
        build_with_key=GroqLLMAdapter.from_api_key,
        is_available=_has_groq_key,
        label=_LABELS[capabilities.provider_id],
    )


_REGISTRY: Final[Mapping[str, LLMProviderRegistration]] = {
    **{
        capabilities.provider_id: _anthropic_registration(capabilities)
        for capabilities in ANTHROPIC_CATALOG
    },
    **{
        capabilities.provider_id: _openai_registration(capabilities)
        for capabilities in OPENAI_CATALOG
    },
    **{capabilities.provider_id: _groq_registration(capabilities) for capabilities in GROQ_CATALOG},
}


def resolve_llm_provider(
    provider_id: str, settings: Settings, *, override_api_key: str | None = None
) -> LLMPort:
    """Instancie l'adaptateur correspondant à `provider_id`, sous disjoncteur.

    Lève `ValidationError` (→ 422) si le modèle est inconnu ou si sa
    configuration est absente : le catalogue distingue « inexistant » de
    « existant mais non configuré » dans le message, pas dans le statut HTTP —
    les deux sont des requêtes sémantiquement invalides.

    `override_api_key` — clé LLM personnelle de l'utilisateur appelant, déjà
    déchiffrée (`UserLlmKeyService.resolve_key`), le cas échéant. Quand elle
    est fournie, elle **remplace** la clé serveur : `registration.build_with_key`
    est utilisé à la place de `registration.build`, et `is_available` n'est
    même pas consulté — une clé personnelle rend le fournisseur utilisable même
    si aucune clé serveur n'est configurée pour lui (c'est tout l'objet de la
    fonctionnalité, pas un effet de bord). Le chemin par défaut (aucune clé
    personnelle) est **inchangé** : mêmes branches, même message d'erreur,
    mêmes conditions qu'avant l'introduction de ce paramètre.

    L'adaptateur concret est enveloppé d'un disjoncteur (`CircuitBreakerLLMAdapter`,
    ADR-019), posé ici et nulle part ailleurs : c'est le seul point qui résout
    un fournisseur pour tous ses appelants (`RetrievalService.chat`, la
    génération d'évaluation). Un `_Breaker` par `provider_id` persiste pour la
    durée de vie du process (`breaker_for`), indépendamment du fait que
    `registration.build`/`build_with_key` reconstruise un adaptateur concret
    neuf à chaque appel — seul l'état du disjoncteur doit survivre entre deux
    requêtes, pas le client SDK sous-jacent. Un disjoncteur ouvert par la clé
    serveur reste donc partagé avec la clé personnelle du même `provider_id` :
    c'est le même modèle, la panne observée (rate limit, 5xx) est aussi
    probablement partagée côté fournisseur.
    """
    registration = _REGISTRY.get(provider_id)
    if registration is None:
        raise ValidationError(f"Modèle de génération « {provider_id} » inconnu.")
    if override_api_key is not None:
        delegate = registration.build_with_key(registration.capabilities, override_api_key)
    else:
        if not registration.is_available(settings):
            message = (
                f"Modèle de génération « {provider_id} » indisponible : "
                f"la clé API du fournisseur « {registration.capabilities.vendor} » n'est pas "
                "configurée."
            )
            raise ValidationError(message)
        delegate = registration.build(settings)
    breaker = breaker_for(
        provider_id,
        failure_threshold=settings.llm_circuit_breaker_failure_threshold,
        reset_timeout_seconds=float(settings.llm_circuit_breaker_reset_timeout_seconds),
    )
    return CircuitBreakerLLMAdapter(delegate, breaker=breaker)


def llm_provider_vendor(provider_id: str) -> LLMVendor | None:
    """Fournisseur (`LLMVendor`) d'un `provider_id` connu, `None` si inconnu.

    Utilisé par `RetrievalService.chat` pour savoir quelle clé LLM personnelle
    chercher (`UserLlmKeyService`) avant de résoudre l'adaptateur — sans
    dupliquer `_REGISTRY` hors de ce module. Un `provider_id` inconnu retourne
    `None` plutôt que de lever : `resolve_llm_provider` reste le seul point qui
    décide que c'est une erreur, cette fonction ne fait qu'informer.
    """
    registration = _REGISTRY.get(provider_id)
    return None if registration is None else registration.capabilities.vendor


def list_llm_providers(settings: Settings) -> list[LLMProviderStatus]:
    """Catalogue complet, chaque entrée annotée de sa disponibilité."""
    return [
        LLMProviderStatus(
            provider_id=registration.capabilities.provider_id,
            label=registration.label,
            vendor=registration.capabilities.vendor,
            context_window=registration.capabilities.context_window,
            supports_streaming=registration.capabilities.supports_streaming,
            available=registration.is_available(settings),
        )
        for registration in _REGISTRY.values()
    ]
