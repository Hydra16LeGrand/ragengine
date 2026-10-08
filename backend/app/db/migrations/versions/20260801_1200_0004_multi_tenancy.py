"""Multi-tenancy (ADR-022, Axe 1) : `organizations`, `organization_members`.

Premier axe du backlog Premium (ADR-021) : `Organization` devient le tenant,
futur porteur des quotas et de la facturation (axes 2/3, hors périmètre ici).
`KnowledgeBase.owner_id` **ne change pas** — l'organisation est une dimension
orthogonale, pas un remplacement du propriétaire.

Trois temps, dans cet ordre :

1. Crée le type `organization_role`, les tables `organizations` et
   `organization_members`, puis `knowledge_bases.organization_id` **nullable**
   (aucune ligne existante n'a de tenant à cet instant).
2. **Backfill** en Python (`op.get_bind()`), pas en `INSERT...SELECT` SQL : pour
   chaque `User` existant, une organisation personnelle (nom = `display_name`)
   et une ligne `OrganizationMember(role=owner)`, avec un slug dérivé de
   `app.domain.slug.slugify` — désambiguïsé par un suffixe déterministe tiré de
   l'UUID de l'utilisateur en cas de collision (même `display_name` chez deux
   comptes). Chaque `KnowledgeBase` est rattachée à l'organisation de son
   `owner_id`. `slugify` est une fonction pure (aucune dépendance DB/ORM), donc
   sûre à importer depuis une migration.
2bis. `knowledge_bases.organization_id` passe `NOT NULL` une fois le backfill
   terminé.

Revision ID: 0004_multi_tenancy
Revises: 0003_evaluations
Create Date: 2026-08-01
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.slug import slugify

revision: str = "0004_multi_tenancy"
down_revision: str | None = "0003_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORGANIZATION_ID_FK = "fk_knowledge_bases_organization_id_organizations"

# `create_type=False` : la création/destruction du type est pilotée explicitement.
organization_role = postgresql.ENUM("owner", "member", name="organization_role", create_type=False)

_FALLBACK_SLUG = "organisation"
"""Repli quand `display_name` ne produit aucun caractère exploitable (`slugify`)."""


def upgrade() -> None:
    """Crée le schéma tenant, backfille, puis verrouille `organization_id`."""
    bind = op.get_bind()
    organization_role.create(bind, checkfirst=False)

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)

    op.create_table(
        "organization_members",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            organization_role,
            server_default=sa.text("'member'::organization_role"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_members_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", "user_id", name=op.f("pk_organization_members")),
    )
    op.create_index(
        op.f("ix_organization_members_user_id"),
        "organization_members",
        ["user_id"],
        unique=False,
    )

    op.add_column("knowledge_bases", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_knowledge_bases_organization_id"),
        "knowledge_bases",
        ["organization_id"],
        unique=False,
    )
    op.create_foreign_key(
        ORGANIZATION_ID_FK,
        "knowledge_bases",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    _backfill(bind)

    op.alter_column("knowledge_bases", "organization_id", nullable=False)


def _backfill(bind: sa.engine.Connection) -> None:
    """Une organisation personnelle + une appartenance `owner` par `User` existant.

    Boucle Python plutôt qu'un `INSERT...SELECT` : la génération d'un slug
    unique globalement (désambiguïsation déterministe sur collision de
    `display_name`) n'est pas exprimable proprement en SQL pur.
    """
    users_table = sa.table(
        "users", sa.column("id", sa.Uuid()), sa.column("display_name", sa.Text())
    )
    organizations_table = sa.table(
        "organizations",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("slug", sa.Text()),
    )
    organization_members_table = sa.table(
        "organization_members",
        sa.column("organization_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        # Le type `ENUM` natif, pas `sa.Text()` : asyncpg refuse un paramètre
        # `varchar` face à une colonne `organization_role` sans cast explicite
        # ("DatatypeMismatchError"), constaté à l'exécution réelle de la migration.
        sa.column("role", organization_role),
    )
    knowledge_bases_table = sa.table(
        "knowledge_bases",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("organization_id", sa.Uuid()),
    )

    users = bind.execute(sa.select(users_table.c.id, users_table.c.display_name)).all()

    used_slugs: set[str] = set()
    organization_id_by_user: dict[uuid.UUID, uuid.UUID] = {}

    for user_id, display_name in users:
        slug = _unique_slug(display_name, user_id, used_slugs)
        used_slugs.add(slug)
        organization_id = uuid.uuid4()
        organization_id_by_user[user_id] = organization_id

        bind.execute(
            organizations_table.insert().values(id=organization_id, name=display_name, slug=slug)
        )
        bind.execute(
            organization_members_table.insert().values(
                organization_id=organization_id, user_id=user_id, role="owner"
            )
        )

    knowledge_bases = bind.execute(
        sa.select(knowledge_bases_table.c.id, knowledge_bases_table.c.owner_id)
    ).all()
    for knowledge_base_id, owner_id in knowledge_bases:
        bind.execute(
            knowledge_bases_table.update()
            .where(knowledge_bases_table.c.id == knowledge_base_id)
            .values(organization_id=organization_id_by_user[owner_id])
        )


def _unique_slug(display_name: str, user_id: uuid.UUID, used_slugs: set[str]) -> str:
    """Dérive un slug globalement unique, désambiguïsé de façon déterministe.

    `slugify` seul peut collisionner entre deux comptes de même `display_name` :
    un suffixe tiré de l'UUID de l'utilisateur lève l'ambiguïté sans dépendre de
    l'ordre de traitement. Un ultime compteur couvre le cas — improbable mais
    pas impossible — où ce suffixe collisionnerait à son tour.
    """
    base_slug = slugify(display_name) or _FALLBACK_SLUG
    if base_slug not in used_slugs:
        return base_slug

    disambiguated = f"{base_slug}-{user_id.hex[:8]}"
    if disambiguated not in used_slugs:
        return disambiguated

    attempt = 1
    candidate = f"{disambiguated}-{attempt}"
    while candidate in used_slugs:
        attempt += 1
        candidate = f"{disambiguated}-{attempt}"
    return candidate


def downgrade() -> None:
    """Défait le schéma tenant dans l'ordre inverse, type `ENUM` compris."""
    op.drop_constraint(ORGANIZATION_ID_FK, "knowledge_bases", type_="foreignkey")
    op.drop_index(op.f("ix_knowledge_bases_organization_id"), table_name="knowledge_bases")
    op.drop_column("knowledge_bases", "organization_id")

    op.drop_index(op.f("ix_organization_members_user_id"), table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_table("organizations")

    bind = op.get_bind()
    organization_role.drop(bind, checkfirst=False)
