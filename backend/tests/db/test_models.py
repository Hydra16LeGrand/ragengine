"""Création et relecture de chaque entité contre PostgreSQL.

Les colonnes `JSONB`, `TEXT[]` et `ENUM` doivent revenir de la base sous leur
forme Python d'origine : c'est ce qu'aucun test sur SQLite ne pourrait garantir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ApiKey,
    Document,
    IngestionJob,
    IngestionJobEvent,
    KnowledgeBase,
    KnowledgeBaseMember,
    Session,
    User,
)
from app.domain.enums import DocumentStatus, JobState, MemberRole, UserRole, Visibility
from tests.db import factories


async def test_user_round_trip(db_session: AsyncSession) -> None:
    """Un compte réel se relit avec ses défauts serveur renseignés."""
    user = factories.make_user(display_name="Alice", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    reloaded = await db_session.get(User, user.id)

    assert reloaded is not None
    assert isinstance(reloaded.id, uuid.UUID)
    assert reloaded.role is UserRole.ADMIN
    assert reloaded.is_guest is False
    assert reloaded.is_active is True
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.updated_at is not None


async def test_guest_user_round_trip(db_session: AsyncSession) -> None:
    """Un invité est une ligne `users` éphémère, sans e-mail ni mot de passe."""
    guest = factories.make_guest()
    db_session.add(guest)
    await db_session.flush()

    reloaded = await db_session.get(User, guest.id)

    assert reloaded is not None
    assert reloaded.is_guest is True
    assert reloaded.email is None
    assert reloaded.password_hash is None
    assert reloaded.expires_at is not None


async def test_guest_elevation_keeps_the_same_identity(db_session: AsyncSession) -> None:
    """L'élévation est un `UPDATE` : le `user_id` ne change pas (ADR-009)."""
    guest = factories.make_guest()
    db_session.add(guest)
    await db_session.flush()
    guest_id = guest.id

    kb = factories.make_knowledge_base(guest, slug="espace-invite")
    db_session.add(kb)
    await db_session.flush()

    guest.email = factories.unique_email("promu")
    guest.password_hash = "$argon2id$v=19$fake"
    guest.is_guest = False
    guest.expires_at = None
    await db_session.flush()

    reloaded = await db_session.get(User, guest_id)
    assert reloaded is not None
    assert reloaded.is_guest is False
    assert reloaded.expires_at is None

    owned = (
        (await db_session.execute(select(KnowledgeBase).where(KnowledgeBase.owner_id == guest_id)))
        .scalars()
        .all()
    )
    assert [base.id for base in owned] == [kb.id], "le travail de l'invité reste attaché"


async def test_session_round_trip(db_session: AsyncSession) -> None:
    """Une session porte l'empreinte du jeton, jamais le jeton lui-même."""
    user = factories.make_user()
    db_session.add(user)
    await db_session.flush()

    session_row = factories.make_session(user, token="jeton-de-test")
    db_session.add(session_row)
    await db_session.flush()

    reloaded = await db_session.get(Session, session_row.id)

    assert reloaded is not None
    assert reloaded.user_id == user.id
    assert reloaded.revoked_at is None
    assert reloaded.token_hash != "jeton-de-test"


async def test_api_key_round_trip_with_array_default(db_session: AsyncSession) -> None:
    """`scopes` est un `TEXT[]` dont le défaut serveur vaut `{"*"}`."""
    user = factories.make_user()
    db_session.add(user)
    await db_session.flush()

    key = factories.make_api_key(user)
    db_session.add(key)
    await db_session.flush()
    await db_session.refresh(key)

    assert key.scopes == ["*"]
    assert key.prefix.startswith("rag_test_")
    assert key.revoked_at is None


async def test_knowledge_base_round_trip_with_jsonb(db_session: AsyncSession) -> None:
    """Le value object `ProviderConfig` revient de `JSONB` sous forme de dictionnaire."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()

    kb = factories.make_knowledge_base(owner, visibility=Visibility.PUBLIC)
    db_session.add(kb)
    await db_session.flush()
    await db_session.refresh(kb)

    assert kb.visibility is Visibility.PUBLIC
    assert kb.provider_config == {"retrieval_top_k": 8}
    assert kb.document_count == 0
    assert kb.observability_enabled is False
    assert kb.ragflow_dataset_external_id is None


async def test_knowledge_base_member_round_trip(db_session: AsyncSession) -> None:
    """La paire (base, utilisateur) est la clé primaire et porte le rôle."""
    owner = factories.make_user()
    editor = factories.make_user()
    db_session.add_all([owner, editor])
    await db_session.flush()

    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()

    db_session.add_all(
        [
            factories.make_member(kb, owner, role=MemberRole.OWNER),
            factories.make_member(kb, editor, role=MemberRole.EDITOR),
        ]
    )
    await db_session.flush()

    members = (
        (
            await db_session.execute(
                select(KnowledgeBaseMember).where(KnowledgeBaseMember.knowledge_base_id == kb.id)
            )
        )
        .scalars()
        .all()
    )

    assert {member.role for member in members} == {MemberRole.OWNER, MemberRole.EDITOR}


async def test_document_round_trip_with_array_and_jsonb(db_session: AsyncSession) -> None:
    """Langues détectées (`TEXT[]`) et rapport de precheck (`JSONB`) survivent au trajet."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()

    document = factories.make_document(kb)
    db_session.add(document)
    await db_session.flush()
    await db_session.refresh(document)

    assert document.status is DocumentStatus.PENDING
    assert document.detected_languages == ["fr", "en"]
    assert document.precheck_report == {"tables": 3, "scanned_pages": [2, 5]}
    assert document.precheck_decision is None
    assert document.current_job_id is None
    assert document.size_bytes == 1_048_576


async def test_ingestion_job_and_events_round_trip(db_session: AsyncSession) -> None:
    """Un job et son journal se relisent, y compris via la relation `events`."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()
    document = factories.make_document(kb)
    db_session.add(document)
    await db_session.flush()

    job = factories.make_job(document)
    db_session.add(job)
    await db_session.flush()

    db_session.add_all(
        [
            factories.make_event(job, state=JobState.QUEUED, message="job créé"),
            factories.make_event(job, state=JobState.PRECHECKING),
        ]
    )
    document.current_job_id = job.id
    job.state = JobState.PRECHECKING
    job.progress_percent = 20
    job.started_at = datetime.now(UTC)
    await db_session.flush()

    reloaded = (
        await db_session.execute(
            select(IngestionJob)
            .options(selectinload(IngestionJob.events))
            .where(IngestionJob.id == job.id)
        )
    ).scalar_one()

    assert reloaded.state is JobState.PRECHECKING
    assert reloaded.progress_percent == 20
    assert reloaded.attempts == 0
    assert [event.state for event in reloaded.events] == [
        JobState.QUEUED,
        JobState.PRECHECKING,
    ]

    refreshed_document = await db_session.get(Document, document.id)
    assert refreshed_document is not None
    assert refreshed_document.current_job_id == job.id


async def test_job_error_payload_round_trip(db_session: AsyncSession) -> None:
    """La cause d'échec `{code, message, retryable}` est stockée en `JSONB`."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()
    document = factories.make_document(kb)
    db_session.add(document)
    await db_session.flush()

    job = factories.make_job(document, state=JobState.FAILED)
    job.error = {"code": "ocr_timeout", "message": "OCR trop long", "retryable": True}
    job.finished_at = datetime.now(UTC)
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)

    assert job.error == {"code": "ocr_timeout", "message": "OCR trop long", "retryable": True}


async def test_ingestion_job_event_is_readable_alone(db_session: AsyncSession) -> None:
    """Le journal est lisible sans charger le job, pour la frise de progression."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()
    document = factories.make_document(kb)
    db_session.add(document)
    await db_session.flush()
    job = factories.make_job(document)
    db_session.add(job)
    await db_session.flush()

    event = factories.make_event(job, state=JobState.CHUNKING, message="découpage")
    db_session.add(event)
    await db_session.flush()

    reloaded = await db_session.get(IngestionJobEvent, event.id)

    assert reloaded is not None
    assert reloaded.state is JobState.CHUNKING
    assert reloaded.message == "découpage"


async def test_api_key_and_session_belong_to_their_user(db_session: AsyncSession) -> None:
    """Les relations chargées explicitement relient bien les agrégats d'identité."""
    user = factories.make_user()
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([factories.make_session(user), factories.make_api_key(user)])
    await db_session.flush()

    reloaded = (
        await db_session.execute(
            select(User)
            .options(selectinload(User.sessions), selectinload(User.api_keys))
            .where(User.id == user.id)
        )
    ).scalar_one()

    assert len(reloaded.sessions) == 1
    assert len(reloaded.api_keys) == 1
    assert isinstance(reloaded.api_keys[0], ApiKey)
