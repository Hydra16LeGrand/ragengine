"""Retrieval : recherche hybride (dense + BM25) et chat génératif sur une base.

Les deux endpoints sont **racines** (CONTRIBUTING.md §4) : `knowledge_base_id` arrive
dans le corps de la requête, pas dans le chemin — `require_kb_permission` ne
peut donc pas s'y monter (elle exige un `kb_id` de chemin). L'accès est résolu
par `RetrievalService`, via `PermissionService`, exactement comme pour
`/documents/{document_id}` et `/jobs/{job_id}`.

Le routeur ne fait que valider, déléguer et sérialiser : toute l'orchestration
(matrice §4, arbitrage du reranking, filtrage par documents, appel au moteur,
résolution du fournisseur LLM, construction du prompt système) vit dans
`app.services.retrieval.RetrievalService`.

`POST /retrieval/chat` répond en flux `text/event-stream` (SSE), pas en JSON
classique — premier précédent de streaming du dépôt. Point le plus facile à
rater : `RetrievalService.chat(...)` est **attendu directement dans le corps du
routeur**, avant toute construction de `StreamingResponse` — pas à l'intérieur
du générateur asynchrone `chat_events`. Un générateur async n'exécute aucun
code tant qu'il n'est pas itéré : si la résolution d'accès, de reranking ou du
fournisseur LLM vivait dans `chat_events`, l'erreur ne surviendrait qu'après
que Starlette a déjà commencé à envoyer une réponse `200`, et remonterait donc
encapsulée dans le flux SSE plutôt que comme une `ProblemDetail` RFC 9457
normale. En résolvant tout dans `chat()` avant de construire le générateur, une
`RAGEngineError` (base introuvable, rôle insuffisant, reranking sans reranker,
fournisseur LLM inconnu ou non configuré) traverse le routeur comme une
exception ordinaire et est traduite par les handlers globaux
(`app.core.errors`), exactement comme pour `/retrieval/search`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.v1.dependencies import CurrentPrincipal, SettingsDep, require_scope
from app.core.errors import RAGEngineError, build_problem
from app.core.rate_limit import normalize_client_ip
from app.db.session import DbSession
from app.domain.schemas.retrieval import (
    RetrievalChatRequest,
    RetrievalResult,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.domain.scopes import ApiKeyScope
from app.services.retrieval import RetrievalService

router = APIRouter(tags=["retrieval"])
_logger = logging.getLogger(__name__)

SSE_MEDIA_TYPE: Final = "text/event-stream"

ScopeSearch = Depends(require_scope(ApiKeyScope.RETRIEVAL_SEARCH))
"""Portée de clé API exigée pour `/retrieval/search` — sans effet hors clé API."""

ScopeChat = Depends(require_scope(ApiKeyScope.RETRIEVAL_CHAT))
"""Portée distincte pour `/retrieval/chat` : coûte un appel LLM externe réel."""


@router.post(
    "/retrieval/search",
    summary="Rechercher dans une base de connaissances",
    description=(
        "Recherche hybride native RAGFlow (dense + BM25), reranking optionnel. "
        "`knowledge_base_id` est dans le corps : anonyme sur une base `public` "
        "peut chercher, membre `viewer`/`editor`/`owner` peut chercher sur une "
        "base `private` dont il est membre, tout autre cas répond `404` (une "
        "base invisible pour ce principal n'existe pas, de son point de vue).\n\n"
        "Une base jamais ingérée, ou dont aucun `document_ids` demandé n'est "
        "encore chunké, répond une liste vide sans appeler le moteur. "
        "`rerank=true` sans reranker configuré sur la base répond `422`."
    ),
    dependencies=[ScopeSearch],
)
async def search(
    payload: RetrievalSearchRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> RetrievalSearchResponse:
    """Délègue la recherche au service, sans logique métier ici."""
    return await RetrievalService(db, settings).search(principal, payload)


def _format_sse_event(event: str, data: object) -> str:
    """Formate un évènement SSE unique : ligne `event:`, ligne `data:` JSON, ligne vide.

    `data` est **toujours** sérialisé en JSON, y compris l'évènement `token`
    (un fragment de texte devient une chaîne JSON `"..."`). Écart assumé par
    rapport au brief (« `data` = le texte brut du fragment ») : un fragment de
    génération peut contenir un saut de ligne, qui casserait le framing SSE
    (une donnée par ligne `data:`) s'il était émis tel quel. Encoder en JSON
    garde le flux valide sur toute réponse plausible du modèle ; côté client,
    un seul `JSON.parse(event.data)` suffit pour les quatre types d'évènement.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_events(
    results: list[RetrievalResult], tokens: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Assemble le flux SSE : `sources` une fois, puis `token`*, puis `error`? et `done`.

    `results` est déjà résolu à l'appel (la recherche a eu lieu avant la
    construction de ce générateur, dans `RetrievalService.chat` — voir le
    docstring de module) : `sources` peut donc être le tout premier évènement
    émis, avant même d'entamer l'itération de `tokens`.

    Une erreur du fournisseur LLM levée pendant le streaming (`RAGEngineError`,
    p. ex. `LLMError`/`LLMRateLimitError`/`LLMRefusalError` — tout ce qui peut
    fuiter d'un adaptateur `LLMPort` par contrat, `docs/providers-architecture.md`
    §0) ne peut plus changer le code HTTP : la réponse `200` est déjà en cours
    d'envoi. Elle devient donc un évènement `error` (`data` = `ProblemDetail`
    JSON minimal), et le flux s'arrête **sans** évènement `done` — c'est ce qui
    distingue, côté client, une fin propre d'une coupure. Toute autre exception
    (bug, jamais attendue par contrat) est journalisée puis traduite en un
    `error` générique, filet de sécurité pour ne jamais laisser la connexion se
    couper sans évènement terminal.
    """
    yield _format_sse_event("sources", [result.model_dump(mode="json") for result in results])
    try:
        async for fragment in tokens:
            yield _format_sse_event("token", fragment)
    except RAGEngineError as exc:
        error_payload = exc.to_problem().model_dump(mode="json", exclude_none=True)
        yield _format_sse_event("error", error_payload)
        return
    except Exception:
        _logger.exception("Erreur inattendue pendant le streaming du chat")
        fallback_problem = build_problem(
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail="Une erreur interne est survenue pendant la génération.",
        )
        yield _format_sse_event(
            "error", fallback_problem.model_dump(mode="json", exclude_none=True)
        )
        return
    yield _format_sse_event("done", {})


@router.post(
    "/retrieval/chat",
    summary="Chat génératif adossé au retrieval d'une base de connaissances",
    description=(
        "Recherche hybride puis génération par un LLM externalisé (CONTRIBUTING.md "
        "§2), en flux `text/event-stream`. Mêmes règles d'accès que "
        "`/retrieval/search` (`knowledge_base_id` dans le corps).\n\n"
        "Évènements SSE, dans l'ordre : `sources` (une fois, avant tout texte — "
        "tableau JSON de `RetrievalResult`, la recherche a déjà eu lieu) ; "
        "`token` (un par fragment reçu du modèle, `data` est une chaîne JSON) ; "
        "`error` (uniquement si le fournisseur LLM échoue après le début du "
        "flux, `data` = `ProblemDetail` JSON minimal, puis la connexion se "
        "ferme sans `done`) ; `done` (une fois, `data: {}`, uniquement en fin "
        "de flux réussi).\n\n"
        "Toute erreur *avant* le premier octet envoyé — base introuvable ou "
        "invisible, rôle insuffisant, reranking demandé sans reranker "
        "configuré, `document_ids` hors base, `llm_provider_id` inconnu ou non "
        "configuré — répond une `ProblemDetail` RFC 9457 classique (`404`/"
        "`403`/`422`…), jamais un évènement SSE : le client peut distinguer "
        "sans ambiguïté un refus de la requête d'une panne survenue en cours "
        "de génération."
    ),
    dependencies=[ScopeChat],
)
async def chat(
    payload: RetrievalChatRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    request: Request,
) -> StreamingResponse:
    """Résout tout ce qui peut échouer, puis ne fait que streamer `ChatStream`.

    `RetrievalService(db, settings).chat(...)` est **attendu ici**, hors de
    `chat_events` : voir le docstring de module pour pourquoi c'est le détail
    qui fait toute la différence entre une erreur RFC 9457 normale et une
    erreur encapsulée en SSE.

    `client_ip` (ADR-047, ADR-054) : transmis à la politique de financement
    active (`LlmFundingPolicy.quota_scope`), qui peut compter par adresse un
    appelant sans compte — le cœur, lui, compte par compte. C'est l'IP vue
    par l'application : derrière un proxy, elle n'est la vraie IP du visiteur
    que si les en-têtes de proxy sont honorés en amont. Normalisée par
    `normalize_client_ip` (ADR-052) : une IPv6 individuelle n'a aucun coût pour
    qui en change à volonté dans son `/64`.
    """
    client_ip = normalize_client_ip(
        request.client.host if request.client is not None else "unknown"
    )
    stream = await RetrievalService(db, settings).chat(principal, payload, client_ip=client_ip)
    return StreamingResponse(
        chat_events(stream.results, stream.tokens),
        media_type=SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache"},
    )
