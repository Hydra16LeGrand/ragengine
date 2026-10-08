"""Énumérations du domaine — source de vérité des types `ENUM` PostgreSQL.

Ces objets Python sont les seuls à définir les valeurs autorisées : les colonnes
SQLAlchemy les réutilisent via `app.db.types`, si bien qu'ajouter une valeur ici
et oublier la migration devient immédiatement visible.

`StrEnum` garantit qu'une valeur se sérialise nativement en JSON et se compare à
une chaîne, ce qui évite les conversions défensives dans les routers.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Rôle **plateforme** d'un compte (ADR-009)."""

    ADMIN = "admin"
    MEMBER = "member"


class MemberRole(StrEnum):
    """Rôle porté **par base de connaissances** (table d'association)."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class OrganizationRole(StrEnum):
    """Rôle porté **par organisation** (tenant, ADR-022).

    Deux niveaux seulement : un `admin` intermédiaire s'ajoutera par migration
    future si un besoin réel apparaît, pas par anticipation. À ne pas confondre
    avec `UserRole` (plateforme) ni `MemberRole` (par base de connaissances).
    """

    OWNER = "owner"
    MEMBER = "member"


class Visibility(StrEnum):
    """Visibilité d'une base de connaissances (ADR-005)."""

    PRIVATE = "private"
    PUBLIC = "public"


class DocumentStatus(StrEnum):
    """Projection dénormalisée de l'état du job courant d'un document.

    Écrivain unique : le service de transition d'état des jobs.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    INDEXED = "indexed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    """État d'un job d'ingestion (machine à états de `docs/domain-model.md`)."""

    QUEUED = "queued"
    PRECHECKING = "prechecking"
    AWAITING_REVIEW = "awaiting_review"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationRunStatus(StrEnum):
    """État d'un `EvaluationRun` (Phase 12, Étape 4 — ADR-028).

    Délibérément **pas** une machine à états au sens de `JobState` : un run
    d'évaluation n'a que deux issues possibles depuis `pending`, jamais
    d'étapes intermédiaires observables (`app.services.evaluations.
    EvaluationService.run_pending_evaluation` calcule tout, puis persiste une
    fois — aucune écriture `running` n'existe dans le code, un état de plus
    n'aurait donc porté aucune information). Un run synchrone (`create_run`,
    plafond `MAX_EVALUATION_SAMPLE_SIZE`) est toujours persisté déjà résolu en
    `completed` ou n'est jamais persisté du tout (une erreur de requête ou
    l'absence de document éligible échoue avant toute écriture).
    """

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
