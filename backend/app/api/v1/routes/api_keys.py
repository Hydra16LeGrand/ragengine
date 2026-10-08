"""Clés API d'un compte : liste, création, révocation.

Toute la ressource est réservée aux comptes réels (matrice §4) : la dépendance
`require_account` le garantit au niveau du routeur, et `ApiKeyService` le
revérifie — un service ne se repose pas sur son appelant pour une règle métier.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import (
    RegisteredPrincipal,
    SettingsDep,
    require_account,
    require_scope,
)
from app.db.session import DbSession
from app.domain.pagination import Page, PaginationParams, build_page
from app.domain.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.domain.scopes import ApiKeyScope
from app.services.identity.api_keys import ApiKeyService

router = APIRouter(
    tags=["api-keys"],
    # Appliquée à toutes les routes : aucune n'est accessible à un compte éphémère ni à
    # un anonyme, y compris celles ajoutées plus tard.
    dependencies=[Depends(require_account)],
    responses={
        int(HTTPStatus.UNAUTHORIZED): {"description": "Authentification requise."},
        int(HTTPStatus.FORBIDDEN): {"description": "Réservé aux comptes réels."},
    },
)

ScopeRead = Depends(require_scope(ApiKeyScope.API_KEYS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé.

Une clé qui gère d'autres clés du même compte reste un cas volontairement
couvert : `ApiKeyService` reste la seule autorité sur « du même compte »."""

ScopeWrite = Depends(require_scope(ApiKeyScope.API_KEYS_WRITE))
"""Portée de clé API exigée en écriture (création, révocation)."""


@router.get(
    "/api-keys",
    summary="Lister ses clés API",
    description=(
        "Liste paginée par curseur, de la plus récente à la plus ancienne. Les "
        "clés révoquées y figurent : elles restent auditables. Le jeton n'y "
        "apparaît jamais, seul son préfixe permet de reconnaître une clé."
    ),
    dependencies=[ScopeRead],
)
async def list_api_keys(
    principal: RegisteredPrincipal,
    db: DbSession,
    settings: SettingsDep,
    pagination: Annotated[PaginationParams, Query()],
) -> Page[ApiKeyRead]:
    """Liste les clés du compte courant."""
    rows = await ApiKeyService(db, settings).list_for(
        principal.require_account(),
        cursor=pagination.decoded_cursor(),
        limit=pagination.limit,
    )
    return build_page(rows, serializer=ApiKeyRead.model_validate, limit=pagination.limit)


@router.post(
    "/api-keys",
    status_code=HTTPStatus.CREATED,
    summary="Créer une clé API",
    description=(
        "Émet une clé au format `rag_<env>_<43 caractères>`. **Le jeton n'est "
        "renvoyé qu'ici** : la base n'en conserve que le SHA-256, il est donc "
        "impossible de le réafficher ensuite."
    ),
    dependencies=[ScopeWrite],
)
async def create_api_key(
    payload: ApiKeyCreate,
    principal: RegisteredPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> ApiKeyCreated:
    """Crée une clé API et renvoie son jeton en clair, une seule fois."""
    issued = await ApiKeyService(db, settings).create(principal.require_account(), payload)
    return ApiKeyCreated(
        **ApiKeyRead.model_validate(issued.api_key).model_dump(), token=issued.token
    )


@router.delete(
    "/api-keys/{api_key_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Révoquer une clé API",
    description=(
        "Révocation non destructive : la ligne subsiste avec son horodatage. "
        "Idempotent. Une clé appartenant à un autre compte répond `404`."
    ),
    responses={int(HTTPStatus.NOT_FOUND): {"description": "Clé inconnue."}},
    dependencies=[ScopeWrite],
)
async def revoke_api_key(
    api_key_id: Annotated[uuid.UUID, Path(description="Identifiant de la clé à révoquer.")],
    principal: RegisteredPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> None:
    """Révoque une clé du compte courant."""
    await ApiKeyService(db, settings).revoke(principal.require_account(), api_key_id)
