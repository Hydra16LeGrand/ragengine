"""Clés LLM personnelles d'un compte : liste, déclaration, révocation.

Réservé aux comptes réels (`require_account`) — même garde qu'`api_keys.py`.
La clé en clair n'apparaît jamais en lecture : `UserLlmKeyRead` (docstring)
n'expose que le fournisseur et les horodatages.

Contrairement à `GET/PATCH /me` (profil), ces routes portent leurs propres
scopes (`ApiKeyScope.LLM_KEYS_READ`/`LLM_KEYS_WRITE`, audit sécurité 2026-09
constat 13, ADR-063) : une clé LLM tierce déclarée ici est un secret de
facturation externe, pas un réglage de profil — elle ne relève pas du même
principe que le carve-out `/me*` d'ADR-018 (« un porteur de clé API gère
toujours son propre compte »).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.v1.dependencies import RegisteredPrincipal, SettingsDep, require_account, require_scope
from app.db.session import DbSession
from app.domain.schemas.user_llm_key import UserLlmKeyCreate, UserLlmKeyRead
from app.domain.scopes import ApiKeyScope
from app.providers.llm.base import LLMVendor
from app.services.identity.user_llm_keys import UserLlmKeyService

router = APIRouter(
    tags=["llm-keys"],
    dependencies=[Depends(require_account)],
    responses={
        int(HTTPStatus.UNAUTHORIZED): {"description": "Authentification requise."},
        int(HTTPStatus.FORBIDDEN): {"description": "Réservé aux comptes réels."},
    },
)

ScopeRead = Depends(require_scope(ApiKeyScope.LLM_KEYS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.LLM_KEYS_WRITE))
"""Portée de clé API exigée en écriture (déclaration, révocation)."""


@router.get(
    "/me/llm-keys",
    summary="Lister ses clés LLM personnelles",
    description="Fournisseur et horodatages uniquement — la clé elle-même n'est jamais renvoyée.",
    dependencies=[ScopeRead],
)
async def list_llm_keys(principal: RegisteredPrincipal, db: DbSession) -> list[UserLlmKeyRead]:
    rows = await UserLlmKeyService(db).list_keys(principal.require_user())
    return [UserLlmKeyRead.model_validate(row) for row in rows]


@router.put(
    "/me/llm-keys/{provider}",
    summary="Déclarer ou remplacer sa clé personnelle d'un fournisseur",
    description=(
        "Utilisée en priorité sur la clé serveur pour ce fournisseur, sur "
        "`POST /retrieval/chat`. Remplace silencieusement une clé déjà "
        "déclarée pour ce même fournisseur."
    ),
    status_code=status.HTTP_200_OK,
    dependencies=[ScopeWrite],
)
async def set_llm_key(
    provider: Annotated[LLMVendor, Path()],
    payload: UserLlmKeyCreate,
    principal: RegisteredPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> UserLlmKeyRead:
    row = await UserLlmKeyService(db, settings).set_key(
        principal.require_user(), provider, payload.api_key
    )
    await db.commit()
    return UserLlmKeyRead.model_validate(row)


@router.delete(
    "/me/llm-keys/{provider}",
    summary="Retirer sa clé personnelle d'un fournisseur",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[ScopeWrite],
)
async def remove_llm_key(
    provider: Annotated[LLMVendor, Path()], principal: RegisteredPrincipal, db: DbSession
) -> None:
    await UserLlmKeyService(db).remove_key(principal.require_user(), provider)
    await db.commit()
