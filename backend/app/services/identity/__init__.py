"""Cas d'usage de l'**identité** : comptes, sessions, clés API, permissions.

Le paquet s'appelle `identity` et non `auth` parce qu'il couvre tout l'agrégat
décrit en §1 de `docs/domain-model.md` — cycle de vie des comptes compris — et
pas seulement l'acte d'authentifier. Les modèles et les schémas correspondants
portent déjà ce nom (« agrégat identité »).

Répartition des responsabilités :

- `app.core.security` : primitives cryptographiques pures (argon2id, jetons) ;
- ce paquet : orchestration et règles métier, seul à connaître la base ;
- `app.api.v1.dependencies` : injection FastAPI et transport du cookie.
"""

from app.services.identity.api_keys import ApiKeyService, IssuedApiKey
from app.services.identity.errors import (
    AccountRequiredError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
)
from app.services.identity.permissions import (
    KnowledgeBaseAccess,
    OrganizationAccess,
    OrganizationPermissionService,
    PermissionService,
    organization_role_satisfies,
    satisfies,
)
from app.services.identity.principal import (
    ANONYMOUS,
    Principal,
    PrincipalResolver,
    parse_bearer_token,
)
from app.services.identity.sessions import IssuedSession, SessionService
from app.services.identity.users import UserService

__all__ = [
    "ANONYMOUS",
    "AccountRequiredError",
    "ApiKeyService",
    "AuthenticationRequiredError",
    "InvalidCredentialsError",
    "IssuedApiKey",
    "IssuedSession",
    "KnowledgeBaseAccess",
    "OrganizationAccess",
    "OrganizationPermissionService",
    "PermissionService",
    "Principal",
    "PrincipalResolver",
    "SessionService",
    "UserService",
    "organization_role_satisfies",
    "parse_bearer_token",
    "satisfies",
]
