"""Application des matrices de permissions par base et par organisation.

Point d'application **unique** des règles d'accès à une base de connaissances
(`docs/domain-model.md` §4) et, depuis ADR-022, à une organisation (le tenant) :
aucun routeur ne doit refaire ce raisonnement, sous peine de le faire dériver.

Deux décisions structurent `PermissionService` (bases de connaissances) :

- **`404` plutôt que `403` sur une base invisible** (§5) : répondre « interdit »
  sur une base privée en confirmerait l'existence à un tiers, ce qui suffirait à
  cartographier les bases d'un compte en énumérant des identifiants.
- **`401` plutôt que `403` pour un anonyme** qui vise une action réservée : le
  refus est réparable en se connectant, et c'est ce code qui déclenche le
  parcours d'élévation de l'interface.

`OrganizationPermissionService` reprend la première décision à l'identique,
mais **sans** branche de visibilité publique : il n'existe pas d'organisation
« publique » (ADR-022), donc un non-membre reçoit systématiquement `404`, y
compris anonyme — jamais l'occasion d'un `401` réparable par connexion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.db.models import (
    KnowledgeBase,
    KnowledgeBaseMember,
    Organization,
    OrganizationMember,
    User,
)
from app.domain.enums import MemberRole, OrganizationRole, UserRole, Visibility
from app.services.identity.errors import AuthenticationRequiredError
from app.services.identity.principal import Principal

_MEMBER_ROLE_RANK: Final[dict[MemberRole, int]] = {
    MemberRole.VIEWER: 0,
    MemberRole.EDITOR: 1,
    MemberRole.OWNER: 2,
}
"""Ordre total des rôles par base : `owner` ⊃ `editor` ⊃ `viewer`."""

_ORGANIZATION_ROLE_RANK: Final[dict[OrganizationRole, int]] = {
    OrganizationRole.MEMBER: 0,
    OrganizationRole.OWNER: 1,
}
"""Ordre total des rôles d'organisation : `owner` ⊃ `member`."""

_UNKNOWN_KB: Final = "Base de connaissances introuvable."
_INSUFFICIENT_ROLE: Final = "Votre rôle sur cette base ne permet pas cette action."
_AUTHENTICATION_REQUIRED: Final = "Cette action requiert d'être connecté."
_UNKNOWN_ORGANIZATION: Final = "Organisation introuvable."
_INSUFFICIENT_ORGANIZATION_ROLE: Final = (
    "Votre rôle sur cette organisation ne permet pas cette action."
)


@dataclass(frozen=True, slots=True)
class KnowledgeBaseAccess:
    """Accès accordé : la base chargée, le principal, et le rôle qui l'a permis.

    `role` vaut `None` quand l'accès découle de la seule visibilité `public` —
    l'appelant n'est alors pas membre, et ne peut que lire.
    """

    knowledge_base: KnowledgeBase
    principal: Principal
    role: MemberRole | None

    @property
    def is_member(self) -> bool:
        """`True` si l'accès vient d'une appartenance, pas de la visibilité publique."""
        return self.role is not None


class KnowledgeBaseAuthorizer(Protocol):
    """Résout l'accès d'un principal à une base — `PermissionService` en est l'implémentation
    du cœur. Un greffon qui sert une capacité propre (un lien partagé, par exemple) en
    fournit une autre à `RetrievalService`, qui délègue d'abord à la matrice du cœur."""

    async def resolve_access(
        self, principal: Principal, knowledge_base_id: uuid.UUID, *, minimum: MemberRole
    ) -> KnowledgeBaseAccess:
        """Autorise `principal` sur la base, ou lève l'erreur adaptée."""
        ...


def satisfies(role: MemberRole | None, minimum: MemberRole) -> bool:
    """Indique si `role` couvre au moins `minimum`."""
    if role is None:
        return False
    return _MEMBER_ROLE_RANK[role] >= _MEMBER_ROLE_RANK[minimum]


class PermissionService:
    """Résout les droits d'un principal sur une base de connaissances."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def member_role(
        self, knowledge_base: KnowledgeBase, user: User | None
    ) -> MemberRole | None:
        """Rôle effectif de `user` sur la base, ou `None` s'il n'y a aucun lien.

        Le propriétaire est `owner` par construction, même si la ligne
        d'appartenance venait à manquer : `knowledge_bases.owner_id` est la
        source de vérité de la propriété.
        """
        if user is None:
            return None
        if knowledge_base.owner_id == user.id:
            return MemberRole.OWNER
        statement = select(KnowledgeBaseMember.role).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
            KnowledgeBaseMember.user_id == user.id,
        )
        return (await self._db.execute(statement)).scalar_one_or_none()

    async def resolve_access(
        self,
        principal: Principal,
        knowledge_base_id: uuid.UUID,
        *,
        minimum: MemberRole,
    ) -> KnowledgeBaseAccess:
        """Autorise `principal` sur une base, ou lève l'erreur adaptée.

        Raises:
            NotFoundError: base inexistante, ou invisible pour ce principal.
            AuthenticationRequiredError: principal anonyme visant une action
                réservée à un compte.
            ForbiddenError: compte authentifié dont le rôle est insuffisant.
        """
        knowledge_base = await self._db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None:
            raise NotFoundError(_UNKNOWN_KB)
        return await self.authorize(principal, knowledge_base, minimum=minimum)

    async def authorize(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        *,
        minimum: MemberRole,
    ) -> KnowledgeBaseAccess:
        """Applique la matrice à une base **déjà chargée** (évite un second `SELECT`)."""
        role = await self.member_role(knowledge_base, principal.user)
        is_public = knowledge_base.visibility is Visibility.PUBLIC

        # L'administrateur plateforme voit et fait tout (matrice §4).
        if principal.role is UserRole.ADMIN:
            return KnowledgeBaseAccess(
                knowledge_base=knowledge_base, principal=principal, role=role or MemberRole.OWNER
            )
        if satisfies(role, minimum):
            return KnowledgeBaseAccess(
                knowledge_base=knowledge_base, principal=principal, role=role
            )
        if role is None and not is_public:
            # Ni membre ni base publique : la base n'existe pas, de son point de vue.
            raise NotFoundError(_UNKNOWN_KB)
        if is_public and minimum is MemberRole.VIEWER:
            return KnowledgeBaseAccess(
                knowledge_base=knowledge_base, principal=principal, role=role
            )
        if not principal.is_authenticated:
            raise AuthenticationRequiredError(_AUTHENTICATION_REQUIRED)
        raise ForbiddenError(_INSUFFICIENT_ROLE)


@dataclass(frozen=True, slots=True)
class OrganizationAccess:
    """Accès accordé sur une organisation : l'organisation chargée, le principal, le rôle.

    Contrairement à `KnowledgeBaseAccess.role`, `role` n'est jamais `None` ici :
    il n'existe pas de notion d'organisation « publique » (ADR-022, à la
    différence de `Visibility.PUBLIC` pour une KB). Un accès résolu porte donc
    toujours un rôle réel — `resolve_access`/`authorize` lève `NotFoundError`
    avant de construire quoi que ce soit d'autre.
    """

    organization: Organization
    principal: Principal
    role: OrganizationRole


def organization_role_satisfies(role: OrganizationRole | None, minimum: OrganizationRole) -> bool:
    """Indique si `role` couvre au moins `minimum` (`owner` ⊃ `member`)."""
    if role is None:
        return False
    return _ORGANIZATION_ROLE_RANK[role] >= _ORGANIZATION_ROLE_RANK[minimum]


class OrganizationPermissionService:
    """Résout les droits d'un principal sur une organisation (ADR-022).

    Strictement analogue à `PermissionService`, avec une différence assumée :
    aucune branche « visibilité publique ». Un principal qui n'est pas membre
    d'une organisation ne doit jamais apprendre qu'elle existe : `404`
    systématique, y compris pour un visiteur anonyme, jamais `403` ni `401` —
    même rationale que pour une base privée (§5, `docs/domain-model.md`) :
    confirmer l'existence d'une ressource invisible par un refus explicite
    suffirait à cartographier les organisations en énumérant des identifiants.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def member_role(
        self, organization: Organization, user: User | None
    ) -> OrganizationRole | None:
        """Rôle effectif de `user` sur l'organisation, ou `None` s'il n'y a aucun lien."""
        if user is None:
            return None
        statement = select(OrganizationMember.role).where(
            OrganizationMember.organization_id == organization.id,
            OrganizationMember.user_id == user.id,
        )
        return (await self._db.execute(statement)).scalar_one_or_none()

    async def resolve_access(
        self, principal: Principal, organization_id: uuid.UUID, *, minimum: OrganizationRole
    ) -> OrganizationAccess:
        """Autorise `principal` sur une organisation, ou lève l'erreur adaptée.

        Raises:
            NotFoundError: organisation inexistante, ou invisible pour ce principal.
            ForbiddenError: compte membre dont le rôle est insuffisant.
        """
        organization = await self._db.get(Organization, organization_id)
        if organization is None:
            raise NotFoundError(_UNKNOWN_ORGANIZATION)
        return await self.authorize(principal, organization, minimum=minimum)

    async def authorize(
        self, principal: Principal, organization: Organization, *, minimum: OrganizationRole
    ) -> OrganizationAccess:
        """Applique la matrice à une organisation **déjà chargée** (évite un second `SELECT`)."""
        role = await self.member_role(organization, principal.user)

        # L'administrateur plateforme voit et gère toute organisation, même
        # sans ligne d'appartenance réelle (même logique que pour une KB).
        if principal.role is UserRole.ADMIN:
            return OrganizationAccess(
                organization=organization,
                principal=principal,
                role=role or OrganizationRole.OWNER,
            )
        if role is not None and organization_role_satisfies(role, minimum):
            return OrganizationAccess(organization=organization, principal=principal, role=role)
        if role is None:
            # Aucune notion d'organisation publique : un non-membre n'apprend
            # jamais qu'elle existe, authentifié ou non (voir docstring §classe).
            raise NotFoundError(_UNKNOWN_ORGANIZATION)
        raise ForbiddenError(_INSUFFICIENT_ORGANIZATION_ROLE)
