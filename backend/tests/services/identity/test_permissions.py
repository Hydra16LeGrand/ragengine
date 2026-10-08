"""Matrice de permissions par base de connaissances (`docs/domain-model.md` §4).

Le point sensible que ces tests verrouillent : une base privée invisible répond
`404`, jamais `403` — un `403` confirmerait son existence à qui énumère des
identifiants.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.db.models import KnowledgeBase, User
from app.domain.enums import MemberRole, UserRole, Visibility
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.user import UserCreate
from app.services.identity.errors import AuthenticationRequiredError
from app.services.identity.permissions import PermissionService
from app.services.identity.principal import ANONYMOUS, Principal
from app.services.identity.users import UserService
from tests.db import factories

PASSWORD = "mot-de-passe-solide-2026"


async def _account(db: AsyncSession, *, role: UserRole = UserRole.MEMBER) -> User:
    """Compte réel, `member` par défaut."""
    user = await UserService(db).register(
        UserCreate(
            email=factories.unique_email("perm"),
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


async def _knowledge_base(
    db: AsyncSession, owner: User, *, visibility: Visibility = Visibility.PRIVATE
) -> KnowledgeBase:
    """Base persistée appartenant à `owner`."""
    knowledge_base = factories.make_knowledge_base(
        owner, slug=f"kb-{owner.id.hex[:8]}", visibility=visibility
    )
    db.add(knowledge_base)
    await db.flush()
    return knowledge_base


async def test_the_owner_has_every_right_on_their_base(db_session: AsyncSession) -> None:
    """La propriété vaut `owner`, sans dépendre d'une ligne d'appartenance."""
    owner = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)

    access = await PermissionService(db_session).resolve_access(
        _session_principal(owner), knowledge_base.id, minimum=MemberRole.OWNER
    )

    assert access.role is MemberRole.OWNER
    assert access.knowledge_base.id == knowledge_base.id


async def test_a_member_role_is_read_from_the_association_table(
    db_session: AsyncSession,
) -> None:
    """Le rôle par base vient de `knowledge_base_members`."""
    owner = await _account(db_session)
    editor = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)
    db_session.add(factories.make_member(knowledge_base, editor, role=MemberRole.EDITOR))
    await db_session.flush()

    access = await PermissionService(db_session).resolve_access(
        _session_principal(editor), knowledge_base.id, minimum=MemberRole.EDITOR
    )

    assert access.role is MemberRole.EDITOR


async def test_roles_are_ordered(db_session: AsyncSession) -> None:
    """`owner` ⊃ `editor` ⊃ `viewer` : un éditeur peut lire."""
    owner = await _account(db_session)
    editor = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)
    db_session.add(factories.make_member(knowledge_base, editor, role=MemberRole.EDITOR))
    await db_session.flush()

    access = await PermissionService(db_session).resolve_access(
        _session_principal(editor), knowledge_base.id, minimum=MemberRole.VIEWER
    )

    assert access.role is MemberRole.EDITOR


async def test_a_viewer_cannot_write(db_session: AsyncSession) -> None:
    """Rôle insuffisant sur une base qu'il voit : `403`, l'existence est déjà connue."""
    owner = await _account(db_session)
    viewer = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)
    db_session.add(factories.make_member(knowledge_base, viewer, role=MemberRole.VIEWER))
    await db_session.flush()

    with pytest.raises(ForbiddenError):
        await PermissionService(db_session).resolve_access(
            _session_principal(viewer), knowledge_base.id, minimum=MemberRole.EDITOR
        )


async def test_a_private_base_is_invisible_to_a_stranger(db_session: AsyncSession) -> None:
    """`404` et non `403` : on ne confirme pas l'existence d'une base privée."""
    owner = await _account(db_session)
    stranger = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)

    with pytest.raises(NotFoundError):
        await PermissionService(db_session).resolve_access(
            _session_principal(stranger), knowledge_base.id, minimum=MemberRole.VIEWER
        )


async def test_a_private_base_is_invisible_to_an_anonymous_visitor(
    db_session: AsyncSession,
) -> None:
    """Même règle pour un visiteur : `404`, jamais `401` qui trahirait la base."""
    owner = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner)

    with pytest.raises(NotFoundError):
        await PermissionService(db_session).resolve_access(
            ANONYMOUS, knowledge_base.id, minimum=MemberRole.VIEWER
        )


async def test_a_public_base_is_readable_by_anyone(db_session: AsyncSession) -> None:
    """ADR-005 : une base `public` s'ouvre à la lecture et au Playground."""
    owner = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner, visibility=Visibility.PUBLIC)

    access = await PermissionService(db_session).resolve_access(
        ANONYMOUS, knowledge_base.id, minimum=MemberRole.VIEWER
    )

    assert access.role is None
    assert access.is_member is False


async def test_a_guest_can_read_a_public_base(db_session: AsyncSession) -> None:
    """L'espace invité accède au Playground des bases publiques."""
    owner = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner, visibility=Visibility.PUBLIC)
    guest = (await factories.ephemeral_session(db_session)).user

    access = await PermissionService(db_session).resolve_access(
        _session_principal(guest), knowledge_base.id, minimum=MemberRole.VIEWER
    )

    assert access.is_member is False


async def test_writing_on_a_public_base_requires_authentication(
    db_session: AsyncSession,
) -> None:
    """`401` pour un anonyme : le refus est réparable en se connectant."""
    owner = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner, visibility=Visibility.PUBLIC)

    with pytest.raises(AuthenticationRequiredError):
        await PermissionService(db_session).resolve_access(
            ANONYMOUS, knowledge_base.id, minimum=MemberRole.EDITOR
        )


async def test_writing_on_a_public_base_requires_a_role(db_session: AsyncSession) -> None:
    """`403` pour un compte connecté sans rôle sur la base."""
    owner = await _account(db_session)
    stranger = await _account(db_session)
    knowledge_base = await _knowledge_base(db_session, owner, visibility=Visibility.PUBLIC)

    with pytest.raises(ForbiddenError):
        await PermissionService(db_session).resolve_access(
            _session_principal(stranger), knowledge_base.id, minimum=MemberRole.EDITOR
        )


async def test_an_admin_passes_through_everything(db_session: AsyncSession) -> None:
    """Matrice §4 : l'administrateur plateforme voit et administre toutes les bases."""
    owner = await _account(db_session)
    admin = await _account(db_session, role=UserRole.ADMIN)
    knowledge_base = await _knowledge_base(db_session, owner)

    access = await PermissionService(db_session).resolve_access(
        _session_principal(admin), knowledge_base.id, minimum=MemberRole.OWNER
    )

    assert access.role is MemberRole.OWNER


async def test_an_unknown_base_answers_not_found(db_session: AsyncSession) -> None:
    """Un identifiant inventé ne produit pas de 500."""
    owner = await _account(db_session)

    with pytest.raises(NotFoundError):
        await PermissionService(db_session).resolve_access(
            _session_principal(owner), uuid.uuid4(), minimum=MemberRole.VIEWER
        )
