"""Port `ObservabilityPort` : traçage façonné par le domaine.

Signatures normatives : `docs/providers-architecture.md` §4. L'API est
volontairement **exprimée en termes métier** (étape d'ingestion, retrieval,
événement) et non en enveloppe 1:1 d'OpenTelemetry : les services ne doivent
rien savoir des spans ni des traces, sans quoi remplacer le collecteur
reviendrait à réécrire les services.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import JsonValue

from app.domain.enums import JobState


@runtime_checkable
class ObservabilityPort(Protocol):
    """Interface structurelle du traçage applicatif.

    Brique optionnelle : l'implémentation par défaut est un no-op strict
    (CONTRIBUTING.md §3.4). Aucune méthode ne lève jamais — l'observabilité ne doit
    pas pouvoir faire échouer l'opération qu'elle observe.
    """

    def observe_ingestion_step(
        self, *, job_id: UUID, state: JobState, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Encadre une étape du pipeline d'ingestion d'un job."""
        ...

    def observe_retrieval(
        self, *, knowledge_base_id: UUID, query: str, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Encadre une recherche dans une base de connaissances."""
        ...

    def record_event(self, name: str, **attributes: JsonValue) -> None:
        """Enregistre un événement ponctuel, sans durée."""
        ...
