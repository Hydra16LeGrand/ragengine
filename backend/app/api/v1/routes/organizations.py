"""Organisations : cycle de vie et gestion des membres (ADR-022).

Conformément à CONTRIBUTING.md §3.2, ces handlers ne portent **aucune** règle
métier : ils valident (Pydantic), délèguent à `OrganizationService`,
sérialisent. Le contrôle d'accès est **déclaré**, pas écrit ici — les alias
`OrgRead` / `OrgAdmin` montent `require_org_permission`, seul point
d'application de la matrice ADR-022.

Une différence assumée par rapport à `knowledge_bases.py` : **lire l'annuaire
des membres est ouvert à tout membre** (`OrganizationRole.MEMBER`), pas
réservé à l'`owner` comme pour une base de connaissances. L'annuaire d'une
équipe est normalement visible par ses membres — la confidentialité par KB
répond à une autre logique (isoler des bases privées entre équipes), qui ne
s'applique pas à la composition de l'équipe elle-même. Seule la **mutation**
(ajout/retrait/changement de rôle, modification/suppression de l'organisation)
exige `OrganizationRole.OWNER`.

`POST /organizations` est réservé à un compte réel (`RegisteredPrincipal`,
ADR-005) : le provisionnement de l'organisation personnelle d'un compte passe
par `OrganizationService.create_personal`, jamais par cette route.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.v1.dependencies import (
    CurrentPrincipal,
    RegisteredPrincipal,
    require_org_permission,
    require_scope,
)
from app.db.models import Organization
from app.db.session import DbSession
from app.domain.enums import OrganizationRole
from app.domain.pagination import Page, PaginationParams, build_page
from app.domain.schemas.organization import (
    OrganizationCreate,
    OrganizationMemberRead,
    OrganizationMemberUpdate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.domain.scopes import ApiKeyScope
from app.services.identity.permissions import OrganizationAccess
from app.services.organizations import OrganizationService

router = APIRouter(tags=["organizations"])

ScopeRead = Depends(require_scope(ApiKeyScope.ORGANIZATIONS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.ORGANIZATIONS_WRITE))
"""Portée de clé API exigée en écriture (création, modification, membres)."""

INVISIBLE_ORGANIZATION: Final[dict[int | str, dict[str, Any]]] = {
    int(HTTPStatus.NOT_FOUND): {
        "description": "Organisation inexistante, ou invisible pour le principal."
    }
}
"""Déclaré route par route, jamais sur le routeur : les deux chemins de
collection (`POST` et `GET /organizations`) ne peuvent pas répondre `404`."""

OrgRead = Annotated[OrganizationAccess, Depends(require_org_permission(OrganizationRole.MEMBER))]
"""Accès en lecture : n'importe quel membre de l'organisation."""

OrgAdmin = Annotated[OrganizationAccess, Depends(require_org_permission(OrganizationRole.OWNER))]
"""Accès d'administration : `owner` de l'organisation, ou administrateur plateforme."""

MemberPath = Annotated[uuid.UUID, Path(description="Identifiant du membre visé.")]


def _to_read_model(
    organization: Organization, *, principal_role: OrganizationRole | None
) -> OrganizationRead:
    """Sérialise une organisation **avec** le rôle du principal, absent de l'ORM.

    Même logique de fusion que `knowledge_bases._to_read_model` : rien sur
    `Organization` ne porte `principal_role`, il est donc résolu à côté
    (`access.role`, ou une requête complémentaire pour la liste) puis fusionné
    ici, au point unique de sérialisation du routeur.
    """
    return OrganizationRead.model_validate(organization).model_copy(
        update={"principal_role": principal_role}
    )


@router.post(
    "/organizations",
    status_code=HTTPStatus.CREATED,
    summary="Créer une organisation",
    description=(
        "Réservée à un compte réel (ADR-005) : l'organisation personnelle d'un "
        "compte est provisionnée automatiquement à sa création, jamais via "
        "cette route. Le créateur en devient `owner`. Le `slug` est dérivé du "
        "nom lorsqu'il n'est pas fourni, et reste unique **globalement** — à "
        "la différence du slug d'une base de connaissances."
    ),
    responses={int(HTTPStatus.CONFLICT): {"description": "Slug déjà utilisé."}},
    dependencies=[ScopeWrite],
)
async def create_organization(
    payload: OrganizationCreate, principal: RegisteredPrincipal, db: DbSession
) -> OrganizationRead:
    """Crée une organisation appartenant au principal courant."""
    organization = await OrganizationService(db).create(principal.require_account(), payload)
    # Le créateur en est toujours `owner` : `create` l'inscrit explicitement.
    return _to_read_model(organization, principal_role=OrganizationRole.OWNER)


@router.get(
    "/organizations",
    summary="Lister les organisations visibles",
    description=(
        "Liste paginée par curseur, de la plus récente à la plus ancienne. Le "
        "périmètre est exactement celui de la lecture unitaire : organisations "
        "dont le principal est membre. Un administrateur plateforme les voit "
        "toutes, un appelant sans compte n'en voit aucune (ADR-022 : pas de notion "
        "d'organisation publique)."
    ),
    dependencies=[ScopeRead],
)
async def list_organizations(
    principal: CurrentPrincipal,
    db: DbSession,
    pagination: Annotated[PaginationParams, Query()],
) -> Page[OrganizationRead]:
    """Liste les organisations visibles par le principal courant."""
    service = OrganizationService(db)
    rows = await service.list_visible(
        principal, cursor=pagination.decoded_cursor(), limit=pagination.limit
    )
    # Une seule requête complémentaire pour toute la page — jamais un
    # `resolve_access` par ligne (`OrganizationService.resolve_principal_roles`).
    roles = await service.resolve_principal_roles(principal, rows)
    return build_page(
        rows,
        serializer=lambda organization: _to_read_model(
            organization, principal_role=roles.get(organization.id)
        ),
        limit=pagination.limit,
    )


@router.get(
    "/organizations/{organization_id}",
    summary="Lire une organisation",
    description=(
        "Une organisation invisible pour le principal répond `404`, jamais "
        "`403` : un refus explicite confirmerait son existence (ADR-022)."
    ),
    responses=INVISIBLE_ORGANIZATION,
    dependencies=[ScopeRead],
)
async def read_organization(access: OrgRead) -> OrganizationRead:
    """Renvoie l'organisation résolue par la dépendance d'autorisation."""
    return _to_read_model(access.organization, principal_role=access.role)


@router.patch(
    "/organizations/{organization_id}",
    summary="Modifier une organisation",
    description="Mise à jour partielle réservée à l'`owner`.",
    responses=INVISIBLE_ORGANIZATION
    | {int(HTTPStatus.CONFLICT): {"description": "Slug déjà utilisé."}},
    dependencies=[ScopeWrite],
)
async def update_organization(
    payload: OrganizationUpdate, access: OrgAdmin, db: DbSession
) -> OrganizationRead:
    """Applique une mise à jour partielle sur l'organisation."""
    organization = await OrganizationService(db).update(access.organization, payload)
    return _to_read_model(organization, principal_role=access.role)


@router.delete(
    "/organizations/{organization_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Supprimer une organisation",
    description=(
        "Refusée tant que l'organisation possède encore des bases de "
        "connaissances (`409`) : pas de cascade physique multi-KB depuis la "
        "suppression d'un tenant (ADR-022)."
    ),
    responses=INVISIBLE_ORGANIZATION
    | {
        int(HTTPStatus.CONFLICT): {
            "description": "L'organisation possède encore des bases de connaissances."
        }
    },
    dependencies=[ScopeWrite],
)
async def delete_organization(access: OrgAdmin, db: DbSession) -> None:
    """Supprime l'organisation."""
    await OrganizationService(db).delete(access.organization)


@router.get(
    "/organizations/{organization_id}/members",
    summary="Lister les membres d'une organisation",
    description=(
        "Ouvert à tout membre (`OrganizationRole.MEMBER`) : l'annuaire d'une "
        "équipe est normalement visible par ses membres, à la différence de la "
        "gestion des membres d'une base de connaissances."
    ),
    responses=INVISIBLE_ORGANIZATION,
    dependencies=[ScopeRead],
)
async def list_members(access: OrgRead, db: DbSession) -> list[OrganizationMemberRead]:
    """Renvoie l'annuaire complet des membres de l'organisation."""
    members = await OrganizationService(db).list_members(access.organization)
    return [OrganizationMemberRead.model_validate(member) for member in members]


@router.get(
    "/organizations/{organization_id}/members/{user_id}",
    summary="Lire l'appartenance d'un utilisateur",
    description="Ouvert à tout membre, même rationale que le listing des membres.",
    responses=INVISIBLE_ORGANIZATION
    | {
        int(HTTPStatus.NOT_FOUND): {
            "description": "Organisation invisible, ou appartenance inexistante."
        }
    },
    dependencies=[ScopeRead],
)
async def read_member(
    user_id: MemberPath, access: OrgRead, db: DbSession
) -> OrganizationMemberRead:
    """Renvoie le rôle d'un utilisateur sur l'organisation."""
    member = await OrganizationService(db).get_member(access.organization, user_id)
    return OrganizationMemberRead.model_validate(member)


@router.put(
    "/organizations/{organization_id}/members/{user_id}",
    summary="Ajouter un membre ou changer son rôle",
    description=(
        "Réservé à l'`owner`. Idempotent : le même appel répété laisse le même "
        "rôle. Répond `201` lorsque l'appartenance vient d'être créée, `200` "
        "lorsqu'elle est modifiée. Rétrograder le dernier `owner` est refusé (`409`)."
    ),
    responses=INVISIBLE_ORGANIZATION
    | {
        int(HTTPStatus.CREATED): {"description": "Appartenance créée."},
        int(HTTPStatus.NOT_FOUND): {
            "description": "Organisation invisible, ou compte visé inexistant."
        },
        int(HTTPStatus.CONFLICT): {"description": "Rétrogradation du dernier `owner`."},
    },
    dependencies=[ScopeWrite],
)
async def set_member(
    user_id: MemberPath,
    payload: OrganizationMemberUpdate,
    access: OrgAdmin,
    db: DbSession,
    response: Response,
) -> OrganizationMemberRead:
    """Crée ou met à jour l'appartenance d'un utilisateur à l'organisation."""
    assignment = await OrganizationService(db).set_member(access.organization, user_id, payload)
    if assignment.created:
        response.status_code = HTTPStatus.CREATED
    return OrganizationMemberRead.model_validate(assignment.member)


@router.delete(
    "/organizations/{organization_id}/members/{user_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Retirer un membre",
    description=(
        "Réservé à l'`owner`. Retirer le dernier `owner` d'une organisation est "
        "refusé (`409`) : elle deviendrait ingérable."
    ),
    responses=INVISIBLE_ORGANIZATION
    | {int(HTTPStatus.CONFLICT): {"description": "Retrait du dernier `owner`."}},
    dependencies=[ScopeWrite],
)
async def remove_member(user_id: MemberPath, access: OrgAdmin, db: DbSession) -> None:
    """Retire l'appartenance d'un utilisateur à l'organisation."""
    await OrganizationService(db).remove_member(access.organization, user_id)
