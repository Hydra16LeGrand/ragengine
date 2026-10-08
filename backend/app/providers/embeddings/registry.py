"""Registre des fournisseurs d'embeddings : catalogue, disponibilité, résolution.

Le catalogue est une table **construite au chargement du module** : pas de
découverte dynamique, pas de plugins. La liste des fournisseurs supportés est un
choix de code assumé (`docs/providers-architecture.md` §6).

**Disponibilité ≠ joignabilité** (§0). `is_available` ne regarde que la
configuration — une clé d'API présente, une URL renseignée. Aucune fonction de ce
module n'ouvre de socket : `list_embedding_providers` lit des `Settings`, et
`resolve_embedding_provider` ne fait qu'instancier un adaptateur dont le client
réseau naît plus tard, au premier appel. Un fournisseur `available=True` peut
donc encore échouer à l'exécution ; ce n'est pas le rôle du catalogue de le
garantir.

`EmbeddingProviderStatus` est **colocalisé ici** plutôt que dans
`app/domain/schemas/` : ce n'est pas un concept du domaine (rien ne le persiste,
aucun service ne le manipule), c'est la projection du registre pour
`GET /api/v1/providers`. Le mettre dans le paquet des schémas d'entité aurait
créé une dépendance du domaine vers la couche providers, à rebours de
l'architecture hexagonale (CONTRIBUTING.md §3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.embeddings.base import EmbeddingCapabilities, EmbeddingPort
from app.providers.embeddings.local import BGE_M3_CAPABILITIES, LocalEmbeddingAdapter
from app.providers.embeddings.openai import (
    TEXT_EMBEDDING_3_LARGE_CAPABILITIES,
    OpenAIEmbeddingAdapter,
)


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """Entrée du catalogue : ce que sait faire un fournisseur, et comment l'obtenir."""

    capabilities: EmbeddingCapabilities
    build: Callable[[Settings], EmbeddingPort]
    is_available: Callable[[Settings], bool]
    label: str
    """Nom lisible (`GET /api/v1/providers`) — jamais dérivé de `provider_id` côté
    frontend : voir `EmbeddingProviderStatus.label`."""


class EmbeddingProviderStatus(BaseModel):
    """Ligne du catalogue exposée par `GET /api/v1/providers` (§7)."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    label: str = Field(description="Nom lisible, ex. « BGE-M3 (local) » — pas un identifiant.")
    is_local: bool
    dimensions: int
    languages: tuple[str, ...] | None = None
    available: bool = Field(
        description="Configuration présente — jamais le résultat d'un appel réseau."
    )


def _always_available(settings: Settings) -> bool:
    """Le service d'inférence est toujours *déclaré* : son URL a une valeur par défaut.

    Sa joignabilité réelle relève de la Phase 3, pas du catalogue (§0).
    """
    return True


def _openai_is_configured(settings: Settings) -> bool:
    """`text-embedding-3-large` n'est proposé que si une clé d'API est configurée."""
    return settings.openai_api_key is not None


_REGISTRY: Final[Mapping[str, ProviderRegistration]] = {
    # Premier = défaut du produit, cohérent avec `settings.embedding_model_id`.
    BGE_M3_CAPABILITIES.provider_id: ProviderRegistration(
        capabilities=BGE_M3_CAPABILITIES,
        build=LocalEmbeddingAdapter.from_settings,
        is_available=_always_available,
        label="BGE-M3 (local)",
    ),
    TEXT_EMBEDDING_3_LARGE_CAPABILITIES.provider_id: ProviderRegistration(
        capabilities=TEXT_EMBEDDING_3_LARGE_CAPABILITIES,
        build=OpenAIEmbeddingAdapter.from_settings,
        is_available=_openai_is_configured,
        label="OpenAI — Text Embedding 3 Large",
    ),
}
"""Catalogue des fournisseurs d'embeddings supportés, dans l'ordre d'exposition."""


def resolve_embedding_provider(provider_id: str, settings: Settings) -> EmbeddingPort:
    """Résout un identifiant de modèle en adaptateur prêt à l'emploi.

    Point d'application unique de la validation d'un `embedding_model_id` : un
    identifiant inconnu **ou** non configuré lève `ValidationError` (422), avant
    toute écriture en base (`docs/providers-architecture.md` §8).
    """
    registration = _REGISTRY.get(provider_id)
    if registration is None or not registration.is_available(settings):
        raise ValidationError(
            f"Modèle d'embedding « {provider_id} » indisponible.",
            extensions={
                "provider_id": provider_id,
                "supported": [
                    identifier
                    for identifier, candidate in _REGISTRY.items()
                    if candidate.is_available(settings)
                ],
            },
        )
    return registration.build(settings)


def list_embedding_providers(settings: Settings) -> list[EmbeddingProviderStatus]:
    """Projette le catalogue complet, disponibles ou non, dans l'ordre d'enregistrement."""
    return [
        EmbeddingProviderStatus(
            provider_id=registration.capabilities.provider_id,
            label=registration.label,
            is_local=registration.capabilities.is_local,
            dimensions=registration.capabilities.dimensions,
            languages=registration.capabilities.languages,
            available=registration.is_available(settings),
        )
        for registration in _REGISTRY.values()
    ]
