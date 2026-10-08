"""Identité : inscription, sessions et principal courant.

Conformément à CONTRIBUTING.md §3.2, ces handlers ne portent **aucune** règle métier :
ils lisent la requête, appellent un service de `app.services.identity`, posent ou
effacent le cookie, et sérialisent. Toute décision — unicité d'une adresse,
validité d'un mot de passe, quotas — appartient aux services.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    AuthenticatedPrincipal,
    SettingsDep,
    clear_session_cookie,
    set_session_cookie,
)
from app.core.config import Settings
from app.core.plugins import principal_extra_fields
from app.db.session import DbSession
from app.domain.schemas.principal import AuthMethod, PrincipalRead
from app.domain.schemas.session import SessionCreate
from app.domain.schemas.user import UserCreate, UserRead
from app.services.identity.principal import Principal
from app.services.identity.sessions import IssuedSession, SessionService
from app.services.identity.users import UserService

router = APIRouter(tags=["identity"])


def client_ip(request: Request) -> str | None:
    """Adresse de l'appelant, pour la traçabilité des sessions."""
    return request.client.host if request.client is not None else None


async def describe_principal(
    principal: Principal, db: AsyncSession, settings: Settings
) -> PrincipalRead:
    """Projette le principal courant, champs de greffon compris (`Plugin.principal_fields`).

    Le compte est sérialisé **avant** la lecture des champs de greffon : une
    lecture en base déclencherait un `autoflush` qui, en réécrivant `updated_at`,
    expirerait la colonne. Pydantic ne pourrait alors plus la relire, l'accès à
    la base étant impossible depuis son contexte synchrone.
    """
    user = principal.user
    described = UserRead.model_validate(user) if user is not None else None
    extra = await principal_extra_fields(db, settings, user)
    return PrincipalRead(
        auth_method=principal.auth_method,
        user=described,
        role=principal.role,
        is_guest=principal.is_guest,
        **extra,
    )


@router.post(
    "/users",
    status_code=HTTPStatus.CREATED,
    summary="Inscrire un compte",
    description=(
        "Crée un compte réel. L'inscription n'ouvre pas de session : le client "
        "enchaîne sur `POST /api/v1/sessions`."
    ),
    responses={int(HTTPStatus.CONFLICT): {"description": "Adresse e-mail déjà utilisée."}},
)
async def register_user(payload: UserCreate, db: DbSession) -> UserRead:
    """Inscrit un compte réel."""
    user = await UserService(db).register(payload)
    return UserRead.model_validate(user)


@router.post(
    "/sessions",
    status_code=HTTPStatus.CREATED,
    summary="Ouvrir une session",
    description="Vérifie les identifiants et pose le cookie de session `HttpOnly`.",
    responses={int(HTTPStatus.UNAUTHORIZED): {"description": "Identifiants invalides."}},
)
async def create_session(
    payload: SessionCreate,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
) -> PrincipalRead:
    """Connecte un compte réel et renvoie le principal résultant."""
    issued = await SessionService(db, settings).login(
        payload,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    return await authenticated_response(issued, response, db, settings)


@router.delete(
    "/sessions/current",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Fermer la session courante",
    description=(
        "Révoque la session portée par le cookie et l'efface. Idempotent : sans "
        "session valable, la réponse reste `204` — se déconnecter deux fois n'est "
        "pas une erreur."
    ),
)
async def delete_current_session(
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    principal: AuthenticatedPrincipal,
) -> None:
    """Révoque la session courante et efface le cookie."""
    if principal.session is not None:
        await SessionService(db, settings).revoke(principal.session)
    clear_session_cookie(response, settings)


@router.get(
    "/me",
    summary="Principal courant",
    description="Compte, rôle et méthode d'authentification du principal authentifié.",
)
async def read_me(
    principal: AuthenticatedPrincipal, db: DbSession, settings: SettingsDep
) -> PrincipalRead:
    """Décrit le principal courant."""
    return await describe_principal(principal, db, settings)


async def authenticated_response(
    issued: IssuedSession, response: Response, db: AsyncSession, settings: Settings
) -> PrincipalRead:
    """Pose le cookie de la session émise et décrit le principal correspondant."""
    set_session_cookie(response, issued.token, settings)
    principal = Principal(auth_method=AuthMethod.SESSION, user=issued.user, session=issued.session)
    return await describe_principal(principal, db, settings)
