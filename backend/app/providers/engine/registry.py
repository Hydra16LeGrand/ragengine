"""Registre des moteurs d'ingestion et fabrique de résolution (ADR-070).

Même patron que `llm/registry.py` : un `Mapping` construit au chargement du
module, jamais de découverte dynamique. Le moteur du déploiement se choisit par
`Settings.ingestion_engine` (`INGESTION_ENGINE`), `ragflow` par défaut.

`production_grade` sépare les moteurs servables à de vrais utilisateurs de ceux
qui ne sont que des doubles : `Settings` refuse les seconds en staging/production
(`_forbid_fake_engine_when_exposed`), et le catalogue `GET /api/v1/providers` les
annonce comme factices.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.engine.base import IngestionEnginePort
from app.providers.engine.fake import FAKE_ENGINE_ID, shared_fake_engine
from app.providers.engine.fake_shared import SHARED_FAKE_ENGINE_ID, SharedStateFakeEngine
from app.providers.engine.ragflow.client import RagflowEngineAdapter

RAGFLOW_ENGINE_ID: Final = "ragflow"


class EngineProviderStatus(BaseModel):
    """Moteur d'ingestion du déploiement, tel qu'exposé par `GET /api/v1/providers`."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    label: str
    is_fake: bool
    """`True` : double en mémoire, sans recherche sémantique — jamais un moteur de production."""
    available: bool


@dataclass(frozen=True)
class EngineRegistration:
    """Entrée du registre : constructeur, test de disponibilité, nature du moteur."""

    build: Callable[[Settings], IngestionEnginePort]
    is_available: Callable[[Settings], bool]
    label: str
    production_grade: bool


_REGISTRY: Final[Mapping[str, EngineRegistration]] = {
    RAGFLOW_ENGINE_ID: EngineRegistration(
        build=RagflowEngineAdapter.from_settings,
        is_available=lambda settings: settings.ragflow_api_key is not None,
        label="RAGFlow",
        production_grade=True,
    ),
    FAKE_ENGINE_ID: EngineRegistration(
        build=lambda settings: shared_fake_engine(),
        is_available=lambda settings: True,
        label="Moteur factice en mémoire (tests et démonstrations)",
        production_grade=False,
    ),
    SHARED_FAKE_ENGINE_ID: EngineRegistration(
        build=lambda settings: SharedStateFakeEngine(settings.redis_url),
        is_available=lambda settings: True,
        label="Moteur factice, état partagé via Redis (démonstration)",
        production_grade=False,
    ),
}


def resolve_ingestion_engine(settings: Settings) -> IngestionEnginePort:
    """Renvoie l'adaptateur du moteur d'ingestion choisi par `settings.ingestion_engine`.

    Construire l'adaptateur n'ouvre aucune socket (le client HTTP est créé au
    premier appel) : le résoudre par avance ne coûte donc rien.

    Raises:
        EngineError: `RAGFLOW_API_KEY` absente avec le moteur `ragflow` — erreur
            de déploiement, pas d'exécution, signalée sans attendre le premier
            appel réseau.
        ValidationError: identifiant de moteur inconnu (inatteignable via
            `Settings`, dont le type est fermé ; garde pour un appelant direct).
    """
    registration = _REGISTRY.get(settings.ingestion_engine)
    if registration is None:
        raise ValidationError(f"Moteur d'ingestion « {settings.ingestion_engine} » inconnu.")
    return registration.build(settings)


def ingestion_engine_status(settings: Settings) -> EngineProviderStatus:
    """Moteur du déploiement, pour le catalogue — aucun appel réseau."""
    registration = _REGISTRY[settings.ingestion_engine]
    return EngineProviderStatus(
        provider_id=settings.ingestion_engine,
        label=registration.label,
        is_fake=not registration.production_grade,
        available=registration.is_available(settings),
    )
