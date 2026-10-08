"""Cycle de vie d'une organisation et composition de ses membres (ADR-022).

Deux points sensibles que ces tests verrouillent :

- **une organisation ne peut pas perdre son dernier `owner`** — même invariant
  que `KnowledgeBaseMember`, porté par le service faute de `CHECK` possible ;
- **`create_personal` retry sur collision de slug sans jamais laisser la
  session dans un état inutilisable** — le principal risque de bug invisible
  à ruff/mypy/pytest de surface sur cette tâche (cf. brief), à l'image des
  bugs d'ordonnancement déjà rencontrés sur ce dépôt (Phase 4, Phase 9).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Organization, OrganizationMember, User
from app.domain.enums import OrganizationRole, UserRole
from app.domain.pagination import build_page
from app.domain.schemas.knowledge_base import KnowledgeBaseCreate
from app.domain.schemas.organization import (
    OrganizationCreate,
    OrganizationMemberUpdate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.user import UserCreate
from app.services.identity.principal import ANONYMOUS, Principal
from app.services.identity.users import UserService
from app.services.knowledge_bases import KnowledgeBaseService
from app.services.organizations import OrganizationService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def account(
    db: AsyncSession, *, role: UserRole = UserRole.MEMBER, display_name: str = "Amara"
) -> User:
    """Compte réel persisté, `member` par défaut — reçoit son organisation
    personnelle via le câblage de `UserService.register` (ADR-022)."""
    user = await UserService(db).register(
        UserCreate(
            email=factories.unique_email("org"),
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


def creation(name: str = "Baradji Services", **overrides: object) -> OrganizationCreate:
    """Charge utile de création valide, surchargeable champ par champ."""
    payload: dict[str, object] = {"name": name}
    return OrganizationCreate.model_validate(payload | overrides)


async def personal_organization_of(db: AsyncSession, user: User) -> Organization:
    """Organisation personnelle auto-provisionnée à l'inscription (`account`)."""
    row = (
        await db.execute(
            select(OrganizationMember.organization_id).where(OrganizationMember.user_id == user.id)
        )
    ).scalar_one()
    organization = await db.get(Organization, row)
    assert organization is not None
    return organization


# --- create -----------------------------------------------------------------


async def test_creating_an_organization_derives_its_slug(db_session: AsyncSession) -> None:
    """Le slug vient du nom via `app.domain.slug` : diacritiques décomposés."""
    owner = await account(db_session)

    organization = await OrganizationService(db_session).create(owner, creation("Équipe Qualité"))

    assert organization.slug == "equipe-qualite"


async def test_the_creator_is_registered_as_owner(db_session: AsyncSession) -> None:
    """Le créateur d'une organisation en est `owner` (ADR-022)."""
    owner = await account(db_session)

    organization = await OrganizationService(db_session).create(owner, creation())

    member = await db_session.get(OrganizationMember, (organization.id, owner.id))
    assert member is not None
    assert member.role is OrganizationRole.OWNER


async def test_an_explicit_slug_wins_over_the_derived_one(db_session: AsyncSession) -> None:
    """Le client garde la main sur l'identifiant lisible de son organisation."""
    owner = await account(db_session)

    organization = await OrganizationService(db_session).create(
        owner, creation(slug="baradji-2026")
    )

    assert organization.slug == "baradji-2026"


async def test_a_name_without_any_letter_requires_an_explicit_slug(
    db_session: AsyncSession,
) -> None:
    """Aucun repli silencieux sur un identifiant technique : `422` explicite."""
    owner = await account(db_session)

    with pytest.raises(ValidationError):
        await OrganizationService(db_session).create(owner, creation(name="!!!"))


async def test_a_duplicate_slug_is_refused_globally(db_session: AsyncSession) -> None:
    """À la différence d'une KB, l'unicité du slug d'une organisation est
    **globale** : deux comptes distincts ne peuvent pas se le disputer."""
    first = await account(db_session)
    second = await account(db_session)
    service = OrganizationService(db_session)
    await service.create(first, creation(slug="partage"))

    with pytest.raises(ConflictError):
        await service.create(second, creation(slug="partage"))


# --- create_personal ----------------------------------------------------------


async def test_create_personal_derives_a_slug_from_the_display_name(
    db_session: AsyncSession,
) -> None:
    """`create_personal` dérive le slug de `display_name`, même logique que `create`."""
    user = factories.make_user(display_name="Référentiel Qualité")
    db_session.add(user)
    await db_session.flush()

    organization = await OrganizationService(db_session).create_personal(user)

    assert organization.slug == "referentiel-qualite"
    assert organization.name == "Référentiel Qualité"
    member = await db_session.get(OrganizationMember, (organization.id, user.id))
    assert member is not None
    assert member.role is OrganizationRole.OWNER


async def test_create_personal_disambiguates_a_slug_collision_and_keeps_the_session_usable(
    db_session: AsyncSession,
) -> None:
    """Le point sensible du brief : deux créations avec le **même** `display_name`
    obtiennent des slugs distincts, et la session reste utilisable ensuite —
    sans `begin_nested()` autour de la tentative, l'`IntegrityError` laisserait
    la session en état rollback-only et la requête suivante échouerait en
    cascade avec une erreur sans rapport avec la collision réelle.
    """
    first_user = factories.make_user(display_name="Amara Baradji")
    second_user = factories.make_user(display_name="Amara Baradji")
    db_session.add_all([first_user, second_user])
    await db_session.flush()
    service = OrganizationService(db_session)

    first_org = await service.create_personal(first_user)
    second_org = await service.create_personal(second_user)

    assert first_org.slug != second_org.slug
    assert first_org.slug == "amara-baradji"
    assert second_org.slug.startswith("amara-baradji-")

    # La session reste utilisable : une requête ultérieure, sans lien avec la
    # collision, doit réussir sans lever d'erreur héritée d'un état rollback-only.
    count = await db_session.scalar(select(func.count()).select_from(Organization))
    assert count == 2
    third_user = factories.make_user(display_name="Quelqu'un d'autre")
    db_session.add(third_user)
    await db_session.flush()
    third_org = await service.create_personal(third_user)
    assert third_org.slug == "quelqu-un-d-autre"


async def test_create_personal_falls_back_when_the_name_has_no_letter(
    db_session: AsyncSession,
) -> None:
    """Contrairement à `create`, `create_personal` ne peut jamais échouer sur un
    nom illisible : c'est un provisionnement automatique, pas une saisie utilisateur."""
    user = factories.make_user(display_name="!!!")
    db_session.add(user)
    await db_session.flush()

    organization = await OrganizationService(db_session).create_personal(user)

    assert organization.slug == "organisation"


# --- update / delete ----------------------------------------------------------


async def test_updating_the_name_does_not_touch_the_slug(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    updated = await service.update(organization, OrganizationUpdate(name="Nouveau Nom"))

    assert updated.name == "Nouveau Nom"
    assert updated.slug == "baradji-services"


async def test_updating_refuses_to_empty_the_name(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    with pytest.raises(ValidationError):
        await service.update(organization, OrganizationUpdate.model_validate({"name": None}))


async def test_updating_to_a_taken_slug_is_refused(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    await service.create(owner, creation(name="Première", slug="premiere"))
    second = await service.create(owner, creation(name="Seconde", slug="seconde"))

    with pytest.raises(ConflictError):
        await service.update(second, OrganizationUpdate(slug="premiere"))


async def test_deleting_an_organization_without_bases_succeeds(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    await service.delete(organization)

    assert await db_session.get(Organization, organization.id) is None


async def test_deleting_an_organization_with_a_knowledge_base_is_refused(
    db_session: AsyncSession,
) -> None:
    """ADR-022 : pas de cascade physique multi-KB depuis la suppression d'un tenant."""
    owner = await account(db_session)
    org_service = OrganizationService(db_session)
    organization = await org_service.create(owner, creation())
    await KnowledgeBaseService(db_session).create(
        owner,
        KnowledgeBaseCreate(
            name="Base rattachée",
            embedding_model_id="bge-m3",
            organization_id=organization.id,
        ),
    )

    with pytest.raises(ConflictError):
        await org_service.delete(organization)

    # L'organisation survit au refus.
    assert await db_session.get(Organization, organization.id) is not None


# --- list_visible / resolve_principal_roles -----------------------------------


async def test_listing_shows_only_organizations_the_principal_belongs_to(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    # Capturée avant toute création supplémentaire : `owner` n'a encore que
    # son organisation personnelle à cet instant.
    personal = await personal_organization_of(db_session, owner)
    stranger = await account(db_session)
    service = OrganizationService(db_session)
    mine = await service.create(owner, creation(name="La mienne"))
    await service.create(stranger, creation(name="Celle d'un autre"))

    visible = await service.list_visible(principal_of(owner))

    visible_ids = {organization.id for organization in visible}
    assert visible_ids == {mine.id, personal.id}


async def test_an_anonymous_visitor_lists_no_organization(db_session: AsyncSession) -> None:
    """ADR-022 : pas de notion d'organisation publique."""
    owner = await account(db_session)
    await OrganizationService(db_session).create(owner, creation())

    visible = await OrganizationService(db_session).list_visible(ANONYMOUS)

    assert visible == []


async def test_an_admin_lists_every_organization(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    admin = await account(db_session, role=UserRole.ADMIN)
    service = OrganizationService(db_session)
    secret = await service.create(owner, creation(name="Isolée"))

    visible = await service.list_visible(principal_of(admin))

    assert secret.id in {organization.id for organization in visible}


async def test_listing_paginates_by_cursor(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    for index in range(3):
        await service.create(owner, creation(name=f"Org {index}", slug=f"org-{index}"))

    first = build_page(
        await service.list_visible(principal_of(owner), limit=2),
        serializer=OrganizationRead.model_validate,
        limit=2,
    )
    second = build_page(
        await service.list_visible(principal_of(owner), cursor=first.next_cursor, limit=2),
        serializer=OrganizationRead.model_validate,
        limit=2,
    )

    assert first.next_cursor is not None
    assert second.next_cursor is None
    # 3 créées + 1 personnelle auto-provisionnée à l'inscription = 4 au total.
    assert len({o.id for o in first.data} | {o.id for o in second.data}) == 4


async def test_principal_roles_reflect_a_real_membership(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    member = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())
    await service.set_member(
        organization, member.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )

    roles = await service.resolve_principal_roles(principal_of(member), [organization])

    assert roles[organization.id] is OrganizationRole.MEMBER


async def test_principal_roles_are_none_for_a_stranger(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    stranger = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    roles = await service.resolve_principal_roles(principal_of(stranger), [organization])

    assert roles[organization.id] is None


async def test_principal_roles_grant_a_platform_admin_an_effective_owner_role(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    admin = await account(db_session, role=UserRole.ADMIN)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    roles = await service.resolve_principal_roles(principal_of(admin), [organization])

    assert roles[organization.id] is OrganizationRole.OWNER


async def test_resolving_principal_roles_for_a_page_costs_a_single_query(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même point sensible que pour les KB : jamais de N+1 sur `GET /organizations`."""
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organizations = [
        await service.create(owner, creation(name=f"Org {index}", slug=f"batch-{index}"))
        for index in range(5)
    ]

    calls = 0
    original_execute = AsyncSession.execute

    async def counting_execute(self: AsyncSession, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return await original_execute(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    roles = await service.resolve_principal_roles(principal_of(owner), organizations)

    assert calls == 1
    assert all(role is OrganizationRole.OWNER for role in roles.values())


async def test_resolving_principal_roles_for_an_empty_page_makes_no_query(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)

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


# --- members -------------------------------------------------------------------


async def test_adding_a_member_then_changing_their_role(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    other = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    created = await service.set_member(
        organization, other.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )
    promoted = await service.set_member(
        organization, other.id, OrganizationMemberUpdate(role=OrganizationRole.OWNER)
    )

    assert created.created is True
    assert promoted.created is False
    assert promoted.member.role is OrganizationRole.OWNER


async def test_adding_an_unknown_user_answers_not_found(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    with pytest.raises(NotFoundError):
        await service.set_member(
            organization, uuid.uuid4(), OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
        )


async def test_removing_a_non_owner_member_leaves_the_organization_intact(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    member = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())
    await service.set_member(
        organization, member.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )

    await service.remove_member(organization, member.id)

    with pytest.raises(NotFoundError):
        await service.get_member(organization, member.id)


async def test_removing_a_non_member_answers_not_found(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    stranger = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    with pytest.raises(NotFoundError):
        await service.remove_member(organization, stranger.id)


async def test_the_last_owner_cannot_be_removed(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    with pytest.raises(ConflictError):
        await service.remove_member(organization, owner.id)


async def test_the_last_owner_cannot_be_demoted(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    with pytest.raises(ConflictError):
        await service.set_member(
            organization, owner.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
        )


async def test_a_second_owner_may_be_removed(db_session: AsyncSession) -> None:
    """Contrairement à une KB, `Organization` n'a pas d'`owner_id` : n'importe
    quel `owner` — y compris le créateur — peut être retiré tant qu'un autre
    `owner` subsiste."""
    owner = await account(db_session)
    co_owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())
    await service.set_member(
        organization, co_owner.id, OrganizationMemberUpdate(role=OrganizationRole.OWNER)
    )

    await service.remove_member(organization, owner.id)

    remaining = await service.get_member(organization, co_owner.id)
    assert remaining.role is OrganizationRole.OWNER


async def test_listing_members_returns_the_full_directory(db_session: AsyncSession) -> None:
    owner = await account(db_session)
    member = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())
    await service.set_member(
        organization, member.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )

    members = await service.list_members(organization)

    roles = {member_row.user_id: member_row.role for member_row in members}
    assert roles == {owner.id: OrganizationRole.OWNER, member.id: OrganizationRole.MEMBER}


async def test_listing_members_of_a_freshly_created_organization_shows_only_its_owner(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    service = OrganizationService(db_session)
    organization = await service.create(owner, creation())

    members = await service.list_members(organization)

    assert [member.user_id for member in members] == [owner.id]
