"""Injection FastAPI de l'authentification et transport du cookie de session.

Ce module est la **frontière** entre le protocole HTTP et les services : il lit
les en-têtes et le cookie, délègue la décision à `app.services.identity`, et pose
les alias `Annotated` que les routeurs consomment. Les règles d'accès de la §4 de
`docs/domain-model.md` ne s'appliquent qu'ici et dans `PermissionService` —
jamais dispersées dans un routeur (§6).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Final

from fastapi import Depends, Path, Request, Response

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError
from app.core.plugins import admits_anonymous
from app.db.models import User
from app.db.session import DbSession
from app.domain.enums import MemberRole, OrganizationRole, UserRole
from app.domain.scopes import scope_satisfied
from app.providers.storage import StoragePort, resolve_storage
from app.services.identity.errors import AuthenticationRequiredError, InsufficientScopeError
from app.services.identity.permissions import (
    KnowledgeBaseAccess,
    OrganizationAccess,
    OrganizationPermissionService,
    PermissionService,
)
from app.services.identity.principal import Principal, PrincipalResolver

SettingsDep = Annotated[Settings, Depends(get_settings)]
"""Configuration injectée, pour ne jamais lire l'environnement dans un routeur."""


def get_storage(settings: SettingsDep) -> StoragePort:
    """Résout le port de stockage des documents (ADR-014).

    Passer par une dépendance plutôt que par un import direct dans le routeur
    donne au déploiement — et aux tests — un point de substitution unique, sans
    qu'aucune couche au-dessus ne connaisse le système de fichiers.
    """
    return resolve_storage(settings)


StorageDep = Annotated[StoragePort, Depends(get_storage)]
"""Port de stockage injecté, seul chemin par lequel un octet de document est écrit."""

_PLATFORM_ROLE_RANK: Final[dict[UserRole, int]] = {UserRole.MEMBER: 0, UserRole.ADMIN: 1}
"""Ordre total des rôles plateforme : un `admin` satisfait toute exigence."""

_SESSION_COOKIE_PATH: Final = "/"
_INSUFFICIENT_ROLE: Final = "Votre rôle ne permet pas cette action."
_LOGIN_REQUIRED: Final = "Connectez-vous pour accéder à cette ressource."


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """Pose le cookie de session (`docs/domain-model.md` §1).

    `HttpOnly` : le jeton reste hors de portée de tout script, ce qui neutralise
    l'exfiltration par XSS. `SameSite=Lax` : le cookie n'accompagne pas les
    requêtes inter-sites autres qu'une navigation de premier niveau, ce qui
    couvre le CSRF sur toutes les méthodes mutatives. `Secure` suit
    `SESSION_COOKIE_SECURE`, faux en développement où l'API est servie en clair.
    Le nom émis est `session_cookie_effective_name` (audit sécurité 2026-09,
    constat 4, ADR-064) — préfixé `__Host-` en staging/production, jamais
    `session_cookie_name` brut.
    """
    response.set_cookie(
        key=settings.session_cookie_effective_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        path=_SESSION_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Efface le cookie de session — mêmes attributs, sinon le navigateur le garde."""
    response.delete_cookie(
        key=settings.session_cookie_effective_name,
        path=_SESSION_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def read_session_token(request: Request, settings: Settings) -> str | None:
    """Lit le jeton de session porté par le cookie, s'il est présent.

    Sous le nom **effectivement émis** (`session_cookie_effective_name`) : lire
    `session_cookie_name` brut ici rendrait tout cookie préfixé `__Host-`
    invisible au serveur qui l'a lui-même posé.
    """
    return request.cookies.get(settings.session_cookie_effective_name)


async def get_principal(request: Request, db: DbSession, settings: SettingsDep) -> Principal:
    """Résout le principal courant ; un visiteur sans identifiant n'est admis que par un greffon.

    Une clé API présentée et invalide reste une erreur (`401`) : voir l'ordre de
    résolution documenté dans `app.services.identity.principal`.

    **Le cœur exige une connexion pour tout** (ADR-069) : sans identifiant
    valable, `401` (`/problems/authentication-required`), avant même la matrice
    d'accès. Un greffon qui sert des visiteurs sans compte
    (`Plugin.admits_anonymous`) laisse au contraire passer le principal anonyme,
    que `PermissionService` traite alors comme tel (lecture des bases `public`).

    Raises:
        AuthenticationRequiredError: aucun identifiant valable, et aucun greffon
            n'admet de visiteur anonyme.
    """
    principal = await PrincipalResolver(db).resolve(
        authorization=request.headers.get("authorization"),
        session_token=read_session_token(request, settings),
    )
    if not principal.is_authenticated and not admits_anonymous():
        raise AuthenticationRequiredError(_LOGIN_REQUIRED)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
"""Principal courant — anonyme seulement si un greffon admet les visiteurs sans compte."""


async def require_authenticated(principal: CurrentPrincipal) -> Principal:
    """Exige un principal authentifié — compte réel **ou** invité.

    Raises:
        AuthenticationRequiredError: principal anonyme (`401`).
    """
    principal.require_user()
    return principal


AuthenticatedPrincipal = Annotated[Principal, Depends(require_authenticated)]
"""Principal garanti authentifié (invité compris)."""


async def require_account(principal: AuthenticatedPrincipal) -> Principal:
    """Exige un **compte réel** : un espace invité est refusé.

    Raises:
        AuthenticationRequiredError: principal anonyme (`401`).
        AccountRequiredError: espace invité (`403`, réparable par l'élévation).
    """
    principal.require_account()
    return principal


RegisteredPrincipal = Annotated[Principal, Depends(require_account)]
"""Principal garanti rattaché à un compte réel (jamais un invité)."""


async def current_user(principal: AuthenticatedPrincipal) -> User:
    """Compte courant, pour les routeurs qui n'ont pas besoin du principal complet."""
    return principal.require_user()


CurrentUser = Annotated[User, Depends(current_user)]
"""Compte courant (réel ou invité)."""


def require_role(role: UserRole) -> Callable[..., Awaitable[Principal]]:
    """Fabrique une dépendance exigeant au moins le rôle plateforme `role`.

    Usage : `Depends(require_role(UserRole.ADMIN))`. Les rôles sont ordonnés, un
    `admin` satisfait donc une exigence de `member`.
    """

    async def dependency(principal: AuthenticatedPrincipal) -> Principal:
        current = principal.require_user().role
        if _PLATFORM_ROLE_RANK[current] < _PLATFORM_ROLE_RANK[role]:
            raise ForbiddenError(_INSUFFICIENT_ROLE, extensions={"required_role": role.value})
        return principal

    return dependency


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    """Fabrique une dépendance exigeant `scope` sur la clé API du principal courant.

    Usage : `dependencies=[Depends(require_scope(ApiKeyScope.DOCUMENTS_WRITE))]`
    sur un routeur ou une route (`app.domain.scopes.ApiKeyScope`).

    **Sans effet hors authentification par clé API.** `principal.api_key` n'est
    posé que par `PrincipalResolver.resolve_api_key` : un principal anonyme ou
    authentifié par cookie de session (navigateur) traverse cette dépendance
    sans y être soumis — les scopes sont un concept propre aux clés API
    (`docs/adr/018-scopes-de-cle-api-et-securite.md`), pas au principal en
    général. L'authentification elle-même (401 si anonyme) reste portée par
    les dépendances existantes (`AuthenticatedPrincipal`,
    `require_kb_permission`…) : composer les deux, pas remplacer l'une par
    l'autre.

    Raises:
        InsufficientScopeError: clé API présente, mais dont les portées ne
            couvrent pas `scope` et n'incluent pas le joker `"*"` (`403`).
    """

    async def dependency(principal: CurrentPrincipal) -> Principal:
        api_key = principal.api_key
        if api_key is not None and not scope_satisfied(api_key.scopes, scope):
            raise InsufficientScopeError(scope)
        return principal

    return dependency


def require_kb_permission(
    minimum: MemberRole,
) -> Callable[..., Awaitable[KnowledgeBaseAccess]]:
    """Fabrique une dépendance appliquant la matrice §4 sur `/knowledge-bases/{kb_id}`.

    Le paramètre de chemin s'appelle `kb_id`, conformément à la surface REST de
    `docs/domain-model.md` §5 : toute route qui monte cette dépendance doit le
    nommer ainsi.

    L'accès résolu porte la base **déjà chargée** : le routeur la réutilise au
    lieu de la relire. Exemples :

    ```python
    KbRead = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))]
    KbWrite = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.EDITOR))]
    KbAdmin = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.OWNER))]
    ```
    """

    async def dependency(
        principal: CurrentPrincipal,
        db: DbSession,
        kb_id: Annotated[uuid.UUID, Path(description="Identifiant de la base de connaissances.")],
    ) -> KnowledgeBaseAccess:
        return await PermissionService(db).resolve_access(principal, kb_id, minimum=minimum)

    return dependency


def require_org_permission(
    minimum: OrganizationRole,
) -> Callable[..., Awaitable[OrganizationAccess]]:
    """Fabrique une dépendance appliquant la matrice ADR-022 sur `/organizations/{organization_id}`.

    Le paramètre de chemin s'appelle `organization_id` (pas d'abréviation façon
    `kb_id` — aucune autre route du dépôt n'abrège ainsi en dehors des bases de
    connaissances). L'accès résolu porte l'organisation **déjà chargée** : le
    routeur la réutilise au lieu de la relire. Exemples :

    ```python
    OrgRead = Annotated[
        OrganizationAccess, Depends(require_org_permission(OrganizationRole.MEMBER))
    ]
    OrgAdmin = Annotated[
        OrganizationAccess, Depends(require_org_permission(OrganizationRole.OWNER))
    ]
    ```
    """

    async def dependency(
        principal: CurrentPrincipal,
        db: DbSession,
        organization_id: Annotated[uuid.UUID, Path(description="Identifiant de l'organisation.")],
    ) -> OrganizationAccess:
        return await OrganizationPermissionService(db).resolve_access(
            principal, organization_id, minimum=minimum
        )

    return dependency
