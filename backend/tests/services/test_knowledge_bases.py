"""Cycle de vie d'une base de connaissances et composition de ses membres.

Le point sensible que ces tests verrouillent : **une base ne peut pas perdre son
dernier `owner`**. L'invariant porte sur un ensemble de lignes, donc aucune
contrainte SQL ne le tient — s'il n'est pas testé, rien ne le protège.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Document, IngestionJob, KnowledgeBase, KnowledgeBaseMember, User
from app.domain.enums import MemberRole, UserRole, Visibility
from app.domain.pagination import build_page
from app.domain.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseMemberUpdate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.user import UserCreate
from app.providers.engine.base import (
    EngineError,
    EngineTransientError,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.storage import LocalFilesystemStorageAdapter
from app.services.documents import DocumentService, UploadedDocument
from app.services.identity.permissions import KnowledgeBaseAccess
from app.services.identity.principal import ANONYMOUS, Principal
from app.services.identity.users import UserService
from app.services.knowledge_bases import KnowledgeBaseService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def account(
    db: AsyncSession, *, role: UserRole = UserRole.MEMBER, display_name: str = "Amara"
) -> User:
    """Compte réel persisté, `member` par défaut."""
    user = await UserService(db).register(
        UserCreate(
            email=factories.unique_email("kb"),
            password=SecretStr(PASSWORD),
            display_name=display_name,
        )
    )
    user.role = role
    await db.flush()
    return user


def principal_of(user: User) -> Principal:
    """Principal authentifié par cookie, sans passer par le transport HTTP."""
    return Principal(auth_method=AuthMethod.SESSION, user=user)


def creation(name: str = "Référentiel Qualité", **overrides: object) -> KnowledgeBaseCreate:
    """Charge utile de création valide, surchargeable champ par champ."""
    payload: dict[str, object] = {"name": name, "embedding_model_id": "bge-m3"}
    return KnowledgeBaseCreate.model_validate(payload | overrides)


def access_of(knowledge_base: KnowledgeBase, user: User) -> KnowledgeBaseAccess:
    """Accès `owner` déjà résolu, tel que le produirait `require_kb_permission`."""
    return KnowledgeBaseAccess(
        knowledge_base=knowledge_base, principal=principal_of(user), role=MemberRole.OWNER
    )


def dataset_deleting_engine(failure: Exception | None = None) -> FakeIngestionEngine:
    """Moteur dont seul `delete_dataset` compte — injecté explicitement, il vérifie aussi que
    la suppression ne résout **jamais** le vrai adaptateur RAGFlow."""
    engine = FakeIngestionEngine(strict=False)
    if failure is not None:
        engine.failures["delete_dataset"] = failure
    return engine


async def test_creating_a_base_derives_its_slug(db_session: AsyncSession) -> None:
    """Le slug vient du nom via `app.domain.slug` : diacritiques décomposés."""
    owner = await account(db_session)

    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())

    assert knowledge_base.slug == "referentiel-qualite"
    assert knowledge_base.owner_id == owner.id
    assert knowledge_base.document_count == 0


async def test_the_creator_is_registered_as_owner(db_session: AsyncSession) -> None:
    """`docs/domain-model.md` §2 : le créateur d'une base en est `owner`."""
    owner = await account(db_session)

    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())

    member = await db_session.get(KnowledgeBaseMember, (knowledge_base.id, owner.id))
    assert member is not None
    assert member.role is MemberRole.OWNER


async def test_an_explicit_slug_wins_over_the_derived_one(db_session: AsyncSession) -> None:
    """Le client garde la main sur l'identifiant lisible de sa base."""
    owner = await account(db_session)

    knowledge_base = await KnowledgeBaseService(db_session).create(
        owner, creation(slug="qualite-2026")
    )

    assert knowledge_base.slug == "qualite-2026"


async def test_the_provider_config_is_serialized_to_jsonb(db_session: AsyncSession) -> None:
    """`ProviderConfig` est un value object sérialisé, pas une table (ADR-009)."""
    owner = await account(db_session)

    knowledge_base = await KnowledgeBaseService(db_session).create(
        owner, creation(provider_config={"retrieval_top_k": 8, "rerank": True})
    )

    reloaded = await db_session.get(KnowledgeBase, knowledge_base.id)
    assert reloaded is not None
    assert reloaded.provider_config == {"retrieval_top_k": 8, "rerank": True}


async def test_a_name_without_any_letter_requires_an_explicit_slug(
    db_session: AsyncSession,
) -> None:
    """Aucun repli silencieux sur un identifiant technique : `422` explicite."""
    owner = await account(db_session)

    with pytest.raises(ValidationError):
        await KnowledgeBaseService(db_session).create(owner, creation(name="!!!"))


async def test_a_duplicate_slug_is_refused_for_the_same_owner(db_session: AsyncSession) -> None:
    """L'unicité `(owner_id, slug)` remonte en `409`."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    await service.create(owner, creation())

    with pytest.raises(ConflictError):
        await service.create(owner, creation())


async def test_two_owners_may_share_the_same_slug(db_session: AsyncSession) -> None:
    """L'unicité est **par propriétaire** : deux comptes peuvent nommer pareil."""
    first = await account(db_session)
    second = await account(db_session)
    service = KnowledgeBaseService(db_session)

    mine = await service.create(first, creation())
    yours = await service.create(second, creation())

    assert mine.slug == yours.slug
    assert mine.id != yours.id


async def test_updating_keeps_the_slug_when_only_the_name_changes(
    db_session: AsyncSession,
) -> None:
    """Renommer ne réécrit pas l'identifiant lisible : les liens partagés survivent."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    updated = await service.update(
        knowledge_base, KnowledgeBaseUpdate(name="Référentiel Qualité 2026")
    )

    assert updated.name == "Référentiel Qualité 2026"
    assert updated.slug == "referentiel-qualite"


async def test_updating_can_clear_a_nullable_field(db_session: AsyncSession) -> None:
    """`description` et `reranker_model_id` se vident explicitement."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(
        owner, creation(description="Procédures internes.", reranker_model_id="bge-reranker-v2-m3")
    )

    updated = await service.update(
        knowledge_base,
        KnowledgeBaseUpdate.model_validate({"description": None, "reranker_model_id": None}),
    )

    assert updated.description is None
    assert updated.reranker_model_id is None


async def test_updating_refuses_to_empty_a_required_field(db_session: AsyncSession) -> None:
    """Vider `name` produit un `422`, jamais un `NOT NULL` en 500."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    with pytest.raises(ValidationError):
        await service.update(knowledge_base, KnowledgeBaseUpdate.model_validate({"name": None}))


async def test_updating_to_a_taken_slug_is_refused(db_session: AsyncSession) -> None:
    """Le conflit d'unicité vaut aussi à la mise à jour."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    await service.create(owner, creation(name="Première", slug="premiere"))
    second = await service.create(owner, creation(name="Seconde", slug="seconde"))

    with pytest.raises(ConflictError):
        await service.update(second, KnowledgeBaseUpdate(slug="premiere"))


async def test_deleting_a_base_cascades_on_its_documents_and_jobs(
    db_session: AsyncSession, storage: LocalFilesystemStorageAdapter
) -> None:
    """Suppression physique : rien ne survit à la base (§0)."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    documents = DocumentService(db_session, storage=storage)
    await documents.register(
        access_of(knowledge_base, owner),
        UploadedDocument(
            content=b"%PDF-1.4 rapport",
            filename="rapport.pdf",
            declared_content_type="application/pdf",
        ),
    )

    await service.delete(knowledge_base)

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await db_session.scalar(select(func.count()).select_from(IngestionJob)) == 0
    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBaseMember)) == 0


async def test_deleting_a_base_withdraws_its_dataset_from_the_engine(
    db_session: AsyncSession,
) -> None:
    """Une base déjà versée retire son dataset RAGFlow (ADR-039) — la fuite d'avant."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    await db_session.flush()
    engine = dataset_deleting_engine()
    service = KnowledgeBaseService(db_session, engine=engine)

    await service.delete(knowledge_base)

    assert engine.datasets_deleted == ["dataset-42"]
    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0


async def test_a_base_never_synced_is_deleted_without_calling_the_engine(
    db_session: AsyncSession,
) -> None:
    """Sans identifiant externe, il n'y a rien à retirer — et surtout rien à faire échouer."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    engine = dataset_deleting_engine()
    service = KnowledgeBaseService(db_session, engine=engine)

    await service.delete(knowledge_base)

    assert engine.datasets_deleted == []
    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0


async def test_a_permanent_engine_refusal_does_not_block_the_deletion(
    db_session: AsyncSession,
) -> None:
    """`102` est ce que RAGFlow répond pour un dataset déjà retiré : la suite doit passer."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    await db_session.flush()
    engine = dataset_deleting_engine(EngineError("Dataset not found"))
    service = KnowledgeBaseService(db_session, engine=engine)

    await service.delete(knowledge_base)

    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0


async def test_a_transient_engine_failure_aborts_the_whole_deletion(
    db_session: AsyncSession,
) -> None:
    """Rien n'est supprimé : le moteur revient, et l'orphelin serait sinon inatteignable."""
    owner = await account(db_session)
    knowledge_base = await KnowledgeBaseService(db_session).create(owner, creation())
    knowledge_base.ragflow_dataset_external_id = "dataset-42"
    await db_session.flush()
    engine = dataset_deleting_engine(EngineTransientError("Moteur injoignable"))
    service = KnowledgeBaseService(db_session, engine=engine)

    with pytest.raises(EngineTransientError):
        await service.delete(knowledge_base)

    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBase)) == 1
    assert (
        await db_session.scalar(select(KnowledgeBase.ragflow_dataset_external_id)) == "dataset-42"
    )


async def test_listing_shows_owned_shared_and_public_bases(db_session: AsyncSession) -> None:
    """Le périmètre de la liste est exactement celui de la lecture unitaire."""
    owner = await account(db_session)
    stranger = await account(db_session)
    service = KnowledgeBaseService(db_session)
    mine = await service.create(owner, creation(name="La mienne"))
    shared = await service.create(stranger, creation(name="Partagée"))
    public = await service.create(stranger, creation(name="Publique", visibility=Visibility.PUBLIC))
    await service.create(stranger, creation(name="Secrète"))
    await service.set_member(shared, owner.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER))

    visible = await service.list_visible(principal_of(owner))

    assert {base.id for base in visible} == {mine.id, shared.id, public.id}


async def test_an_anonymous_visitor_only_lists_public_bases(db_session: AsyncSession) -> None:
    """Une base privée n'apparaît jamais à un visiteur (ADR-005)."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    await service.create(owner, creation(name="Secrète"))
    public = await service.create(owner, creation(name="Publique", visibility=Visibility.PUBLIC))

    visible = await service.list_visible(ANONYMOUS)

    assert [base.id for base in visible] == [public.id]


async def test_an_admin_lists_every_base(db_session: AsyncSession) -> None:
    """Matrice §4 : l'administrateur plateforme voit tout."""
    owner = await account(db_session)
    admin = await account(db_session, role=UserRole.ADMIN)
    service = KnowledgeBaseService(db_session)
    secret = await service.create(owner, creation(name="Secrète"))

    visible = await service.list_visible(principal_of(admin))

    assert secret.id in {base.id for base in visible}


async def test_listing_paginates_by_cursor(db_session: AsyncSession) -> None:
    """Deux pages consécutives couvrent tout le jeu sans jamais se recouper."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    for index in range(3):
        await service.create(owner, creation(name=f"Base {index}", slug=f"base-{index}"))

    first = build_page(
        await service.list_visible(principal_of(owner), limit=2),
        serializer=KnowledgeBaseRead.model_validate,
        limit=2,
    )
    second = build_page(
        await service.list_visible(principal_of(owner), cursor=first.next_cursor, limit=2),
        serializer=KnowledgeBaseRead.model_validate,
        limit=2,
    )

    assert first.next_cursor is not None
    assert second.next_cursor is None
    assert len({base.id for base in first.data} | {base.id for base in second.data}) == 3


async def test_adding_a_member_then_changing_their_role(db_session: AsyncSession) -> None:
    """`PUT …/members/{user_id}` crée puis met à jour la même ligne."""
    owner = await account(db_session)
    guest_member = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    created = await service.set_member(
        knowledge_base, guest_member.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
    )
    promoted = await service.set_member(
        knowledge_base, guest_member.id, KnowledgeBaseMemberUpdate(role=MemberRole.EDITOR)
    )

    assert created.created is True
    assert promoted.created is False
    assert promoted.member.role is MemberRole.EDITOR


async def test_adding_an_unknown_user_answers_not_found(db_session: AsyncSession) -> None:
    """Un compte inexistant produit un `404`, pas une violation de clé étrangère."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    with pytest.raises(NotFoundError):
        await service.set_member(
            knowledge_base, uuid.uuid4(), KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
        )


async def test_removing_a_member_leaves_the_base_intact(db_session: AsyncSession) -> None:
    """Retirer un `editor` est un cas nominal."""
    owner = await account(db_session)
    editor = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    await service.set_member(
        knowledge_base, editor.id, KnowledgeBaseMemberUpdate(role=MemberRole.EDITOR)
    )

    await service.remove_member(knowledge_base, editor.id)

    with pytest.raises(NotFoundError):
        await service.get_member(knowledge_base, editor.id)


async def test_removing_a_non_member_answers_not_found(db_session: AsyncSession) -> None:
    """On ne retire pas une appartenance qui n'existe pas."""
    owner = await account(db_session)
    stranger = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    with pytest.raises(NotFoundError):
        await service.remove_member(knowledge_base, stranger.id)


async def test_the_owner_of_the_base_cannot_be_removed(db_session: AsyncSession) -> None:
    """`knowledge_bases.owner_id` est la source de vérité : sa ligne est intouchable."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    with pytest.raises(ConflictError):
        await service.remove_member(knowledge_base, owner.id)


async def test_the_owner_of_the_base_cannot_be_demoted(db_session: AsyncSession) -> None:
    """Une ligne `viewer` pour le propriétaire mentirait : `PermissionService` le voit `owner`."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    with pytest.raises(ConflictError):
        await service.set_member(
            knowledge_base, owner.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
        )


async def test_the_last_owner_cannot_be_removed(db_session: AsyncSession) -> None:
    """Invariant §1, isolé de la règle sur `owner_id`.

    La base est fabriquée à la main pour que son unique ligne `owner` appartienne
    à un autre compte que `owner_id` : c'est le **compte des `owner`** qui doit
    refuser le retrait, pas la protection du propriétaire.
    """
    owner = await account(db_session)
    co_owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(owner, slug="fabriquee")
    db_session.add(knowledge_base)
    await db_session.flush()
    db_session.add(factories.make_member(knowledge_base, co_owner, role=MemberRole.OWNER))
    await db_session.flush()

    with pytest.raises(ConflictError):
        await KnowledgeBaseService(db_session).remove_member(knowledge_base, co_owner.id)


async def test_the_last_owner_cannot_be_demoted(db_session: AsyncSession) -> None:
    """Rétrograder revient à retirer : même refus, même invariant."""
    owner = await account(db_session)
    co_owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(owner, slug="fabriquee")
    db_session.add(knowledge_base)
    await db_session.flush()
    db_session.add(factories.make_member(knowledge_base, co_owner, role=MemberRole.OWNER))
    await db_session.flush()

    with pytest.raises(ConflictError):
        await KnowledgeBaseService(db_session).set_member(
            knowledge_base, co_owner.id, KnowledgeBaseMemberUpdate(role=MemberRole.EDITOR)
        )


async def test_a_second_owner_may_be_removed(db_session: AsyncSession) -> None:
    """Tant qu'un `owner` subsiste, le retrait est permis."""
    owner = await account(db_session)
    co_owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    await service.set_member(
        knowledge_base, co_owner.id, KnowledgeBaseMemberUpdate(role=MemberRole.OWNER)
    )

    await service.remove_member(knowledge_base, co_owner.id)

    remaining = await service.get_member(knowledge_base, owner.id)
    assert remaining.member.role is MemberRole.OWNER


async def test_listing_members_returns_the_full_directory(db_session: AsyncSession) -> None:
    """Défaut 1 (plan interne) : `list_members` remplace l'absence de listing."""
    owner = await account(db_session)
    editor = await account(db_session)
    viewer = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    await service.set_member(
        knowledge_base, editor.id, KnowledgeBaseMemberUpdate(role=MemberRole.EDITOR)
    )
    await service.set_member(
        knowledge_base, viewer.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
    )

    members = await service.list_members(knowledge_base)

    roles = {entry.member.user_id: entry.member.role for entry in members}
    assert roles == {
        owner.id: MemberRole.OWNER,
        editor.id: MemberRole.EDITOR,
        viewer.id: MemberRole.VIEWER,
    }


async def test_listing_members_of_a_freshly_created_base_shows_only_its_owner(
    db_session: AsyncSession,
) -> None:
    """`create` inscrit déjà le créateur : la liste n'est jamais vide."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    members = await service.list_members(knowledge_base)

    assert [entry.member.user_id for entry in members] == [owner.id]


async def test_listing_members_carries_the_display_name_of_each_account(
    db_session: AsyncSession,
) -> None:
    """L'annuaire nomme les membres : l'écran affiche un nom, jamais un UUID nu.

    Le nom vient de `users` par jointure — la ligne d'appartenance ne le porte
    pas, et la relation `KnowledgeBaseMember.user` est `raise_on_sql` : sans la
    jointure, cette lecture lèverait au lieu de charger paresseusement.
    """
    owner = await account(db_session, display_name="Amara Baradji")
    viewer = await account(db_session, display_name="Camille Dupont")
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    await service.set_member(
        knowledge_base, viewer.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
    )

    members = await service.list_members(knowledge_base)

    # Indexé par compte, jamais par position : dans une même transaction,
    # `created_at` vaut l'horodatage de transaction pour toutes les lignes —
    # l'ordre relatif de deux membres n'y est donc pas garanti.
    assert {entry.member.user_id: entry.display_name for entry in members} == {
        owner.id: "Amara Baradji",
        viewer.id: "Camille Dupont",
    }


async def test_reading_and_assigning_a_member_carry_the_same_display_name(
    db_session: AsyncSession,
) -> None:
    """Les trois chemins qui rendent une appartenance nomment le compte identiquement.

    Le frontend enchaîne recherche unitaire (`get_member`) et ajout
    (`set_member`) sur le même écran que l'annuaire : un nom présent d'un côté
    et absent de l'autre y ferait clignoter un UUID.
    """
    owner = await account(db_session, display_name="Amara Baradji")
    invited = await account(db_session, display_name="Camille Dupont")
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    assignment = await service.set_member(
        knowledge_base, invited.id, KnowledgeBaseMemberUpdate(role=MemberRole.VIEWER)
    )
    read = await service.get_member(knowledge_base, invited.id)

    assert assignment.display_name == "Camille Dupont"
    assert read.display_name == "Camille Dupont"


async def test_principal_roles_are_none_for_public_visibility_only(
    db_session: AsyncSession,
) -> None:
    """Défaut 2 : un compte sans appartenance sur une base `public` reçoit `None`."""
    owner = await account(db_session)
    stranger = await account(db_session)
    service = KnowledgeBaseService(db_session)
    public = await service.create(owner, creation(name="Publique", visibility=Visibility.PUBLIC))

    roles = await service.resolve_principal_roles(principal_of(stranger), [public])

    assert roles[public.id] is None


async def test_principal_roles_are_none_for_an_anonymous_principal(
    db_session: AsyncSession,
) -> None:
    """Un visiteur anonyme n'est jamais qu'un lecteur public : `None`, jamais `owner`."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    public = await service.create(owner, creation(name="Publique", visibility=Visibility.PUBLIC))

    roles = await service.resolve_principal_roles(ANONYMOUS, [public])

    assert roles[public.id] is None


async def test_principal_roles_reflect_a_real_editor_membership(
    db_session: AsyncSession,
) -> None:
    """Un `editor` réel se voit correctement, pas seulement l'`owner`."""
    owner = await account(db_session)
    editor = await account(db_session)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())
    await service.set_member(
        knowledge_base, editor.id, KnowledgeBaseMemberUpdate(role=MemberRole.EDITOR)
    )

    roles = await service.resolve_principal_roles(principal_of(editor), [knowledge_base])

    assert roles[knowledge_base.id] is MemberRole.EDITOR


async def test_principal_roles_grant_a_platform_admin_an_effective_owner_role(
    db_session: AsyncSession,
) -> None:
    """Même sémantique que `PermissionService.resolve_access` : un administrateur
    plateforme reçoit `owner` même sans ligne d'appartenance réelle."""
    owner = await account(db_session)
    admin = await account(db_session, role=UserRole.ADMIN)
    service = KnowledgeBaseService(db_session)
    knowledge_base = await service.create(owner, creation())

    roles = await service.resolve_principal_roles(principal_of(admin), [knowledge_base])

    assert roles[knowledge_base.id] is MemberRole.OWNER


async def test_resolving_principal_roles_for_a_page_costs_a_single_query(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut 2, le point sensible : jamais de N+1 sur `GET /knowledge-bases`.

    Un `resolve_access` par ligne serait aussi correct, mais coûterait `N`
    requêtes sur une page de `N` bases. `resolve_principal_roles` doit rester à
    une seule requête complémentaire, quelle que soit la taille de la page.
    """
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)
    bases = [
        await service.create(owner, creation(name=f"Base {index}", slug=f"base-{index}"))
        for index in range(5)
    ]

    calls = 0
    original_execute = AsyncSession.execute

    async def counting_execute(self: AsyncSession, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return await original_execute(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    roles = await service.resolve_principal_roles(principal_of(owner), bases)

    assert calls == 1
    assert all(role is MemberRole.OWNER for role in roles.values())


async def test_resolving_principal_roles_for_an_empty_page_makes_no_query(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pas de requête de rôle quand il n'y a rien à annoter (dernière page vide)."""
    owner = await account(db_session)
    service = KnowledgeBaseService(db_session)

    calls = 0
    original_execute = AsyncSession.execute

    async def counting_execute(self: AsyncSession, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return await original_execute(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    roles = await service.resolve_principal_roles(principal_of(owner), [])

    assert calls == 0
    assert roles == {}
