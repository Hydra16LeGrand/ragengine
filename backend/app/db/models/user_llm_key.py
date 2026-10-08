"""Clé API LLM tierce fournie par l'utilisateur (Anthropic/OpenAI/Groq).

Distincte d'`ApiKey` (`user.py`) : une `ApiKey` authentifie un appel *entrant*
vers RAGEngine et n'a besoin que d'être comparée (hachage SHA-256 à sens
unique). Une `UserLlmKey` est envoyée en *sortant* au fournisseur pour le
compte de l'utilisateur — elle doit rester récupérable en clair, donc
chiffrée de façon réversible (`app.core.security.encrypt_secret`), jamais
hachée.

Une ligne par (utilisateur, fournisseur) : renseigner une nouvelle clé pour un
fournisseur déjà couvert remplace l'ancienne plutôt que d'en empiler une
seconde (`UserLlmKeyService.set_key`).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover - imports de typage uniquement
    from app.db.models.user import User

_KNOWN_VENDORS = "('anthropic', 'openai', 'groq')"


class UserLlmKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Clé de génération personnelle d'un utilisateur, un fournisseur à la fois."""

    __tablename__ = "user_llm_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_llm_keys_user_provider"),
        CheckConstraint(f"provider IN {_KNOWN_VENDORS}", name="ck_user_llm_keys_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(
        doc="Un des trois fournisseurs LLM connus (LLMVendor) — pas un ENUM PostgreSQL "
        "dédié : LLMVendor est un Literal Python, pas une enum.Enum, contrairement aux "
        "autres colonnes de statut de ce schéma."
    )
    encrypted_key: Mapped[str] = mapped_column(
        Text, doc="Clé API chiffrée (Fernet, app.core.security.encrypt_secret) — jamais en clair."
    )

    user: Mapped[User] = relationship(back_populates="llm_keys", lazy="raise_on_sql")
