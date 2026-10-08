"""Constructeurs d'entités non persistées, pour des tests lisibles.

Chaque fabrique produit une ligne **valide au regard des contraintes de base** :
un test qui veut en violer une le fait explicitement, ce qui rend son intention
évidente à la lecture.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.db.models import (
    ApiKey,
    Document,
    EvaluationItem,
    EvaluationRun,
    IngestionJob,
    IngestionJobEvent,
    KnowledgeBase,
    KnowledgeBaseMember,
    Organization,
    OrganizationMember,
    Session,
    User,
)
from app.domain.enums import JobState, MemberRole, OrganizationRole, UserRole, Visibility

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.identity.sessions import IssuedSession


def unique_email(prefix: str = "user") -> str:
    """Adresse unique par appel : les tests partagent la même base."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def checksum_of(payload: str) -> str:
    """Empreinte SHA-256 hexadécimale d'un contenu de test."""
    return hashlib.sha256(payload.encode()).hexdigest()


def make_user(
    *,
    email: str | None = None,
    display_name: str = "Alice",
    role: UserRole = UserRole.MEMBER,
) -> User:
    """Compte réel : e-mail et empreinte de mot de passe présents, sans échéance."""
    return User(
        email=email or unique_email(),
        password_hash="$argon2id$v=19$fake",
        display_name=display_name,
        role=role,
        is_guest=False,
        is_active=True,
        expires_at=None,
    )


def make_guest(*, display_name: str = "Invité", ttl_hours: int = 24) -> User:
    """Invité : ni e-mail ni mot de passe, mais une échéance obligatoire (ADR-009)."""
    return User(
        email=None,
        password_hash=None,
        display_name=display_name,
        role=UserRole.MEMBER,
        is_guest=True,
        is_active=True,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
    )


def make_session(user: User, *, token: str | None = None) -> Session:
    """Session d'interface rattachée à un compte."""
    return Session(
        user_id=user.id,
        token_hash=hashlib.sha256((token or uuid.uuid4().hex).encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        user_agent="pytest",
        ip_address="127.0.0.1",
    )


def make_api_key(user: User, *, name: str = "CI") -> ApiKey:
    """Clé API dont seule l'empreinte est stockée."""
    token = f"rag_test_{uuid.uuid4().hex}"
    return ApiKey(
        user_id=user.id,
        name=name,
        prefix=token[:12],
        key_hash=hashlib.sha256(token.encode()).hexdigest(),
    )


def make_organization(*, name: str = "Organisation Test", slug: str | None = None) -> Organization:
    """Organisation non persistée, nom et slug génériques par défaut."""
    return Organization(name=name, slug=slug or f"org-{uuid.uuid4().hex[:12]}")


def make_organization_member(
    organization: Organization, user: User, *, role: OrganizationRole = OrganizationRole.OWNER
) -> OrganizationMember:
    """Appartenance d'un utilisateur à une organisation."""
    return OrganizationMember(organization_id=organization.id, user_id=user.id, role=role)


def make_knowledge_base(
    owner: User,
    *,
    slug: str = "referentiel-qualite",
    visibility: Visibility = Visibility.PRIVATE,
    organization: Organization | None = None,
) -> KnowledgeBase:
    """Base de connaissances appartenant à `owner`.

    Sans `organization` explicite, une organisation jetable est assignée via la
    **relation** ORM plutôt que `organization_id` : le cascade `save-update`
    par défaut d'une relationship SQLAlchemy insère l'organisation transitoire
    au même flush que la base, sans que l'appelant n'ait à la persister
    lui-même. La grande majorité des appelants n'ont besoin d'aucune
    organisation particulière, seulement d'une FK satisfaite (ADR-022).
    """
    knowledge_base = KnowledgeBase(
        slug=slug,
        name="Référentiel Qualité",
        description="Procédures internes.",
        owner_id=owner.id,
        visibility=visibility,
        embedding_model_id="bge-m3",
        reranker_model_id="bge-reranker-v2-m3",
        provider_config={"retrieval_top_k": 8},
    )
    knowledge_base.organization = organization if organization is not None else make_organization()
    return knowledge_base


def make_member(
    knowledge_base: KnowledgeBase, user: User, *, role: MemberRole = MemberRole.OWNER
) -> KnowledgeBaseMember:
    """Appartenance d'un utilisateur à une base."""
    return KnowledgeBaseMember(knowledge_base_id=knowledge_base.id, user_id=user.id, role=role)


def make_document(knowledge_base: KnowledgeBase, *, filename: str = "rapport.pdf") -> Document:
    """Document téléversé dans une base, avant tout traitement."""
    return Document(
        knowledge_base_id=knowledge_base.id,
        filename=filename,
        content_type="application/pdf",
        size_bytes=1_048_576,
        checksum_sha256=checksum_of(filename),
        storage_uri=f"file:///srv/ragengine/{filename}",
        detected_languages=["fr", "en"],
        precheck_report={"tables": 3, "scanned_pages": [2, 5]},
    )


def make_job(document: Document, *, state: JobState = JobState.QUEUED) -> IngestionJob:
    """Job d'ingestion pour un document donné."""
    return IngestionJob(
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        state=state,
    )


def make_event(
    job: IngestionJob, *, state: JobState = JobState.QUEUED, message: str | None = None
) -> IngestionJobEvent:
    """Entrée du journal des transitions."""
    return IngestionJobEvent(job_id=job.id, state=state, message=message)


def make_evaluation_run(
    knowledge_base: KnowledgeBase,
    *,
    requested_by: User | None = None,
    llm_provider_id: str = "llama-3.3-70b-versatile",
    sample_size: int = 1,
) -> EvaluationRun:
    """Run d'évaluation (Phase 8) non persisté, rattaché à `knowledge_base`."""
    return EvaluationRun(
        knowledge_base_id=knowledge_base.id,
        requested_by_id=requested_by.id if requested_by is not None else None,
        llm_provider_id=llm_provider_id,
        sample_size=sample_size,
    )


def make_evaluation_item(
    run: EvaluationRun,
    document: Document,
    *,
    retrieval_rank: int | None = 1,
    judge_score: float = 1.0,
) -> EvaluationItem:
    """Item d'évaluation (une question notée) rattaché à `run` et `document`."""
    return EvaluationItem(
        evaluation_run_id=run.id,
        source_document_id=document.id,
        question="Quelle est la politique de remboursement ?",
        reference_answer="14 jours sans justification.",
        generated_answer="14 jours sans justification.",
        retrieval_rank=retrieval_rank,
        judge_score=judge_score,
        judge_rationale="Réponse équivalente à la référence.",
    )


async def ephemeral_session(db: AsyncSession) -> IssuedSession:
    """Compte éphémère (`is_guest`, échéance) et sa session, émise par le cœur.

    Le cœur ne crée jamais de tel compte (ADR-069) mais en garde les garde-fous :
    ces tests les exercent sans passer par un greffon.
    """
    from app.services.identity.sessions import SessionService

    account = make_guest()
    db.add(account)
    await db.flush([account])
    return await SessionService(db).issue(account, user_agent=None, ip_address=None)
