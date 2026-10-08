"""Agrégat document : fichier téléversé dans une base de connaissances.

`status` est une **projection dénormalisée** de l'état du job courant, destinée
à lister sans jointure. Son écrivain unique est le service de transition d'état
des jobs (`app.domain.state_machine`) : aucun autre code ne l'écrit, faute de
quoi deux sources de vérité entreraient en concurrence.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import DOCUMENT_STATUS_ENUM
from app.domain.enums import DocumentStatus

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.ingestion_job import IngestionJob
    from app.db.models.knowledge_base import KnowledgeBase

CURRENT_JOB_FK = "fk_documents_current_job_id_ingestion_jobs"
"""Nom de la clé étrangère circulaire, créée après `ingestion_jobs` (`use_alter`)."""


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Fichier d'origine et son état d'indexation."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_non_negative"),
        CheckConstraint("chunk_count IS NULL OR chunk_count >= 0", name="chunk_count_non_negative"),
        # Un seul index composite pour deux besoins : sa colonne de tête indexe
        # la clé étrangère (listing des documents d'une base) et le couple
        # complet répond à l'idempotence « ce fichier est-il déjà dans cette
        # base ? ». Un index dédié à chaque colonne serait redondant.
        Index(
            "ix_documents_knowledge_base_id_checksum_sha256",
            "knowledge_base_id",
            "checksum_sha256",
        ),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    filename: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(
        BigInteger(), doc="Taille du fichier d'origine, en octets."
    )
    checksum_sha256: Mapped[str] = mapped_column(
        doc="Empreinte du contenu : détecte un re-téléversement identique."
    )
    storage_uri: Mapped[str] = mapped_column(doc="Emplacement du fichier d'origine.")
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS_ENUM,
        default=DocumentStatus.PENDING,
        server_default=text(f"'{DocumentStatus.PENDING.value}'::document_status"),
        doc="Projection de l'état du job courant — écrivain unique.",
    )
    page_count: Mapped[int | None] = mapped_column(default=None)
    detected_languages: Mapped[list[str]] = mapped_column(
        server_default=text("'{}'::text[]"),
        doc="Codes ISO 639-1 détectés au precheck (ADR-003).",
    )
    theme: Mapped[str | None] = mapped_column(
        default=None,
        doc=(
            "Étiquette de thème saisie par l'utilisateur pour organiser une base "
            "par sujet — jamais déduite (contrairement à `detected_languages`). "
            "Réglable à l'ingestion ou après coup (`PATCH /documents/{id}`). "
            "Filtrable au retrieval (`RetrievalFilters.themes`), même sémantique "
            "descriptive que `languages`/`content_types`."
        ),
    )
    precheck_report: Mapped[dict[str, Any] | None] = mapped_column(
        default=None, doc="Tableaux détectés, pages scannées, figures, langues."
    )
    precheck_decision: Mapped[dict[str, Any] | None] = mapped_column(
        default=None,
        doc="Choix utilisateur : optimisation tableaux, OCR lourd, profil de chunking.",
    )
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "ingestion_jobs.id",
            ondelete="SET NULL",
            # Référence circulaire documents ↔ ingestion_jobs : la contrainte
            # est posée par un ALTER après la création des deux tables.
            use_alter=True,
            name=CURRENT_JOB_FK,
        ),
        default=None,
        index=True,
        doc="Dernier job en date pour ce document.",
    )
    ragflow_document_external_id: Mapped[str | None] = mapped_column(
        default=None, doc="Clé du système tiers RAGFlow — jamais une clé de jointure interne."
    )
    has_quality_warning: Mapped[bool] = mapped_column(
        default=False,
        server_default=text("false"),
        doc=(
            "Projection dénormalisée de `IngestionJob.warning != NULL` pour le job "
            "courant (ADR-024) — évite une jointure pour peupler une liste de "
            "documents. Écrit dans la même transaction que la transition du job "
            "vers `completed`, par le même écrivain unique (`IngestionJobService."
            "transition`). N'affecte jamais `status` : orthogonal à l'état du "
            "pipeline, le job a réussi techniquement."
        ),
    )
    chunk_count: Mapped[int | None] = mapped_column(
        default=None,
        doc=(
            "Nombre total de fragments indexés côté moteur — même geste et même "
            "écrivain unique que `has_quality_warning` (`IngestionJobService."
            "transition`, `_quality_warning_for_step`, ADR-024) : calculé "
            "gratuitement à la même lecture moteur que le garde-fou qualité, "
            "sans borne d'échantillon contrairement au contenu servant au calcul "
            "des métriques. `None` pour un document ingéré avant cette colonne, "
            "ou si le calcul a échoué (jamais bloquant)."
        ),
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(
        back_populates="documents", lazy="raise_on_sql"
    )
    jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
        foreign_keys="IngestionJob.document_id",
    )
    current_job: Mapped[IngestionJob | None] = relationship(
        lazy="raise_on_sql",
        foreign_keys=lambda: [Document.current_job_id],
        # `post_update` casse le cycle d'insertion : le document est écrit, puis
        # sa référence au job est mise à jour par un UPDATE séparé.
        post_update=True,
    )
