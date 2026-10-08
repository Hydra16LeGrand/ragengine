"""Agrégat évaluation : `EvaluationRun` et son détail `EvaluationItem` (Phase 8).

Un lancement (`EvaluationRun`) génère `sample_size` questions/réponses à partir
de documents déjà indexés d'une base, puis les note (`EvaluationItem`, une ligne
par question). Les scores agrégés d'un run (taux de rappel moyen, score juge
moyen) se calculent à la lecture (`app/services/evaluations.py`) — rien n'est
stocké en double ici.

`requested_by_id` est **nullable**, par écart assumé avec le brief d'origine :
ni `IngestionJob` ni `Document` ne tracent l'auteur d'une action (aucun des deux
n'a de colonne `*_by`), il n'existe donc aucun pattern existant à reproduire à
l'identique. La colonne suit malgré tout la convention générale du dépôt pour
une référence optionnelle vers `users` (nullable, `ON DELETE SET NULL`, comme
`Document.current_job_id`) : un run garde un historique lisible même après la
suppression du compte qui l'a lancé.

`status` (Phase 12, Étape 4, ADR-028) distingue un run encore en cours
d'exécution asynchrone (`pending`) d'un run résolu (`completed`/`failed`) —
voir `EvaluationRunStatus`. Un run **synchrone** (`create_run`,
`sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE`) n'est jamais persisté avant
d'être entièrement résolu : il n'existe donc jamais en base à `pending`.
`sample_size` n'est plus borné à 20 par `CHECK` (seule la borne basse
subsiste) : le plafond synchrone reste appliqué par
`app.domain.schemas.evaluation.MAX_EVALUATION_SAMPLE_SIZE`, et le plafond
asynchrone par `Settings.evaluation_async_max_sample_size` — tous deux
révisables sans migration.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EVALUATION_RUN_STATUS_ENUM
from app.domain.enums import EvaluationRunStatus

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.document import Document
    from app.db.models.knowledge_base import KnowledgeBase
    from app.db.models.user import User


class EvaluationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Un lancement de génération d'évaluation sur une base de connaissances."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (CheckConstraint("sample_size >= 1", name="sample_size_positive"),)

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
        index=True,
        doc="Compte à l'origine du lancement ; `NULL` si le compte a depuis été supprimé.",
    )
    llm_provider_id: Mapped[str] = mapped_column(
        doc="Fournisseur `LLMPort` utilisé à la fois pour générer les Q/R et pour juger."
    )
    sample_size: Mapped[int] = mapped_column(
        doc="Nombre de documents échantillonnés — et donc de questions générées, une par document."
    )
    status: Mapped[EvaluationRunStatus] = mapped_column(
        EVALUATION_RUN_STATUS_ENUM,
        default=EvaluationRunStatus.COMPLETED,
        server_default=text(f"'{EvaluationRunStatus.COMPLETED.value}'::evaluation_run_status"),
        doc=(
            "`pending` tant qu'un run asynchrone n'a pas produit ses items ; "
            "`completed`/`failed` une fois résolu. Un run synchrone est toujours "
            "inséré déjà `completed` (défaut Python) — le défaut serveur ne sert "
            "qu'au backfill des lignes antérieures à cette colonne (ADR-028)."
        ),
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(lazy="raise_on_sql")
    requested_by: Mapped[User | None] = relationship(lazy="raise_on_sql")
    items: Mapped[list[EvaluationItem]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
        order_by=lambda: EvaluationItem.created_at,
    )


class EvaluationItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Une question/réponse générée pour un document, et sa notation.

    `retrieval_rank` est `NULL` quand `source_document_id` n'apparaît pas dans
    le top-k de `RetrievalService.search()` pour la question générée — absence
    d'information, jamais un rang fictif comme `0` ou `-1`.
    """

    __tablename__ = "evaluation_items"
    __table_args__ = (
        CheckConstraint("judge_score BETWEEN 0.0 AND 1.0", name="judge_score_between_0_and_1"),
        CheckConstraint(
            "retrieval_rank IS NULL OR retrieval_rank >= 1", name="retrieval_rank_positive"
        ),
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str]
    reference_answer: Mapped[str]
    generated_answer: Mapped[str]
    retrieval_rank: Mapped[int | None] = mapped_column(
        default=None, doc="Position (1-based) du document source dans le top-k retrieval."
    )
    judge_score: Mapped[float] = mapped_column(doc="Note du juge LLM, 0.0 à 1.0.")
    judge_rationale: Mapped[str] = mapped_column(doc="Brève justification produite par le juge.")

    run: Mapped[EvaluationRun] = relationship(back_populates="items", lazy="raise_on_sql")
    source_document: Mapped[Document] = relationship(lazy="raise_on_sql")
