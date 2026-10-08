"""Cas d'usage des organisations : cycle de vie et gestion des membres (ADR-022).

Même discipline que `KnowledgeBaseService` (§3 CONTRIBUTING.md) : ce service n'arbitre
**jamais** un droit d'accès — c'est le rôle unique d'`OrganizationPermissionService`
(`app.services.identity.permissions`). Les méthodes ici supposent l'autorisation
déjà acquise.

Deux invariants portés ici, faute de pouvoir l'être par le schéma seul :

- l'unicité **globale** du slug (`ix_organizations_slug`, à la différence du
  slug d'une KB, unique par propriétaire) est arbitrée par **la base** ; la
  lecture préalable ne sert qu'à produire un message clair, la violation de
  contrainte fait foi ;
- **une organisation garde toujours au moins un `owner`**, même règle que
  `KnowledgeBaseMember` (portant sur un ensemble de lignes, non exprimable en
  `CHECK` simple).

`create_personal` est le point le plus sensible du module : c'est le
provisionnement automatique appelé par `UserService.register` et par
l'ouverture d'un compte par un greffon (espace d'essai), où deux comptes
peuvent parfaitement partager le même `display_name`. Le retry sur collision
de slug **doit** passer par `begin_nested()` (comme `UserService.save_unique`) : sans ce
point de sauvegarde, l'`IntegrityError` laisserait la session en état
rollback-only et l'écriture suivante (l'appartenance, ou le flush de
l'utilisateur appelant) échouerait en cascade avec une erreur sans rapport
avec la cause réelle.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import KnowledgeBase, Organization, OrganizationMember, User
from app.domain.enums import OrganizationRole
from app.domain.pagination import DEFAULT_PAGE_SIZE, Cursor, apply_cursor_pagination
from app.domain.schemas.organization import (
    OrganizationCreate,
    OrganizationMemberUpdate,
    OrganizationUpdate,
)
from app.domain.slug import slugify
from app.services.identity.principal import Principal

ORGANIZATION_SLUG_UNIQUE_INDEX: Final = "ix_organizations_slug"
"""Index unique **global** sur `organizations.slug` — la contradiction remonte sous ce nom."""

_MAX_PERSONAL_SLUG_ATTEMPTS: Final = 5
"""Tentatives de désambiguïsation avant d'abandonner (`create_personal`)."""

_FALLBACK_SLUG: Final = "organisation"
"""Repli quand `display_name` ne produit aucun caractère exploitable (`slugify`)."""

_UNKNOWN_MEMBER: Final = "Cet utilisateur n'est pas membre de cette organisation."
_UNKNOWN_USER: Final = "Utilisateur introuvable."
_SLUG_TAKEN: Final = "Une organisation possède déjà cet identifiant."
_UNDERIVABLE_SLUG: Final = (
    "Le nom ne permet de dériver aucun identifiant lisible : fournissez un `slug` explicite."
)
_LAST_OWNER: Final = (
    "Une organisation doit conserver au moins un `owner` : "
    "promouvez un autre membre avant de retirer celui-ci."
)
_HAS_KNOWLEDGE_BASES: Final = (
    "Cette organisation possède encore des bases de connaissances : "
    "détachez-les ou supprimez-les avant de supprimer l'organisation."
)
_PERSONAL_SLUG_EXHAUSTED: Final = (
    "Impossible de dériver un identifiant unique pour l'organisation personnelle."
)


@dataclass(frozen=True, slots=True)
class OrganizationMemberAssignment:
    """Résultat d'un `PUT …/members/{user_id}` : la ligne, et si elle vient de naître.

    `created` n'existe que pour le code de statut (`201` contre `200`) : la
    décision métier est identique dans les deux cas.
    """

    member: OrganizationMember
    created: bool


class OrganizationService:
    """Cas d'usage portant sur `organizations` et `organization_members`."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, user: User, payload: OrganizationCreate) -> Organization:
        """Crée une organisation et inscrit son créateur comme `owner`.

        Réservée aux comptes réels : le routeur ne monte cette méthode que
        derrière `RegisteredPrincipal` (ADR-005) — le provisionnement de
        l'organisation personnelle d'un invité passe par `create_personal`,
        jamais par ici.

        Raises:
            ValidationError: le nom ne produit aucun slug exploitable.
            ConflictError: ce slug est déjà pris — unicité **globale** (ADR-022).
        """
        slug = payload.slug or slugify(payload.name)
        if not slug:
            raise ValidationError(_UNDERIVABLE_SLUG, extensions={"name": payload.name})
        await self._ensure_slug_is_free(slug)

        organization = Organization(name=payload.name, slug=slug)
        await self._flush_unique(organization)

        self._db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=OrganizationRole.OWNER
            )
        )
        await self._db.flush()
        return organization

    async def create_personal(self, user: User) -> Organization:
        """Provisionne l'organisation personnelle d'un compte — **usage interne**.

        Aucune vérification de permission : appelée par `UserService.register`
        et par l'ouverture d'un compte par un greffon (espace d'essai) au moment
        même où la ligne `User` est créée, jamais par une route. Le slug est
        dérivé de `display_name`, avec retry sur collision — deux comptes
        homonymes ne sont pas un cas rare, et l'unicité du slug d'une
        organisation est globale.

        Chaque tentative est isolée dans `begin_nested()` : après un échec, la
        session reste utilisable pour la tentative suivante (et pour l'appelant,
        qui doit encore flusher l'utilisateur lui-même dans le même appel).

        Raises:
            ConflictError: aucun slug disponible après
                `_MAX_PERSONAL_SLUG_ATTEMPTS` tentatives (pathologique).
        """
        base_slug = slugify(user.display_name) or _FALLBACK_SLUG
        organization: Organization | None = None

        for attempt in range(_MAX_PERSONAL_SLUG_ATTEMPTS):
            candidate_slug = base_slug if attempt == 0 else f"{base_slug}-{uuid.uuid4().hex[:8]}"
            candidate = Organization(name=user.display_name, slug=candidate_slug)
            try:
                async with self._db.begin_nested():
                    self._db.add(candidate)
                    await self._db.flush([candidate])
            except IntegrityError as exc:
                if ORGANIZATION_SLUG_UNIQUE_INDEX not in str(exc.orig):
                    raise
                continue
            organization = candidate
            break

        if organization is None:
            raise ConflictError(_PERSONAL_SLUG_EXHAUSTED, extensions={"slug": base_slug})

        await self._db.refresh(organization)
        self._db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=OrganizationRole.OWNER
            )
        )
        await self._db.flush()
        return organization

    async def list_visible(
        self,
        principal: Principal,
        *,
        cursor: str | Cursor | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Sequence[Organization]:
        """Liste les organisations que `principal` peut voir — `GET /api/v1/organizations`.

        Périmètre exactement celui de la lecture unitaire (ADR-022, pas de
        notion d'organisation publique) : les organisations dont le principal
        est membre, ou toutes pour un administrateur plateforme.
        """
        statement = select(Organization)
        if not principal.is_admin:
            user = principal.user
            if user is None:
                # Un visiteur anonyme n'est jamais membre de rien : inutile
                # d'interroger la base pour le confirmer.
                return []
            statement = statement.where(
                Organization.id.in_(
                    select(OrganizationMember.organization_id).where(
                        OrganizationMember.user_id == user.id
                    )
                )
            )
        return await self._paginate(statement, cursor=cursor, limit=limit)

    async def update(self, organization: Organization, payload: OrganizationUpdate) -> Organization:
        """Mise à jour partielle — `PATCH /api/v1/organizations/{organization_id}` (rôle `owner`).

        Raises:
            ValidationError: un champ est explicitement mis à `null` (aucun
                champ d'une organisation n'est nullable, à la différence d'une KB).
            ConflictError: le slug demandé est déjà pris (unicité globale).
        """
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            if value is None:
                raise ValidationError(
                    f"Le champ « {field} » ne peut pas être vidé.", extensions={"field": field}
                )

        slug = data.get("slug")
        if slug is not None and slug != organization.slug:
            await self._ensure_slug_is_free(slug, exclude=organization.id)

        for field, value in data.items():
            setattr(organization, field, value)
        await self._flush_unique(organization)
        return organization

    async def delete(self, organization: Organization) -> None:
        """Supprime une organisation — `DELETE /api/v1/organizations/{organization_id}`.

        Raises:
            ConflictError: l'organisation possède encore au moins une base de
                connaissances (`knowledge_bases.organization_id` porte
                `ondelete=RESTRICT` : pas de cascade physique multi-KB depuis
                la suppression d'un tenant, à la différence de la cascade
                `User` → `KnowledgeBase` déjà en place).
        """
        remaining = await self._db.scalar(
            select(func.count())
            .select_from(KnowledgeBase)
            .where(KnowledgeBase.organization_id == organization.id)
        )
        if (remaining or 0) > 0:
            raise ConflictError(_HAS_KNOWLEDGE_BASES, extensions={"knowledge_bases": remaining})
        await self._db.delete(organization)
        await self._db.flush()

    async def list_members(self, organization: Organization) -> Sequence[OrganizationMember]:
        """Liste tous les membres d'une organisation — `GET …/members`.

        Pas de pagination par curseur : même rationale que
        `KnowledgeBaseService.list_members`, une composition d'équipe humaine
        reste bornée en pratique. `created_at` ordonne par ancienneté, le
        créateur (toujours le premier membre écrit) en tête.
        """
        statement = (
            select(OrganizationMember)
            .where(OrganizationMember.organization_id == organization.id)
            .order_by(OrganizationMember.created_at.asc())
        )
        return (await self._db.execute(statement)).scalars().all()

    async def resolve_principal_roles(
        self, principal: Principal, organizations: Sequence[Organization]
    ) -> dict[uuid.UUID, OrganizationRole | None]:
        """Rôle de `principal` sur chaque organisation d'une page — anti-N+1.

        Reproduit `OrganizationPermissionService.resolve_access` sans jamais
        résoudre un rôle par ligne : un `SELECT ... IN (:page)` unique couvre
        toute la page, quelle que soit sa taille.
        """
        if not organizations:
            return {}
        if principal.user is None:
            return {organization.id: None for organization in organizations}

        statement = select(OrganizationMember.organization_id, OrganizationMember.role).where(
            OrganizationMember.user_id == principal.user.id,
            OrganizationMember.organization_id.in_(
                [organization.id for organization in organizations]
            ),
        )
        result = await self._db.execute(statement)
        member_roles: dict[uuid.UUID, OrganizationRole] = dict(result.tuples().all())

        roles: dict[uuid.UUID, OrganizationRole | None] = {}
        for organization in organizations:
            role = member_roles.get(organization.id)
            if role is None and principal.is_admin:
                # Même filet de sécurité que pour les KB : un administrateur
                # plateforme est toujours `owner` effectif.
                role = OrganizationRole.OWNER
            roles[organization.id] = role
        return roles

    async def get_member(
        self, organization: Organization, user_id: uuid.UUID
    ) -> OrganizationMember:
        """Lit une appartenance — `GET …/members/{user_id}`.

        Raises:
            NotFoundError: cet utilisateur n'a pas de rôle sur cette organisation.
        """
        member = await self._find_member(organization, user_id)
        if member is None:
            raise NotFoundError(_UNKNOWN_MEMBER)
        return member

    async def set_member(
        self,
        organization: Organization,
        user_id: uuid.UUID,
        payload: OrganizationMemberUpdate,
    ) -> OrganizationMemberAssignment:
        """Ajoute un membre ou change son rôle — `PUT …/members/{user_id}`.

        À la différence de `KnowledgeBaseService.set_member`, il n'existe
        aucune notion de propriétaire immuable (`Organization` n'a pas
        d'`owner_id`) : seule la protection du dernier `owner` s'applique.

        Raises:
            NotFoundError: le compte visé n'existe pas.
            ConflictError: rétrogradation du dernier `owner`.
        """
        if await self._db.get(User, user_id) is None:
            raise NotFoundError(_UNKNOWN_USER)

        member = await self._find_member(organization, user_id)
        created = member is None
        if member is None:
            member = OrganizationMember(
                organization_id=organization.id, user_id=user_id, role=payload.role
            )
            self._db.add(member)
        else:
            if member.role is OrganizationRole.OWNER and payload.role is not OrganizationRole.OWNER:
                await self._ensure_not_the_last_owner(organization)
            member.role = payload.role

        await self._db.flush()
        await self._db.refresh(member)
        return OrganizationMemberAssignment(member=member, created=created)

    async def remove_member(self, organization: Organization, user_id: uuid.UUID) -> None:
        """Retire un membre — `DELETE …/members/{user_id}`.

        Raises:
            NotFoundError: cet utilisateur n'a pas de rôle sur cette organisation.
            ConflictError: retrait du dernier `owner`.
        """
        member = await self.get_member(organization, user_id)
        if member.role is OrganizationRole.OWNER:
            await self._ensure_not_the_last_owner(organization)
        await self._db.delete(member)
        await self._db.flush()

    async def _paginate(
        self,
        statement: Select[tuple[Organization]],
        *,
        cursor: str | Cursor | None,
        limit: int,
    ) -> Sequence[Organization]:
        """Applique le curseur et exécute — `limit + 1` lignes pour `build_page`."""
        paginated = apply_cursor_pagination(statement, Organization, cursor=cursor, limit=limit)
        return (await self._db.execute(paginated)).scalars().all()

    async def _find_member(
        self, organization: Organization, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        """Charge une appartenance par sa clé primaire composite."""
        return await self._db.get(OrganizationMember, (organization.id, user_id))

    async def _ensure_not_the_last_owner(self, organization: Organization) -> None:
        """Refuse de laisser une organisation sans `owner`.

        Raises:
            ConflictError: l'organisation n'a plus qu'un seul `owner`.
        """
        owners = await self._db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
        )
        if (owners or 0) <= 1:
            raise ConflictError(_LAST_OWNER, extensions={"owners": owners or 0})

    async def _ensure_slug_is_free(self, slug: str, *, exclude: uuid.UUID | None = None) -> None:
        """Vérifie l'unicité **globale** du slug avant écriture, pour un message clair.

        Raises:
            ConflictError: ce slug est déjà pris par une autre organisation.
        """
        statement = select(Organization.id).where(Organization.slug == slug)
        if exclude is not None:
            statement = statement.where(Organization.id != exclude)
        if (await self._db.execute(statement)).first() is not None:
            raise ConflictError(_SLUG_TAKEN, extensions={"slug": slug})

    async def _flush_unique(self, organization: Organization) -> None:
        """Écrit la ligne en traduisant la violation d'unicité en `ConflictError`.

        Le `begin_nested()` isole l'écriture dans un point de sauvegarde : après
        l'échec, la session reste utilisable et le routeur peut répondre
        proprement au lieu d'être condamné au rollback complet.
        """
        try:
            async with self._db.begin_nested():
                self._db.add(organization)
                await self._db.flush([organization])
        except IntegrityError as exc:
            if ORGANIZATION_SLUG_UNIQUE_INDEX in str(exc.orig):
                raise ConflictError(_SLUG_TAKEN, extensions={"slug": organization.slug}) from exc
            raise
        await self._db.refresh(organization)
