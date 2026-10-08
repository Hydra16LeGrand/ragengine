"""Modèles SQLAlchemy du schéma applicatif, un module par agrégat.

Importer ce package suffit à enregistrer **toutes** les tables dans
`Base.metadata` : c'est la garantie qu'Alembic voit le schéma complet et que les
relations déclarées par nom de classe se résolvent.
"""

from app.db.base import Base
from app.db.models.document import Document
from app.db.models.evaluation import EvaluationItem, EvaluationRun
from app.db.models.ingestion_job import IngestionJob, IngestionJobEvent
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.organization import Organization, OrganizationMember
from app.db.models.user import ApiKey, Session, User
from app.db.models.user_llm_key import UserLlmKey

__all__ = [
    "ApiKey",
    "Base",
    "Document",
    "EvaluationItem",
    "EvaluationRun",
    "IngestionJob",
    "IngestionJobEvent",
    "KnowledgeBase",
    "KnowledgeBaseMember",
    "Organization",
    "OrganizationMember",
    "Session",
    "User",
    "UserLlmKey",
]
