"""Matrice de permissions par organisation (ADR-022).

Le point sensible que ces tests verrouillent : une organisation dont le
principal n'est pas membre répond **`404`, systématiquement** — y compris pour
un visiteur anonyme, jamais `403` ni `401` — puisqu'il n'existe aucune notion
d'organisation « publique », à la différence d'une base de connaissances.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.db.models import User
from app.domain.enums import OrganizationRole, UserRole
from app.domain.schemas.organization import OrganizationMemberUpdate
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.user import UserCreate
from app.services.identity.permissions import OrganizationPermissionService
from app.services.identity.principal import ANONYMOUS, Principal
from app.services.identity.users import UserService
from app.services.organizations import OrganizationService
from tests.db import factories
from tests.services.test_organizations import creation

PASSWORD = "mot-de-passe-solide-2026"


async def _account(db: AsyncSession, *, role: UserRole = UserRole.MEMBER) -> User:
    """Compte réel, `member` par défaut — reçoit son organisation personnelle."""
    user = await UserService(db).register(
        UserCreate(
            email=factories.unique_email("orgperm"),
            password=SecretStr(PASSWORD),
            display_name="Amara",
        )
    )
    user.role = role
    await db.flush()
    return user


def _session_principal(user: User) -> Principal:
    """Principal authentifié par cookie, sans passer par le transport HTTP."""
    return Principal(auth_method=AuthMethod.SESSION, user=user)


async def test_the_owner_has_every_right_on_their_organization(db_session: AsyncSession) -> None:
    owner = await _account(db_session)
    organization = await OrganizationService(db_session).create(owner, creation())

    access = await OrganizationPermissionService(db_session).resolve_access(
        _session_principal(owner), organization.id, minimum=OrganizationRole.OWNER
    )

    assert access.role is OrganizationRole.OWNER
    assert access.organization.id == organization.id


async def test_a_member_role_is_read_from_the_association_table(db_session: AsyncSession) -> None:
    owner = await _account(db_session)
    member = await _account(db_session)
    org_service = OrganizationService(db_session)
    organization = await org_service.create(owner, creation())
    await org_service.set_member(
        organization, member.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )

    access = await OrganizationPermissionService(db_session).resolve_access(
        _session_principal(member), organization.id, minimum=OrganizationRole.MEMBER
    )

    assert access.role is OrganizationRole.MEMBER


async def test_a_member_cannot_administrate(db_session: AsyncSession) -> None:
    """Rôle insuffisant sur une organisation qu'il voit déjà : `403`."""
    owner = await _account(db_session)
    member = await _account(db_session)
    org_service = OrganizationService(db_session)
    organization = await org_service.create(owner, creation())
    await org_service.set_member(
        organization, member.id, OrganizationMemberUpdate(role=OrganizationRole.MEMBER)
    )

    with pytest.raises(ForbiddenError):
        await OrganizationPermissionService(db_session).resolve_access(
            _session_principal(member), organization.id, minimum=OrganizationRole.OWNER
        )


async def test_a_non_member_gets_not_found(db_session: AsyncSession) -> None:
    """`404`, pas `403` : on ne confirme pas l'existence de l'organisation."""
    owner = await _account(db_session)
    stranger = await _account(db_session)
    organization = await OrganizationService(db_session).create(owner, creation())

    with pytest.raises(NotFoundError):
        await OrganizationPermissionService(db_session).resolve_access(
            _session_principal(stranger), organization.id, minimum=OrganizationRole.MEMBER
        )


async def test_an_anonymous_visitor_gets_not_found_too(db_session: AsyncSession) -> None:
    """Même refus opaque pour un anonyme : jamais de `401` réparable par
    connexion, contrairement à la matrice des bases de connaissances — il n'y
    a pas de notion d'organisation publique dont la lecture serait ouverte."""
    owner = await _account(db_session)
    organization = await OrganizationService(db_session).create(owner, creation())

    with pytest.raises(NotFoundError):
        await OrganizationPermissionService(db_session).resolve_access(
            ANONYMOUS, organization.id, minimum=OrganizationRole.MEMBER
        )


async def test_an_admin_passes_through_everything(db_session: AsyncSession) -> None:
    """Un administrateur plateforme voit et gère toute organisation, même sans
    ligne d'appartenance réelle (même logique que pour une KB)."""
    owner = await _account(db_session)
    admin = await _account(db_session, role=UserRole.ADMIN)
    organization = await OrganizationService(db_session).create(owner, creation())

    access = await OrganizationPermissionService(db_session).resolve_access(
        _session_principal(admin), organization.id, minimum=OrganizationRole.OWNER
    )

    assert access.role is OrganizationRole.OWNER


async def test_an_unknown_organization_answers_not_found(db_session: AsyncSession) -> None:
    owner = await _account(db_session)

    with pytest.raises(NotFoundError):
        await OrganizationPermissionService(db_session).resolve_access(
            _session_principal(owner), uuid.uuid4(), minimum=OrganizationRole.MEMBER
        )
