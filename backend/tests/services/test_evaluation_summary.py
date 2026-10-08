"""Agrégation transverse d'évaluation (Phase 12, Étape 5) : `compute_visible_evaluation_summary`.

RBAC exercée directement ici plutôt que renvoyée à un mock de
`KnowledgeBaseService` : le périmètre réel de `list_visible` (bases possédées,
`public`, ou dont on est membre) est justement la propriété que ce module doit
respecter sans jamais la recalculer lui-même — un test qui mockerait
`list_visible` ne vérifierait rien de la jointure RBAC réelle.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Visibility
from app.services.evaluation_summary import compute_visible_evaluation_summary
from app.services.identity.principal import ANONYMOUS
from tests.db import factories
from tests.services.test_knowledge_bases import account, principal_of


async def test_no_visible_knowledge_base_returns_an_empty_summary(
    db_session: AsyncSession,
) -> None:
    """Aucune base visible (visiteur anonyme, aucune base publique) : réponse vide, pas d'erreur."""
    result = await compute_visible_evaluation_summary(db_session, ANONYMOUS)

    assert result.knowledge_bases == []
    assert result.overall.knowledge_base_count == 0
    assert result.overall.item_count == 0
    assert result.overall.recall_at_k == {1: None, 5: None}
    assert result.overall.mrr is None
    assert result.overall.average_judge_score is None


async def test_a_visible_base_without_any_item_is_omitted_from_the_list(
    db_session: AsyncSession,
) -> None:
    """Base visible mais jamais évaluée : absente de `knowledge_bases`, `overall` reste vide."""
    owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(owner, slug="jamais-evaluee")
    db_session.add(knowledge_base)
    await db_session.flush()

    result = await compute_visible_evaluation_summary(db_session, principal_of(owner))

    assert result.knowledge_bases == []
    assert result.overall.item_count == 0


async def test_aggregates_items_across_two_visible_knowledge_bases(
    db_session: AsyncSession,
) -> None:
    """Deux bases possédées par le même compte : entrées distinctes, `overall` pondéré par item."""
    owner = await account(db_session)

    # `make_knowledge_base` fixe toujours le même `name` ("Référentiel Qualité") : distingué
    # ici à la main, sinon les deux bases collapseraient sous la même clé `by_name`.
    kb_a = factories.make_knowledge_base(owner, slug="base-a")
    kb_a.name = "Base A"
    kb_b = factories.make_knowledge_base(owner, slug="base-b")
    kb_b.name = "Base B"
    db_session.add_all([kb_a, kb_b])
    await db_session.flush()

    doc_a1 = factories.make_document(kb_a, filename="a1.pdf")
    doc_a2 = factories.make_document(kb_a, filename="a2.pdf")
    doc_b1 = factories.make_document(kb_b, filename="b1.pdf")
    db_session.add_all([doc_a1, doc_a2, doc_b1])
    await db_session.flush()

    run_a = factories.make_evaluation_run(kb_a, requested_by=owner, sample_size=2)
    run_b = factories.make_evaluation_run(kb_b, requested_by=owner, sample_size=1)
    db_session.add_all([run_a, run_b])
    await db_session.flush()

    # Base A : deux items, l'un trouvé (rang 1), l'autre absent (None) — recall@1 = 0.5.
    # Base B : un item trouvé (rang 1) — recall@1 = 1.0.
    db_session.add_all(
        [
            factories.make_evaluation_item(run_a, doc_a1, retrieval_rank=1, judge_score=0.8),
            factories.make_evaluation_item(run_a, doc_a2, retrieval_rank=None, judge_score=0.2),
            factories.make_evaluation_item(run_b, doc_b1, retrieval_rank=1, judge_score=1.0),
        ]
    )
    await db_session.flush()

    result = await compute_visible_evaluation_summary(
        db_session, principal_of(owner), k_values=(1,)
    )

    by_name = {entry.knowledge_base_name: entry for entry in result.knowledge_bases}
    assert set(by_name) == {kb_a.name, kb_b.name}
    assert by_name[kb_a.name].item_count == 2
    assert by_name[kb_a.name].run_count == 1
    assert by_name[kb_a.name].recall_at_k == {1: 0.5}
    assert by_name[kb_a.name].average_judge_score == 0.5
    assert by_name[kb_b.name].item_count == 1
    assert by_name[kb_b.name].recall_at_k == {1: 1.0}

    # Overall : 3 items au total, 2 trouvés au rang 1 -> recall@1 = 2/3, jamais la moyenne
    # (0.5 + 1.0) / 2 = 0.75 des deux bases prise séparément.
    assert result.overall.knowledge_base_count == 2
    assert result.overall.run_count == 2
    assert result.overall.item_count == 3
    assert result.overall.recall_at_k[1] is not None
    assert abs(result.overall.recall_at_k[1] - 2 / 3) < 1e-9
    assert result.overall.average_judge_score is not None
    assert abs(result.overall.average_judge_score - (0.8 + 0.2 + 1.0) / 3) < 1e-9

    # Résultat trié par nom de base.
    assert [entry.knowledge_base_name for entry in result.knowledge_bases] == sorted(
        entry.knowledge_base_name for entry in result.knowledge_bases
    )


async def test_a_private_base_owned_by_someone_else_is_excluded(db_session: AsyncSession) -> None:
    """Base privée d'un tiers, avec des items réels : invisible pour un autre principal."""
    owner = await account(db_session, display_name="Propriétaire")
    stranger = await account(db_session, display_name="Étranger")

    knowledge_base = factories.make_knowledge_base(owner, slug="privee-avec-items")
    db_session.add(knowledge_base)
    await db_session.flush()
    document = factories.make_document(knowledge_base, filename="a.pdf")
    db_session.add(document)
    await db_session.flush()
    run = factories.make_evaluation_run(knowledge_base, requested_by=owner)
    db_session.add(run)
    await db_session.flush()
    db_session.add(factories.make_evaluation_item(run, document, retrieval_rank=1))
    await db_session.flush()

    stranger_result = await compute_visible_evaluation_summary(db_session, principal_of(stranger))
    owner_result = await compute_visible_evaluation_summary(db_session, principal_of(owner))

    assert stranger_result.knowledge_bases == []
    assert stranger_result.overall.item_count == 0
    assert owner_result.overall.item_count == 1


async def test_a_public_base_is_visible_to_an_anonymous_principal(
    db_session: AsyncSession,
) -> None:
    """Base `public` : comptée même pour un visiteur non authentifié (mode invité, ADR-005)."""
    owner = await account(db_session)
    knowledge_base = factories.make_knowledge_base(
        owner, slug="publique", visibility=Visibility.PUBLIC
    )
    db_session.add(knowledge_base)
    await db_session.flush()
    document = factories.make_document(knowledge_base, filename="a.pdf")
    db_session.add(document)
    await db_session.flush()
    run = factories.make_evaluation_run(knowledge_base, requested_by=owner)
    db_session.add(run)
    await db_session.flush()
    db_session.add(factories.make_evaluation_item(run, document, retrieval_rank=1))
    await db_session.flush()

    result = await compute_visible_evaluation_summary(db_session, ANONYMOUS)

    assert len(result.knowledge_bases) == 1
    assert result.knowledge_bases[0].knowledge_base_id == knowledge_base.id
    assert result.overall.item_count == 1
