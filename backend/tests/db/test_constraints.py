"""Contraintes garanties **par la base**, pas seulement par le code Python.

Un test qui se contenterait de vérifier une validation Pydantic ne dirait rien
du jour où un script de maintenance, une tâche Celery ou un `INSERT` manuel
écrira dans la table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Document, IngestionJob, KnowledgeBase, Session, User
from app.domain.enums import JobState, MemberRole, UserRole
from tests.db import factories

FUTURE = datetime.now(UTC) + timedelta(hours=24)


async def test_a_guest_with_an_email_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    """Un invité ne peut pas porter d'e-mail : la contrainte le refuse en base."""
    inconsistent = User(
        email=factories.unique_email("faux-invite"),
        password_hash=None,
        display_name="Invité incohérent",
        role=UserRole.MEMBER,
        is_guest=True,
        is_active=True,
        expires_at=FUTURE,
    )
    db_session.add(inconsistent)

    with pytest.raises(IntegrityError, match="ck_users_guest_identity"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_guest_without_expiry_is_rejected(db_session: AsyncSession) -> None:
    """Un invité sans échéance échapperait à la purge : la base l'interdit."""
    inconsistent = User(
        email=None,
        password_hash=None,
        display_name="Invité éternel",
        role=UserRole.MEMBER,
        is_guest=True,
        is_active=True,
        expires_at=None,
    )
    db_session.add(inconsistent)

    with pytest.raises(IntegrityError, match="ck_users_guest_identity"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_real_account_without_password_is_rejected(db_session: AsyncSession) -> None:
    """Un compte réel sans empreinte de mot de passe serait inconnectable."""
    inconsistent = User(
        email=factories.unique_email("sans-mdp"),
        password_hash=None,
        display_name="Compte bancal",
        role=UserRole.MEMBER,
        is_guest=False,
        is_active=True,
        expires_at=None,
    )
    db_session.add(inconsistent)

    with pytest.raises(IntegrityError, match="ck_users_guest_identity"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_real_account_with_an_expiry_is_rejected(db_session: AsyncSession) -> None:
    """Une échéance sur un compte réel le ferait purger comme un invité."""
    inconsistent = User(
        email=factories.unique_email("expirable"),
        password_hash="$argon2id$v=19$fake",
        display_name="Compte expirable",
        role=UserRole.MEMBER,
        is_guest=False,
        is_active=True,
        expires_at=FUTURE,
    )
    db_session.add(inconsistent)

    with pytest.raises(IntegrityError, match="ck_users_guest_identity"):
        await db_session.flush()
    await db_session.rollback()


async def test_consistent_users_are_accepted(db_session: AsyncSession) -> None:
    """Les deux formes légitimes passent : compte réel et invité."""
    db_session.add_all([factories.make_user(), factories.make_guest()])

    await db_session.flush()


async def test_email_uniqueness_is_case_insensitive(db_session: AsyncSession) -> None:
    """`lower(email)` est unique : deux casses d'une même adresse se heurtent."""
    address = factories.unique_email("doublon")
    db_session.add(factories.make_user(email=address))
    await db_session.flush()

    db_session.add(factories.make_user(email=address.upper()))

    with pytest.raises(IntegrityError, match="ix_users_email_lower"):
        await db_session.flush()
    await db_session.rollback()


async def test_guests_do_not_collide_on_a_null_email(db_session: AsyncSession) -> None:
    """Deux `NULL` restent distincts : les invités ne se gênent pas entre eux."""
    db_session.add_all([factories.make_guest(), factories.make_guest()])

    await db_session.flush()


async def test_slug_is_unique_per_owner_only(db_session: AsyncSession) -> None:
    """Deux propriétaires peuvent nommer leur base pareil, un seul ne le peut pas."""
    first_owner = factories.make_user()
    second_owner = factories.make_user()
    db_session.add_all([first_owner, second_owner])
    await db_session.flush()

    db_session.add(factories.make_knowledge_base(first_owner, slug="support"))
    db_session.add(factories.make_knowledge_base(second_owner, slug="support"))
    await db_session.flush()

    db_session.add(factories.make_knowledge_base(first_owner, slug="support"))
    with pytest.raises(IntegrityError, match="uq_knowledge_bases_owner_id_slug"):
        await db_session.flush()
    await db_session.rollback()


async def test_api_key_hash_is_unique(db_session: AsyncSession) -> None:
    """Deux clés ne peuvent pas partager la même empreinte."""
    user = factories.make_user()
    db_session.add(user)
    await db_session.flush()

    first = factories.make_api_key(user)
    db_session.add(first)
    await db_session.flush()

    duplicate = ApiKey(
        user_id=user.id, name="doublon", prefix=first.prefix, key_hash=first.key_hash
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError, match="ix_api_keys_key_hash"):
        await db_session.flush()
    await db_session.rollback()


async def test_session_token_hash_is_unique(db_session: AsyncSession) -> None:
    """Un jeton de session ne peut pas être partagé par deux lignes."""
    user = factories.make_user()
    db_session.add(user)
    await db_session.flush()

    first = factories.make_session(user, token="jeton-partage")
    db_session.add(first)
    await db_session.flush()

    db_session.add(
        Session(
            user_id=user.id,
            token_hash=first.token_hash,
            expires_at=FUTURE,
        )
    )
    with pytest.raises(IntegrityError, match="ix_sessions_token_hash"):
        await db_session.flush()
    await db_session.rollback()


async def test_membership_pair_is_unique(db_session: AsyncSession) -> None:
    """Un utilisateur ne peut pas porter deux rôles sur la même base."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()

    db_session.add(factories.make_member(kb, owner, role=MemberRole.OWNER))
    await db_session.flush()

    db_session.add(factories.make_member(kb, owner, role=MemberRole.VIEWER))
    with pytest.raises(IntegrityError, match="pk_knowledge_base_members"):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.parametrize("progress", [-1, 101])
async def test_job_progress_is_bounded(db_session: AsyncSession, progress: int) -> None:
    """`progress_percent` reste dans 0-100, y compris écrit par un worker."""
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
    job.progress_percent = progress
    db_session.add(job)

    with pytest.raises(IntegrityError, match="progress_percent"):
        await db_session.flush()
    await db_session.rollback()


async def test_negative_document_size_is_rejected(db_session: AsyncSession) -> None:
    """Une taille négative trahirait un calcul faux en amont."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()

    document = factories.make_document(kb)
    document.size_bytes = -1
    db_session.add(document)

    with pytest.raises(IntegrityError, match="size_bytes"):
        await db_session.flush()
    await db_session.rollback()


async def test_unknown_enum_value_is_rejected_by_the_type(db_session: AsyncSession) -> None:
    """Le type `ENUM` PostgreSQL refuse un état inconnu, même en SQL brut."""
    with pytest.raises(DBAPIError):
        await db_session.execute(text("SELECT 'zombie'::job_state"))
    await db_session.rollback()


async def test_deleting_a_knowledge_base_cascades(db_session: AsyncSession) -> None:
    """Supprimer une base emporte ses documents, ses jobs et ses membres."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    kb = factories.make_knowledge_base(owner)
    db_session.add(kb)
    await db_session.flush()
    db_session.add(factories.make_member(kb, owner))
    document = factories.make_document(kb)
    db_session.add(document)
    await db_session.flush()
    job = factories.make_job(document)
    db_session.add(job)
    await db_session.flush()
    db_session.add(factories.make_event(job, state=JobState.QUEUED))
    document.current_job_id = job.id
    await db_session.flush()

    # Suppression en SQL, comme le ferait un service : c'est la base qui cascade.
    db_session.expunge_all()
    await db_session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
    await db_session.flush()

    remaining_documents = (
        (await db_session.execute(select(Document).where(Document.knowledge_base_id == kb.id)))
        .scalars()
        .all()
    )
    remaining_jobs = (
        (
            await db_session.execute(
                select(IngestionJob).where(IngestionJob.knowledge_base_id == kb.id)
            )
        )
        .scalars()
        .all()
    )

    assert remaining_documents == []
    assert remaining_jobs == []


async def test_purging_a_guest_cascades_to_everything_they_own(
    db_session: AsyncSession,
) -> None:
    """La purge des invités expirés ne laisse ni base, ni session, ni clé orpheline."""
    guest = factories.make_guest()
    db_session.add(guest)
    await db_session.flush()
    db_session.add(factories.make_session(guest))
    kb = factories.make_knowledge_base(guest, slug="essai-invite")
    db_session.add(kb)
    await db_session.flush()
    db_session.add(factories.make_document(kb))
    await db_session.flush()

    db_session.expunge_all()
    await db_session.execute(delete(User).where(User.id == guest.id))
    await db_session.flush()

    assert (
        await db_session.execute(select(KnowledgeBase).where(KnowledgeBase.owner_id == guest.id))
    ).scalars().all() == []
    assert (
        await db_session.execute(select(Session).where(Session.user_id == guest.id))
    ).scalars().all() == []


async def test_deleting_a_job_nulls_the_document_pointer(db_session: AsyncSession) -> None:
    """`documents.current_job_id` est mis à `NULL`, le document survit à son job."""
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
    document.current_job_id = job.id
    await db_session.flush()
    document_id = document.id

    db_session.expunge_all()
    await db_session.execute(delete(IngestionJob).where(IngestionJob.id == job.id))
    await db_session.flush()

    reloaded = await db_session.get(Document, document_id)
    assert reloaded is not None
    assert reloaded.current_job_id is None


async def test_a_document_requires_an_existing_knowledge_base(
    db_session: AsyncSession,
) -> None:
    """La clé étrangère empêche un document orphelin."""
    document = Document(
        knowledge_base_id=uuid.uuid4(),  # base inexistante
        filename="orphelin.pdf",
        content_type="application/pdf",
        size_bytes=1,
        checksum_sha256=factories.checksum_of("orphelin"),
        storage_uri="file:///srv/ragengine/orphelin.pdf",
    )
    db_session.add(document)

    with pytest.raises(IntegrityError, match="fk_documents_knowledge_base_id_knowledge_bases"):
        await db_session.flush()
    await db_session.rollback()
