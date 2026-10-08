"""Implémentation de `ObservabilityPort` qui enregistre les appels en mémoire.

Destinée aux tests d'orchestration : ils vérifient **que** l'observabilité a été
sollicitée, avec quels attributs, sans dépendre d'un collecteur réel ni d'un
réseau. Contrairement au no-op, elle conserve une trace exploitable ; comme lui,
elle ne lève jamais.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from app.domain.enums import JobState

ObservabilityCallKind = Literal["ingestion_step", "retrieval", "event"]


@dataclass(frozen=True)
class ObservabilityCall:
    """Appel observé : sa nature, son nom et les attributs transmis."""

    kind: ObservabilityCallKind
    name: str
    attributes: Mapping[str, JsonValue]


@dataclass
class FakeObservability:
    """Collecte les appels d'observabilité dans une liste ordonnée."""

    calls: list[ObservabilityCall] = field(default_factory=list)
    completed: list[ObservabilityCall] = field(default_factory=list)
    """Sous-ensemble des appels dont le bloc encadré s'est terminé sans erreur."""

    def observe_ingestion_step(
        self, *, job_id: UUID, state: JobState, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Enregistre l'étape d'ingestion observée."""
        return self._record(
            "ingestion_step",
            str(job_id),
            {"job_id": str(job_id), "state": state.value, **attributes},
        )

    def observe_retrieval(
        self, *, knowledge_base_id: UUID, query: str, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Enregistre la recherche observée."""
        return self._record(
            "retrieval",
            query,
            {"knowledge_base_id": str(knowledge_base_id), "query": query, **attributes},
        )

    def record_event(self, name: str, **attributes: JsonValue) -> None:
        """Enregistre l'événement ponctuel."""
        call = ObservabilityCall(kind="event", name=name, attributes=dict(attributes))
        self.calls.append(call)
        self.completed.append(call)

    def kinds(self) -> list[ObservabilityCallKind]:
        """Raccourci d'assertion : la suite des natures d'appels observées."""
        return [call.kind for call in self.calls]

    @contextmanager
    def _record(
        self, kind: ObservabilityCallKind, name: str, attributes: Mapping[str, JsonValue]
    ) -> Iterator[None]:
        call = ObservabilityCall(kind=kind, name=name, attributes=dict(attributes))
        self.calls.append(call)
        yield
        self.completed.append(call)
