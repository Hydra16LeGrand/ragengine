"""Agrégat ingestion : `IngestionJob` et son journal `IngestionJobEvent`.

Les transitions d'état passent exclusivement par le service dédié, qui valide la
légalité du saut (`app.domain.state_machine`), écrit `Document.status` et
journalise l'événement — le tout dans la même transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JOB_STATE_ENUM
from app.domain.enums import JobState

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.document import Document
    from app.db.models.knowledge_base import KnowledgeBase


class IngestionJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Traitement asynchrone d'un document, de la file d'attente à l'indexation."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100", name="progress_percent_between_0_and_100"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        doc="Dénormalisé : lister les jobs d'une base sans jointure sur `documents`.",
    )
    state: Mapped[JobState] = mapped_column(
        JOB_STATE_ENUM,
        default=JobState.QUEUED,
        server_default=text(f"'{JobState.QUEUED.value}'::job_state"),
    )
    progress_percent: Mapped[int] = mapped_column(
        SmallInteger(),
        default=0,
        server_default=text("0"),
        doc="Avancement 0-100 projeté depuis le moteur (Phase 4).",
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    celery_task_id: Mapped[str | None] = mapped_column(default=None)
    error: Mapped[dict[str, Any] | None] = mapped_column(
        default=None, doc="`{code, message, retryable}` du dernier échec."
    )
    warning: Mapped[dict[str, Any] | None] = mapped_column(
        default=None,
        doc=(
            "`{code, message, metrics}` du garde-fou qualité (ADR-024) — écrit "
            "uniquement à la transition finale vers `completed` de l'étape "
            "d'indexation, jamais ailleurs. `None` : rien de suspect détecté."
        ),
    )
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- Témoins de vivacité du moteur (veille anti-blocage, ADR-012) --------
    # RAGFlow peut laisser un document `RUNNING` indéfiniment sur un bug de
    # parsing, sans jamais atteindre d'état terminal (fait constaté). Ces trois
    # colonnes portent la dernière observation *différente* rapportée par le
    # moteur : le délai de garde se mesure depuis elle, jamais depuis le début
    # du job — un document volumineux a le droit d'être lent, un document figé
    # n'a pas le droit de l'être indéfiniment.
    engine_progress: Mapped[float | None] = mapped_column(
        default=None, doc="Dernier `progress` brut du moteur dans [-1, 1] — PAS un pourcentage."
    )
    engine_last_message: Mapped[str | None] = mapped_column(
        default=None, doc="Dernière ligne du journal de progression du moteur."
    )
    engine_progress_observed_at: Mapped[datetime | None] = mapped_column(
        default=None, doc="Instant de la dernière évolution observée — origine du délai de garde."
    )

    document: Mapped[Document] = relationship(
        back_populates="jobs", lazy="raise_on_sql", foreign_keys=lambda: [IngestionJob.document_id]
    )
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="jobs", lazy="raise_on_sql")
    events: Mapped[list[IngestionJobEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
        order_by=lambda: IngestionJobEvent.created_at,
    )


class IngestionJobEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Journal des transitions : frise de progression et piste d'audit.

    Table en ajout seul, écrite par le seul service de transition.
    """

    __tablename__ = "ingestion_job_events"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[JobState] = mapped_column(JOB_STATE_ENUM, doc="État atteint par la transition.")
    message: Mapped[str | None] = mapped_column(default=None)

    job: Mapped[IngestionJob] = relationship(back_populates="events", lazy="raise_on_sql")
