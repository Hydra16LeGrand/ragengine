"""Plafonds de création propres à un compte : point d'extension (ADR-069).

`KnowledgeBaseService.create` et l'ingestion de `DocumentService` consultent la
politique active **avant** toute écriture. Le cœur n'impose aucun plafond de ce
genre — un compte crée autant de bases et verse autant de documents que le
stockage le permet, sous les seules limites globales (`upload_max_bytes`). Un
greffon peut brancher sa propre politique (`Plugin.resource_quotas`) : elle
lève alors l'erreur de domaine de son choix, traduite en Problem Details comme
toute autre (`app.core.errors`).
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins import resource_quotas_factory
from app.db.models import User


class ResourceQuotaPolicy(Protocol):
    """Vérifications appelées avant une création ; ne renvoient rien, lèvent pour refuser."""

    async def ensure_can_create_knowledge_base(self, user: User) -> None:
        """Lève si `user` ne peut plus créer de base."""
        ...

    async def ensure_can_ingest_document(self, user: User, *, size_bytes: int) -> None:
        """Lève si `user` ne peut plus verser un document de `size_bytes` octets."""
        ...


class NoResourceQuotas:
    """Politique du cœur : aucun plafond de création propre au compte."""

    async def ensure_can_create_knowledge_base(self, user: User) -> None:
        del user

    async def ensure_can_ingest_document(self, user: User, *, size_bytes: int) -> None:
        del user, size_bytes


def resolve_resource_quotas(db: AsyncSession, settings: Settings) -> ResourceQuotaPolicy:
    """Politique active : celle d'un greffon s'il en fournit une, sinon `NoResourceQuotas`."""
    factory = resource_quotas_factory()
    return factory(db, settings) if factory is not None else NoResourceQuotas()
