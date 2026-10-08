"""Bases de connaissances : cycle de vie et gestion des membres.

Conformément à CONTRIBUTING.md §3.2, ces handlers ne portent **aucune** règle métier :
ils valident (Pydantic), délèguent à `KnowledgeBaseService`, sérialisent. Le
contrôle d'accès lui-même n'est pas écrit ici mais **déclaré** — les alias
`KbRead` / `KbWrite` / `KbAdmin` montent `require_kb_permission`, seul point
d'application de la matrice §4 (`docs/domain-model.md`).

Répartition des rôles exigés, ligne à ligne de cette matrice : lecture d'une base
au `viewer`, modification et suppression de la base à l'`owner`, gestion des
membres à l'`owner`.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.v1.dependencies import (
    AuthenticatedPrincipal,
    CurrentPrincipal,
    SettingsDep,
    require_kb_permission,
    require_scope,
)
from app.core.plugins import knowledge_base_extra_fields
from app.db.models import KnowledgeBase, KnowledgeBaseMember
from app.db.session import DbSession
from app.domain.enums import MemberRole
from app.domain.pagination import Page, PaginationParams, build_page
from app.domain.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseMemberRead,
    KnowledgeBaseMemberUpdate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from app.domain.scopes import ApiKeyScope
from app.services.identity.permissions import KnowledgeBaseAccess
from app.services.knowledge_bases import KnowledgeBaseService

router = APIRouter(tags=["knowledge-bases"])

ScopeRead = Depends(require_scope(ApiKeyScope.KNOWLEDGE_BASES_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.KNOWLEDGE_BASES_WRITE))
"""Portée de clé API exigée en écriture (création, modification, membres)."""

INVISIBLE_BASE: Final[dict[int | str, dict[str, Any]]] = {
    int(HTTPStatus.NOT_FOUND): {"description": "Base inexistante, ou invisible pour le principal."}
}
"""Déclaré route par route, jamais sur le routeur : les deux chemins de
collection (`POST` et `GET /knowledge-bases`) ne peuvent pas répondre `404`, et
l'annoncer rendrait le schéma OpenAPI faux."""

KbRead = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))]
"""Accès en lecture : membre `viewer`, ou visiteur d'une base `public` (ADR-005)."""

KbAdmin = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.OWNER))]
"""Accès d'administration : `owner` de la base, ou administrateur plateforme."""

MemberPath = Annotated[uuid.UUID, Path(description="Identifiant du membre visé.")]


def to_read_model(
    knowledge_base: KnowledgeBase, *, principal_role: MemberRole | None
) -> KnowledgeBaseRead:
    """Sérialise une base **avec** le rôle du principal, absent de l'ORM (§4 défaut 2).

    `model_validate` seul ne peut pas peupler `principal_role` : rien ne le
    porte sur `KnowledgeBase`. Il est donc résolu à côté (`access.role`, ou une
    requête complémentaire pour la liste) puis fusionné ici, au point unique de
    sérialisation — avec les champs de greffon (`Plugin.knowledge_base_fields`,
    ADR-069), lus sur la même ligne.
    """
    return KnowledgeBaseRead.model_validate(knowledge_base).model_copy(
        update={"principal_role": principal_role, **knowledge_base_extra_fields(knowledge_base)}
    )


def _to_member_read(member: KnowledgeBaseMember, *, display_name: str) -> KnowledgeBaseMemberRead:
    """Sérialise une appartenance **avec** le nom du compte, absent de sa ligne.

    Construction champ par champ plutôt que `model_validate` + `model_copy` :
    `display_name` est requis, la validation depuis l'ORM échouerait donc avant
    même d'arriver à la copie — et le laisser optionnel pour contourner cela
    rendrait un annuaire sans nom représentable, ce qu'il n'est pas.
    """
    return KnowledgeBaseMemberRead(
        knowledge_base_id=member.knowledge_base_id,
        user_id=member.user_id,
        display_name=display_name,
        role=member.role,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.post(
    "/knowledge-bases",
    status_code=HTTPStatus.CREATED,
    summary="Créer une base de connaissances",
    description=(
        "Le créateur en devient `owner`. Le `slug` est dérivé du nom lorsqu'il "
        "n'est pas fourni, et reste unique **par propriétaire** : deux comptes "
        "peuvent nommer leur base « support-technique »."
    ),
    responses={
        int(HTTPStatus.FORBIDDEN): {"description": "Plafond de création du compte atteint."},
        int(HTTPStatus.CONFLICT): {"description": "Slug déjà utilisé par ce propriétaire."},
    },
    dependencies=[ScopeWrite],
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: AuthenticatedPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> KnowledgeBaseRead:
    """Crée une base de connaissances appartenant au principal courant."""
    knowledge_base = await KnowledgeBaseService(db, settings).create(
        principal.require_user(), payload
    )
    # Le créateur en est toujours `owner` : `create` l'inscrit explicitement.
    return to_read_model(knowledge_base, principal_role=MemberRole.OWNER)


@router.get(
    "/knowledge-bases",
    summary="Lister les bases de connaissances visibles",
    description=(
        "Liste paginée par curseur, de la plus récente à la plus ancienne. Le "
        "périmètre est exactement celui de la lecture unitaire : bases possédées, "
        "bases dont on est membre, et bases `public` ; un administrateur plateforme "
        "les voit toutes."
    ),
    dependencies=[ScopeRead],
)
async def list_knowledge_bases(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    pagination: Annotated[PaginationParams, Query()],
) -> Page[KnowledgeBaseRead]:
    """Liste les bases visibles par le principal courant."""
    service = KnowledgeBaseService(db, settings)
    rows = await service.list_visible(
        principal, cursor=pagination.decoded_cursor(), limit=pagination.limit
    )
    # Une seule requête complémentaire pour toute la page — jamais un
    # `resolve_access` par ligne (`KnowledgeBaseService.resolve_principal_roles`).
    roles = await service.resolve_principal_roles(principal, rows)
    return build_page(
        rows,
        serializer=lambda knowledge_base: to_read_model(
            knowledge_base, principal_role=roles.get(knowledge_base.id)
        ),
        limit=pagination.limit,
    )


@router.get(
    "/knowledge-bases/{kb_id}",
    summary="Lire une base de connaissances",
    description=(
        "Une base `private` invisible pour le principal répond `404`, jamais "
        "`403` : un refus explicite confirmerait son existence."
    ),
    responses=INVISIBLE_BASE,
    dependencies=[ScopeRead],
)
async def read_knowledge_base(access: KbRead) -> KnowledgeBaseRead:
    """Renvoie la base résolue par la dépendance d'autorisation."""
    return to_read_model(access.knowledge_base, principal_role=access.role)


@router.patch(
    "/knowledge-bases/{kb_id}",
    summary="Modifier une base de connaissances",
    description=(
        "Mise à jour partielle réservée à l'`owner`. Le `slug` n'est pas "
        "re-dérivé lorsque le nom change : il appartient à l'identité lisible de "
        "la base, et le muter au détour d'un renommage casserait les liens "
        "déjà partagés. Ni `owner_id` ni `document_count` ne sont modifiables."
    ),
    responses=INVISIBLE_BASE | {int(HTTPStatus.CONFLICT): {"description": "Slug déjà utilisé."}},
    dependencies=[ScopeWrite],
)
async def update_knowledge_base(
    payload: KnowledgeBaseUpdate, access: KbAdmin, db: DbSession, settings: SettingsDep
) -> KnowledgeBaseRead:
    """Applique une mise à jour partielle sur la base."""
    knowledge_base = await KnowledgeBaseService(db, settings).update(access.knowledge_base, payload)
    # `access.role` plutôt que `MemberRole.OWNER` en dur : `KbAdmin` ne l'accorde
    # qu'à un `owner` réel ou à un administrateur plateforme (`role or OWNER`),
    # mais la valeur réelle reste la source de vérité, pas la supposition.
    return to_read_model(knowledge_base, principal_role=access.role)


@router.delete(
    "/knowledge-bases/{kb_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Supprimer une base de connaissances",
    description=(
        "Suppression physique en cascade : appartenances, documents, jobs "
        "d'ingestion et journal des transitions disparaissent avec la base."
    ),
    responses=INVISIBLE_BASE,
    dependencies=[ScopeWrite],
)
async def delete_knowledge_base(access: KbAdmin, db: DbSession, settings: SettingsDep) -> None:
    """Supprime la base et tout ce qu'elle porte."""
    await KnowledgeBaseService(db, settings).delete(access.knowledge_base)


@router.get(
    "/knowledge-bases/{kb_id}/members",
    summary="Lister les membres d'une base de connaissances",
    description="Réservé à l'`owner` : la composition d'une base fait partie de sa gestion.",
    responses=INVISIBLE_BASE,
    dependencies=[ScopeRead],
)
async def list_members(
    access: KbAdmin, db: DbSession, settings: SettingsDep
) -> list[KnowledgeBaseMemberRead]:
    """Renvoie l'annuaire complet des membres de la base."""
    entries = await KnowledgeBaseService(db, settings).list_members(access.knowledge_base)
    return [_to_member_read(entry.member, display_name=entry.display_name) for entry in entries]


@router.get(
    "/knowledge-bases/{kb_id}/members/{user_id}",
    summary="Lire l'appartenance d'un utilisateur",
    description="Réservé à l'`owner` : la composition d'une base fait partie de sa gestion.",
    responses=INVISIBLE_BASE
    | {int(HTTPStatus.NOT_FOUND): {"description": "Base invisible, ou appartenance inexistante."}},
    dependencies=[ScopeRead],
)
async def read_member(
    user_id: MemberPath, access: KbAdmin, db: DbSession, settings: SettingsDep
) -> KnowledgeBaseMemberRead:
    """Renvoie le rôle d'un utilisateur sur la base."""
    entry = await KnowledgeBaseService(db, settings).get_member(access.knowledge_base, user_id)
    return _to_member_read(entry.member, display_name=entry.display_name)


@router.put(
    "/knowledge-bases/{kb_id}/members/{user_id}",
    summary="Ajouter un membre ou changer son rôle",
    description=(
        "Idempotent : le même appel répété laisse le même rôle. Répond `201` "
        "lorsque l'appartenance vient d'être créée, `200` lorsqu'elle est "
        "modifiée. Rétrograder le dernier `owner` d'une base est refusé (`409`)."
    ),
    responses=INVISIBLE_BASE
    | {
        int(HTTPStatus.CREATED): {"description": "Appartenance créée."},
        int(HTTPStatus.NOT_FOUND): {"description": "Base invisible, ou compte visé inexistant."},
        int(HTTPStatus.CONFLICT): {"description": "Rétrogradation du dernier `owner`."},
    },
    dependencies=[ScopeWrite],
)
async def set_member(
    user_id: MemberPath,
    payload: KnowledgeBaseMemberUpdate,
    access: KbAdmin,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> KnowledgeBaseMemberRead:
    """Crée ou met à jour l'appartenance d'un utilisateur à la base."""
    assignment = await KnowledgeBaseService(db, settings).set_member(
        access.knowledge_base, user_id, payload
    )
    if assignment.created:
        response.status_code = HTTPStatus.CREATED
    return _to_member_read(assignment.member, display_name=assignment.display_name)


@router.delete(
    "/knowledge-bases/{kb_id}/members/{user_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Retirer un membre",
    description=(
        "Retirer le dernier `owner` d'une base est refusé (`409`) : une base "
        "sans propriétaire deviendrait ingérable (`docs/domain-model.md` §1)."
    ),
    responses=INVISIBLE_BASE
    | {int(HTTPStatus.CONFLICT): {"description": "Retrait du dernier `owner` de la base."}},
    dependencies=[ScopeWrite],
)
async def remove_member(
    user_id: MemberPath, access: KbAdmin, db: DbSession, settings: SettingsDep
) -> None:
    """Retire l'appartenance d'un utilisateur à la base."""
    await KnowledgeBaseService(db, settings).remove_member(access.knowledge_base, user_id)
