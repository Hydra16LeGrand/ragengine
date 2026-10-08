"""Cas d'usage `EvaluationService` : échantillonnage, génération, notation.

Le moteur est `FakeIngestionEngine` (`app.providers.engine.fake`), scénarisé par fragments et
résultats de recherche ; `ScriptedLLM` est un double minimal du LLM. `ScriptedLLM.complete`
distingue une
génération d'un jugement par la **forme** du dernier message utilisateur (le
prompt de juge commence toujours par `"Question : "`, cf. `_judge` du
service) plutôt que par l'ordre d'appel : le tirage des documents est mélangé
(`random.shuffle`), l'ordre des appels ne doit donc jamais être une hypothèse
d'un test.

La matrice de permissions (§4) n'est pas ré-exercée ici : `EvaluationService`
suppose l'autorisation déjà acquise par `require_kb_permission`, exactement
comme `KnowledgeBaseService` (cf. sa docstring).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit as rate_limit_module
from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    ValidationError,
)
from app.core.rate_limit import RateLimiterUnavailableError
from app.db.models import Document, EvaluationRun, KnowledgeBase
from app.db.models import User as UserModel
from app.domain.enums import DocumentStatus, EvaluationRunStatus
from app.domain.schemas.evaluation import MAX_EVALUATION_SAMPLE_SIZE, EvaluationCreate
from app.providers.engine.base import (
    EngineChunk,
    EngineSearchResult,
)
from app.providers.engine.fake import FakeIngestionEngine
from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMRateLimitError
from app.services.evaluations import EvaluationService
from app.services.identity.errors import AccountRequiredError
from app.services.identity.platform_llm_budget import (
    enforce_evaluation_quota,
    peek_evaluation_quota,
    peek_global_budget,
)
from app.services.identity.user_llm_keys import UserLlmKeyService
from tests.db import factories
from tests.services.test_knowledge_bases import account, principal_of
from tests.services.test_retrieval import seeded_kb

pytestmark = pytest.mark.usefixtures("explicit_llm_caps")

PASSAGE_A = "La politique de remboursement accorde 14 jours sans justification."
PASSAGE_B = "Le télétravail est autorisé jusqu'à trois jours par semaine."


@dataclass(slots=True)
class ScriptedLLM:
    """Double de `LLMPort` : réponses de génération scriptées par passage, juge fixe."""

    generation_by_passage: dict[str, str]
    judge_response: str
    stream_text: str = "réponse générée par le chat"
    complete_calls: list[list[ChatMessage]] = field(default_factory=list)
    stream_calls: list[list[ChatMessage]] = field(default_factory=list)
    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="scripted-llm",
            vendor="anthropic",
            context_window=100_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        self.complete_calls.append(list(messages))
        user_content = messages[-1].content
        if user_content.startswith("Question : "):
            return self.judge_response
        return self.generation_by_passage[user_content]

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        self.stream_calls.append(list(messages))
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        yield self.stream_text


def generation_json(question: str, answer: str) -> str:
    return json.dumps({"question": question, "answer": answer})


def judge_json(score: float, rationale: str) -> str:
    return json.dumps({"score": score, "rationale": rationale})


async def eligible_document(
    db: AsyncSession, knowledge_base: KnowledgeBase, *, filename: str, external_id: str
) -> Document:
    """Document `INDEXED` avec référence moteur — seul état éligible à l'échantillonnage."""
    document = factories.make_document(knowledge_base, filename=filename)
    document.status = DocumentStatus.INDEXED
    document.ragflow_document_external_id = external_id
    db.add(document)
    await db.flush()
    return document


async def enabled_kb(db: AsyncSession, owner: UserModel) -> KnowledgeBase:
    """Base avec `evaluation_enabled=True`, dataset connu — condition de `create_run`."""
    knowledge_base = await seeded_kb(db, owner)
    knowledge_base.evaluation_enabled = True
    await db.flush()
    return knowledge_base


async def test_a_nominal_run_produces_one_item_scored_on_both_axes(
    db_session: AsyncSession,
) -> None:
    """Cas nominal : un document échantillonné, une question, deux scores calculés."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    document = await eligible_document(
        db_session, knowledge_base, filename="politique.pdf", external_id="doc-politique"
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            "doc-politique": [EngineChunk(chunk_id="c1", content=PASSAGE_A)],
        },
        search_results=[
            EngineSearchResult(
                chunk_id="c1",
                document_external_id="doc-politique",
                content=PASSAGE_A,
                similarity=0.9,
                vector_similarity=0.9,
                term_similarity=0.9,
            )
        ],
    )
    llm = ScriptedLLM(
        generation_by_passage={
            PASSAGE_A: generation_json(
                "Combien de jours pour un remboursement ?", "14 jours sans justification."
            )
        },
        judge_response=judge_json(0.8, "Réponse fidèle à la référence."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
    )

    assert result.sample_size == 1
    assert result.item_count == 1
    assert result.average_judge_score == 0.8
    assert result.retrieval_recall_rate == 1.0
    item = result.items[0]
    assert item.source_document_id == document.id
    assert item.question == "Combien de jours pour un remboursement ?"
    assert item.reference_answer == "14 jours sans justification."
    assert item.generated_answer == "réponse générée par le chat"
    assert item.retrieval_rank == 1
    assert item.judge_score == 0.8
    assert item.judge_rationale == "Réponse fidèle à la référence."
    assert [call[1] for call in engine.chunk_list_calls] == ["doc-politique"]
    assert len(engine.search_calls) == 2  # `_retrieval_rank` + recherche interne de `chat()`


async def test_evaluation_disabled_on_the_base_is_refused(db_session: AsyncSession) -> None:
    """`evaluation_enabled=False` : refusé avant tout tirage, aucun appel moteur/LLM."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    assert knowledge_base.evaluation_enabled is False
    engine = FakeIngestionEngine(
        strict=False,
    )
    service = EvaluationService(db_session, engine=engine, llm=ScriptedLLM({}, ""))

    with pytest.raises(ValidationError):
        await service.create_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
        )

    assert engine.chunk_list_calls == []
    assert engine.search_calls == []


async def test_no_eligible_document_is_a_conflict(db_session: AsyncSession) -> None:
    """Aucun document `INDEXED` : `409`, même une base par ailleurs bien configurée."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    pending = factories.make_document(knowledge_base, filename="en-cours.pdf")
    db_session.add(pending)
    await db_session.flush()
    engine = FakeIngestionEngine(
        strict=False,
    )
    service = EvaluationService(db_session, engine=engine, llm=ScriptedLLM({}, ""))

    with pytest.raises(ConflictError):
        await service.create_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
        )


async def test_sample_size_is_capped_to_the_number_of_eligible_documents(
    db_session: AsyncSession,
) -> None:
    """`sample_size=5` demandé, deux documents éligibles seulement : cible ramenée à 2."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    await eligible_document(db_session, knowledge_base, filename="b.pdf", external_id="doc-b")
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            "doc-a": [EngineChunk(chunk_id="ca", content=PASSAGE_A)],
            "doc-b": [EngineChunk(chunk_id="cb", content=PASSAGE_B)],
        },
        search_results=[],
    )
    llm = ScriptedLLM(
        generation_by_passage={
            PASSAGE_A: generation_json("Q-A ?", "R-A"),
            PASSAGE_B: generation_json("Q-B ?", "R-B"),
        },
        judge_response=judge_json(0.5, "Correct."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=5, llm_provider_id="claude-opus-5"),
    )

    assert result.sample_size == 2
    assert result.item_count == 2
    assert result.retrieval_recall_rate == 0.0  # `search_results=[]` : jamais retrouvé


async def test_a_document_whose_generation_is_unparsable_is_skipped(
    db_session: AsyncSession,
) -> None:
    """Réponse de génération non-JSON pour un document : ignoré, l'autre produit son item."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    good_document = await eligible_document(
        db_session, knowledge_base, filename="b.pdf", external_id="doc-b"
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            "doc-a": [EngineChunk(chunk_id="ca", content=PASSAGE_A)],
            "doc-b": [EngineChunk(chunk_id="cb", content=PASSAGE_B)],
        },
        search_results=[],
    )
    llm = ScriptedLLM(
        generation_by_passage={
            PASSAGE_A: "réponse hors sujet, pas du JSON du tout",
            PASSAGE_B: generation_json("Q-B ?", "R-B"),
        },
        judge_response=judge_json(0.6, "Correct."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=2, llm_provider_id="claude-opus-5"),
    )

    # Cible de tirage inchangée (2 documents éligibles), mais un seul item exploitable.
    assert result.sample_size == 2
    assert result.item_count == 1
    assert result.items[0].source_document_id == good_document.id


async def test_a_judge_score_outside_bounds_is_clamped(db_session: AsyncSession) -> None:
    """Le juge peut déborder de [0, 1] malgré la consigne : borné plutôt que rejeté."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={"doc-a": [EngineChunk(chunk_id="ca", content=PASSAGE_A)]},
        search_results=[],
    )
    llm = ScriptedLLM(
        generation_by_passage={PASSAGE_A: generation_json("Q ?", "R")},
        judge_response=judge_json(1.4, "Excellent."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
    )

    assert result.items[0].judge_score == 1.0


async def test_list_runs_is_empty_without_error_when_none_exist(db_session: AsyncSession) -> None:
    """Aucun run encore lancé : liste vide, pas une erreur."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    service = EvaluationService(
        db_session,
        engine=FakeIngestionEngine(
            strict=False,
        ),
        llm=ScriptedLLM({}, ""),
    )

    assert await service.list_runs(knowledge_base) == []


async def test_get_run_from_a_foreign_base_is_not_found(db_session: AsyncSession) -> None:
    """Un run existant mais rattaché à une autre base : `404`, jamais un détail qui fuit."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    other_base = await enabled_kb(db_session, owner)
    service = EvaluationService(
        db_session,
        engine=FakeIngestionEngine(
            strict=False,
        ),
        llm=ScriptedLLM({}, ""),
    )

    with pytest.raises(NotFoundError):
        await service.get_run(other_base, uuid.uuid4())

    # Sanity check indépendant : `knowledge_base` existe bien, seul l'identifiant du run est faux.
    assert knowledge_base.id != other_base.id


async def test_list_and_get_run_do_not_resolve_the_engine(db_session: AsyncSession) -> None:
    """`list_runs`/`get_run` sont des lectures pures : elles ne doivent jamais résoudre le moteur.

    Régression : `EvaluationService.__init__` résolvait `resolve_ingestion_engine`
    inconditionnellement, ce qui faisait échouer ces deux endpoints en `502`
    dès que `RAGFLOW_API_KEY` n'était pas configurée — alors qu'aucun des deux
    n'a besoin du moteur. Aucun `engine=` n'est injecté ici : si `__init__` ou
    `list_runs`/`get_run` tentait de le résoudre, `resolve_ingestion_engine`
    lèverait `EngineError` faute de clé, et le test échouerait.
    """
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    settings = Settings(ragflow_api_key=None)
    service = EvaluationService(db_session, settings)

    assert await service.list_runs(knowledge_base) == []
    with pytest.raises(NotFoundError):
        await service.get_run(knowledge_base, uuid.uuid4())


# ─── Run asynchrone à la demande (Phase 12 Étape 4, item restant, ADR-028) ───


@dataclass(slots=True)
class FailingLLM:
    """`LLMPort` qui échoue systématiquement — simule un disjoncteur ouvert/fournisseur à sec."""

    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="scripted-llm",
            vendor="anthropic",
            context_window=100_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        raise LLMRateLimitError("Quota épuisé pour « scripted-llm ».")

    def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise LLMRateLimitError("Quota épuisé pour « scripted-llm ».")


async def test_create_run_refuses_a_sample_size_above_the_synchronous_cap(
    db_session: AsyncSession,
) -> None:
    """`sample_size` au-delà de 20 : refusé avant tout tirage, jamais rejoué en synchrone."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    engine = FakeIngestionEngine(
        strict=False,
    )
    service = EvaluationService(db_session, engine=engine, llm=ScriptedLLM({}, ""))

    with pytest.raises(ValidationError):
        await service.create_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(
                sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
            ),
        )

    assert engine.chunk_list_calls == []


async def test_create_pending_run_persists_a_pending_run_without_any_llm_call(
    db_session: AsyncSession,
) -> None:
    """Au-delà du plafond synchrone : run `pending` immédiat, aucun coût LLM avant l'exécution."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    await eligible_document(db_session, knowledge_base, filename="b.pdf", external_id="doc-b")
    engine = FakeIngestionEngine(
        strict=False,
    )
    llm = ScriptedLLM({}, "")
    service = EvaluationService(db_session, engine=engine, llm=llm)

    result = await service.create_pending_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(
            sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
        ),
    )

    assert result.status == EvaluationRunStatus.PENDING
    assert result.sample_size == 2  # ramené aux deux documents éligibles
    assert result.item_count == 0
    assert result.items == []
    assert result.average_judge_score is None
    assert result.retrieval_recall_rate is None
    assert engine.chunk_list_calls == []
    assert llm.complete_calls == []

    # `GET .../evaluations/{id}` doit refléter ce `pending` sans erreur ni silence.
    read_back = await service.get_run(knowledge_base, result.id)
    assert read_back.status == EvaluationRunStatus.PENDING
    summaries = await service.list_runs(knowledge_base)
    assert summaries[0].status == EvaluationRunStatus.PENDING


async def test_create_pending_run_refuses_when_evaluation_disabled(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    assert knowledge_base.evaluation_enabled is False
    service = EvaluationService(
        db_session,
        engine=FakeIngestionEngine(
            strict=False,
        ),
        llm=ScriptedLLM({}, ""),
    )

    with pytest.raises(ValidationError):
        await service.create_pending_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(
                sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
            ),
        )


async def test_create_pending_run_refuses_when_no_eligible_document(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    service = EvaluationService(
        db_session,
        engine=FakeIngestionEngine(
            strict=False,
        ),
        llm=ScriptedLLM({}, ""),
    )

    with pytest.raises(ConflictError):
        await service.create_pending_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(
                sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
            ),
        )


async def test_create_pending_run_refuses_beyond_the_configured_async_cap(
    db_session: AsyncSession,
) -> None:
    """Le plafond asynchrone reste un réglage, révisable sans migration ni redéploiement du code."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    settings = Settings(evaluation_async_max_sample_size=25)
    service = EvaluationService(
        db_session,
        settings,
        engine=FakeIngestionEngine(
            strict=False,
        ),
        llm=ScriptedLLM({}, ""),
    )

    with pytest.raises(ValidationError):
        await service.create_pending_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(sample_size=26, llm_provider_id="claude-opus-5"),
        )


async def test_run_pending_evaluation_completes_a_pending_run(db_session: AsyncSession) -> None:
    """Rejoue la génération d'un run déjà `pending`, comme le ferait la tâche Celery."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    document = await eligible_document(
        db_session, knowledge_base, filename="a.pdf", external_id="doc-a"
    )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_A)]},
        search_results=[
            EngineSearchResult(
                chunk_id="c1",
                document_external_id="doc-a",
                content=PASSAGE_A,
                similarity=0.9,
                vector_similarity=0.9,
                term_similarity=0.9,
            )
        ],
    )
    llm = ScriptedLLM(
        generation_by_passage={PASSAGE_A: generation_json("Q ?", "R")},
        judge_response=judge_json(0.7, "Correct."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)
    pending = await service.create_pending_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(
            sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
        ),
    )
    assert pending.status == EvaluationRunStatus.PENDING

    result = await service.run_pending_evaluation(principal_of(owner), knowledge_base, pending.id)

    assert result is not None
    assert result.status == EvaluationRunStatus.COMPLETED
    assert result.item_count == 1
    assert result.items[0].source_document_id == document.id


async def test_run_pending_evaluation_is_a_noop_for_a_run_that_is_not_pending(
    db_session: AsyncSession,
) -> None:
    """Course bénigne (tâche redistribuée) : un run déjà résolu n'est jamais rejoué."""
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_A)]},
        search_results=[],
    )
    llm = ScriptedLLM(
        generation_by_passage={PASSAGE_A: generation_json("Q ?", "R")},
        judge_response=judge_json(0.7, "Correct."),
    )
    service = EvaluationService(db_session, engine=engine, llm=llm)
    completed = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
    )
    assert completed.status == EvaluationRunStatus.COMPLETED

    result = await service.run_pending_evaluation(principal_of(owner), knowledge_base, completed.id)

    assert result is None
    unchanged = await service.get_run(knowledge_base, completed.id)
    assert unchanged.item_count == 1  # inchangé : rien n'a été régénéré


async def test_run_pending_evaluation_marks_the_run_failed_and_persists_it_before_raising(
    db_session: AsyncSession,
) -> None:
    """Le `failed` doit survivre au `ROLLBACK` que la tâche appelante fait sur l'exception relevée.

    Reproduit, au niveau service, l'invariant que `run_in_session`
    (`app.workers.session`) impose à l'appelant réel (`run_async_evaluation`) :
    toute exception non rattrapée déclenche un `ROLLBACK`. Sans le commit
    explicite de `run_pending_evaluation` avant de relever l'exception, ce
    test échouerait — c'est exactement le défaut que ce test verrouille.
    """
    owner = await account(db_session)
    knowledge_base = await enabled_kb(db_session, owner)
    await eligible_document(db_session, knowledge_base, filename="a.pdf", external_id="doc-a")
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={"doc-a": [EngineChunk(chunk_id="c1", content=PASSAGE_A)]},
    )
    service = EvaluationService(db_session, engine=engine, llm=ScriptedLLM({}, ""))
    pending = await service.create_pending_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(
            sample_size=MAX_EVALUATION_SAMPLE_SIZE + 1, llm_provider_id="claude-opus-5"
        ),
    )

    failing_service = EvaluationService(db_session, engine=engine, llm=FailingLLM())
    with pytest.raises(LLMRateLimitError):
        await failing_service.run_pending_evaluation(
            principal_of(owner), knowledge_base, pending.id
        )

    reloaded = await db_session.get(EvaluationRun, pending.id)
    assert reloaded is not None
    assert reloaded.status == EvaluationRunStatus.FAILED


# ─── Dépense LLM : même régime que le chat (ADR-054) ─────────────────────────
#
# Compteurs réels (`platform_llm_budget`) sur le limiteur en mémoire de `tests/conftest.py`.
# Aucune clé plateforme : c'est la clé serveur qui paie, sous quota.


async def _kb_with_documents(
    db: AsyncSession, owner: UserModel, passages: dict[str, str]
) -> tuple[KnowledgeBase, FakeIngestionEngine]:
    """Base activée, un document éligible par passage, moteur qui les sert."""
    knowledge_base = await enabled_kb(db, owner)
    for external_id in passages:
        await eligible_document(
            db, knowledge_base, filename=f"{external_id}.pdf", external_id=external_id
        )
    engine = FakeIngestionEngine(
        strict=False,
        chunks_by_document={
            external_id: [EngineChunk(chunk_id=external_id, content=text)]
            for external_id, text in passages.items()
        },
    )
    return knowledge_base, engine


def _valid_llm(*passages: str) -> ScriptedLLM:
    return ScriptedLLM(
        generation_by_passage={
            text: generation_json("Question ?", "Réponse.") for text in passages
        },
        judge_response=judge_json(0.7, "Correct."),
    )


async def _used(settings: Settings, user_id: object) -> tuple[int, int]:
    """`(unités d'évaluation du compte, unités du budget global)` imputées aujourd'hui.

    `peek_evaluation_quota`, pas `peek_user_quota` : depuis l'addendum
    2026-09-21 à ADR-054, les évaluations ont leur propre compteur de compte,
    distinct de celui du chat — c'est justement ce que ce module vérifie."""
    return (
        (await peek_evaluation_quota(settings, user_id))[0],
        (await peek_global_budget(settings))[0],
    )


async def test_a_guest_cannot_launch_any_evaluation(db_session: AsyncSession) -> None:
    """Ni synchrone ni asynchrone : refus avant tout appel LLM, sur sa propre base d'essai."""
    guest = factories.make_guest()
    db_session.add(guest)
    await db_session.flush()
    knowledge_base, engine = await _kb_with_documents(db_session, guest, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    service = EvaluationService(db_session, engine=engine, llm=llm)
    payload = EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5")

    with pytest.raises(AccountRequiredError):
        await service.create_run(principal_of(guest), knowledge_base, payload)
    with pytest.raises(AccountRequiredError):
        await service.create_pending_run(principal_of(guest), knowledge_base, payload)

    assert llm.complete_calls == []
    assert llm.stream_calls == []


async def test_access_is_checked_before_any_paid_call(db_session: AsyncSession) -> None:
    """Un compte sans accès à la base n'émet aucun appel — le service vérifie lui-même,
    sans compter sur le routeur (les chemins Celery n'en ont pas)."""
    owner = await account(db_session)
    stranger = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    service = EvaluationService(db_session, engine=engine, llm=llm)

    with pytest.raises(NotFoundError):
        await service.create_run(
            principal_of(stranger),
            knowledge_base,
            EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
        )

    assert llm.complete_calls == []
    assert await _used(Settings(), stranger.id) == (0, 0)


async def test_every_llm_call_of_a_run_is_counted(db_session: AsyncSession) -> None:
    """Un item = génération + réponse du chat + jugement : trois unités, au compte et au global."""
    owner = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    settings = Settings()
    service = EvaluationService(db_session, settings, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
    )

    assert result.item_count == 1
    assert len(llm.complete_calls) + len(llm.stream_calls) == 3
    assert await _used(settings, owner.id) == (3, 3)


async def test_a_generation_that_yields_nothing_still_counts_its_call(
    db_session: AsyncSession,
) -> None:
    """Document ignoré après une génération inexploitable : l'appel a eu lieu, il est compté
    (1 + 3 unités pour un item produit sur deux documents tentés)."""
    owner = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(
        db_session, owner, {"doc-a": PASSAGE_A, "doc-b": PASSAGE_B}
    )
    llm = ScriptedLLM(
        generation_by_passage={
            PASSAGE_A: "pas du JSON",
            PASSAGE_B: generation_json("Q-B ?", "R-B"),
        },
        judge_response=judge_json(0.6, "Correct."),
    )
    settings = Settings()
    service = EvaluationService(db_session, settings, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=2, llm_provider_id="claude-opus-5"),
    )

    assert result.item_count == 1
    assert await _used(settings, owner.id) == (4, 4)


async def test_a_run_on_a_personal_key_consumes_no_quota(db_session: AsyncSession) -> None:
    """BYOK : clé personnelle pour le fournisseur demandé → aucun compteur ne bouge, pas même
    pour la réponse du chat, résolue par la même table de vérité."""
    owner = await account(db_session)
    await UserLlmKeyService(db_session).set_key(owner, "anthropic", "sk-ant-personal-xyz")
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    settings = Settings(platform_llm_evaluation_daily_limit=1)
    await enforce_evaluation_quota(settings, owner.id)  # plafond d'évaluation déjà atteint
    service = EvaluationService(db_session, settings, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=1, llm_provider_id="claude-haiku-4-5"),
    )

    assert result.item_count == 1
    assert await _used(settings, owner.id) == (1, 0)


async def test_an_exhausted_quota_stops_the_run_before_any_llm_call(
    db_session: AsyncSession,
) -> None:
    owner = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    settings = Settings(platform_llm_evaluation_daily_limit=1)
    await enforce_evaluation_quota(settings, owner.id)
    service = EvaluationService(db_session, settings, engine=engine, llm=llm)

    with pytest.raises(TooManyRequestsError):
        await service.create_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
        )

    assert llm.complete_calls == []
    assert llm.stream_calls == []


async def test_a_quota_exhausted_mid_run_keeps_the_items_already_produced(
    db_session: AsyncSession,
) -> None:
    """Plafond de 4 : premier item (3 unités), puis génération du second (4e) ; la réponse du
    chat est refusée → le run s'arrête et garde son item, sans jamais appeler le juge du
    second. Le budget global n'est pas entamé par l'unité refusée."""
    owner = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(
        db_session, owner, {"doc-a": PASSAGE_A, "doc-b": PASSAGE_B}
    )
    llm = _valid_llm(PASSAGE_A, PASSAGE_B)
    settings = Settings(platform_llm_evaluation_daily_limit=4)
    service = EvaluationService(db_session, settings, engine=engine, llm=llm)

    result = await service.create_run(
        principal_of(owner),
        knowledge_base,
        EvaluationCreate(sample_size=2, llm_provider_id="claude-opus-5"),
    )

    assert result.item_count == 1
    assert len(llm.complete_calls) == 3  # génération 1, jugement 1, génération 2
    assert len(llm.stream_calls) == 1
    assert (await peek_global_budget(settings))[0] == 4


async def test_an_async_run_whose_requester_lost_access_fails_without_any_llm_call(
    db_session: AsyncSession,
) -> None:
    """À l'exécution Celery, aucun routeur n'a revérifié l'accès : le service le fait, et le
    run passe en `failed`."""
    owner = await account(db_session)
    former_member = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    run = EvaluationRun(
        knowledge_base_id=knowledge_base.id,
        requested_by_id=former_member.id,
        llm_provider_id="claude-opus-5",
        sample_size=1,
        status=EvaluationRunStatus.PENDING,
    )
    db_session.add(run)
    await db_session.flush()
    llm = _valid_llm(PASSAGE_A)
    service = EvaluationService(db_session, engine=engine, llm=llm)

    with pytest.raises(NotFoundError):
        await service.run_pending_evaluation(principal_of(former_member), knowledge_base, run.id)

    await db_session.refresh(run)
    assert run.status == EvaluationRunStatus.FAILED
    assert llm.complete_calls == []


async def test_an_unreachable_quota_counter_refuses_the_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échec fermé : Redis injoignable → `503`, aucun appel LLM."""

    class _UnreachableLimiter:
        async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
            raise RateLimiterUnavailableError("Connection refused")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(rate_limit_module, "_limiter", _UnreachableLimiter())
    owner = await account(db_session)
    knowledge_base, engine = await _kb_with_documents(db_session, owner, {"doc-a": PASSAGE_A})
    llm = _valid_llm(PASSAGE_A)
    service = EvaluationService(db_session, engine=engine, llm=llm)

    with pytest.raises(ServiceUnavailableError):
        await service.create_run(
            principal_of(owner),
            knowledge_base,
            EvaluationCreate(sample_size=1, llm_provider_id="claude-opus-5"),
        )

    assert llm.complete_calls == []
