"""Types `ENUM` PostgreSQL nommés, dérivés des énumérations du domaine.

Chaque type est instancié **une seule fois** : plusieurs tables peuvent
référencer le même objet (`job_state` sert aux jobs et à leur journal) sans
qu'un second `CREATE TYPE` soit émis, et le nom du type reste stable pour
Alembic.
"""

from __future__ import annotations

import enum
from typing import Final

from sqlalchemy import Enum

from app.domain.enums import (
    DocumentStatus,
    EvaluationRunStatus,
    JobState,
    MemberRole,
    OrganizationRole,
    UserRole,
    Visibility,
)


def _enum_values(enum_class: type[enum.Enum]) -> list[str]:
    """Stocke les **valeurs** de l'énumération, pas les noms des membres."""
    return [str(member.value) for member in enum_class]


def pg_enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """Construit un type `ENUM` PostgreSQL natif nommé à partir d'une énumération."""
    return Enum(
        python_enum,
        name=name,
        native_enum=True,
        create_constraint=False,
        validate_strings=True,
        values_callable=_enum_values,
    )


USER_ROLE_ENUM: Final = pg_enum(UserRole, "user_role")
MEMBER_ROLE_ENUM: Final = pg_enum(MemberRole, "member_role")
ORGANIZATION_ROLE_ENUM: Final = pg_enum(OrganizationRole, "organization_role")
VISIBILITY_ENUM: Final = pg_enum(Visibility, "visibility")
DOCUMENT_STATUS_ENUM: Final = pg_enum(DocumentStatus, "document_status")
JOB_STATE_ENUM: Final = pg_enum(JobState, "job_state")
EVALUATION_RUN_STATUS_ENUM: Final = pg_enum(EvaluationRunStatus, "evaluation_run_status")

ENUM_TYPE_NAMES: Final[tuple[str, ...]] = (
    "user_role",
    "member_role",
    "organization_role",
    "visibility",
    "document_status",
    "job_state",
    "evaluation_run_status",
)
"""Noms des types `ENUM` à créer/détruire explicitement dans les migrations."""
