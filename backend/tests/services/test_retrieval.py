"""Cas d'usage `RetrievalService` : arbitrage du reranking, filtrage par documents,
mapping des résultats moteur vers leurs documents RAGEngine.

Le moteur est `FakeIngestionEngine` (`app.providers.engine.fake`), scénarisé par
`scripted_engine` : résultats imposés ou panne de `search`.

La matrice de permissions elle-même (§4) est déjà verrouillée par
`tests/services/identity/test_permissions.py` et `tests/services/test_documents.py`
— `resolve_access` n'est pas reproduite ici, seule son intégration dans l'ordre
d'orchestration du service l'est (matrice d'autorisation → arbitrage du
reranking → court-circuit sans dataset → filtrage → appel moteur → mapping).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.db.models import KnowledgeBase
from app.db.models import User as UserModel
from app.domain.enums import Visibility
from app.domain.schemas.retrieval import RetrievalResult, RetrievalSearchRequest
from app.providers.engine.base import (
    EngineDatasetRef,
    EngineDocumentRef,
    EngineSearchResult,
    EngineTransientError,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.services.retrieval import RetrievalService
from tests.db import factories
from tests.services.test_knowledge_bases import account, principal_of


def scripted_engine(
    *,
    results: list[EngineSearchResult] | None = None,
    failure: Exception | None = None,
) -> FakeIngestionEngine:
    """Moteur factice dont `search` rend `results`, ou lève `failure` (panne moteur)."""
    engine = FakeIngestionEngine(strict=False)
    if results is not None:
        engine.search_results = results
    if failure is not None:
        engine.failures["search"] = failure
    return engine


def engine_result(
    *,
    document_external_id: str,
    chunk_id: str = "chunk-1",
    content: str = "contenu",
    similarity: float = 0.9,
    vector_similarity: float = 0.8,
    term_similarity: float = 0.7,
) -> EngineSearchResult:
    """Fragment de résultat moteur, tel que l'adaptateur le produirait."""
    return EngineSearchResult(
        chunk_id=chunk_id,
        document_external_id=document_external_id,
        content=content,
        similarity=similarity,
        vector_similarity=vector_similarity,
        term_similarity=term_similarity,
    )


async def seeded_kb(
    db: AsyncSession,
    owner: UserModel,
    *,
    visibility: Visibility = Visibility.PRIVATE,
    dataset_external_id: str | None = "dataset-externe",
    reranker_model_id: str | None = "bge-reranker-v2-m3",
) -> KnowledgeBase:
    """Base persistée directement (sans passer par le registre de providers).

    Slug tiré d'un UUID frais à chaque appel : un même `owner` peut porter
    plusieurs bases dans un même test (`uq_knowledge_bases_owner_id_slug`).
    """
    knowledge_base = factories.make_knowledge_base(
        owner, visibility=visibility, slug=f"kb-{uuid.uuid4().hex[:8]}"
    )
    knowledge_base.reranker_model_id = reranker_model_id
    knowledge_base.ragflow_dataset_external_id = dataset_external_id
    db.add(knowledge_base)
    await db.flush()
    return knowledge_base


async def test_a_nominal_search_maps_the_engine_result_to_its_ragengine_document(
    db_session: AsyncSession,
) -> None:
    """Cas nominal : le document RAGEngine est résolu depuis sa référence moteur.

    `rerank=False` explicite : la base par défaut de `seeded_kb` déclare un
    reranker, donc le défaut intelligent (ADR-026) l'activerait sinon — hors
    du périmètre de ce test, qui ne porte que sur le mapping des résultats.
    """
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    document = factories.make_document(knowledge_base, filename="rapport.pdf")
    document.ragflow_document_external_id = "doc-externe"
    db_session.add(document)
    await db_session.flush()
    engine = scripted_engine(
        results=[
            engine_result(
                document_external_id="doc-externe",
                chunk_id="c1",
                content="Bonjour",
                similarity=0.95,
                vector_similarity=0.85,
                term_similarity=0.75,
            )
        ]
    )
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="bonjour", rerank=False),
    )

    assert response.results == [
        RetrievalResult(
            chunk_id="c1",
            document_id=document.id,
            filename="rapport.pdf",
            content="Bonjour",
            similarity=0.95,
            vector_similarity=0.85,
            term_similarity=0.75,
        )
    ]
    assert len(engine.search_calls) == 1
    call = engine.search_calls[0]
    assert call.datasets == [EngineDatasetRef(external_id="dataset-externe")]
    assert call.rerank_provider_id is None
    assert call.document_refs is None


async def test_a_never_ingested_base_returns_empty_without_calling_the_engine(
    db_session: AsyncSession,
) -> None:
    """Aucun `ragflow_dataset_external_id` : rien à interroger, le moteur n'est pas dérangé."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, dataset_external_id=None)
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question"),
    )

    assert response.results == []
    assert engine.search_calls == []


async def test_reranking_without_a_configured_reranker_is_refused(
    db_session: AsyncSession,
) -> None:
    """`rerank=True` sans `reranker_model_id` : refusé explicitement, jamais silencieux."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id=None)
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    with pytest.raises(ValidationError):
        await service.search(
            principal_of(owner),
            RetrievalSearchRequest(
                knowledge_base_id=knowledge_base.id, query="question", rerank=True
            ),
        )

    assert engine.search_calls == []


async def test_rerank_passes_the_bare_reranker_id_to_the_engine(db_session: AsyncSession) -> None:
    """Le service transmet l'identifiant **nu** : c'est l'adaptateur qui suffixe."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=True),
    )

    assert engine.search_calls[0].rerank_provider_id == "bge-reranker-v2-m3"


async def test_rerank_forwards_the_configured_candidate_pool_size(
    db_session: AsyncSession,
) -> None:
    """Incident réel du 2026-09-06 : sans plafond transmis au moteur, RAGFlow
    soumettait jusqu'à 1024 candidats en un seul lot au reranker externe — sur
    des passages de longueur réelle, l'allocation CUDA correspondante dépassait
    le budget de 12 Go (`CUDA out of memory`, 6,43 Gio demandés pour un seul
    lot). `Settings.rerank_candidate_pool_size` est l'unique valeur qui le
    borne désormais, transmise seulement quand le reranking est effectif.
    """
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=True),
    )

    assert (
        engine.search_calls[0].rerank_candidate_pool_size
        == get_settings().rerank_candidate_pool_size
    )
    assert engine.search_calls[0].rerank_candidate_pool_size is not None


async def test_no_candidate_pool_size_without_reranking(db_session: AsyncSession) -> None:
    """Sans reranker, rien à plafonner — un plafond fantôme tronquerait
    silencieusement le vivier de la recherche vectorielle elle-même."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id=None)
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=False),
    )

    assert engine.search_calls[0].rerank_candidate_pool_size is None


async def test_rerank_none_defaults_to_true_when_the_base_has_a_reranker(
    db_session: AsyncSession,
) -> None:
    """ADR-026 : `rerank` absent de la requête (`None`) active le reranking si la base en
    déclare un — défaut intelligent, pas une case à cocher à découvrir."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question"),
    )

    assert engine.search_calls[0].rerank_provider_id == "bge-reranker-v2-m3"


async def test_rerank_none_defaults_to_false_when_the_base_has_no_reranker(
    db_session: AsyncSession,
) -> None:
    """ADR-026 : `rerank` absent sur une base sans reranker se comporte comme aujourd'hui —
    pas de reranking, et surtout pas d'erreur (la résolution du défaut ne demande jamais
    explicitement un reranking qu'elle sait indisponible)."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id=None)
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question"),
    )

    assert engine.search_calls[0].rerank_provider_id is None


async def test_rerank_false_explicit_is_respected_even_with_a_reranker_configured(
    db_session: AsyncSession,
) -> None:
    """ADR-026 : `rerank=false` explicite reste une désactivation volontaire — jamais
    supplanté par le défaut intelligent, même si la base déclare un reranker."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=False),
    )

    assert engine.search_calls[0].rerank_provider_id is None


async def test_effective_rerank_with_a_nonzero_threshold_is_refused_without_acknowledgement(
    db_session: AsyncSession,
) -> None:
    """ADR-026 décision 2 : reranking effectif + seuil non nul, sans accusé de réception,
    refusé avant tout appel moteur (l'appel coûte une requête RAGFlow qu'on ne veut pas
    dépenser pour une requête qu'on va de toute façon refuser)."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    with pytest.raises(ValidationError):
        await service.search(
            principal_of(owner),
            RetrievalSearchRequest(
                knowledge_base_id=knowledge_base.id,
                query="question",
                rerank=True,
                similarity_threshold=0.2,
            ),
        )

    assert engine.search_calls == []


async def test_effective_rerank_with_a_nonzero_threshold_is_accepted_when_acknowledged(
    db_session: AsyncSession,
) -> None:
    """Le même cas, mais avec `acknowledge_threshold_risk=true` : la requête, informée du
    risque, est honorée telle quelle."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id,
            query="question",
            rerank=True,
            similarity_threshold=0.2,
            acknowledge_threshold_risk=True,
        ),
    )

    assert engine.search_calls[0].similarity_threshold == 0.2
    assert engine.search_calls[0].rerank_provider_id == "bge-reranker-v2-m3"


@pytest.mark.parametrize("threshold", [None, 0.0])
async def test_effective_rerank_with_a_zero_or_unset_threshold_needs_no_acknowledgement(
    db_session: AsyncSession, threshold: float | None
) -> None:
    """`0.0` ne filtre rien (ADR-026) : ni lui ni l'absence de seuil ne déclenchent la garde,
    même avec un reranking effectif — l'accusé de réception resterait un frein sans objet."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id,
            query="question",
            rerank=True,
            similarity_threshold=threshold,
        ),
    )

    assert engine.search_calls[0].rerank_provider_id == "bge-reranker-v2-m3"


async def test_an_unset_threshold_is_never_forwarded_as_none_when_reranking_is_effective(
    db_session: AsyncSession,
) -> None:
    """Régression : `similarity_threshold=None` + reranking effectif ne doit jamais atteindre
    le moteur tel quel.

    Trouvé en campagne E2E, contre l'instance RAGFlow réellement déployée :
    `similarity_threshold=None` laisse RAGFlow appliquer **son propre** défaut
    serveur, calibré pour le score vectoriel seul — contre le score combiné une
    fois le reranking effectif, dont l'échelle change (même risque que documente
    déjà la garde sur un seuil *explicite*, ADR-026, mais invisible ici : aucune
    erreur, une liste de résultats simplement vidée). Une requête connue pour
    faire remonter le bon document sans reranking (`rerank=False`) en a rendu
    zéro avec reranking effectif et seuil non précisé — vérifié sur du contenu
    réellement ingéré, pas seulement en isolation. `RetrievalService.search`
    doit donc résoudre `None` en `0.0` (seuil explicite qui ne filtre rien)
    avant l'appel moteur, jamais laisser passer `None` tel quel.
    """
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=True),
    )

    assert engine.search_calls[0].similarity_threshold == 0.0


async def test_an_unset_threshold_stays_unset_when_reranking_is_not_effective(
    db_session: AsyncSession,
) -> None:
    """Sans reranking effectif, aucun risque de changement d'échelle : `None` reste `None`,
    RAGFlow applique son défaut habituel sans qu'on ait de raison de s'en mêler."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    await service.search(
        principal_of(owner),
        RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question", rerank=False),
    )

    assert engine.search_calls[0].similarity_threshold is None


async def test_filtering_by_document_ids_builds_the_engine_refs(db_session: AsyncSession) -> None:
    """Cas nominal du filtre : seuls les documents demandés partent vers le moteur."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    kept = factories.make_document(knowledge_base, filename="garde.pdf")
    kept.ragflow_document_external_id = "doc-garde"
    other = factories.make_document(knowledge_base, filename="ailleurs.pdf")
    other.ragflow_document_external_id = "doc-ailleurs"
    db_session.add_all([kept, other])
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-garde")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id, query="question", document_ids=[kept.id]
        ),
    )

    assert len(response.results) == 1
    assert engine.search_calls[0].document_refs == [EngineDocumentRef(external_id="doc-garde")]


async def test_a_document_id_foreign_to_the_base_is_refused(db_session: AsyncSession) -> None:
    """Un filtre explicite ne s'ignore jamais en silence : identifiant étranger → 422."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    other_base = await seeded_kb(db_session, owner, dataset_external_id="autre-dataset")
    foreign = factories.make_document(other_base, filename="etranger.pdf")
    foreign.ragflow_document_external_id = "doc-etranger"
    db_session.add(foreign)
    await db_session.flush()
    engine = scripted_engine()
    service = RetrievalService(db_session, engine=engine)

    with pytest.raises(ValidationError):
        await service.search(
            principal_of(owner),
            RetrievalSearchRequest(
                knowledge_base_id=knowledge_base.id, query="question", document_ids=[foreign.id]
            ),
        )

    assert engine.search_calls == []


async def test_filtering_by_language_builds_the_engine_refs(db_session: AsyncSession) -> None:
    """Filtre descriptif : seul le document dont la langue recoupe la demande part au moteur."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    french = factories.make_document(knowledge_base, filename="fr.pdf")
    french.detected_languages = ["fr"]
    french.ragflow_document_external_id = "doc-fr"
    english = factories.make_document(knowledge_base, filename="en.pdf")
    english.detected_languages = ["en"]
    english.ragflow_document_external_id = "doc-en"
    db_session.add_all([french, english])
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-fr")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id, query="question", languages=["fr"]
        ),
    )

    assert len(response.results) == 1
    assert engine.search_calls[0].document_refs == [EngineDocumentRef(external_id="doc-fr")]


async def test_a_language_matching_no_document_returns_empty_without_error(
    db_session: AsyncSession,
) -> None:
    """Contrairement à `document_ids` : un filtre descriptif sans correspondance ne lève rien."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    french = factories.make_document(knowledge_base, filename="fr.pdf")
    french.detected_languages = ["fr"]
    french.ragflow_document_external_id = "doc-fr"
    db_session.add(french)
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-fr")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id, query="question", languages=["de"]
        ),
    )

    assert response.results == []
    assert engine.search_calls == []


async def test_filtering_by_theme_builds_the_engine_refs(db_session: AsyncSession) -> None:
    """Même mécanique que `languages`, sur `theme` — saisi par l'utilisateur, pas déduit."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    juridique = factories.make_document(knowledge_base, filename="contrat.pdf")
    juridique.theme = "juridique"
    juridique.ragflow_document_external_id = "doc-juridique"
    finance = factories.make_document(knowledge_base, filename="budget.pdf")
    finance.theme = "finance"
    finance.ragflow_document_external_id = "doc-finance"
    db_session.add_all([juridique, finance])
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-juridique")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id, query="question", themes=["juridique"]
        ),
    )

    assert len(response.results) == 1
    assert engine.search_calls[0].document_refs == [EngineDocumentRef(external_id="doc-juridique")]


async def test_a_theme_matching_no_document_returns_empty_without_error(
    db_session: AsyncSession,
) -> None:
    """Filtre descriptif : un thème sans correspondance ne lève rien, comme `languages`."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    juridique = factories.make_document(knowledge_base, filename="contrat.pdf")
    juridique.theme = "juridique"
    juridique.ragflow_document_external_id = "doc-juridique"
    db_session.add(juridique)
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-juridique")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id, query="question", themes=["rh"]
        ),
    )

    assert response.results == []
    assert engine.search_calls == []


async def test_filtering_by_content_type_builds_the_engine_refs(db_session: AsyncSession) -> None:
    """Même mécanique que `languages`, sur `content_type`."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    pdf = factories.make_document(knowledge_base, filename="rapport.pdf")
    pdf.content_type = "application/pdf"
    pdf.ragflow_document_external_id = "doc-pdf"
    sheet = factories.make_document(knowledge_base, filename="tableur.xlsx")
    sheet.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sheet.ragflow_document_external_id = "doc-xlsx"
    db_session.add_all([pdf, sheet])
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-pdf")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id,
            query="question",
            content_types=["application/pdf"],
        ),
    )

    assert len(response.results) == 1
    assert engine.search_calls[0].document_refs == [EngineDocumentRef(external_id="doc-pdf")]


async def test_filtering_by_ingested_after_excludes_older_documents(
    db_session: AsyncSession,
) -> None:
    """Même mécanique que `languages`, sur `created_at`."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    old = factories.make_document(knowledge_base, filename="ancien.pdf")
    old.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    old.ragflow_document_external_id = "doc-ancien"
    recent = factories.make_document(knowledge_base, filename="recent.pdf")
    recent.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    recent.ragflow_document_external_id = "doc-recent"
    db_session.add_all([old, recent])
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="doc-recent")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id,
            query="question",
            ingested_after=datetime(2025, 1, 1, tzinfo=UTC),
        ),
    )

    assert len(response.results) == 1
    assert engine.search_calls[0].document_refs == [EngineDocumentRef(external_id="doc-recent")]


async def test_when_no_requested_document_is_chunked_yet_the_response_is_empty(
    db_session: AsyncSession,
) -> None:
    """Les documents existent mais aucun n'a de référence moteur : rien à chercher."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    not_yet_chunked = factories.make_document(knowledge_base, filename="en-attente.pdf")
    db_session.add(not_yet_chunked)
    await db_session.flush()
    engine = scripted_engine(results=[engine_result(document_external_id="jamais-envoye")])
    service = RetrievalService(db_session, engine=engine)

    response = await service.search(
        principal_of(owner),
        RetrievalSearchRequest(
            knowledge_base_id=knowledge_base.id,
            query="question",
            document_ids=[not_yet_chunked.id],
        ),
    )

    assert response.results == []
    assert engine.search_calls == []


async def test_an_engine_result_with_an_unknown_document_is_excluded_and_logged(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Cas théoriquement impossible mais pas garanti par contrat : exclu, pas une exception."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    known = factories.make_document(knowledge_base, filename="connu.pdf")
    known.ragflow_document_external_id = "doc-connu"
    db_session.add(known)
    await db_session.flush()
    engine = scripted_engine(
        results=[
            engine_result(document_external_id="doc-connu", chunk_id="c1"),
            engine_result(document_external_id="doc-fantome", chunk_id="c2"),
        ]
    )
    service = RetrievalService(db_session, engine=engine)

    with caplog.at_level(logging.WARNING, logger="app.services.retrieval"):
        response = await service.search(
            principal_of(owner),
            RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question"),
        )

    assert [result.chunk_id for result in response.results] == ["c1"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_an_engine_transient_failure_propagates_untranslated(
    db_session: AsyncSession,
) -> None:
    """Une panne momentanée du moteur (RAGFlow/ES indisponible) n'est ni avalée ni retraduite.

    `EngineTransientError` porte déjà son propre statut (`503`, `app/providers/
    engine/base.py`) via le contrat d'erreur RFC 9457 (`RAGEngineError.status`) :
    `RetrievalService.search` ne l'intercepte nulle part, elle traverse le
    service telle quelle jusqu'au handler d'erreurs de la façade — c'est ce qui
    distingue déjà, sans code supplémentaire, une indisponibilité passagère du
    moteur (`503`) d'un argument de requête invalide (`422`) ou d'une panne
    définitive (`502`, `EngineError`/`EngineValidationError`).
    """
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine(failure=EngineTransientError("Moteur d'ingestion injoignable."))
    service = RetrievalService(db_session, engine=engine)

    with pytest.raises(EngineTransientError) as raised:
        await service.search(
            principal_of(owner),
            RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query="question"),
        )

    assert raised.value.to_problem().status == 503
