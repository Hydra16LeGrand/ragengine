"""Registre des fournisseurs de reranking : catalogue, disponibilité, résolution.

Miroir exact de `app/providers/embeddings/registry.py` — mêmes principes
(`docs/providers-architecture.md` §6), même absence d'appel réseau : la
disponibilité n'est qu'une lecture de la configuration (§0).

`passthrough` figure au catalogue comme les autres, et non comme un cas
particulier caché : « pas de reranking » est un fournisseur, toujours disponible,
que la Phase 4 résoudra pour toute base dont `reranker_model_id IS NULL`.

`RerankerProviderStatus` est colocalisé ici pour la même raison que son
homologue embeddings : c'est la projection du registre pour
`GET /api/v1/providers`, pas une entité du domaine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.rerankers.base import RerankerCapabilities, RerankerPort
from app.providers.rerankers.local import (
    BGE_RERANKER_V2_M3_CAPABILITIES,
    LocalRerankerAdapter,
)
from app.providers.rerankers.passthrough import PASSTHROUGH_CAPABILITIES, PassthroughReranker


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """Entrée du catalogue : ce que sait faire un fournisseur, et comment l'obtenir."""

    capabilities: RerankerCapabilities
    build: Callable[[Settings], RerankerPort]
    is_available: Callable[[Settings], bool]
    label: str
    """Nom lisible (`GET /api/v1/providers`) — voir `RerankerProviderStatus.label`."""


class RerankerProviderStatus(BaseModel):
    """Ligne du catalogue exposée par `GET /api/v1/providers` (§7)."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    label: str = Field(
        description="Nom lisible, ex. « BGE Reranker v2-M3 (local) » — pas un identifiant."
    )
    is_local: bool
    max_documents: int
    languages: tuple[str, ...] | None = None
    available: bool = Field(
        description="Configuration présente — jamais le résultat d'un appel réseau."
    )


def _always_available(settings: Settings) -> bool:
    """Rien à configurer : ni le service d'inférence ni le Null Object n'ont de secret.

    La joignabilité effective du service GPU relève de la Phase 3 (§0).
    """
    return True


_REGISTRY: Final[Mapping[str, ProviderRegistration]] = {
    # Premier = défaut du produit, cohérent avec `settings.reranker_model_id`.
    BGE_RERANKER_V2_M3_CAPABILITIES.provider_id: ProviderRegistration(
        capabilities=BGE_RERANKER_V2_M3_CAPABILITIES,
        build=LocalRerankerAdapter.from_settings,
        is_available=_always_available,
        label="BGE Reranker v2-M3 (local)",
    ),
    PASSTHROUGH_CAPABILITIES.provider_id: ProviderRegistration(
        capabilities=PASSTHROUGH_CAPABILITIES,
        build=PassthroughReranker.from_settings,
        is_available=_always_available,
        label="Passthrough",
    ),
}
"""Catalogue des fournisseurs de reranking supportés, dans l'ordre d'exposition."""


def resolve_reranker_provider(provider_id: str, settings: Settings) -> RerankerPort:
    """Résout un identifiant de reranker en adaptateur prêt à l'emploi.

    Point d'application unique de la validation d'un `reranker_model_id` : un
    identifiant inconnu ou non configuré lève `ValidationError` (422), avant
    toute écriture en base (`docs/providers-architecture.md` §8).
    """
    registration = _REGISTRY.get(provider_id)
    if registration is None or not registration.is_available(settings):
        raise ValidationError(
            f"Reranker « {provider_id} » indisponible.",
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


def list_reranker_providers(settings: Settings) -> list[RerankerProviderStatus]:
    """Projette le catalogue complet, disponibles ou non, dans l'ordre d'enregistrement."""
    return [
        RerankerProviderStatus(
            provider_id=registration.capabilities.provider_id,
            label=registration.label,
            is_local=registration.capabilities.is_local,
            max_documents=registration.capabilities.max_documents,
            languages=registration.capabilities.languages,
            available=registration.is_available(settings),
        )
        for registration in _REGISTRY.values()
    ]
