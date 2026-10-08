"""Cas d'usage du retrieval : recherche hybride et chat génératif sur une base.

`POST /api/v1/retrieval/search` et `POST /api/v1/retrieval/chat` sont des
endpoints **racines** (CONTRIBUTING.md §4) : la base de connaissances arrive dans le
corps de la requête (`RetrievalFilters.knowledge_base_id`), pas dans le
chemin — `require_kb_permission` (qui suppose un `kb_id` de chemin) ne peut
donc pas s'y monter. L'accès est résolu ici, directement via
`PermissionService`, exactement comme le font déjà `DocumentService.resolve_access`
et `IngestionJobService.resolve_access` pour la même raison.

Aucun filtrage ni scoring n'est réimplémenté ici (ADR-001) : le filtre par
documents part dans l'appel `search()` du moteur lui-même — jamais un `WHERE`
post-hoc sur les résultats déjà rendus. `chat()` ne réimplémente pas non plus
la recherche : il construit une `RetrievalSearchRequest` et appelle `search()`
tel quel (Phase 7).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import TooManyRequestsError, ValidationError
from app.db.models import Document
from app.domain.enums import MemberRole
from app.domain.schemas.retrieval import (
    ChatTurn,
    RetrievalChatRequest,
    RetrievalFilters,
    RetrievalResult,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.providers.engine import resolve_ingestion_engine
from app.providers.engine.base import (
    EngineDatasetRef,
    EngineDocumentRef,
    EngineSearchResult,
    IngestionEnginePort,
)
from app.providers.llm.base import ChatMessage, LLMError, LLMPort
from app.providers.llm.registry import resolve_llm_provider
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.factory import get_observability
from app.services.identity.permissions import KnowledgeBaseAuthorizer, PermissionService
from app.services.identity.platform_llm_budget import LlmQuotaScope, charge_llm_call
from app.services.identity.principal import Principal
from app.services.llm_funding import get_llm_funding_policy

_logger = logging.getLogger(__name__)


def _has_document_filters(payload: RetrievalFilters) -> bool:
    """Un filtre par documents (explicite ou descriptif) est-il demandé ?

    Évite d'ouvrir une requête `Document` supplémentaire quand rien ne la
    justifie — le cas le plus fréquent (aucun filtre) reste aussi rapide
    qu'avant l'ajout de `languages`/`content_types`/`themes`/`ingested_after`/
    `ingested_before`.
    """
    return (
        payload.document_ids is not None
        or bool(payload.languages)
        or bool(payload.content_types)
        or bool(payload.themes)
        or payload.ingested_after is not None
        or payload.ingested_before is not None
    )


_RERANK_WITHOUT_RERANKER: Final = (
    "Reranking demandé mais aucun reranker n'est configuré sur cette base."
)

_RERANK_WITH_UNACKNOWLEDGED_THRESHOLD: Final = (
    "Combiner le reranking et un seuil de similarité non nul dégrade fortement "
    "les résultats (mesuré : recall@5 chute de 0,889 à 0,593, Phase 12 Étape 2 "
    "du plan interne). Posez `acknowledge_threshold_risk=true` pour confirmer "
    "ce choix, ou laissez `similarity_threshold` à `null`/`0.0`."
)

_CHAT_SYSTEM_PROMPT_HEADER: Final = (
    "Tu es l'assistant documentaire de RAGEngine. Réponds exclusivement à partir "
    "des passages numérotés ci-dessous, qui proviennent de la base de "
    "connaissances interrogée. Cite chaque affirmation par le numéro du passage "
    "qui la justifie, entre crochets, à la fin de la phrase et avant la "
    "ponctuation (par exemple [2]). Pour citer plusieurs passages, répète les "
    "crochets ([2][5]) : jamais de virgule dans un même crochet ([2, 5] est "
    "interdit). Au plus 4 citations par phrase. Cite les données chiffrées, "
    "dates, relations de cause à effet, comparaisons, définitions techniques, "
    "attributions directes et affirmations contestées ou prédictives ; ne cite "
    "jamais les évidences, les transitions, ta propre synthèse, ni le bruit "
    "documentaire (en-tête, pied de page, numéro de page répété) même s'il "
    "apparaît dans un passage. Si les passages fournis ne permettent pas de "
    "répondre à la question, dis-le explicitement plutôt que d'inventer une "
    "réponse : n'utilise jamais de connaissance extérieure à ces passages."
)

_CHAT_NO_PASSAGES_NOTICE: Final = (
    "Aucun passage n'a été trouvé pour cette question dans la base interrogée. "
    "Indique-le explicitement à l'utilisateur plutôt que de répondre à partir "
    "d'une autre source."
)

_QUERY_BUILDER_HISTORY_MESSAGES: Final = 6
"""Fenêtre d'historique fournie à la reformulation de requête (jusqu'à 3
échanges). Bornée volontairement : condenser toute la conversation ferait
grossir prompt et coût sans bénéfice au-delà des tours récents, les seuls
pertinents pour comprendre un message qui en complète ou précise un autre."""

_QUERY_BUILDER_MAX_TOKENS: Final = 200
"""Une requête de recherche tient largement dans ce budget ; volontairement
étroit pour qu'un dérapage du modèle (il se met à répondre plutôt que
reformuler) coûte peu et se remarque vite."""

_QUERY_BUILDER_SYSTEM_PROMPT: Final = (
    "Tu prépares la requête envoyée à un moteur de recherche documentaire, à "
    "partir d'un échange avec un utilisateur. Un historique récent peut "
    "précéder le dernier message.\n\n"
    "Si le dernier message est déjà compréhensible seul comme une question ou "
    "une demande de recherche, renvoie-le tel quel, mot pour mot.\n\n"
    "S'il ne l'est pas — message vague, qui complète ou précise une question "
    "précédente, référence implicite (« et pour... », « plus précisément... », "
    "pronom sans antécédent clair dans le message lui-même) — combine-le avec "
    "juste ce qu'il faut de l'historique pour former une requête autonome et "
    "compréhensible seule.\n\n"
    "Règles strictes : ne réponds jamais à la question ; n'ajoute aucun fait, "
    "mot-clé ou nuance qui ne provient pas des messages fournis ; ne change "
    "jamais le sens ni l'intention du dernier message ; conserve sa langue. "
    "Réponds uniquement avec la requête elle-même, sans préfixe, guillemets ni "
    "mise en forme."
)


def _query_rewrite_needed(messages: Sequence[ChatTurn]) -> bool:
    """`_build_retrieval_query` appellera-t-il le LLM ? Seulement à partir du deuxième tour.

    Seule source de vérité de cette condition : `RetrievalService.chat` s'en sert
    pour imputer l'appel de reformulation au quota **avant** qu'il ait lieu, et
    uniquement quand il aura lieu (ADR-054).
    """
    return len(messages) > 1


async def _build_retrieval_query(llm: LLMPort, messages: Sequence[ChatTurn]) -> str:
    """Construit la requête de recherche à partir du dernier tour et, si besoin, de l'historique.

    Un seul appel `LLMPort.complete`, jamais de streaming : la requête de
    recherche n'a pas besoin d'être affichée au fil de l'eau, et la réponse
    tient en quelques mots. Rien à condenser sur un premier message
    (`_query_rewrite_needed`) : l'appel serait purement coûteux pour reproduire
    ce que `messages[-1].content` donne déjà — sauté, comme avant ce
    correctif.

    Volontairement tolérant à l'échec : un fournisseur LLM en panne ou trop
    lent sur cette étape ne doit jamais faire échouer tout le tour de chat,
    seulement dégrader vers le comportement d'avant ce correctif (dernier
    message pris tel quel). Idem si le modèle rend une réponse vide.
    """
    last_turn = messages[-1]
    if not _query_rewrite_needed(messages):
        return last_turn.content

    history = messages[max(0, len(messages) - 1 - _QUERY_BUILDER_HISTORY_MESSAGES) : -1]
    transcript = "\n".join(f"[{turn.role}] {turn.content}" for turn in history)
    prompt = (
        f"Historique récent :\n{transcript}\n\n"
        f"Dernier message de l'utilisateur :\n{last_turn.content}"
    )
    try:
        candidate = await llm.complete(
            [
                ChatMessage(role="system", content=_QUERY_BUILDER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ],
            max_tokens=_QUERY_BUILDER_MAX_TOKENS,
        )
    except LLMError:
        _logger.warning(
            "Reformulation de la requête de retrieval échouée, repli sur le dernier message",
            exc_info=True,
        )
        return last_turn.content

    candidate = candidate.strip()
    return candidate if candidate else last_turn.content


@dataclass(slots=True)
class ChatStream:
    """Résultat de `RetrievalService.chat`, prêt à streamer.

    `results` est déjà résolu : la recherche a eu lieu avant que ce résultat ne
    soit construit, le routeur peut donc émettre l'évènement SSE `sources`
    avant même de commencer à itérer `tokens`. `tokens` reste un itérateur
    asynchrone **paresseux** — produit par `LLMPort.stream`, il n'entame aucun
    appel réseau tant qu'il n'est pas itéré (vérifié sur les adaptateurs Fake,
    Anthropic et OpenAI : `stream()` ne fait qu'instancier un générateur async,
    dont le corps ne s'exécute qu'au premier `__anext__`). Toute erreur de
    résolution (accès, reranking, fournisseur LLM inconnu ou non configuré) a
    donc déjà été levée, en tant qu'exception normale, avant que ce dataclass
    n'existe.
    """

    results: list[RetrievalResult]
    tokens: AsyncIterator[str]


def _build_grounding_prompt(results: Sequence[RetrievalResult]) -> str:
    """Prompt système de mise à la terre RAG : passages numérotés, jamais d'invention.

    La numérotation (1-based, dans l'ordre de `results`) est stable et
    correspond exactement à l'ordre des `RetrievalResult` de l'évènement SSE
    `sources` — les citations `[n]` produites par le modèle pointent donc vers
    la bonne source côté client, sans recalcul ni ambiguïté.
    """
    if not results:
        return f"{_CHAT_SYSTEM_PROMPT_HEADER}\n\n{_CHAT_NO_PASSAGES_NOTICE}"
    passages = "\n\n".join(
        f"[{index}] (source : {result.filename})\n{result.content}"
        for index, result in enumerate(results, start=1)
    )
    return f"{_CHAT_SYSTEM_PROMPT_HEADER}\n\nPassages disponibles :\n\n{passages}"


class RetrievalService:
    """Cas d'usage portant sur `POST /api/v1/retrieval/search` et `/retrieval/chat`."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        *,
        engine: IngestionEnginePort | None = None,
        llm: LLMPort | None = None,
        observability: ObservabilityPort | None = None,
    ) -> None:
        """Construit le service.

        `engine` est résolu une seule fois ici (comme `resolve_ingestion_engine`
        n'a besoin que de `Settings`, indépendant de toute requête). `llm` ne
        peut pas suivre le même schéma : le fournisseur de génération dépend de
        `RetrievalChatRequest.llm_provider_id`, connu seulement au moment de
        l'appel à `chat()` — `llm` n'existe donc que pour l'injection de tests
        (contourner le registre), `None` laissant `chat()` résoudre lui-même via
        `resolve_llm_provider`. `observability` suit le même schéma qu'`engine` :
        résolu une seule fois ici via `get_observability` (déjà mémoïsé par
        `Settings`, `providers/observability/factory.py`), `None` n'existant que
        pour l'injection d'un double de test (`FakeObservability`).
        """
        self._db = db
        self._settings = settings if settings is not None else get_settings()
        self._engine = engine if engine is not None else resolve_ingestion_engine(self._settings)
        self._llm = llm
        self._observability = (
            observability if observability is not None else get_observability(self._settings)
        )

    async def search(
        self,
        principal: Principal,
        payload: RetrievalSearchRequest,
        *,
        authorizer: KnowledgeBaseAuthorizer | None = None,
    ) -> RetrievalSearchResponse:
        """Résout l'accès, arbitre le reranking, filtre par documents, interroge le moteur.

        Ordre précis : la matrice §4 tranche d'abord (une base invisible ne doit
        rien laisser fuiter, pas même l'existence d'un reranker) ; le défaut de
        `rerank` se résout ensuite, seulement une fois la base connue (ADR-026:
        `rerank=None` devient `True` si `knowledge_base.reranker_model_id is
        not None`, sinon `False` — un `rerank` explicite, `True` ou `False`,
        est toujours respecté tel quel) ; le reranking **effectif** sans
        reranker configuré est ensuite refusé explicitement plutôt que
        d'échouer côté moteur ou de reranker en silence (ne peut plus se
        produire qu'avec un `rerank=true` explicite sur une base sans
        reranker : la résolution du défaut ne peut jamais produire cette
        combinaison elle-même) ; la garde sur la combinaison reranking effectif
        + seuil de similarité non nul (ADR-026) suit, avant tout appel moteur ;
        une base jamais ingérée, ou dont aucun document demandé n'est encore
        chunké, répond une liste vide **sans** appeler le moteur. Il en va de
        même si `languages`/`content_types`/`themes`/`ingested_after`/`ingested_before`
        n'excluent aucun document valable : ce sont des filtres descriptifs,
        jamais une citation d'objets précis — seul `document_ids` lève une
        erreur sur un identifiant absent de la base (`_require_documents_exist`).

        La résolution d'accès a lieu **avant** l'ouverture du span
        `observe_retrieval` : un principal sans accès n'a rien exécuté qui
        vaille la peine d'être tracé comme un « retrieval », et cela permet
        d'ouvrir le span avec le `rerank` **effectif** (ADR-026) plutôt que la
        valeur brute reçue — plus utile en télémétrie, mais connue seulement
        une fois la base résolue.

        Raises:
            NotFoundError: base inexistante, ou invisible pour ce principal.
            AuthenticationRequiredError: principal anonyme visant une base
                privée ou une action réservée.
            ForbiddenError: compte authentifié dont le rôle est insuffisant.
            ValidationError: reranking effectif sans reranker configuré sur la
                base ; reranking effectif combiné à un `similarity_threshold`
                non nul sans `acknowledge_threshold_risk` (ADR-026) ;
                `document_ids` citant un document hors de cette base.

        Encadrée par `ObservabilityPort.observe_retrieval` : `chat()` appelle
        cette méthode en interne plutôt que de réimplémenter la recherche
        (docstring de tête du module), donc l'encadrer ici couvre aussi `chat()`
        sans double-comptage.

        `authorizer` remplace la matrice d'accès par défaut (`PermissionService`)
        pour un appelant qui a déjà prouvé une capacité propre — un greffon,
        jamais un routeur du cœur. Il ne peut qu'**ajouter** un chemin d'accès en
        lecture : la résolution reste faite ici, avant toute autre étape.
        """
        access = await self._authorizer(authorizer).resolve_access(
            principal, payload.knowledge_base_id, minimum=MemberRole.VIEWER
        )
        knowledge_base = access.knowledge_base
        effective_rerank = (
            payload.rerank
            if payload.rerank is not None
            else knowledge_base.reranker_model_id is not None
        )
        # `similarity_threshold=None` ne veut pas dire « pas de seuil » une fois
        # le reranking effectif : RAGFlow applique alors **son propre** défaut
        # serveur, calibré pour l'échelle du score vectoriel seul, contre le
        # score combiné (vecteur + reranker) qui n'a plus la même échelle — le
        # même changement d'échelle que documente déjà la garde ci-dessous pour
        # un seuil *explicite*, sauf que là il est invisible : aucune erreur,
        # juste une liste vide, y compris pour un document dont le texte
        # correspond mot pour mot à la requête (vérifié empiriquement, campagne
        # E2E — `similarity_threshold=None` + reranking effectif a rendu 0
        # résultat sur une requête dont `rerank=false` en rend 3, le bon document
        # en tête). `0.0` est un seuil explicite qui n'exclut rien : il court-
        # circuite le défaut de RAGFlow sans changer le comportement pour
        # l'appelant, qui n'a jamais rien demandé de plus strict que « aucun ».
        effective_similarity_threshold = (
            payload.similarity_threshold
            if payload.similarity_threshold is not None
            else (0.0 if effective_rerank else None)
        )

        with self._observability.observe_retrieval(
            knowledge_base_id=payload.knowledge_base_id,
            query=payload.query,
            top_k=payload.top_k,
            rerank=effective_rerank,
        ):
            if effective_rerank and knowledge_base.reranker_model_id is None:
                raise ValidationError(_RERANK_WITHOUT_RERANKER)

            if (
                effective_rerank
                and payload.similarity_threshold is not None
                and payload.similarity_threshold != 0.0
                and not payload.acknowledge_threshold_risk
            ):
                raise ValidationError(_RERANK_WITH_UNACKNOWLEDGED_THRESHOLD)

            dataset_external_id = knowledge_base.ragflow_dataset_external_id
            if dataset_external_id is None:
                return self._empty()

            document_refs: list[EngineDocumentRef] | None = None
            if _has_document_filters(payload):
                document_refs = await self._resolve_document_refs(knowledge_base.id, payload)
                if not document_refs:
                    return self._empty()

            results = await self._engine.search(
                datasets=[EngineDatasetRef(external_id=dataset_external_id)],
                query=payload.query,
                top_k=payload.top_k,
                rerank_provider_id=knowledge_base.reranker_model_id if effective_rerank else None,
                similarity_threshold=effective_similarity_threshold,
                document_refs=document_refs,
                rerank_candidate_pool_size=(
                    self._settings.rerank_candidate_pool_size if effective_rerank else None
                ),
            )
            return await self._to_response(results)

    async def chat(
        self,
        principal: Principal,
        payload: RetrievalChatRequest,
        *,
        authorizer: KnowledgeBaseAuthorizer | None = None,
        quota_scope: LlmQuotaScope | None = None,
        via_evaluation: bool = False,
        client_ip: str | None = None,
    ) -> ChatStream:
        """Vérifie l'accès, impute les appels LLM au quota, réutilise `search()`, prépare le flux.

        `via_evaluation` (addendum 2026-09-21 à ADR-054) : posé par
        `EvaluationService._generate_answer`, qui rejoue le Playground pour
        noter la réponse d'un item — impute alors au compteur **dédié aux
        évaluations** (`QuotaSubject.EVALUATION`) plutôt qu'au compteur de
        chat (`QuotaSubject.USER`) du même compte (`LlmFundingPolicy.quota_scope`).

        `authorizer` et `quota_scope` (ADR-069) : points d'extension d'un
        greffon qui sert une capacité propre — accès par un lien partagé,
        compteur dédié à ce lien. Absents, la matrice d'accès et le compteur de
        la politique de financement active s'appliquent.

        Tout ce qui peut échouer se produit ici, en dehors de tout générateur :
        le routeur peut laisser une exception se propager normalement (→ Problem
        Details RFC 9457) tant qu'il n'a pas commencé à itérer `ChatStream.tokens`
        au premier `yield` de sa réponse SSE (`docstring` de `ChatStream`).

        Ordre, chaque étape avant la suivante (ADR-054) :

        1. **Accès à la base.** Avant tout le reste : un appelant sans accès ne
           doit ni consommer de quota ni déclencher d'appel LLM. Sans ce
           contrôle préalable, la reformulation (étape 5) partait vers le
           fournisseur *avant* que `search()` ne refuse l'accès — un anonyme
           pouvait faire payer un appel par requête sur un identifiant de base
           quelconque.
        2. **Source de clé** (`LlmFundingPolicy.resolve`) : clé personnelle,
           sinon la clé partagée de la politique active.
        3. **Fournisseur LLM** : un `llm_provider_id` inconnu ou non configuré
           est refusé ici, sans consommer de quota ni déranger le moteur.
        4. **Quotas**, dès que la clé n'est pas la clé personnelle de
           l'appelant : un appel pour la génération, puis un second pour la
           reformulation si elle aura lieu (`_query_rewrite_needed`). Si ce
           second appel est refusé, la reformulation est sautée (repli sur le
           dernier message, comme sur une panne, ADR-049) et la génération,
           déjà imputée, a lieu.
        5. **Reformulation** de la requête de retrieval, puis `search()`, puis
           préparation de `stream()` — paresseux, n'appelle rien avant
           itération.

        Raises:
            ValidationError: fournisseur LLM (`llm_provider_id`) inconnu ou non
                configuré ; reranking effectif sans reranker configuré sur la
                base ; reranking effectif combiné à un `similarity_threshold`
                non nul sans `acknowledge_threshold_risk` (ADR-026) ;
                `document_ids` citant un document hors de cette base.
            NotFoundError: base inexistante, ou invisible pour ce principal.
            AuthenticationRequiredError: principal anonyme visant une base
                privée ou une action réservée.
            ForbiddenError: compte authentifié dont le rôle est insuffisant.
            TooManyRequestsError: appel non financé par une clé personnelle
                au-delà du plafond journalier de son compteur d'identité, ou
                budget global épuisé.
            AuthenticationRequiredError: appel hors clé personnelle sans
                compte à qui l'imputer (politique du cœur).
            ServiceUnavailableError: compteur de quota injoignable (échec
                fermé) — jamais pour un appelant muni de sa propre clé.
        """
        await self._authorizer(authorizer).resolve_access(
            principal, payload.knowledge_base_id, minimum=MemberRole.VIEWER
        )

        policy = get_llm_funding_policy()
        funding = await policy.resolve(self._db, self._settings, principal, payload.llm_provider_id)
        llm = (
            self._llm
            if self._llm is not None
            else resolve_llm_provider(
                funding.provider_id, self._settings, override_api_key=funding.override_api_key
            )
        )

        rewrite_query = _query_rewrite_needed(payload.messages)
        if not funding.is_personal:
            scope = quota_scope or policy.quota_scope(
                principal, via_evaluation=via_evaluation, client_ip=client_ip
            )
            await charge_llm_call(self._settings, scope)
            if rewrite_query:
                try:
                    await charge_llm_call(self._settings, scope)
                except TooManyRequestsError:
                    _logger.info(
                        "Quota atteint pour la reformulation de la requête de retrieval, "
                        "repli sur le dernier message",
                        extra={"quota_subject": scope.subject},
                    )
                    rewrite_query = False

        retrieval_query = (
            await _build_retrieval_query(llm, payload.messages)
            if rewrite_query
            else payload.messages[-1].content
        )
        search_response = await self.search(
            principal,
            RetrievalSearchRequest(
                knowledge_base_id=payload.knowledge_base_id,
                query=retrieval_query,
                top_k=payload.top_k,
                rerank=payload.rerank,
                similarity_threshold=payload.similarity_threshold,
                acknowledge_threshold_risk=payload.acknowledge_threshold_risk,
                document_ids=payload.document_ids,
            ),
            authorizer=authorizer,
        )

        system_prompt = _build_grounding_prompt(search_response.results)
        conversation = [
            ChatMessage(role="system", content=system_prompt),
            *(ChatMessage(role=turn.role, content=turn.content) for turn in payload.messages),
        ]
        tokens = llm.stream(conversation, max_tokens=payload.max_tokens)
        return ChatStream(results=search_response.results, tokens=tokens)

    def _authorizer(self, authorizer: KnowledgeBaseAuthorizer | None) -> KnowledgeBaseAuthorizer:
        """Matrice d'accès du cœur, sauf autorisateur fourni par l'appelant."""
        return authorizer if authorizer is not None else PermissionService(self._db)

    async def _require_documents_exist(
        self, knowledge_base_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
    ) -> None:
        """`document_ids` cite des objets précis : un identifiant absent est une erreur.

        Distinct des filtres descriptifs (`languages`, `content_types`,
        `ingested_after`/`ingested_before`) : ceux-là ne citent rien, ils
        décrivent — une combinaison qui n'exclut aucun document ne lève jamais
        d'erreur (`_resolve_document_refs`).

        Raises:
            ValidationError: un ou plusieurs identifiants ne correspondent à
                aucun document de cette base.
        """
        statement = select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.id.in_(document_ids),
        )
        found_ids = set((await self._db.execute(statement)).scalars().all())
        missing = set(document_ids) - found_ids
        if missing:
            ids = ", ".join(str(identifier) for identifier in sorted(missing))
            raise ValidationError(f"Documents introuvables dans cette base : {ids}.")

    async def _resolve_document_refs(
        self, knowledge_base_id: uuid.UUID, payload: RetrievalFilters
    ) -> list[EngineDocumentRef]:
        """Charge les documents correspondant aux filtres, restreints à cette base.

        Combine `document_ids` (citation explicite, existence exigée) et les
        filtres descriptifs (`languages`, `content_types`, `themes`, `ingested_after`/
        `ingested_before`, combinés en ET) en une seule requête — jamais un
        filtre post-hoc sur des résultats déjà rendus par le moteur (docstring
        de tête du module, ADR-001). Un document trouvé mais jamais chunké
        (`ragflow_document_external_id` `None`) n'a simplement rien à filtrer
        côté moteur : il est omis de la liste rendue sans faire échouer la
        requête.

        Raises:
            ValidationError: `document_ids` cite un identifiant absent de
                cette base (`_require_documents_exist`).
        """
        if payload.document_ids is not None:
            await self._require_documents_exist(knowledge_base_id, payload.document_ids)

        statement = select(Document).where(Document.knowledge_base_id == knowledge_base_id)
        if payload.document_ids is not None:
            statement = statement.where(Document.id.in_(payload.document_ids))
        if payload.languages:
            statement = statement.where(Document.detected_languages.overlap(payload.languages))
        if payload.content_types:
            statement = statement.where(Document.content_type.in_(payload.content_types))
        if payload.themes:
            statement = statement.where(Document.theme.in_(payload.themes))
        if payload.ingested_after is not None:
            statement = statement.where(Document.created_at >= payload.ingested_after)
        if payload.ingested_before is not None:
            statement = statement.where(Document.created_at < payload.ingested_before)

        found = (await self._db.execute(statement)).scalars().all()
        return [
            EngineDocumentRef(external_id=document.ragflow_document_external_id)
            for document in found
            if document.ragflow_document_external_id is not None
        ]

    async def _to_response(self, results: Sequence[EngineSearchResult]) -> RetrievalSearchResponse:
        """Mappe les résultats du moteur vers leurs documents RAGEngine.

        Un résultat dont `document_external_id` n'a pas de correspondance en
        base est exclu et journalisé — cas théoriquement impossible mais pas
        garanti par contrat, jamais une raison de faire échouer toute la
        sérialisation.
        """
        external_ids = {result.document_external_id for result in results}
        if not external_ids:
            return self._empty()
        statement = select(Document).where(Document.ragflow_document_external_id.in_(external_ids))
        documents = (await self._db.execute(statement)).scalars().all()
        by_external_id = {
            document.ragflow_document_external_id: document
            for document in documents
            if document.ragflow_document_external_id is not None
        }

        mapped: list[RetrievalResult] = []
        for result in results:
            document = by_external_id.get(result.document_external_id)
            if document is None:
                _logger.warning(
                    "Résultat de recherche sans document RAGEngine correspondant",
                    extra={"document_external_id": result.document_external_id},
                )
                continue
            mapped.append(
                RetrievalResult(
                    chunk_id=result.chunk_id,
                    document_id=document.id,
                    filename=document.filename,
                    content=result.content,
                    similarity=result.similarity,
                    vector_similarity=result.vector_similarity,
                    term_similarity=result.term_similarity,
                    page_start=result.page_start,
                    page_end=result.page_end,
                )
            )
        return RetrievalSearchResponse(results=mapped)

    @staticmethod
    def _empty() -> RetrievalSearchResponse:
        """Réponse vide, sans appeler le moteur — base jamais ingérée, ou rien à filtrer."""
        return RetrievalSearchResponse(results=[])
