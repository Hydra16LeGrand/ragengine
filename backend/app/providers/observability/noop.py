"""Implémentation no-op de `ObservabilityPort` — le défaut du système.

Pattern Null Object (CONTRIBUTING.md §3.4) : quand Langfuse n'est pas configuré, les
services appellent exactement le même port, avec exactement les mêmes appels.
Il n'existe donc aucun `if settings.observability_enabled` dispersé dans le
code métier — la branche est prise une seule fois, dans la factory.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from uuid import UUID

from pydantic import JsonValue

from app.domain.enums import JobState


class NoOpObservability:
    """Ne trace rien, ne lève rien, n'alloue rien de durable."""

    def observe_ingestion_step(
        self, *, job_id: UUID, state: JobState, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Renvoie un context manager vide."""
        return nullcontext()

    def observe_retrieval(
        self, *, knowledge_base_id: UUID, query: str, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Renvoie un context manager vide."""
        return nullcontext()

    def record_event(self, name: str, **attributes: JsonValue) -> None:
        """Ignore l'événement."""
