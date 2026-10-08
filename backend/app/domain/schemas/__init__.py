"""Schémas Pydantic v2 d'entrée/sortie, distincts des modèles ORM.

Séparer les deux n'est pas une redite : le modèle ORM décrit ce que la base
stocke (dont des secrets et des colonnes dénormalisées), le schéma décrit ce que
le contrat d'API accepte et renvoie. Aucun `...Read` ne porte de secret.
"""

from app.domain.schemas.api_key import (
    DEFAULT_SCOPES,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ApiKeyUpdate,
)
from app.domain.schemas.common import (
    Description,
    DisplayName,
    InputModel,
    NormalizedEmail,
    Password,
    ReadModel,
    ResourceName,
    Sha256Hex,
    Slug,
)
from app.domain.schemas.document import (
    DocumentPrecheckDecisionUpdate,
    DocumentRead,
    DocumentUpdate,
    PrecheckDecision,
)
from app.domain.schemas.ingestion_job import (
    IngestionJobCreate,
    IngestionJobEventRead,
    IngestionJobRead,
    JobError,
)
from app.domain.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseMemberCreate,
    KnowledgeBaseMemberRead,
    KnowledgeBaseMemberUpdate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    ProviderConfig,
)
from app.domain.schemas.principal import AuthMethod, PrincipalRead
from app.domain.schemas.retrieval import (
    RetrievalResult,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.domain.schemas.session import SessionCreate, SessionRead
from app.domain.schemas.user import (
    UserCreate,
    UserRead,
    UserUpdate,
)

__all__ = [
    "DEFAULT_SCOPES",
    "ApiKeyCreate",
    "ApiKeyCreated",
    "ApiKeyRead",
    "ApiKeyUpdate",
    "AuthMethod",
    "Description",
    "DisplayName",
    "DocumentPrecheckDecisionUpdate",
    "DocumentRead",
    "DocumentUpdate",
    "IngestionJobCreate",
    "IngestionJobEventRead",
    "IngestionJobRead",
    "InputModel",
    "JobError",
    "KnowledgeBaseCreate",
    "KnowledgeBaseMemberCreate",
    "KnowledgeBaseMemberRead",
    "KnowledgeBaseMemberUpdate",
    "KnowledgeBaseRead",
    "KnowledgeBaseUpdate",
    "NormalizedEmail",
    "Password",
    "PrecheckDecision",
    "PrincipalRead",
    "ProviderConfig",
    "ReadModel",
    "ResourceName",
    "RetrievalResult",
    "RetrievalSearchRequest",
    "RetrievalSearchResponse",
    "SessionCreate",
    "SessionRead",
    "Sha256Hex",
    "Slug",
    "UserCreate",
    "UserRead",
    "UserUpdate",
]
