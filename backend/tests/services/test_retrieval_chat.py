"""Cas d'usage `RetrievalService.chat` : résolution du fournisseur LLM, réutilisation de
`search()`, construction du prompt système de mise à la terre, préparation du flux de tokens.

`RecordingFakeLLM` est un double minimal de `LLMPort` (le moteur, lui, est le
`FakeIngestionEngine` de `scripted_engine`, `tests/services/test_retrieval.py`) : seule
`stream` est exercée ici, mais elle enregistre les messages reçus pour vérifier l'ordre
de la conversation et le contenu exact du prompt système — `complete()` n'a pas besoin
d'exister pour ces tests,
rien ici ne vérifie `isinstance(..., LLMPort)`.

La matrice de permissions et l'arbitrage du reranking sont déjà verrouillés par
`tests/services/test_retrieval.py` — `chat()` les délègue intégralement à `search()`, ce
module ne revérifie que leur intégration dans l'ordre d'orchestration propre au chat
(résolution du fournisseur LLM → recherche → construction du prompt → appel `stream`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    ValidationError,
)
from app.core.rate_limit import RateLimiterUnavailableError
from app.domain.schemas.principal import AuthMethod
from app.domain.schemas.retrieval import RetrievalChatRequest
from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMError
from app.services.identity.errors import AuthenticationRequiredError
from app.services.identity.platform_llm_budget import (
    enforce_global_budget,
    peek_global_budget,
    peek_user_quota,
)
from app.services.identity.principal import ANONYMOUS, Principal
from app.services.retrieval import RetrievalService
from tests.db import factories
from tests.services.test_knowledge_bases import account, principal_of
from tests.services.test_retrieval import engine_result, scripted_engine, seeded_kb

pytestmark = pytest.mark.usefixtures("explicit_llm_caps")


@dataclass(slots=True)
class RecordingFakeLLM:
    """Double minimal de `LLMPort` : `stream` enregistre les messages reçus et rejoue des
    fragments scénarisés. `capabilities` est un champ figé, jamais lu par `chat()`.

    `complete` sert à `_build_retrieval_query` (reformulation de la requête de
    retrieval sur un deuxième tour ou plus) : `complete_response=None` (défaut)
    lève `NotImplementedError` si appelé sans avoir été scénarisé — un test à
    un seul tour ne doit jamais l'atteindre (`_build_retrieval_query` le
    saute), et un test qui l'atteint sans le scénariser a un trou de
    couverture, pas un comportement à tolérer silencieusement.
    `failure`, s'il est renseigné, est levé par `complete` au lieu de rendre
    `complete_response` — sert à vérifier le repli sur le dernier message."""

    fragments: Sequence[str] = ()
    complete_response: str | None = None
    failure: Exception | None = None
    calls: list[tuple[list[ChatMessage], int]] = field(default_factory=list)
    complete_calls: list[tuple[list[ChatMessage], int]] = field(default_factory=list)
    capabilities: LLMCapabilities = field(
        default_factory=lambda: LLMCapabilities(
            provider_id="recording-fake-llm",
            vendor="anthropic",
            context_window=200_000,
            supports_streaming=True,
        )
    )

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        self.complete_calls.append((list(messages), max_tokens))
        if self.failure is not None:
            raise self.failure
        if self.complete_response is None:
            raise NotImplementedError("Non exercé par ce test : `complete_response` non scénarisé.")
        return self.complete_response

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        self.calls.append((list(messages), max_tokens))
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        for fragment in self.fragments:
            yield fragment


def chat_request(
    knowledge_base_id: object, *, llm_provider_id: str = "claude-opus-5", **overrides: object
) -> RetrievalChatRequest:
    """Charge utile minimale valide, un seul tour utilisateur.

    `llm_provider_id` par défaut est un identifiant **réel** du catalogue
    (`providers/llm/registry.py`) — depuis l'addendum 2026-09-21 à ADR-054,
    `resolve_llm_funding` rejette tout identifiant inconnu avant même de
    décider qui paie (`ValidationError`), y compris quand `llm=` est injecté
    directement (`RetrievalService(..., llm=...)` contourne `resolve_llm_provider`,
    jamais `resolve_llm_funding`). Peu importe lequel : aucun de ces tests
    n'atteint un vrai fournisseur réseau, `llm=`/`resolve_llm_provider` est
    systématiquement remplacé par un double."""
    payload: dict[str, object] = {
        "knowledge_base_id": knowledge_base_id,
        "messages": [{"role": "user", "content": "Quelle est la politique de congés ?"}],
        "llm_provider_id": llm_provider_id,
    } | overrides
    return RetrievalChatRequest.model_validate(payload)


async def test_chat_grounds_the_system_prompt_with_numbered_search_results(
    db_session: AsyncSession,
) -> None:
    """Le prompt système numérote les passages dans l'ordre des résultats de recherche."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    first = factories.make_document(knowledge_base, filename="premier.pdf")
    first.ragflow_document_external_id = "doc-1"
    second = factories.make_document(knowledge_base, filename="second.pdf")
    second.ragflow_document_external_id = "doc-2"
    db_session.add_all([first, second])
    await db_session.flush()
    engine = scripted_engine(
        results=[
            engine_result(document_external_id="doc-1", chunk_id="c1", content="Contenu un"),
            engine_result(document_external_id="doc-2", chunk_id="c2", content="Contenu deux"),
        ]
    )
    llm = RecordingFakeLLM(fragments=("Bon", "jour"))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    stream = await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert [result.filename for result in stream.results] == ["premier.pdf", "second.pdf"]
    fragments = [fragment async for fragment in stream.tokens]
    assert fragments == ["Bon", "jour"]

    assert len(llm.calls) == 1
    messages, max_tokens = llm.calls[0]
    assert max_tokens == 1024  # défaut du schéma
    assert messages[0].role == "system"
    assert "[1] (source : premier.pdf)\nContenu un" in messages[0].content
    assert "[2] (source : second.pdf)\nContenu deux" in messages[0].content
    assert messages[1:] == [ChatMessage(role="user", content="Quelle est la politique de congés ?")]


async def test_chat_grounds_with_an_explicit_notice_when_search_finds_nothing(
    db_session: AsyncSession,
) -> None:
    """Base jamais ingérée : la recherche renvoie une liste vide, le chat continue tout de
    même — le prompt système instruit alors le modèle de dire explicitement qu'il ne sait pas,
    plutôt que de court-circuiter l'appel au LLM."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, dataset_external_id=None)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("Je ne sais pas.",))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    stream = await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert stream.results == []
    assert len(llm.calls) == 1
    system_message = llm.calls[0][0][0]
    assert system_message.role == "system"
    assert "Aucun passage n'a été trouvé" in system_message.content


async def test_chat_passes_the_full_conversation_history_after_the_system_prompt(
    db_session: AsyncSession,
) -> None:
    """L'historique complet (system injecté + tours passés + dernier tour utilisateur) part
    vers `LLMPort.stream`, dans l'ordre — inchangé par la reformulation de la requête de
    recherche, qui ne modifie que ce qui part vers le moteur, jamais la conversation envoyée
    au modèle de génération."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, dataset_external_id=None)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",), complete_response="Relance reformulée")
    service = RetrievalService(db_session, engine=engine, llm=llm)
    payload = chat_request(
        knowledge_base.id,
        messages=[
            {"role": "user", "content": "Première question ?"},
            {"role": "assistant", "content": "Première réponse."},
            {"role": "user", "content": "Question de relance ?"},
        ],
    )

    await service.chat(principal_of(owner), payload)

    messages, _ = llm.calls[0]
    assert [message.role for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[1].content == "Première question ?"
    assert messages[2].content == "Première réponse."
    assert messages[3].content == "Question de relance ?"


async def test_chat_uses_the_last_message_as_is_on_a_first_turn(db_session: AsyncSession) -> None:
    """Un seul tour : rien à condenser, `_build_retrieval_query` ne doit même pas appeler
    `complete` (`RecordingFakeLLM` lèverait sinon, `complete_response` n'étant pas scénarisé)."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert llm.complete_calls == []
    assert engine.search_calls[0].query == "Quelle est la politique de congés ?"


async def test_chat_sends_the_reformulated_query_to_the_search_engine(
    db_session: AsyncSession,
) -> None:
    """Dès le deuxième tour, c'est la requête reformulée — pas le dernier message brut — qui
    part vers le moteur de recherche."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",), complete_response="Congés : combien de jours ?")
    service = RetrievalService(db_session, engine=engine, llm=llm)
    payload = chat_request(
        knowledge_base.id,
        messages=[
            {"role": "user", "content": "Quelle est la politique de congés ?"},
            {"role": "assistant", "content": "Pouvez-vous préciser votre question ?"},
            {"role": "user", "content": "Combien de jours ?"},
        ],
    )

    await service.chat(principal_of(owner), payload)

    assert engine.search_calls[0].query == "Congés : combien de jours ?"
    builder_messages, builder_max_tokens = llm.complete_calls[0]
    assert builder_max_tokens == 200
    assert builder_messages[0].role == "system"
    assert "Combien de jours ?" in builder_messages[1].content
    assert "Quelle est la politique de congés ?" in builder_messages[1].content
    assert "Pouvez-vous préciser votre question ?" in builder_messages[1].content


async def test_chat_falls_back_to_the_last_message_when_query_building_fails(
    db_session: AsyncSession,
) -> None:
    """Un fournisseur LLM en panne sur la reformulation ne doit jamais faire échouer le tour de
    chat : repli silencieux sur le dernier message, comportement d'avant ce correctif."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",), failure=LLMError("En panne"))
    service = RetrievalService(db_session, engine=engine, llm=llm)
    payload = chat_request(
        knowledge_base.id,
        messages=[
            {"role": "user", "content": "Première question ?"},
            {"role": "user", "content": "Et pour ce cas précis ?"},
        ],
    )

    stream = await service.chat(principal_of(owner), payload)

    assert [fragment async for fragment in stream.tokens] == ["réponse"]
    assert engine.search_calls[0].query == "Et pour ce cas précis ?"


async def test_chat_falls_back_to_the_last_message_when_the_builder_returns_blank(
    db_session: AsyncSession,
) -> None:
    """Une réponse vide (ou blanche) du modèle de reformulation n'est pas une requête valable :
    même repli que sur une panne, jamais une recherche sur une chaîne vide."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",), complete_response="   ")
    service = RetrievalService(db_session, engine=engine, llm=llm)
    payload = chat_request(
        knowledge_base.id,
        messages=[
            {"role": "user", "content": "Première question ?"},
            {"role": "user", "content": "Et pour ce cas précis ?"},
        ],
    )

    await service.chat(principal_of(owner), payload)

    assert engine.search_calls[0].query == "Et pour ce cas précis ?"


async def test_chat_bounds_the_history_window_sent_to_the_query_builder(
    db_session: AsyncSession,
) -> None:
    """La fenêtre d'historique de `_build_retrieval_query` est bornée
    (`_QUERY_BUILDER_HISTORY_MESSAGES`) : un très long historique ne doit pas grossir sans
    limite le prompt de reformulation."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("réponse",), complete_response="requête")
    service = RetrievalService(db_session, engine=engine, llm=llm)
    turns = [{"role": "user", "content": "Message trop ancien pour compter"}]
    for index in range(4):
        turns.append({"role": "assistant", "content": f"Réponse {index}"})
        turns.append({"role": "user", "content": f"Question {index}"})
    payload = chat_request(knowledge_base.id, messages=turns)

    await service.chat(principal_of(owner), payload)

    builder_prompt = llm.complete_calls[0][0][1].content
    # Les 8 tours précédant le dernier dépassent la fenêtre de 6 : seuls les 6
    # plus récents (à partir de « Question 0 ») doivent apparaître.
    assert "Message trop ancien pour compter" not in builder_prompt
    assert "Réponse 0" not in builder_prompt
    assert "Question 0" in builder_prompt
    assert "Réponse 3" in builder_prompt
    assert "Question 3" in builder_prompt  # dernier message, toujours présent


async def test_chat_resolves_the_llm_provider_before_calling_search(
    db_session: AsyncSession,
) -> None:
    """`llm_provider_id` inconnu : `ValidationError`, et le moteur n'est jamais dérangé — la
    résolution du fournisseur LLM précède la recherche (aucune injection `llm=` ici, le
    service retombe donc sur le vrai `resolve_llm_provider`, sans réseau : c'est une pure
    lecture de registre + `Settings`, cf. `tests/providers/llm/test_registry.py`)."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-jamais-atteint")])
    service = RetrievalService(db_session, engine=engine)

    with pytest.raises(ValidationError, match="inconnu"):
        await service.chat(
            principal_of(owner), chat_request(knowledge_base.id, llm_provider_id="does-not-exist")
        )

    assert engine.search_calls == []


async def test_chat_prefers_the_caller_s_personal_llm_key_over_the_server_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clé LLM personnelle (`UserLlmKeyService`) : passée à `resolve_llm_provider` en
    `override_api_key` dès qu'elle existe pour le fournisseur demandé — avant même de savoir
    si une clé serveur est configurée."""
    import app.services.retrieval as retrieval_module
    from app.services.identity.user_llm_keys import UserLlmKeyService

    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    await UserLlmKeyService(db_session).set_key(owner, "anthropic", "sk-ant-personal-xyz")
    await db_session.commit()

    captured: dict[str, object] = {}

    def _spy(provider_id: str, settings: object, **kwargs: object) -> RecordingFakeLLM:
        captured["provider_id"] = provider_id
        captured["override_api_key"] = kwargs.get("override_api_key")
        return RecordingFakeLLM(fragments=("ok",))

    monkeypatch.setattr(retrieval_module, "resolve_llm_provider", _spy)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, llm_provider_id="claude-opus-5")
    )

    assert captured == {"provider_id": "claude-opus-5", "override_api_key": "sk-ant-personal-xyz"}


async def test_chat_without_a_personal_key_leaves_override_api_key_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-régression explicite : aucune clé personnelle déclarée → `override_api_key=None`,
    exactement le comportement d'avant l'introduction de cette fonctionnalité."""
    import app.services.retrieval as retrieval_module

    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)

    captured: dict[str, object] = {}

    def _spy(provider_id: str, settings: object, **kwargs: object) -> RecordingFakeLLM:
        captured["override_api_key"] = kwargs.get("override_api_key")
        return RecordingFakeLLM(fragments=("ok",))

    monkeypatch.setattr(retrieval_module, "resolve_llm_provider", _spy)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, llm_provider_id="claude-opus-5")
    )

    assert captured == {"override_api_key": None}


# ─── Quotas hors clé personnelle (ADR-047, ADR-054) ─────────────────────────
#
# Les compteurs sont les vrais `enforce_*`/`peek_*` de `platform_llm_budget`, adossés au
# limiteur en mémoire que `tests/conftest.py` installe pour chaque test — aucun quota n'est
# simulé ici. Le double du port LLM (`RecordingFakeLLM`) est rendu par un faux
# `resolve_llm_provider` : il prouve qu'aucun appel `complete`/`stream` n'est émis quand un
# plafond refuse le tour.
#
# Table de vérité (ADR-054) : appelant et source de clé → compteur d'identité + global.


def _two_turn_messages() -> list[dict[str, str]]:
    """Deuxième tour : `_build_retrieval_query` appelle `complete` pour reformuler."""
    return [
        {"role": "user", "content": "Quelle est la politique de congés ?"},
        {"role": "assistant", "content": "Pour quelle population ?"},
        {"role": "user", "content": "Et pour les cadres ?"},
    ]


def _resolver_returning(
    monkeypatch: pytest.MonkeyPatch, llm: RecordingFakeLLM
) -> dict[str, object]:
    """Remplace `resolve_llm_provider` par un espion qui rend `llm` et note ses arguments."""
    import app.services.retrieval as retrieval_module

    captured: dict[str, object] = {}

    def _spy(provider_id: str, settings: object, **kwargs: object) -> RecordingFakeLLM:
        captured["provider_id"] = provider_id
        captured["override_api_key"] = kwargs.get("override_api_key")
        return llm

    monkeypatch.setattr(retrieval_module, "resolve_llm_provider", _spy)
    return captured


async def _usage(settings: Settings, *, user_id: object = None) -> dict[str, int]:
    """Appels imputés aujourd'hui, par compteur — lecture seule (`peek`)."""
    usage = {"global": (await peek_global_budget(settings))[0]}
    if user_id is not None:
        usage["user"] = (await peek_user_quota(settings, user_id))[0]
    return usage


async def test_chat_on_the_server_key_is_under_quota_when_no_platform_key_is_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-054, constat 2 de l'audit : clé plateforme absente (le défaut) → clé serveur `.env`,
    **et** quota appliqué. Avant ce correctif, ce chemin ne comptait rien."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings()
    _resolver_returning(monkeypatch, RecordingFakeLLM(fragments=("ok",)))
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, llm_provider_id="claude-opus-5")
    )

    assert await _usage(settings, user_id=owner.id) == {"global": 1, "user": 1}


async def test_chat_on_the_server_key_also_forces_the_model_since_the_addendum(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Addendum 2026-09-21 à ADR-054 : clé plateforme absente → clé serveur `.env`, mais le
    modèle demandé (`claude-opus-5`) est tout de même remplacé par `platform_llm_model_id` —
    exactement comme sur la clé plateforme. Avant l'addendum, ce chemin laissait l'appelant
    choisir n'importe quel modèle sous le même plafond de *messages*."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings(platform_llm_model_id="claude-haiku-4-5")
    captured = _resolver_returning(monkeypatch, RecordingFakeLLM(fragments=("ok",)))
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, llm_provider_id="claude-opus-5")
    )

    assert captured == {"provider_id": "claude-haiku-4-5", "override_api_key": None}


async def test_chat_forces_the_model_for_an_api_key_caller_without_a_personal_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Table de vérité, ligne « Clé API » : même règle que la session, sans clé personnelle."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings(platform_llm_model_id="claude-haiku-4-5")
    captured = _resolver_returning(monkeypatch, RecordingFakeLLM(fragments=("ok",)))
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        Principal(auth_method=AuthMethod.API_KEY, user=owner),
        chat_request(knowledge_base.id, llm_provider_id="claude-opus-5"),
    )

    assert captured == {"provider_id": "claude-haiku-4-5", "override_api_key": None}


async def test_chat_on_the_server_key_stops_before_any_llm_call_once_the_quota_is_exhausted(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans clé plateforme, plafond atteint : `429` avant toute émission vers le port LLM —
    ni `complete` (reformulation), ni `stream` — et sans déranger le moteur."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings(platform_llm_user_daily_limit=1)
    llm = RecordingFakeLLM(fragments=("ok",), complete_response="congés des cadres")
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)
    await service.chat(principal_of(owner), chat_request(knowledge_base.id))
    llm.calls.clear()
    engine.search_calls.clear()

    with pytest.raises(TooManyRequestsError, match="Plafond quotidien"):
        await service.chat(
            principal_of(owner),
            chat_request(knowledge_base.id, messages=_two_turn_messages()),
        )

    assert llm.complete_calls == []
    assert llm.calls == []
    assert engine.search_calls == []


async def test_chat_with_a_personal_key_consumes_no_quota_even_when_every_cap_is_reached(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BYOK : seule source de clé sans quota. Budget global déjà épuisé, deux tours (donc
    reformulation) — le tour passe, aucun compteur ne bouge."""
    from app.services.identity.user_llm_keys import UserLlmKeyService

    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    await UserLlmKeyService(db_session).set_key(owner, "anthropic", "sk-ant-personal-xyz")
    settings = Settings(
        platform_llm_daily_budget_usd=0.01,
        platform_llm_assumed_cost_per_message_usd=0.01,
    )
    await enforce_global_budget(settings)  # plafond global (1) atteint
    llm = RecordingFakeLLM(fragments=("ok",), complete_response="congés des cadres")
    captured = _resolver_returning(monkeypatch, llm)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        principal_of(owner),
        chat_request(
            knowledge_base.id, llm_provider_id="claude-opus-5", messages=_two_turn_messages()
        ),
    )

    assert captured == {"provider_id": "claude-opus-5", "override_api_key": "sk-ant-personal-xyz"}
    assert len(llm.complete_calls) == 1
    assert len(llm.calls) == 1
    assert await _usage(settings, user_id=owner.id) == {"global": 1, "user": 0}


async def test_chat_through_an_api_key_without_a_personal_key_is_under_the_account_quota(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Appelant par clé API : `principal.user` est le propriétaire de la clé, imputé comme un
    compte réel sans clé personnelle."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings()
    _resolver_returning(monkeypatch, RecordingFakeLLM(fragments=("ok",)))
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        Principal(auth_method=AuthMethod.API_KEY, user=owner),
        chat_request(knowledge_base.id),
        client_ip="203.0.113.42",
    )

    assert await _usage(settings, user_id=owner.id) == {"global": 1, "user": 1}


async def test_chat_counts_the_query_reformulation_as_a_second_llm_call(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-054 : sur un deuxième tour, `complete` (reformulation, ADR-049) puis `stream`
    (génération) — deux appels LLM, deux unités imputées à chaque compteur."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings()
    llm = RecordingFakeLLM(fragments=("ok",), complete_response="congés des cadres")
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, messages=_two_turn_messages())
    )

    assert len(llm.complete_calls) == 1
    assert len(llm.calls) == 1
    assert await _usage(settings, user_id=owner.id) == {"global": 2, "user": 2}


async def test_chat_does_not_count_a_reformulation_that_never_calls_the_llm(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Premier tour : `_build_retrieval_query` n'appelle pas `complete` — une seule unité."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings()
    llm = RecordingFakeLLM(fragments=("ok",))
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert llm.complete_calls == []
    assert await _usage(settings, user_id=owner.id) == {"global": 1, "user": 1}


async def test_chat_skips_the_reformulation_when_only_the_generation_fits_in_the_quota(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une seule unité restante sur un deuxième tour : la génération passe, la reformulation
    est sautée (repli sur le dernier message, comme sur une panne) — jamais un appel non
    imputé. Le budget global n'est pas entamé par l'unité refusée."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings(platform_llm_user_daily_limit=1)
    llm = RecordingFakeLLM(fragments=("ok",), complete_response="ne doit pas servir")
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(
        principal_of(owner), chat_request(knowledge_base.id, messages=_two_turn_messages())
    )

    assert llm.complete_calls == []
    assert len(llm.calls) == 1
    assert [call.query for call in engine.search_calls] == ["Et pour les cadres ?"]
    assert (await _usage(settings, user_id=owner.id))["global"] == 1


async def test_chat_an_exhausted_identity_does_not_drain_the_global_budget(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compteur d'identité avant budget global : un appelant qui insiste au-delà de son plafond
    n'incrémente plus le compteur global partagé par tous."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings(platform_llm_user_daily_limit=1)
    _resolver_returning(monkeypatch, RecordingFakeLLM(fragments=("ok",)))
    engine = scripted_engine(results=[engine_result(document_external_id="doc-1")])
    service = RetrievalService(db_session, settings, engine=engine)

    await service.chat(principal_of(owner), chat_request(knowledge_base.id))
    for _ in range(3):
        with pytest.raises(TooManyRequestsError):
            await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert (await _usage(settings, user_id=owner.id))["global"] == 1


async def test_chat_refuses_access_before_counting_or_calling_the_llm(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Base privée, anonyme, deuxième tour : refus d'accès **avant** la reformulation. Avant
    ADR-054, `complete` partait vers le fournisseur puis `search()` refusait l'accès — un
    appel payé par requête, sur n'importe quel identifiant de base."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    settings = Settings()
    llm = RecordingFakeLLM(fragments=("jamais",), complete_response="jamais")
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine()
    service = RetrievalService(db_session, settings, engine=engine)

    with pytest.raises((NotFoundError, AuthenticationRequiredError)):
        await service.chat(
            ANONYMOUS,
            chat_request(knowledge_base.id, messages=_two_turn_messages()),
            client_ip="203.0.113.42",
        )

    assert llm.complete_calls == []
    assert llm.calls == []
    assert await _usage(settings) == {"global": 0}


async def test_chat_refuses_with_a_503_when_the_quota_counter_is_unreachable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échec fermé : Redis injoignable sur un appel hors clé personnelle → `503`, aucun appel
    LLM. Laisser passer rouvrirait la dépense sans plafond."""
    from app.core import rate_limit as rate_limit_module

    class _UnreachableLimiter:
        async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
            raise RateLimiterUnavailableError("Connection refused")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(rate_limit_module, "_limiter", _UnreachableLimiter())
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    llm = RecordingFakeLLM(fragments=("jamais",))
    _resolver_returning(monkeypatch, llm)
    engine = scripted_engine()
    service = RetrievalService(db_session, Settings(), engine=engine)

    with pytest.raises(ServiceUnavailableError):
        await service.chat(principal_of(owner), chat_request(knowledge_base.id))

    assert llm.calls == []
    assert engine.search_calls == []


async def test_chat_propagates_search_errors_like_reranking_without_a_configured_reranker(
    db_session: AsyncSession,
) -> None:
    """`chat()` ne réimplémente pas la logique de `search()` : ses erreurs (ici, reranking
    sans reranker) remontent telles quelles, avant tout appel au fournisseur LLM."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id=None)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("jamais atteint",))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    with pytest.raises(ValidationError):
        await service.chat(principal_of(owner), chat_request(knowledge_base.id, rerank=True))

    assert llm.calls == []


async def test_chat_propagates_the_rerank_and_threshold_guard_from_search(
    db_session: AsyncSession,
) -> None:
    """ADR-026 : le garde-fou rerank effectif + seuil non nul, porté par `search()`,
    s'applique aussi au chat via la réutilisation interne — pas de logique dupliquée."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner, reranker_model_id="bge-reranker-v2-m3")
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("jamais atteint",))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    with pytest.raises(ValidationError):
        await service.chat(
            principal_of(owner),
            chat_request(knowledge_base.id, rerank=True, similarity_threshold=0.2),
        )

    assert llm.calls == []
    assert engine.search_calls == []


async def test_chat_refuses_an_anonymous_visitor_on_a_private_base(
    db_session: AsyncSession,
) -> None:
    """Matrice d'accès du cœur : un anonyme visant une base privée ne la voit pas."""
    owner = await account(db_session)
    knowledge_base = await seeded_kb(db_session, owner)
    engine = scripted_engine()
    llm = RecordingFakeLLM(fragments=("jamais atteint",))
    service = RetrievalService(db_session, engine=engine, llm=llm)

    with pytest.raises(NotFoundError):
        await service.chat(ANONYMOUS, chat_request(knowledge_base.id))

    assert llm.calls == []
    assert engine.search_calls == []
