"""Enregistrement d'un document, idempotence par empreinte et quotas invité.

Ce que ces tests verrouillent : **un même fichier re-téléversé dans la même base
ne produit ni doublon, ni second job, ni seconde écriture sur disque**
(`docs/domain-model.md` §2, ADR-014), les métadonnées sont **déduites des octets
reçus** et non déclarées par le client, et le compteur dénormalisé
`document_count` suit exactement les créations et les suppressions.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import URL, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from app.core.plugins import Plugin, plugins_overridden
from app.db.models import Document, IngestionJob, KnowledgeBase, Organization, User
from app.domain.enums import DocumentStatus, JobState, MemberRole, Visibility
from app.domain.schemas.document import (
    DocumentPrecheckDecisionUpdate,
    DocumentUpdate,
    PrecheckDecision,
)
from app.providers.engine.base import (
    EngineChunk,
    EngineChunkPosition,
    EngineDocumentChunksMeta,
    EngineDocumentChunksPage,
    EngineError,
    EngineTransientError,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.storage import LocalFilesystemStorageAdapter, StorageError, StoragePort
from app.services.documents import (
    UPLOAD_READ_CHUNK_BYTES,
    DocumentService,
    UploadedDocument,
    ensure_upload_within_limits,
    read_upload_content,
    resolve_content_type,
    resolve_filename,
)
from app.services.identity.principal import ANONYMOUS
from app.services.knowledge_bases import KnowledgeBaseService
from tests.services.test_knowledge_bases import access_of, account, creation, principal_of


def content_of(filename: str, *, size_bytes: int = 1024) -> bytes:
    """Contenu binaire distinct par nom : deux documents, deux empreintes."""
    return (f"%PDF-1.4 {filename} ".encode() + b"x" * size_bytes)[:size_bytes]


def ingestion(
    filename: str = "rapport.pdf", *, size_bytes: int = 1024, theme: str | None = None
) -> UploadedDocument:
    """Fichier reçu par la façade — seuls le contenu et deux indices de format."""
    return UploadedDocument(
        content=content_of(filename, size_bytes=size_bytes),
        filename=filename,
        declared_content_type="application/pdf",
        theme=theme,
    )


def documents_of(db: AsyncSession, storage: StoragePort) -> DocumentService:
    """Service branché sur le stockage du test."""
    return DocumentService(db, storage=storage)


def deleting_engine(failure: Exception | None = None) -> FakeIngestionEngine:
    """Moteur dont seul `delete_document` compte — injecté explicitement, il vérifie aussi que
    la suppression ne résout **jamais** le vrai adaptateur RAGFlow (clé d'API, moteur joignable)."""
    engine = FakeIngestionEngine(strict=False)
    if failure is not None:
        engine.failures["delete_document"] = failure
    return engine


@pytest.mark.parametrize(
    ("received", "expected"),
    [
        ("rapport.pdf", "rapport.pdf"),
        ("  rapport.pdf  ", "rapport.pdf"),
        (None, "document"),
        ("", "document"),
        # Un nom porteur de chemin ne doit jamais pouvoir désigner un emplacement.
        ("../../etc/passwd", "passwd"),
        (r"C:\Users\amara\rapport.pdf", "rapport.pdf"),
    ],
)
def test_the_retained_filename_is_never_a_path(received: str | None, expected: str) -> None:
    """Seul le nom de base survit, et il n'est jamais vide."""
    assert resolve_filename(received) == expected


@pytest.mark.parametrize(
    ("filename", "declared", "expected"),
    [
        ("rapport.pdf", "application/octet-stream", "application/pdf"),
        ("donnees.csv", None, "text/csv"),
        ("archive.inconnu", "application/vnd.exemple", "application/vnd.exemple"),
        ("archive.inconnu", "application/vnd.exemple; charset=utf-8", "application/vnd.exemple"),
        ("archive.inconnu", None, "application/octet-stream"),
    ],
)
def test_the_content_type_prefers_the_extension_then_the_client(
    filename: str, declared: str | None, expected: str
) -> None:
    """L'extension prime : elle se trompe moins souvent que le type annoncé."""
    assert resolve_content_type(filename, declared) == expected


def test_the_content_type_is_always_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-060, défaut trouvé en chemin : `mimetypes.guess_type(".xlsm")` renvoie
    `macroEnabled` en casse mixte sur certaines tables système, alors que la liste
    blanche (`Settings.upload_allowed_content_types`) est déclarée en minuscules — sans
    cette normalisation, un `.xlsm` légitime pouvait être rejeté avant tout autre
    contrôle. Le comportement système réel n'est pas fiable d'une machine à l'autre
    (déjà en casse mixte sur celle-ci) : la casse est forcée ici pour tester
    l'invariant, pas la table de la machine du jour."""
    monkeypatch.setattr(
        "app.services.documents.mimetypes.guess_type",
        lambda filename: ("application/vnd.ms-excel.sheet.macroEnabled.12", None),
    )

    assert (
        resolve_content_type("classeur.xlsm", None)
        == "application/vnd.ms-excel.sheet.macroenabled.12"
    )


async def test_registering_a_document_opens_its_job(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Un document accepté naît `pending` avec un job `queued` déjà porté."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())

    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )

    document = registered.document
    assert registered.created is True
    assert document.status is DocumentStatus.PENDING
    assert document.current_job_id is not None
    job = await db_session.get(IngestionJob, document.current_job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.knowledge_base_id == knowledge_base.id


async def test_registering_a_document_increments_the_denormalized_counter(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """`document_count` est maintenu par le service d'ingestion (§2)."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())

    await documents_of(db_session, storage).register(access_of(knowledge_base, owner), ingestion())

    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.document_count == 1


async def test_concurrent_uploads_do_not_lose_document_count_updates(
    migrated_database: URL, tmp_path: Path
) -> None:
    """Régression : `document_count` doit rester exact sous des envois concurrents.

    Trouvé en campagne E2E (9 documents envoyés en parallèle dans une base neuve
    via l'API réelle : `document_count` valait 1 à la fin, pas 9). `db_session`
    (transaction externe + points de sauvegarde, `tests/postgres.py`) ne peut pas
    reproduire ceci : tout y passe par la même connexion, donc par la même
    transaction Postgres — jamais deux `UPDATE` concurrents sur la même ligne.
    Il faut de vraies connexions séparées, comme le ferait le pool de connexions
    de la façade sous plusieurs requêtes HTTP simultanées.

    `knowledge_base.document_count += 1` (lecture Python puis réécriture de toute
    la ligne) perdrait ici la plupart des dix incréments : chaque session lit le
    même compteur de départ avant qu'aucune n'ait commité. Le correctif
    (`DocumentService._adjust_document_count`, `UPDATE ... SET document_count =
    document_count + 1` évalué côté serveur) n'a pas ce problème : chaque
    connexion se sérialise naturellement au niveau de la ligne.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    storage = LocalFilesystemStorageAdapter(root=tmp_path / "documents")
    concurrency = 10

    async with factory() as db:
        owner = await account(db)
        knowledge_base = await KnowledgeBaseService(db).create(owner, creation())
        await db.commit()
        owner_id, knowledge_base_id = owner.id, knowledge_base.id
        # ADR-022 : `create` assigne une organisation jetable au propriétaire
        # (aucune n'existait encore pour lui) — elle n'a pas de FK vers `User`,
        # donc pas de cascade à sa suppression : sans son id ici, elle
        # survivrait orpheline dans la base partagée du reste de la suite
        # après le `finally` ci-dessous, faussant tout comptage global
        # d'organisations dans un autre module (`tests/services/
        # test_organizations.py`, déjà pris en défaut ainsi).
        organization_id = knowledge_base.organization_id

    async def _upload(index: int) -> None:
        async with factory() as db:
            user = await db.get(User, owner_id)
            knowledge_base = await db.get(KnowledgeBase, knowledge_base_id)
            assert user is not None
            assert knowledge_base is not None
            await DocumentService(db, storage=storage).register(
                access_of(knowledge_base, user), ingestion(f"concurrent-{index}.pdf")
            )
            await db.commit()

    try:
        await asyncio.gather(*(_upload(index) for index in range(concurrency)))

        async with factory() as db:
            reloaded = await db.get(KnowledgeBase, knowledge_base_id)
            assert reloaded is not None
            assert reloaded.document_count == concurrency
    finally:
        async with factory() as db:
            # L'utilisateur d'abord : la cascade emporte base, documents et
            # jobs. L'organisation ensuite, une fois qu'il n'existe plus rien
            # pour la référencer.
            await db.execute(delete(User).where(User.id == owner_id))
            await db.execute(delete(Organization).where(Organization.id == organization_id))
            await db.commit()
        await engine.dispose()


async def test_the_metadata_is_computed_from_the_received_bytes(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """ADR-014 : rien de ce qui compte n'est repris du client."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    content = content_of("rapport.pdf")

    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )

    document = registered.document
    assert document.size_bytes == len(content)
    assert document.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert await storage.read(document.storage_uri) == content


async def test_a_lying_content_type_is_overridden_by_the_extension(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Un navigateur annonce souvent `octet-stream` pour un fichier identifiable."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    upload = UploadedDocument(
        content=content_of("rapport.pdf"),
        filename="rapport.pdf",
        declared_content_type="application/octet-stream",
    )

    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), upload
    )

    assert registered.document.content_type == "application/pdf"


async def test_a_replayed_upload_writes_nothing_on_disk(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """L'idempotence est arbitrée **avant** le stockage : un rejeu ne coûte rien."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    access = access_of(knowledge_base, owner)
    await service.register(access, ingestion())
    before = sorted(path.name for path in storage.root.rglob("*") if path.is_file())

    await service.register(access, ingestion())

    assert sorted(path.name for path in storage.root.rglob("*") if path.is_file()) == before


class _RefusingQuotas:
    """Politique de plafonds qui refuse tout versement — point d'extension d'un greffon."""

    async def ensure_can_create_knowledge_base(self, user: User) -> None:
        del user

    async def ensure_can_ingest_document(self, user: User, *, size_bytes: int) -> None:
        raise ConflictError(f"Plafond atteint ({size_bytes} octets).")


async def test_a_quota_refusal_leaves_no_orphan_file(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La politique de plafonds (`ResourceQuotaPolicy`) est arbitrée avant l'écriture : un
    refus ne laisse rien sur le volume."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    refusing = Plugin(name="refus", resource_quotas=lambda db, settings: _RefusingQuotas())

    with plugins_overridden([refusing]), pytest.raises(ConflictError, match="Plafond atteint"):
        await documents_of(db_session, storage).register(
            access_of(knowledge_base, owner), ingestion("refuse.pdf")
        )

    assert not storage.root.exists()


async def test_a_duplicate_checksum_returns_the_existing_document(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Idempotence : le rejeu d'un téléversement ne crée ni document ni job."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    access = access_of(knowledge_base, owner)
    first = await service.register(access, ingestion())

    replay = await service.register(access, ingestion())

    assert replay.created is False
    assert replay.document.id == first.document.id
    assert await db_session.scalar(select(func.count()).select_from(Document)) == 1
    assert await db_session.scalar(select(func.count()).select_from(IngestionJob)) == 1
    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.document_count == 1


async def test_the_same_file_may_live_in_two_bases(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """L'empreinte n'est unique que **par base** : deux bases sont indépendantes."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    first = await service.create(owner, creation(name="Première", slug="premiere"))
    second = await service.create(owner, creation(name="Seconde", slug="seconde"))
    documents = documents_of(db_session, storage)

    here = await documents.register(access_of(first, owner), ingestion())
    there = await documents.register(access_of(second, owner), ingestion())

    assert here.document.id != there.document.id


async def test_a_real_account_ignores_the_guest_quotas(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Les `ensure_*` traversent un compte réel sans effet."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    access = access_of(knowledge_base, owner)

    for index in range(4):
        await service.register(access, ingestion(f"piece-{index}.pdf"))

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 4


async def test_listing_documents_of_a_base(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La liste ne contient que les documents de la base demandée."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    mine = await service.create(owner, creation(name="Première", slug="premiere"))
    other = await service.create(owner, creation(name="Seconde", slug="seconde"))
    documents = documents_of(db_session, storage)
    kept = await documents.register(access_of(mine, owner), ingestion("garde.pdf"))
    await documents.register(access_of(other, owner), ingestion("ailleurs.pdf"))

    rows = await documents.list_for_knowledge_base(mine)

    assert [row.id for row in rows] == [kept.document.id]


async def test_listing_documents_filters_by_status_and_theme(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`status`/`theme` filtrent en base — audit dette technique 2026-09, motif A.

    Sans ce filtre serveur, un client qui a besoin de « tous les documents
    indexés d'un thème » ne peut le reconstituer qu'en tronquant une page —
    invisible au-delà de la première page.
    """
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    documents = documents_of(db_session, storage)
    access = access_of(knowledge_base, owner)

    indexed_produit = await documents.register(access, ingestion("a.pdf", theme="produit"))
    indexed_produit.document.status = DocumentStatus.INDEXED
    pending_produit = await documents.register(access, ingestion("b.pdf", theme="produit"))
    indexed_autre = await documents.register(access, ingestion("c.pdf", theme="autre"))
    indexed_autre.document.status = DocumentStatus.INDEXED
    await db_session.flush()

    by_status = await documents.list_for_knowledge_base(
        knowledge_base, status=DocumentStatus.INDEXED
    )
    assert {row.id for row in by_status} == {
        indexed_produit.document.id,
        indexed_autre.document.id,
    }

    by_theme = await documents.list_for_knowledge_base(knowledge_base, theme="produit")
    assert {row.id for row in by_theme} == {
        indexed_produit.document.id,
        pending_produit.document.id,
    }

    by_both = await documents.list_for_knowledge_base(
        knowledge_base, status=DocumentStatus.INDEXED, theme="produit"
    )
    assert [row.id for row in by_both] == [indexed_produit.document.id]


async def test_deleting_a_document_cascades_and_decrements(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Suppression physique du document, de ses jobs, et compteur remis à jour."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await db_session.scalar(select(func.count()).select_from(IngestionJob)) == 0
    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.document_count == 0


# --- Nettoyage complet à la suppression -----------------------------------


async def test_deleting_a_document_erases_its_bytes_from_the_storage(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La ligne partie, les octets aussi : plus d'orphelin sur le volume."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    storage_uri = registered.document.storage_uri
    assert await storage.read(storage_uri) == ingestion().content
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    with pytest.raises(StorageError):
        await storage.read(storage_uri)


async def test_deleting_a_document_keeps_bytes_shared_with_another_base(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le stockage adresse par empreinte : le même fichier versé deux fois, un seul objet.

    L'idempotence de `register` étant scopée à une base, deux `Document`
    coexistent sur un unique `storage_uri`. Effacer les octets à la première
    suppression casserait silencieusement le second document.
    """
    owner = await account(db_session)
    bases = KnowledgeBaseService(db_session)
    first = await bases.create(owner, creation(name="Première", slug="premiere"))
    second = await bases.create(owner, creation(name="Seconde", slug="seconde"))
    service = documents_of(db_session, storage)
    here = await service.register(access_of(first, owner), ingestion())
    there = await service.register(access_of(second, owner), ingestion())
    assert here.document.storage_uri == there.document.storage_uri
    resolved = await service.resolve_access(
        principal_of(owner), here.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    assert await storage.read(there.document.storage_uri) == ingestion().content


async def test_deleting_a_document_withdraws_it_from_the_engine(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Un document déjà indexé est retiré du dataset avec ses fragments."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    engine = deleting_engine()
    service = DocumentService(db_session, storage=storage, engine=engine)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    registered.document.ragflow_document_external_id = "document-7"
    await db_session.flush()
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    assert engine.documents_deleted == [("dataset-42", "document-7")]


async def test_a_document_never_parsed_is_deleted_without_calling_the_engine(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Sans identifiant externe, il n'y a rien à retirer — et surtout rien à faire échouer."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    engine = deleting_engine()
    service = DocumentService(db_session, storage=storage, engine=engine)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    assert engine.documents_deleted == []
    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0


async def test_a_permanent_engine_refusal_does_not_block_the_deletion(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`102` est ce que RAGFlow répond pour un document déjà retiré : la suite doit passer."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    engine = deleting_engine(EngineError("Document not found"))
    service = DocumentService(db_session, storage=storage, engine=engine)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    registered.document.ragflow_document_external_id = "document-7"
    await db_session.flush()
    storage_uri = registered.document.storage_uri
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    await service.delete(resolved)

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    with pytest.raises(StorageError):
        await storage.read(storage_uri)


async def test_a_transient_engine_failure_aborts_the_whole_deletion(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Rien n'est supprimé : le moteur revient, et l'orphelin serait sinon inatteignable."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    engine = deleting_engine(EngineTransientError("Moteur injoignable"))
    service = DocumentService(db_session, storage=storage, engine=engine)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    registered.document.ragflow_document_external_id = "document-7"
    await db_session.flush()
    storage_uri = registered.document.storage_uri
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.EDITOR
    )

    with pytest.raises(EngineTransientError):
        await service.delete(resolved)

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 1
    assert await storage.read(storage_uri) == ingestion().content


async def test_reading_the_content_returns_the_uploaded_bytes(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le service rend les octets d'origine, relus par le port de stockage."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())
    resolved = await service.resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.VIEWER
    )

    assert await service.read_content(resolved) == ingestion().content


async def test_the_precheck_decision_is_persisted_as_jsonb(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La décision est stockée sous les clés du catalogue fermé (§4)."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())

    updated = await service.record_precheck_decision(
        registered.document,
        DocumentPrecheckDecisionUpdate(
            decision=PrecheckDecision(optimize_for_tables=True, heavy_ocr=False)
        ),
    )

    reloaded = await db_session.get(Document, updated.id)
    assert reloaded is not None
    assert reloaded.precheck_decision == {
        "optimize_for_tables": True,
        "heavy_ocr": False,
        "use_parent_child": False,
        "chunk_method_override": None,
    }
    # La reprise du pipeline est publiée par le routeur, après le commit : la
    # persistance seule ne fait transiter aucun job.
    job = await db_session.get(IngestionJob, reloaded.current_job_id)
    assert job is not None
    assert job.state is JobState.QUEUED


async def test_updating_a_document_sets_its_theme(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le thème, saisi par l'utilisateur, se pose et s'efface via `PATCH`."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())

    updated = await service.update(registered.document, DocumentUpdate(theme="juridique"))
    reloaded = await db_session.get(Document, updated.id)
    assert reloaded is not None
    assert reloaded.theme == "juridique"

    cleared = await service.update(reloaded, DocumentUpdate(theme=None))
    assert cleared.theme is None


async def test_updating_a_document_refuses_to_clear_the_filename(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Contrairement à `theme`, `filename` n'est pas vidable — un document garde un nom."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())

    with pytest.raises(ValidationError):
        await service.update(registered.document, DocumentUpdate(filename=None))


async def test_updating_a_document_normalizes_a_path_carrying_filename(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`PATCH` reconverge vers `resolve_filename` (audit sécurité 2026-09, constat 13) : un
    nom porteur de chemin ne doit pas survivre en base par ce chemin d'écriture non plus,
    sous peine de partir tel quel vers RAGFlow (`filename=document.filename`)."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    service = documents_of(db_session, storage)
    registered = await service.register(access_of(knowledge_base, owner), ingestion())

    updated = await service.update(registered.document, DocumentUpdate(filename="../../etc/passwd"))

    assert updated.filename == "passwd"


async def test_resolving_an_unknown_document_answers_not_found(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """Un identifiant inventé ne produit pas de 500."""
    owner = await account(db_session)

    with pytest.raises(NotFoundError):
        await documents_of(db_session, storage).resolve_access(
            principal_of(owner), uuid.uuid4(), minimum=MemberRole.VIEWER
        )


async def test_a_document_of_a_private_base_is_invisible(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """`404` et non `403` : la visibilité d'un document est celle de sa base."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )

    with pytest.raises(NotFoundError):
        await documents_of(db_session, storage).resolve_access(
            ANONYMOUS, registered.document.id, minimum=MemberRole.VIEWER
        )


async def test_a_document_of_a_public_base_is_readable_by_anyone(
    db_session: AsyncSession,
    storage: LocalFilesystemStorageAdapter,
) -> None:
    """ADR-005 : une base `public` ouvre la lecture de ses documents."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(
        owner, creation(visibility=Visibility.PUBLIC)
    )
    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )

    resolved = await documents_of(db_session, storage).resolve_access(
        ANONYMOUS, registered.document.id, minimum=MemberRole.VIEWER
    )

    assert resolved.document.id == registered.document.id
    assert resolved.knowledge_base.id == knowledge_base.id


# --- Consultation des fragments indexés (Phase 13) --------------------------


def chunk_engine(
    *,
    page_to_return: EngineDocumentChunksPage,
    pages_by_call: list[EngineDocumentChunksPage] | None = None,
) -> FakeIngestionEngine:
    """Moteur dont `list_document_chunks` rend `page_to_return` à chaque appel, ou
    `pages_by_call` page après page — nécessaire pour la boucle de `parents_only`."""
    engine = FakeIngestionEngine(strict=False)
    engine.chunk_pages = pages_by_call if pages_by_call is not None else [page_to_return]
    return engine


async def indexed_document(
    db: AsyncSession,
    storage: StoragePort,
    *,
    dataset_external_id: str = "dataset-externe",
    document_external_id: str = "document-externe",
) -> tuple[Document, User]:
    """Document dont le job a atteint l'indexation : les deux identifiants moteur sont posés."""
    owner = await account(db)
    knowledge_base = await KnowledgeBaseService(db).create(owner, creation())
    registered = await documents_of(db, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )
    knowledge_base.ragflow_dataset_external_id = dataset_external_id
    registered.document.ragflow_document_external_id = document_external_id
    await db.flush()
    return registered.document, owner


def engine_chunks_page(**overrides: object) -> EngineDocumentChunksPage:
    """Page moteur par défaut : un fragment, positions réelles sur deux pages."""
    defaults: dict[str, object] = {
        "chunks": [
            EngineChunk(
                chunk_id="chunk-1",
                content="Un extrait du document.",
                positions=[
                    EngineChunkPosition(page=3, x0=10, x1=90, top=20, bottom=40),
                    EngineChunkPosition(page=4, x0=10, x1=90, top=0, bottom=15),
                ],
                important_keywords=["remboursement"],
                questions=["Quel est le délai de remboursement ?"],
                available=True,
            )
        ],
        "document": EngineDocumentChunksMeta(chunk_method="naive", chunk_count=1, token_count=42),
        "total": 1,
        "page": 1,
        "page_size": 20,
    }
    defaults.update(overrides)
    return EngineDocumentChunksPage(**defaults)  # type: ignore[arg-type]


async def test_listing_chunks_of_an_indexed_document_returns_the_engine_page(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Le fragment moteur est traduit — position en numéros de page, pas en pixels."""
    document, owner = await indexed_document(db_session, storage)
    engine = chunk_engine(page_to_return=engine_chunks_page())
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20)

    assert len(page.data) == 1
    chunk = page.data[0]
    assert chunk.chunk_id == "chunk-1"
    assert chunk.content == "Un extrait du document."
    assert chunk.page_start == 3
    assert chunk.page_end == 4
    assert chunk.important_keywords == ["remboursement"]
    assert chunk.questions == ["Quel est le délai de remboursement ?"]
    assert chunk.available is True
    assert page.document.chunk_method == "naive"
    assert page.document.chunk_count == 1
    assert page.document.token_count == 42
    assert page.document.uses_parent_child is False
    assert page.total == 1
    assert page.page == 1
    assert page.page_size == 20


async def test_listing_chunks_reports_parent_child_from_the_stored_precheck_decision(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """ADR-033 : le drapeau vient de la décision persistée, seul signal fiable.

    L'API de listage de RAGFlow n'expose aucun lien enfant→parent : sans cette
    relecture, l'interface ne peut pas distinguer un fragment parent
    (`available=false` par construction) d'un fragment désactivé à la main.
    """
    document, owner = await indexed_document(db_session, storage)
    document.precheck_decision = {
        "optimize_for_tables": False,
        "heavy_ocr": True,
        "use_parent_child": True,
    }
    await db_session.flush()
    engine = chunk_engine(page_to_return=engine_chunks_page())
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20)

    assert page.document.uses_parent_child is True


async def test_listing_chunks_of_a_document_whose_decision_predates_parent_child(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Une décision antérieure à la Phase 13 n'a pas la clé : `False`, jamais d'erreur."""
    document, owner = await indexed_document(db_session, storage)
    document.precheck_decision = {"optimize_for_tables": False, "heavy_ocr": True}
    await db_session.flush()
    engine = chunk_engine(page_to_return=engine_chunks_page())
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20)

    assert page.document.uses_parent_child is False


async def test_listing_chunks_forwards_page_and_page_size_to_the_engine(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La pagination demandée par l'appelant atteint le moteur telle quelle."""
    document, owner = await indexed_document(db_session, storage)
    engine = chunk_engine(page_to_return=engine_chunks_page(page=2, page_size=5))
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    await service.list_chunks(resolved, page=2, page_size=5)

    assert engine.chunk_list_calls == [("dataset-externe", "document-externe", 2, 5)]


async def test_listing_chunks_of_a_document_with_zero_chunks_is_an_empty_page_not_an_error(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """ADR-024 : un document indexé peut légitimement n'avoir aucun fragment."""
    document, owner = await indexed_document(db_session, storage)
    engine = chunk_engine(
        page_to_return=engine_chunks_page(
            chunks=[],
            document=EngineDocumentChunksMeta(chunk_method="naive", chunk_count=0, token_count=0),
            total=0,
        )
    )
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20)

    assert page.data == []
    assert page.total == 0
    assert page.document.chunk_count == 0


async def test_listing_chunks_of_a_document_never_indexed_answers_not_found(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Sans référence moteur (dataset ou document), rien à lire : `404`, pas d'appel moteur."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    registered = await documents_of(db_session, storage).register(
        access_of(knowledge_base, owner), ingestion()
    )
    engine = chunk_engine(page_to_return=engine_chunks_page())
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), registered.document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    with pytest.raises(NotFoundError):
        await service.list_chunks(resolved, page=1, page_size=20)

    assert engine.chunk_list_calls == []


def _chunk(chunk_id: str, *, available: bool) -> EngineChunk:
    return EngineChunk(chunk_id=chunk_id, content=f"Contenu {chunk_id}", available=available)


async def test_parents_only_paginates_over_the_filtered_set_not_the_whole_document(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Incident réel du 2026-09-07 : sans ce mode, la pagination portait sur

    parents + enfants confondus — une page entière d'enfants (`available=
    true`) rendait alors une page « parents seuls » vide, et le total annoncé
    ne correspondait à rien de ce qui s'affichait réellement. RAGFlow ne
    filtre pas côté serveur : ce test récupère tout par lots de 100 (deux
    appels ici, 100 + 50 fragments), avant de filtrer et repaginer nous-mêmes.
    """
    document, owner = await indexed_document(db_session, storage)
    first_batch = [_chunk(f"c{i}", available=i % 3 != 0) for i in range(100)]
    second_batch = [_chunk(f"c{100 + i}", available=i % 3 != 0) for i in range(50)]
    meta = EngineDocumentChunksMeta(chunk_method="naive", chunk_count=150, token_count=999)
    engine = chunk_engine(
        page_to_return=engine_chunks_page(),
        pages_by_call=[
            EngineDocumentChunksPage(
                chunks=first_batch, document=meta, total=150, page=1, page_size=100
            ),
            EngineDocumentChunksPage(
                chunks=second_batch, document=meta, total=150, page=2, page_size=100
            ),
        ],
    )
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)
    expected_parents = [c.chunk_id for c in first_batch + second_batch if not c.available]

    page = await service.list_chunks(resolved, page=1, page_size=5, parents_only=True)

    # Deux appels moteur (100 + 50 fragments), au plafond RAGFlow — jamais un
    # troisième une fois les 150 fragments annoncés effectivement récupérés.
    assert [call[2:] for call in engine.chunk_list_calls] == [(1, 100), (2, 100)]
    assert page.total == len(expected_parents)
    assert [c.chunk_id for c in page.data] == expected_parents[:5]
    assert page.page == 1
    assert page.page_size == 5
    assert page.document.chunk_count == 150


async def test_parents_only_second_page_offsets_into_the_filtered_set(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """La page demandée s'applique après filtrage, pas avant."""
    document, owner = await indexed_document(db_session, storage)
    chunks = [_chunk(f"c{i}", available=i % 2 == 0) for i in range(10)]  # 5 parents
    meta = EngineDocumentChunksMeta(chunk_method="naive", chunk_count=10, token_count=100)
    engine = chunk_engine(
        page_to_return=engine_chunks_page(),
        pages_by_call=[
            EngineDocumentChunksPage(chunks=chunks, document=meta, total=10, page=1, page_size=100)
        ],
    )
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)
    expected_parents = [c.chunk_id for c in chunks if not c.available]

    page = await service.list_chunks(resolved, page=2, page_size=2, parents_only=True)

    assert [c.chunk_id for c in page.data] == expected_parents[2:4]
    assert page.total == 5


async def test_parents_only_stops_on_an_empty_page_rather_than_loop_forever(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Garde-fou : un `total` jamais atteint ne doit jamais boucler indéfiniment."""
    document, owner = await indexed_document(db_session, storage)
    meta = EngineDocumentChunksMeta(chunk_method="naive", chunk_count=1, token_count=1)
    engine = chunk_engine(
        page_to_return=engine_chunks_page(),
        # `total=1000` mais la seule page rendue est vide — un total moteur
        # incohérent avec ce qu'il rend réellement, à ne jamais reproduire
        # en conditions réelles, mais qui ne doit pas planter côté client.
        pages_by_call=[
            EngineDocumentChunksPage(chunks=[], document=meta, total=1000, page=1, page_size=100)
        ],
    )
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20, parents_only=True)

    assert len(engine.chunk_list_calls) == 1
    assert page.data == []
    assert page.total == 0


async def test_parents_only_is_off_by_default_and_makes_a_single_call(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Comportement historique inchangé quand le filtre n'est pas demandé."""
    document, owner = await indexed_document(db_session, storage)
    engine = chunk_engine(page_to_return=engine_chunks_page())
    resolved = await documents_of(db_session, storage).resolve_access(
        principal_of(owner), document.id, minimum=MemberRole.VIEWER
    )
    service = DocumentService(db_session, storage=storage, engine=engine)

    page = await service.list_chunks(resolved, page=1, page_size=20)

    assert len(engine.chunk_list_calls) == 1
    assert engine.chunk_list_calls[0][2:] == (1, 20)
    assert page.total == 1


# --- Validation stricte des uploads (Phase 9, ADR-018) ---------------------


def test_ensure_upload_within_limits_accepts_a_conforming_file() -> None:
    """Taille et type dans les bornes par défaut : aucune exception."""
    settings = Settings()

    ensure_upload_within_limits(settings, size_bytes=1024, content_type="application/pdf")


def test_ensure_upload_within_limits_rejects_an_oversized_file() -> None:
    """Au-delà de `RAGENGINE_UPLOAD_MAX_BYTES` : `413`, limite et taille en extension."""
    settings = Settings(upload_max_bytes=100)

    with pytest.raises(PayloadTooLargeError) as excinfo:
        ensure_upload_within_limits(settings, size_bytes=101, content_type="application/pdf")

    assert excinfo.value.extensions == {"size_bytes": 101, "limit_bytes": 100}


def test_ensure_upload_within_limits_rejects_a_disallowed_content_type() -> None:
    """Hors liste blanche : `415`, le type refusé et la liste autorisée en extension."""
    settings = Settings(upload_allowed_content_types=["application/pdf"])

    with pytest.raises(UnsupportedMediaTypeError) as excinfo:
        ensure_upload_within_limits(
            settings, size_bytes=10, content_type="application/x-msdownload"
        )

    assert excinfo.value.extensions["content_type"] == "application/x-msdownload"
    assert excinfo.value.extensions["allowed_content_types"] == ["application/pdf"]


async def test_registering_an_oversized_document_is_rejected_before_any_write(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Un fichier trop volumineux ne crée ni document, ni job, ni écriture disque."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    settings = Settings(upload_max_bytes=512)
    service = DocumentService(db_session, settings, storage=storage)

    with pytest.raises(PayloadTooLargeError):
        await service.register(access_of(knowledge_base, owner), ingestion(size_bytes=1024))

    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.document_count == 0
    assert (await db_session.scalar(select(func.count()).select_from(Document))) == 0


@dataclass
class CountingStream:
    """Flux téléversé factice : rend `total` octets, et compte ce qu'on lui a lu."""

    total: int | None
    """`None` : flux sans fin, comme un envoi démesuré qu'on ne doit jamais lire en entier."""
    reads: int = 0
    served: int = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        remaining = size if self.total is None else min(size, self.total - self.served)
        self.served += remaining
        return b"x" * remaining


async def test_read_upload_content_reads_by_chunks_and_returns_every_byte() -> None:
    """Sous le plafond : tout le contenu, lu par morceaux bornés plutôt que d'un tenant."""
    settings = Settings(upload_max_bytes=3 * UPLOAD_READ_CHUNK_BYTES)
    stream = CountingStream(total=2 * UPLOAD_READ_CHUNK_BYTES + 10)

    content = await read_upload_content(stream, settings)

    assert len(content) == 2 * UPLOAD_READ_CHUNK_BYTES + 10
    # Trois morceaux pleins ou partiels, puis la lecture vide de fin de flux.
    assert stream.reads == 4


async def test_read_upload_content_accepts_a_file_of_exactly_the_limit() -> None:
    """La borne est inclusive, comme dans `ensure_upload_within_limits`."""
    settings = Settings(upload_max_bytes=UPLOAD_READ_CHUNK_BYTES + 1)

    content = await read_upload_content(CountingStream(total=UPLOAD_READ_CHUNK_BYTES + 1), settings)

    assert len(content) == UPLOAD_READ_CHUNK_BYTES + 1


async def test_read_upload_content_stops_as_soon_as_the_limit_is_crossed() -> None:
    """Flux sans fin : `413` au premier morceau qui franchit le plafond, rien de plus n'est lu."""
    settings = Settings(upload_max_bytes=2 * UPLOAD_READ_CHUNK_BYTES + 1)
    stream = CountingStream(total=None)

    with pytest.raises(PayloadTooLargeError) as excinfo:
        await read_upload_content(stream, settings)

    assert stream.reads == 3
    assert stream.served <= settings.upload_max_bytes + UPLOAD_READ_CHUNK_BYTES
    # Taille totale inconnue : seule la limite est annoncée, jamais un faux `size_bytes`.
    assert excinfo.value.extensions == {"limit_bytes": settings.upload_max_bytes}


async def test_read_upload_content_refuses_a_declared_oversize_without_reading() -> None:
    """La taille mesurée par Starlette suffit à refuser : aucun octet n'est lu."""
    settings = Settings(upload_max_bytes=100)
    stream = CountingStream(total=None)

    with pytest.raises(PayloadTooLargeError) as excinfo:
        await read_upload_content(stream, settings, declared_size=101)

    assert stream.reads == 0
    assert excinfo.value.extensions == {"size_bytes": 101, "limit_bytes": 100}


async def test_read_upload_content_never_trusts_a_declared_size_to_accept() -> None:
    """Une taille annoncée sous le plafond n'exempte pas la lecture de sa propre borne."""
    settings = Settings(upload_max_bytes=UPLOAD_READ_CHUNK_BYTES)

    with pytest.raises(PayloadTooLargeError):
        await read_upload_content(CountingStream(total=None), settings, declared_size=10)


async def test_registering_a_document_of_a_disallowed_content_type_is_rejected(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Un type de média hors liste blanche est refusé avant toute persistance."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    settings = Settings(upload_allowed_content_types=["application/pdf"])
    service = DocumentService(db_session, settings, storage=storage)
    upload = UploadedDocument(
        content=b"MZ" + b"x" * 100,
        filename="installateur.exe",
        declared_content_type="application/x-msdownload",
    )

    with pytest.raises(UnsupportedMediaTypeError):
        await service.register(access_of(knowledge_base, owner), upload)

    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.document_count == 0
