"""Cas d'usage de la génération d'évaluation (Phase 8) — voir `docs/adr/017-*.md`.

Un run échantillonne `sample_size` documents déjà indexés d'une base, génère une
question/réponse de référence par document (source : ses fragments bruts, via
`IngestionEnginePort.list_document_chunks` — ADR-017), puis note chaque
question selon deux axes indépendants : un taux de rappel retrieval objectif
(`RetrievalService.search`) et un score de juge LLM subjectif
(`RetrievalService.chat` + un second appel `LLMPort.complete`, même
`llm_provider_id`).

**Deux chemins d'exécution** (Phase 12 Étape 4, ADR-028) :

- `create_run` — **synchrone**, pas de job Celery : borné par `sample_size`
  (≤ `MAX_EVALUATION_SAMPLE_SIZE`), c'est une action ponctuelle qui bloque la
  requête HTTP jusqu'à la fin du run. Comportement inchangé depuis la Phase 8.
- `create_pending_run` / `run_pending_evaluation` — **asynchrone**, pour un
  `sample_size` au-delà de ce plafond (borné séparément par
  `Settings.evaluation_async_max_sample_size`). `create_pending_run` valide
  tout ce qui peut l'être sans appel LLM (base activée, fournisseur résoluble,
  au moins un document éligible) et persiste immédiatement un `EvaluationRun`
  `pending`, sans item — c'est ce run que `POST .../evaluations` renvoie en
  `202`. `run_pending_evaluation`, appelée depuis `run_async_evaluation`
  (Celery, `app.workers.tasks.evaluations`), charge ce run et produit ses
  items exactement comme `create_run`, puis le fait transiter vers
  `completed` ou `failed`.

**Dépense LLM (ADR-054).** Chaque question tentée coûte jusqu'à trois appels :
génération de la Q/R, réponse du chat, jugement. Ils suivent la même table de
vérité que le chat (`resolve_llm_funding`) : clé personnelle du demandeur, sinon
la clé partagée de la politique active — et, hors clé personnelle, le même modèle
forcé (`platform_llm_model_id`, addendum 2026-09-21 à ADR-054). **Le compteur
est en revanche dédié aux évaluations** (`QuotaSubject.EVALUATION`), distinct de
celui du chat malgré le même identifiant de compte : un run programmé consomme
jusqu'à 24 appels sous les valeurs par défaut (`evaluation_scheduled_sample_size`
x 3), au-delà du plafond de chat qu'il partageait avant cet addendum — il ne
doit ni s'y arrêter en route, ni le vider pour le reste de la journée. Chaque
appel est imputé à ce compteur puis au budget global, juste avant d'avoir lieu
(`_FundedLLM.complete` ; la réponse du chat est imputée par `RetrievalService.chat`
lui-même, avec le même `LlmQuotaScope`, cf. `_fund`). Un run consomme donc
exactement autant d'unités qu'il fait d'appels. Les trois chemins exigent un
**compte réel** (jamais un invité) disposant d'au moins `EDITOR` sur la base,
vérifiés ici avant tout appel payé — y compris à l'exécution d'un run
asynchrone ou programmé, où aucun routeur ne l'a fait.

Un document dont la génération échoue (fragment introuvable, réponse du modèle
non structurée) est simplement **écarté** au profit du document éligible
suivant — jamais une raison de faire échouer tout le run pour une seule
question ratée. Un run peut donc produire moins d'items que `sample_size` si
trop peu de documents éligibles donnent une Q/R exploitable : `sample_size` est
la **cible** au moment du tirage (documents échantillonnés), `item_count`
(calculé à la lecture, jamais stocké) est le résultat réellement obtenu.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, TooManyRequestsError, ValidationError
from app.db.models import Document, EvaluationItem, EvaluationRun, KnowledgeBase
from app.domain.enums import DocumentStatus, EvaluationRunStatus, MemberRole
from app.domain.schemas.evaluation import (
    MAX_EVALUATION_SAMPLE_SIZE,
    EvaluationCreate,
    EvaluationItemRead,
    EvaluationRunRead,
    EvaluationRunSummary,
)
from app.domain.schemas.retrieval import ChatTurn, RetrievalChatRequest, RetrievalSearchRequest
from app.providers.engine import resolve_ingestion_engine
from app.providers.engine.base import EngineDatasetRef, EngineDocumentRef, IngestionEnginePort
from app.providers.llm.base import ChatMessage, LLMPort
from app.providers.llm.registry import resolve_llm_provider
from app.services.identity.permissions import PermissionService
from app.services.identity.platform_llm_budget import (
    LlmQuotaScope,
    QuotaSubject,
    charge_llm_call,
)
from app.services.identity.principal import Principal
from app.services.llm_funding import resolve_llm_funding
from app.services.retrieval import RetrievalService
from app.workers.celery_app import RUN_ASYNC_EVALUATION_TASK, task_by_name

_logger = logging.getLogger(__name__)

_CHUNK_POOL_SIZE: Final = 20
"""Fragments lus par document pour le tirage du passage source — une seule
page de `list_document_chunks` (`docs/ingestion-engine-architecture.md` §8,
pagine désormais réellement mais cet appelant n'en lit qu'une), largement
suffisante pour un document déjà chunké."""

_GENERATION_MAX_TOKENS: Final = 512
_JUDGE_MAX_TOKENS: Final = 512

_EVALUATION_DISABLED: Final = "L'évaluation n'est pas activée sur cette base de connaissances."
_NO_ELIGIBLE_DOCUMENTS: Final = (
    "Aucun document indexé n'est disponible pour générer une évaluation."
)
_UNKNOWN_RUN: Final = "Évaluation introuvable dans cette base."
_ASYNC_SAMPLE_SIZE_TOO_LARGE: Final = (
    "sample_size dépasse le plafond du run asynchrone déployé sur cette instance."
)

_GENERATION_SYSTEM_PROMPT: Final = (
    "Tu es un générateur de jeux de test pour un système RAG francophone. On te "
    "donne un extrait d'un document. Génère UNE question dont la réponse se "
    "trouve exactement et exclusivement dans cet extrait, et la réponse de "
    "référence correspondante — courte, factuelle, tirée du texte. Réponds "
    "uniquement par un objet JSON strict de la forme "
    '{"question": "...", "answer": "..."}, sans aucun texte avant ni après.'
)

_JUDGE_SYSTEM_PROMPT: Final = (
    "Tu es un juge qui évalue la qualité d'une réponse générée par un système "
    "RAG, en la comparant à une réponse de référence. Note la réponse générée "
    "de 0.0 (fausse ou hors sujet) à 1.0 (équivalente à la référence), et "
    "justifie brièvement en une phrase. Réponds uniquement par un objet JSON "
    'strict de la forme {"score": 0.0, "rationale": "..."}, sans aucun texte '
    "avant ni après."
)


@dataclass(frozen=True, slots=True)
class _FundedLLM:
    """Fournisseur d'un run, et le compteur auquel chacun de ses appels est imputé.

    `quota_scope` à `None` : clé personnelle du demandeur, aucun quota. Sinon
    `complete` impute l'appel **avant** de l'émettre — le quota reflète le nombre
    réel d'appels du run, pas un forfait par run (ADR-054).
    """

    llm: LLMPort
    provider_id: str
    settings: Settings
    quota_scope: LlmQuotaScope | None

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        if self.quota_scope is not None:
            await charge_llm_call(self.settings, self.quota_scope)
        return await self.llm.complete(messages, max_tokens=max_tokens)


def _extract_json_object(text: str) -> dict[str, object] | None:
    """Isole le premier objet JSON du texte, en tolérant un habillage Markdown.

    Les modèles entourent parfois leur JSON de ```` ```json … ``` ```` ou d'une
    phrase d'accompagnement malgré la consigne : on cherche la première `{` et
    la dernière `}` plutôt que d'exiger que la réponse entière soit un JSON pur.
    `None` si rien d'exploitable n'est trouvé — jamais une exception qui ferait
    échouer tout le run pour une seule réponse mal formée.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _average_judge_score(items: Sequence[EvaluationItem]) -> float | None:
    if not items:
        return None
    return sum(item.judge_score for item in items) / len(items)


def _retrieval_recall_rate(items: Sequence[EvaluationItem]) -> float | None:
    if not items:
        return None
    found = sum(1 for item in items if item.retrieval_rank is not None)
    return found / len(items)


def _to_run_summary(run: EvaluationRun, items: Sequence[EvaluationItem]) -> EvaluationRunSummary:
    """Construit le résumé champ par champ : un `EvaluationRun` seul ne suffit pas (cf. module)."""
    return EvaluationRunSummary(
        id=run.id,
        knowledge_base_id=run.knowledge_base_id,
        requested_by_id=run.requested_by_id,
        llm_provider_id=run.llm_provider_id,
        sample_size=run.sample_size,
        status=run.status,
        item_count=len(items),
        average_judge_score=_average_judge_score(items),
        retrieval_recall_rate=_retrieval_recall_rate(items),
        created_at=run.created_at,
    )


def _to_run_read(run: EvaluationRun, items: Sequence[EvaluationItem]) -> EvaluationRunRead:
    summary = _to_run_summary(run, items)
    return EvaluationRunRead(
        **summary.model_dump(),
        items=[EvaluationItemRead.model_validate(item) for item in items],
    )


class EvaluationService:
    """Cas d'usage portant sur `evaluation_runs` et `evaluation_items`.

    Les lectures (`list_runs`, `get_run`) supposent l'autorisation déjà acquise
    par `require_kb_permission` au niveau du routeur. Les trois chemins qui
    dépensent (`create_run`, `create_pending_run`, `run_pending_evaluation`)
    la revérifient eux-mêmes (`_authorize_spending`, ADR-054) : deux d'entre
    eux s'exécutent hors de toute requête HTTP (Celery), sans routeur.
    """

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        *,
        engine: IngestionEnginePort | None = None,
        llm: LLMPort | None = None,
    ) -> None:
        """Construit le service.

        `llm` n'existe que pour l'injection de tests (contourner le registre),
        exactement comme sur `RetrievalService` : `None` laisse `create_run`
        résoudre le fournisseur depuis `payload.llm_provider_id` via
        `resolve_llm_provider`, et laisse pareillement `RetrievalService`
        résoudre lui-même le sien (toujours le même `llm_provider_id`) pour
        `search`/`chat`.

        `engine` (et le `RetrievalService` qui en dépend) est résolu **au
        premier accès**, pas ici : `list_runs`/`get_run` ne lisent que des
        runs déjà persistés en base et n'ont besoin ni du moteur ni du
        retrieval. Résoudre `resolve_ingestion_engine` inconditionnellement
        dans `__init__` faisait échouer ces deux lectures pures (`502`) dès
        que `RAGFLOW_API_KEY` n'était pas configurée, alors qu'elles n'en
        dépendent pas — corrigé une fois signalé par une campagne de test.
        """
        self._db = db
        self._settings = settings if settings is not None else get_settings()
        self._engine_override = engine
        self._llm = llm
        self._engine_cache: IngestionEnginePort | None = None
        self._retrieval_cache: RetrievalService | None = None

    @property
    def _engine(self) -> IngestionEnginePort:
        if self._engine_cache is None:
            self._engine_cache = (
                self._engine_override
                if self._engine_override is not None
                else resolve_ingestion_engine(self._settings)
            )
        return self._engine_cache

    @property
    def _retrieval(self) -> RetrievalService:
        if self._retrieval_cache is None:
            self._retrieval_cache = RetrievalService(
                self._db, self._settings, engine=self._engine, llm=self._llm
            )
        return self._retrieval_cache

    async def create_run(
        self, principal: Principal, knowledge_base: KnowledgeBase, payload: EvaluationCreate
    ) -> EvaluationRunRead:
        """Échantillonne, génère, note et persiste un run complet — chemin **synchrone**.

        Appelée par `POST .../evaluations` uniquement quand
        `payload.sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE` (le routeur choisit
        entre ce chemin et `create_pending_run` avant même d'appeler le
        service) ; la garde ci-dessous reste défensive pour tout autre
        appelant (routine programmée, `app.workers.tasks.evaluations`, dont
        `evaluation_scheduled_sample_size` est de toute façon plafonné à 20).

        Si le quota s'épuise en cours de run, la génération s'arrête et le run
        est persisté avec les items déjà produits (`_generate_items`) ; aucun
        item produit → `TooManyRequestsError`.

        Raises:
            AuthenticationRequiredError / AccountRequiredError: principal anonyme
                ou invité — l'évaluation est réservée aux comptes réels.
            ForbiddenError / NotFoundError: moins qu'`EDITOR` sur la base.
            ValidationError: l'évaluation n'est pas activée sur cette base,
                `llm_provider_id` inconnu ou non configuré, ou `sample_size`
                dépasse `MAX_EVALUATION_SAMPLE_SIZE` (utiliser
                `create_pending_run` au-delà, ADR-028).
            ConflictError: aucun document indexé n'est disponible dans cette base.
            TooManyRequestsError: quota épuisé avant le premier item.
            ServiceUnavailableError: compteur de quota injoignable (échec fermé).
        """
        await self._authorize_spending(principal, knowledge_base)
        if not knowledge_base.evaluation_enabled:
            raise ValidationError(_EVALUATION_DISABLED)
        if payload.sample_size > MAX_EVALUATION_SAMPLE_SIZE:
            raise ValidationError(
                f"sample_size ({payload.sample_size}) dépasse le plafond synchrone "
                f"({MAX_EVALUATION_SAMPLE_SIZE}) : utilisez le chemin asynchrone.",
                extensions={"sample_size": payload.sample_size, "max": MAX_EVALUATION_SAMPLE_SIZE},
            )
        # Résolu avant tout tirage : un fournisseur invalide est une erreur de
        # requête indépendante du contenu de la base (même ordre que
        # `RetrievalService.chat`).
        funded = await self._fund(principal, payload.llm_provider_id)

        eligible = await self._eligible_documents(knowledge_base.id)
        if not eligible:
            raise ConflictError(_NO_ELIGIBLE_DOCUMENTS)

        target = min(payload.sample_size, len(eligible))
        pool = list(eligible)
        random.shuffle(pool)
        items = await self._generate_items(principal, knowledge_base, funded, pool, target)

        run = EvaluationRun(
            knowledge_base_id=knowledge_base.id,
            requested_by_id=principal.user_id,
            llm_provider_id=funded.provider_id,
            sample_size=target,
            status=EvaluationRunStatus.COMPLETED,
        )
        self._db.add(run)
        await self._db.flush([run])

        for item in items:
            item.evaluation_run_id = run.id
        self._db.add_all(items)
        if items:
            await self._db.flush(items)
            for item in items:
                await self._db.refresh(item)
        await self._db.refresh(run)

        return _to_run_read(run, items)

    async def create_pending_run(
        self, principal: Principal, knowledge_base: KnowledgeBase, payload: EvaluationCreate
    ) -> EvaluationRunRead:
        """Valide, persiste un run `pending` puis publie son exécution — chemin **asynchrone**.

        Appelée par `POST .../evaluations` quand `payload.sample_size >
        MAX_EVALUATION_SAMPLE_SIZE` (ADR-028) : ne fait **aucun** appel LLM ni
        moteur au-delà de l'échantillonnage (une requête SQL) — tout ce qui
        peut échouer avant tout coût réel est vérifié ici, exactement comme
        `create_run` le fait pour son propre chemin. `run_pending_evaluation`,
        appelée depuis `run_async_evaluation` (Celery), fait le reste.

        Raises:
            AuthenticationRequiredError / AccountRequiredError: principal anonyme
                ou invité — l'évaluation est réservée aux comptes réels.
            ForbiddenError / NotFoundError: moins qu'`EDITOR` sur la base.
            ValidationError: l'évaluation n'est pas activée sur cette base,
                `sample_size` dépasse `Settings.evaluation_async_max_sample_size`,
                ou `llm_provider_id` inconnu ou non configuré.
            ConflictError: aucun document indexé n'est disponible dans cette base.
        """
        await self._authorize_spending(principal, knowledge_base)
        if not knowledge_base.evaluation_enabled:
            raise ValidationError(_EVALUATION_DISABLED)
        if payload.sample_size > self._settings.evaluation_async_max_sample_size:
            raise ValidationError(
                _ASYNC_SAMPLE_SIZE_TOO_LARGE,
                extensions={
                    "sample_size": payload.sample_size,
                    "max": self._settings.evaluation_async_max_sample_size,
                },
            )
        # Validation seule : l'instance résolue ici n'est pas conservée,
        # `run_pending_evaluation` la résoudra à nouveau depuis sa propre
        # session worker (`run.llm_provider_id`, seule source persistée).
        # Aucun appel LLM ici, donc rien à imputer.
        funded = await self._fund(principal, payload.llm_provider_id)

        eligible = await self._eligible_documents(knowledge_base.id)
        if not eligible:
            raise ConflictError(_NO_ELIGIBLE_DOCUMENTS)

        target = min(payload.sample_size, len(eligible))
        run = EvaluationRun(
            knowledge_base_id=knowledge_base.id,
            requested_by_id=principal.user_id,
            llm_provider_id=funded.provider_id,
            sample_size=target,
            status=EvaluationRunStatus.PENDING,
        )
        self._db.add(run)
        await self._db.flush([run])
        await self._db.refresh(run)

        await self._publish_after_commit(knowledge_base.id, run.id)
        return _to_run_read(run, [])

    async def run_pending_evaluation(
        self, principal: Principal, knowledge_base: KnowledgeBase, evaluation_run_id: uuid.UUID
    ) -> EvaluationRunRead | None:
        """Exécute un run `pending` déjà persisté par `create_pending_run`.

        Appelée depuis `run_async_evaluation` (Celery,
        `app.workers.tasks.evaluations`), sur sa propre session (donc son
        propre `EvaluationService`, jamais celui qui a construit le run).

        `None` si le run n'existe plus, n'appartient pas à cette base, ou
        n'est plus `pending` — course bénigne entre publication et
        exécution (même garde que `_run_for_knowledge_base`, routine
        programmée), pas une erreur.

        En cas d'échec (fournisseur à sec, disjoncteur ouvert, panne
        transitoire), le run est marqué `failed` **et commité avant que
        l'exception ne soit relevée** : `run_in_session`
        (`app.workers.session`) fait un `ROLLBACK` sur toute exception non
        rattrapée, ce qui annulerait silencieusement ce `failed` s'il restait
        seulement `flush`é — un run resterait alors bloqué en `pending` pour
        toujours, invisible comme échoué depuis `GET .../evaluations/{id}`.
        L'exception est relevée malgré tout, pour que l'appelant (la tâche
        Celery) journalise le type et la raison réels, même politique que
        `run_scheduled_evaluation`.

        Compte réel et accès `EDITOR` revérifiés à l'exécution, avant tout
        appel payé : le demandeur a pu perdre son accès, ou être suspendu,
        depuis la publication du run.
        """
        run = await self._db.get(EvaluationRun, evaluation_run_id)
        if (
            run is None
            or run.knowledge_base_id != knowledge_base.id
            or run.status != EvaluationRunStatus.PENDING
        ):
            return None

        try:
            await self._authorize_spending(principal, knowledge_base)
            funded = await self._fund(principal, run.llm_provider_id)
            run.llm_provider_id = funded.provider_id
            eligible = await self._eligible_documents(knowledge_base.id)
            pool = list(eligible)
            random.shuffle(pool)
            items = await self._generate_items(
                principal, knowledge_base, funded, pool, run.sample_size
            )
        except Exception:
            run.status = EvaluationRunStatus.FAILED
            await self._db.flush([run])
            await self._db.commit()
            raise

        for item in items:
            item.evaluation_run_id = run.id
        self._db.add_all(items)
        run.status = EvaluationRunStatus.COMPLETED
        await self._db.flush([run, *items])
        for item in items:
            await self._db.refresh(item)
        await self._db.refresh(run)
        return _to_run_read(run, items)

    async def list_runs(self, knowledge_base: KnowledgeBase) -> list[EvaluationRunSummary]:
        """Historique résumé — `GET .../evaluations`, du plus récent au plus ancien."""
        runs = (
            (
                await self._db.execute(
                    select(EvaluationRun)
                    .where(EvaluationRun.knowledge_base_id == knowledge_base.id)
                    .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if not runs:
            return []
        items_by_run = await self._items_by_run([run.id for run in runs])
        return [_to_run_summary(run, items_by_run.get(run.id, [])) for run in runs]

    async def get_run(
        self, knowledge_base: KnowledgeBase, evaluation_id: uuid.UUID
    ) -> EvaluationRunRead:
        """Détail complet — `GET .../evaluations/{evaluation_id}`.

        Raises:
            NotFoundError: run inexistant, ou rattaché à une autre base.
        """
        run = await self._db.get(EvaluationRun, evaluation_id)
        if run is None or run.knowledge_base_id != knowledge_base.id:
            raise NotFoundError(_UNKNOWN_RUN)
        items = (
            (
                await self._db.execute(
                    select(EvaluationItem)
                    .where(EvaluationItem.evaluation_run_id == run.id)
                    .order_by(EvaluationItem.created_at)
                )
            )
            .scalars()
            .all()
        )
        return _to_run_read(run, items)

    async def _authorize_spending(
        self, principal: Principal, knowledge_base: KnowledgeBase
    ) -> None:
        """Compte réel, au moins `EDITOR` sur la base — avant tout appel payé (ADR-054).

        **Pas d'évaluation pour un invité**, ni manuelle, ni asynchrone, ni
        programmée : l'espace invité sert à essayer le Playground et une
        ingestion (ADR-005), pas un outil de qualité à trois appels LLM par
        question, rejoué chaque jour par la routine programmée sur une base
        d'essai jetable.

        Raises:
            AuthenticationRequiredError: principal anonyme.
            AccountRequiredError: principal invité.
            NotFoundError / ForbiddenError: accès insuffisant à la base.
        """
        principal.require_account()
        await PermissionService(self._db).resolve_access(
            principal, knowledge_base.id, minimum=MemberRole.EDITOR
        )

    async def _fund(self, principal: Principal, llm_provider_id: str) -> _FundedLLM:
        """Source de clé (même table de vérité que le chat), fournisseur résolu, compteur associé.

        Raises:
            ValidationError: fournisseur inconnu ou non configuré.
        """
        funding = await resolve_llm_funding(self._db, self._settings, principal, llm_provider_id)
        llm = (
            self._llm
            if self._llm is not None
            else resolve_llm_provider(
                funding.provider_id, self._settings, override_api_key=funding.override_api_key
            )
        )
        user = principal.require_account()
        quota_scope = (
            None if funding.is_personal else LlmQuotaScope(QuotaSubject.EVALUATION, str(user.id))
        )
        return _FundedLLM(llm, funding.provider_id, self._settings, quota_scope)

    async def _publish_after_commit(
        self, knowledge_base_id: uuid.UUID, evaluation_run_id: uuid.UUID
    ) -> None:
        """Commit la transaction courante, **puis** publie la tâche qui va la relire.

        Même invariant, pour la même raison, que `DocumentService.
        _publish_after_commit` (`app/services/documents.py`) : une tâche
        publiée avant le `COMMIT` est décrochée par un worker qui, sous READ
        COMMITTED et sur sa propre connexion, ne verrait pas encore le run —
        `NotFoundError`/`None` silencieux selon la garde de
        `run_pending_evaluation`, jamais une exception qui alerterait. Ni
        `BackgroundTasks`, ni la fermeture des dépendances `yield` ne
        donnent cette garantie (vérifié empiriquement lors du câblage du
        pipeline d'ingestion, Phase 4) : ce commit explicite est donc requis
        ici aussi, au seul endroit qui connaisse à la fois le run et la
        tâche qui en dépend.

        `knowledge_base_id` est un paramètre à part entière, pas un détail —
        `run_async_evaluation` (`app.workers.tasks.evaluations`) l'exige en
        plus de `evaluation_run_id` (sa propre garde d'accès en a besoin,
        `_run_pending_evaluation` recharge la base avant le run). Un seul
        appelant existe (`create_pending_run` ci-dessus), le nom de la tâche
        n'a donc plus besoin d'être un paramètre : régression trouvée en
        campagne E2E — `.delay(str(evaluation_run_id))` seul faisait échouer
        `POST .../evaluations` en 500 (`TypeError: run_async_evaluation()
        missing 1 required positional argument`), invisible aux tests
        existants parce qu'aucun ne fait passer un appel par la vraie
        validation de signature de Celery (`Task.apply_async` →
        `check_arguments`) : les doubles de test (`DispatchProbe`,
        `tests/api/v1/test_pipeline_dispatch.py`) et les appels directs à
        `run_async_evaluation(...)` (`tests/workers/test_evaluation_tasks.py`)
        contournent tous les deux ce point précis.
        """
        await self._db.commit()
        task_by_name(RUN_ASYNC_EVALUATION_TASK).delay(
            str(knowledge_base_id), str(evaluation_run_id)
        )
        _logger.info(
            "Run d'évaluation asynchrone publié",
            extra={
                "knowledge_base_id": str(knowledge_base_id),
                "evaluation_run_id": str(evaluation_run_id),
                "celery_task": RUN_ASYNC_EVALUATION_TASK,
            },
        )

    # ─── Construction d'un item ──────────────────────────────────────────────

    async def _generate_items(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        funded: _FundedLLM,
        pool: Sequence[Document],
        target: int,
    ) -> list[EvaluationItem]:
        """Boucle de génération partagée par `create_run` et `run_pending_evaluation`.

        `pool` est déjà mélangé par l'appelant (`random.shuffle` porte sur
        l'identité du tirage, pas sur la génération elle-même) ; `target`
        borne le nombre d'items produits, jamais le nombre de documents
        tentés (un document sans Q/R exploitable est ignoré, cf. docstring
        du module).

        **Quota épuisé en cours de run** (`TooManyRequestsError`, ADR-054) :
        la boucle s'arrête et rend les items déjà produits plutôt que de jeter
        des appels déjà payés — même issue qu'un run à court de documents
        exploitables (`item_count < sample_size`). Sans aucun item, l'erreur
        remonte. Toute autre erreur, dont `ServiceUnavailableError` (compteur
        injoignable), remonte telle quelle.
        """
        items: list[EvaluationItem] = []
        for document in pool:
            if len(items) >= target:
                break
            try:
                item = await self._build_item(principal, knowledge_base, document, funded)
            except TooManyRequestsError:
                if not items:
                    raise
                _logger.warning(
                    "Quota LLM épuisé en cours d'évaluation, run arrêté avec ses items",
                    extra={
                        "knowledge_base_id": str(knowledge_base.id),
                        "item_count": len(items),
                        "target": target,
                    },
                )
                break
            if item is not None:
                items.append(item)
        return items

    async def _build_item(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        document: Document,
        funded: _FundedLLM,
    ) -> EvaluationItem | None:
        """Génère et note une question pour `document` ; `None` si rien d'exploitable n'en sort.

        Jusqu'à trois appels LLM, chacun imputé juste avant d'avoir lieu :
        génération (`funded.complete`), réponse (`RetrievalService.chat`),
        jugement (`funded.complete`).
        """
        passage = await self._pick_passage(knowledge_base, document)
        if passage is None:
            _logger.warning(
                "Aucun fragment exploitable pour la génération d'évaluation",
                extra={"document_id": str(document.id)},
            )
            return None

        generated = await self._generate_question(funded, passage)
        if generated is None:
            _logger.warning(
                "Réponse de génération de Q/R non exploitable, document ignoré",
                extra={"document_id": str(document.id)},
            )
            return None
        question, reference_answer = generated

        retrieval_rank = await self._retrieval_rank(principal, knowledge_base, document, question)
        generated_answer = await self._generate_answer(
            principal, knowledge_base, question, funded.provider_id
        )

        judged = await self._judge(funded, question, reference_answer, generated_answer)
        if judged is None:
            _logger.warning(
                "Réponse du juge non exploitable, question ignorée",
                extra={"document_id": str(document.id)},
            )
            return None
        judge_score, judge_rationale = judged

        return EvaluationItem(
            source_document_id=document.id,
            question=question,
            reference_answer=reference_answer,
            generated_answer=generated_answer,
            retrieval_rank=retrieval_rank,
            judge_score=judge_score,
            judge_rationale=judge_rationale,
        )

    async def _eligible_documents(self, knowledge_base_id: uuid.UUID) -> Sequence[Document]:
        """Documents dont le job courant a atteint `INDEXED` et qui ont une référence moteur."""
        statement = select(Document).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == DocumentStatus.INDEXED,
            Document.ragflow_document_external_id.is_not(None),
        )
        return (await self._db.execute(statement)).scalars().all()

    async def _pick_passage(self, knowledge_base: KnowledgeBase, document: Document) -> str | None:
        """Tire un passage au hasard parmi la première page de fragments du document.

        `None` si le document n'a en réalité aucun fragment non vide — cas
        défensif, théoriquement impossible pour un document `INDEXED` mais pas
        garanti par contrat côté moteur.
        """
        dataset_external_id = knowledge_base.ragflow_dataset_external_id
        if dataset_external_id is None or document.ragflow_document_external_id is None:
            return None
        page = await self._engine.list_document_chunks(
            EngineDatasetRef(external_id=dataset_external_id),
            EngineDocumentRef(external_id=document.ragflow_document_external_id),
            page_size=_CHUNK_POOL_SIZE,
        )
        candidates = [chunk.content for chunk in page.chunks if chunk.content.strip()]
        if not candidates:
            return None
        return random.choice(candidates)

    async def _generate_question(self, llm: _FundedLLM, passage: str) -> tuple[str, str] | None:
        """Un appel `LLMPort.complete()` : question + réponse de référence, en JSON strict."""
        messages = [
            ChatMessage(role="system", content=_GENERATION_SYSTEM_PROMPT),
            ChatMessage(role="user", content=passage),
        ]
        raw = await llm.complete(messages, max_tokens=_GENERATION_MAX_TOKENS)
        parsed = _extract_json_object(raw)
        if parsed is None:
            return None
        question = parsed.get("question")
        answer = parsed.get("answer")
        if not isinstance(question, str) or not question.strip():
            return None
        if not isinstance(answer, str) or not answer.strip():
            return None
        return question.strip(), answer.strip()

    async def _retrieval_rank(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        document: Document,
        question: str,
    ) -> int | None:
        """Rang (1-based) de `document` dans `RetrievalService.search()` pour `question`."""
        response = await self._retrieval.search(
            principal, RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query=question)
        )
        for rank, result in enumerate(response.results, start=1):
            if result.document_id == document.id:
                return rank
        return None

    async def _generate_answer(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        question: str,
        llm_provider_id: str,
    ) -> str:
        """Réponse complète de `RetrievalService.chat()`, flux consommé jusqu'au bout.

        `via_evaluation=True` (addendum 2026-09-21 à ADR-054) : cet appel doit
        imputer le compteur **d'évaluation**, pas celui du chat — sans quoi
        seuls 2 des 3 appels d'un item (génération, jugement, tous deux via
        `_FundedLLM.complete`) rejoindraient le nouveau compteur dédié, et
        cette réponse continuerait de vider le plafond de chat du compte
        exactement comme avant cet addendum.
        """
        stream = await self._retrieval.chat(
            principal,
            RetrievalChatRequest(
                knowledge_base_id=knowledge_base.id,
                messages=[ChatTurn(role="user", content=question)],
                llm_provider_id=llm_provider_id,
            ),
            via_evaluation=True,
        )
        fragments = [fragment async for fragment in stream.tokens]
        return "".join(fragments)

    async def _judge(
        self, llm: _FundedLLM, question: str, reference_answer: str, generated_answer: str
    ) -> tuple[float, str] | None:
        """Second appel `LLMPort.complete()`, même fournisseur : note 0.0 à 1.0 + justification."""
        prompt = (
            f"Question : {question}\n\n"
            f"Réponse de référence : {reference_answer}\n\n"
            f"Réponse générée à noter : {generated_answer}"
        )
        messages = [
            ChatMessage(role="system", content=_JUDGE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
        raw = await llm.complete(messages, max_tokens=_JUDGE_MAX_TOKENS)
        parsed = _extract_json_object(raw)
        if parsed is None:
            return None
        score = parsed.get("score")
        rationale = parsed.get("rationale")
        # `bool` est une sous-classe d'`int` en Python : exclu explicitement, un
        # JSON `true`/`false` n'est pas une note valide même si `isinstance`
        # le laisserait passer.
        if isinstance(score, bool) or not isinstance(score, int | float):
            return None
        if not isinstance(rationale, str) or not rationale.strip():
            return None
        # Le juge peut déborder de [0, 1] malgré la consigne : borné plutôt que
        # rejeté, la contrainte `CHECK` de la table l'exigerait de toute façon.
        clamped = max(0.0, min(1.0, float(score)))
        return clamped, rationale.strip()

    async def _items_by_run(
        self, run_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[EvaluationItem]]:
        """Regroupe les items de plusieurs runs en une seule requête (`GET .../evaluations`)."""
        statement = select(EvaluationItem).where(EvaluationItem.evaluation_run_id.in_(run_ids))
        rows = (await self._db.execute(statement)).scalars().all()
        grouped: dict[uuid.UUID, list[EvaluationItem]] = {}
        for item in rows:
            grouped.setdefault(item.evaluation_run_id, []).append(item)
        return grouped
