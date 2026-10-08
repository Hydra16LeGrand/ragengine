"""Socle déclaratif SQLAlchemy : convention de nommage et mixins transverses.

La `naming_convention` portée par `MetaData` est **structurante** : sans elle,
PostgreSQL nomme lui-même les contraintes implicites et Alembic ne sait plus les
retrouver, ce qui rend les migrations autogénérées instables et les `downgrade`
non rejouables. Toute contrainte du schéma tire donc son nom de ces gabarits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import DateTime, MetaData, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Gabarits de nommage des contraintes et index (Alembic en dépend)."""

metadata = MetaData(naming_convention=NAMING_CONVENTION)
"""Catalogue unique du schéma applicatif — cible d'Alembic (`target_metadata`)."""

NOW = text("now()")
"""Défaut serveur des horodatages : l'heure est celle de PostgreSQL, pas du client."""

GEN_RANDOM_UUID = text("gen_random_uuid()")
"""Générateur d'UUID natif de PostgreSQL 13+ — aucune extension requise."""


class Base(DeclarativeBase):
    """Classe de base déclarative de toutes les tables applicatives.

    `type_annotation_map` fixe la projection des annotations Python vers les
    types PostgreSQL, pour que les modèles restent lisibles : `str` devient
    `TEXT` (convention du modèle de domaine), `datetime` devient `TIMESTAMPTZ`,
    `dict` devient `JSONB` et `list[str]` devient `TEXT[]`.
    """

    metadata = metadata

    type_annotation_map = {  # noqa: RUF012 - contrat SQLAlchemy, jamais muté
        str: Text(),
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid(as_uuid=True),
        # `Any` est ici la valeur JSON arbitraire d'un document JSONB : la
        # structure est validée par les schémas Pydantic, pas par la colonne.
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text()),
    }


class UUIDPrimaryKeyMixin:
    """Clé primaire `UUID` générée par le serveur (jamais d'entier exposé)."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=GEN_RANDOM_UUID,
        # Les colonnes de mixin sont ajoutées après celles de la classe : on
        # force `id` en tête pour que le DDL reste lisible.
        sort_order=-100,
        doc="Identifiant public de la ressource.",
    )


class TimestampMixin:
    """Horodatages UTC de création et de dernière modification."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=NOW,
        nullable=False,
        sort_order=100,
        doc="Instant de création, fixé par PostgreSQL.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=NOW,
        onupdate=NOW,
        nullable=False,
        sort_order=101,
        doc="Instant de dernière modification, réécrit à chaque UPDATE de l'ORM.",
    )
