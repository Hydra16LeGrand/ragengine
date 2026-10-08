"""Pagination par curseur exécutée contre PostgreSQL.

Le test unitaire vérifie l'encodage ; celui-ci vérifie que le couple
`(created_at, id)` produit bien un parcours **total et sans répétition** sur des
lignes qui partagent le même horodatage — cas fréquent, puisqu'un lot inséré
dans une seule transaction reçoit le `now()` de cette transaction.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.db.models import KnowledgeBase
from app.domain.pagination import apply_cursor_pagination, build_page
from tests.db import factories

PAGE_SIZE = 4
TOTAL = 11


async def _seed(session: AsyncSession) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Crée `TOTAL` bases pour un même propriétaire et renvoie l'ordre attendu."""
    owner = factories.make_user()
    session.add(owner)
    await session.flush()
    bases = [
        factories.make_knowledge_base(owner, slug=f"base-{index:02d}") for index in range(TOTAL)
    ]
    session.add_all(bases)
    await session.flush()
    for base in bases:
        await session.refresh(base)
    ordered = sorted(bases, key=lambda base: (base.created_at, base.id), reverse=True)
    return owner.id, [base.id for base in ordered]


async def _collect(session: AsyncSession, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """Parcourt toutes les pages en suivant `next_cursor`."""
    collected: list[uuid.UUID] = []
    cursor: str | None = None
    for _ in range(TOTAL):  # borne de sécurité : jamais de boucle infinie
        statement = apply_cursor_pagination(
            select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id),
            KnowledgeBase,
            cursor=cursor,
            limit=PAGE_SIZE,
        )
        rows = (await session.execute(statement)).scalars().all()
        page = build_page(rows, serializer=lambda base: base.id, limit=PAGE_SIZE)
        collected.extend(page.data)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    return collected


async def test_cursor_walks_the_whole_collection_exactly_once(
    db_session: AsyncSession,
) -> None:
    """Toutes les lignes sortent, dans l'ordre, sans doublon ni oubli."""
    owner_id, expected = await _seed(db_session)

    collected = await _collect(db_session, owner_id)

    assert collected == expected
    assert len(set(collected)) == TOTAL


async def test_pages_have_the_requested_size(db_session: AsyncSession) -> None:
    """La ligne sentinelle sert à détecter la suite, elle n'est jamais rendue."""
    owner_id, expected = await _seed(db_session)

    statement = apply_cursor_pagination(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id),
        KnowledgeBase,
        limit=PAGE_SIZE,
    )
    rows = (await db_session.execute(statement)).scalars().all()
    page = build_page(rows, serializer=lambda base: base.id, limit=PAGE_SIZE)

    assert len(rows) == PAGE_SIZE + 1
    assert page.data == expected[:PAGE_SIZE]
    assert page.next_cursor is not None


async def test_last_page_has_no_next_cursor(db_session: AsyncSession) -> None:
    """Une collection plus petite que la page tient en une seule réponse."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()
    db_session.add(factories.make_knowledge_base(owner, slug="unique"))
    await db_session.flush()

    statement = apply_cursor_pagination(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == owner.id),
        KnowledgeBase,
        limit=PAGE_SIZE,
    )
    rows = (await db_session.execute(statement)).scalars().all()
    page = build_page(rows, serializer=lambda base: base.id, limit=PAGE_SIZE)

    assert len(page.data) == 1
    assert page.next_cursor is None


async def test_a_corrupted_cursor_never_reaches_the_database(
    db_session: AsyncSession,
) -> None:
    """Le curseur est décodé avant la requête : pas de 500 sur une valeur forgée."""
    owner = factories.make_user()
    db_session.add(owner)
    await db_session.flush()

    with pytest.raises(ValidationError):
        apply_cursor_pagination(
            select(KnowledgeBase).where(KnowledgeBase.owner_id == owner.id),
            KnowledgeBase,
            cursor="curseur-bidon",
            limit=PAGE_SIZE,
        )
