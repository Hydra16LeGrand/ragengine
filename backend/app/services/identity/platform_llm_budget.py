"""Plafonds de dépense LLM hors clé personnelle (ADR-047, ADR-048, ADR-054).

Compteurs Redis indépendants à fenêtre fixe 24h — un par **sujet** (compte pour le chat,
compte pour les évaluations — addendum 2026-09-21 à ADR-054 —, plus ceux qu'un greffon
déclare, `Plugin.llm_quota_rules`) et un budget global —, réutilisant le même mécanisme
`INCR`+`EXPIRE` que `app.core.rate_limit` (`RedisRateLimiter.hit`) plutôt qu'un nouveau
système de comptage : un quota journalier n'est qu'une fenêtre de rate-limiting plus longue.

**Chat et évaluations, même compte, deux compteurs.** `QuotaSubject.USER` (chat,
`RetrievalService.chat`) et `QuotaSubject.EVALUATION` (`EvaluationService._fund`) portent
chacun leur propre clé Redis pour le même identifiant de compte : un run d'évaluation ne
doit ni s'arrêter en route sur le plafond de chat, ni le vider pour le reste de la
journée — les deux usages ont des profils de consommation trop différents (un tour de
chat coûte un ou deux appels ; un item d'évaluation en coûte jusqu'à trois, et un run en
tente plusieurs) pour partager un seul plafond calibré sur l'un des deux.

**Ils s'appliquent dès que la clé qui paie n'est pas la clé personnelle de l'appelant**
(ADR-054). L'unité est l'**appel LLM**, pas la requête : un tour de chat qui reformule sa
requête de retrieval (ADR-049) en consomme deux (`charge_llm_call` appelé deux fois,
toujours *avant* l'appel correspondant).

**Aucune fenêtre « vérifier puis agir ».** `hit` incrémente atomiquement (script Lua) *puis*
compare : N requêtes concurrentes sur un compteur à `limite - 1` obtiennent chacune une
valeur distincte, une seule passe. Un appel refusé incrémente quand même — sans effet
observable, le compteur étant déjà au-delà du plafond jusqu'à la fin de la fenêtre.

**Échec fermé.** Contrairement au rate limiting anti-rafale (`app.core.rate_limit`, qui
laisse passer si Redis est injoignable), un compteur qui protège une dépense réelle refuse
l'appel : `ServiceUnavailableError` (503). Laisser passer faute de pouvoir compter
rouvrirait exactement la dépense sans plafond que ce module ferme.

**Le plafond de dépense globale est une approximation assumée, pas une
réconciliation de facturation.** `LLMPort.stream()` ne restitue aucun compte
de jetons par appel aujourd'hui (juste du texte) : étendre les trois
adaptateurs pour un vrai suivi de coût est un chantier séparé, hors mandat
ici. `platform_llm_daily_budget_usd / platform_llm_assumed_cost_per_message_usd`
donne un plafond d'*appels*, appliqué au même compteur que les plafonds par sujet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError, TooManyRequestsError
from app.core.plugins import extra_llm_quota_rules, llm_global_budget_detail
from app.core.rate_limit import RateLimiterUnavailableError, get_rate_limiter

_logger = logging.getLogger(__name__)

_DAY_SECONDS = 86400

_GLOBAL_BUDGET_KEY = "platform-llm-budget:global"

_ACTIVE_USERS_KEY = "platform-llm-budget:active-users"
_ACTIVE_EVALUATION_USERS_KEY = "platform-llm-budget:active-evaluation-users"

_COUNTER_UNAVAILABLE = (
    "Le compteur de quota du chat génératif est momentanément indisponible — réessayez dans "
    "un instant, ou renseignez votre propre clé LLM."
)


class QuotaSubject(StrEnum):
    """Compteurs d'identité du cœur. Un greffon en déclare d'autres (`Plugin.llm_quota_rules`)."""

    USER = "user"
    """Compte réel authentifié (session ou clé API) sans clé personnelle — chat génératif."""
    EVALUATION = "evaluation"
    """Compte réel demandant ou possédant une évaluation, sans clé personnelle (addendum
    2026-09-21 à ADR-054). Compteur **distinct** de `USER` bien que l'identifiant soit le
    même compte : un run programmé (jusqu'à 24 appels sous les valeurs par défaut) ne doit
    ni s'arrêter en route sur le plafond de chat (20/jour par défaut) ni épuiser ce dernier
    pour le reste de la journée — les deux usages sont désormais des budgets indépendants,
    le budget global restant la seule barrière commune."""


@dataclass(frozen=True, slots=True)
class LlmQuotaScope:
    """Compteur d'identité d'un appel LLM hors clé personnelle.

    `subject` vaut un `QuotaSubject` du cœur ou un sujet déclaré par un greffon.
    """

    subject: str
    identifier: str


def _same(identifier: str) -> str:
    return identifier


@dataclass(frozen=True, slots=True)
class QuotaRule:
    """Comment un sujet est compté : clé Redis, plafond, message de refus, suivi d'activité."""

    key: Callable[[str], str]
    """Clé Redis du compteur, à partir de l'identifiant de `LlmQuotaScope`."""
    limit: Callable[[Settings], int | None]
    """Plafond journalier, lu dans la configuration à chaque appel. `None` : pas de plafond,
    aucun compteur."""
    detail: str
    """Message du `429` quand le plafond est atteint."""
    track_set: str
    """Ensemble Redis des identifiants actifs du jour (lecture d'exploitation, ADR-048)."""
    member: Callable[[str], str] = field(default=_same)
    """Identifiant retenu dans `track_set` (par défaut, celui du scope)."""


def _user_quota_key(user_id: object) -> str:
    return f"platform-llm-budget:user:{user_id}"


def _evaluation_quota_key(user_id: object) -> str:
    return f"platform-llm-budget:evaluation-user:{user_id}"


async def _enforce(
    settings: Settings,
    key: str,
    limit: int | None,
    detail: str,
    *,
    track_set: str | None = None,
    track_member: str | None = None,
) -> None:
    if limit is None:
        return  # Aucun plafond configuré : rien à compter, Redis n'est pas sollicité.
    limiter = get_rate_limiter(settings)
    try:
        outcome = await limiter.hit(key, limit=limit, window_seconds=_DAY_SECONDS)
        # Enregistré même sur un appel refusé : un compte qui vient de heurter son
        # plafond est justement celui qu'une lecture d'exploitation (ADR-048) doit
        # pouvoir montrer, pas seulement ceux encore sous la limite.
        if track_set is not None and track_member is not None:
            await limiter.remember_member(track_set, track_member, window_seconds=_DAY_SECONDS)
    except RateLimiterUnavailableError as exc:
        _logger.error("Compteur de dépense LLM injoignable (%s) — appel refusé.", key)
        raise ServiceUnavailableError(_COUNTER_UNAVAILABLE) from exc
    if not outcome.allowed:
        raise TooManyRequestsError(
            detail, extensions={"retry_after_seconds": outcome.retry_after_seconds}
        )


_USER_QUOTA_DETAIL = (
    "Plafond quotidien de messages atteint pour ce compte — réessayez demain, "
    "ou renseignez votre propre clé LLM pour un accès illimité."
)
_EVALUATION_QUOTA_DETAIL = (
    "Plafond quotidien d'appels d'évaluation atteint pour ce compte — réessayez demain, "
    "ou renseignez votre propre clé LLM pour un accès illimité."
)

CORE_RULES: dict[str, QuotaRule] = {
    QuotaSubject.USER: QuotaRule(
        key=_user_quota_key,
        limit=lambda settings: settings.platform_llm_user_daily_limit,
        detail=_USER_QUOTA_DETAIL,
        track_set=_ACTIVE_USERS_KEY,
    ),
    QuotaSubject.EVALUATION: QuotaRule(
        key=_evaluation_quota_key,
        limit=lambda settings: settings.platform_llm_evaluation_daily_limit,
        detail=_EVALUATION_QUOTA_DETAIL,
        track_set=_ACTIVE_EVALUATION_USERS_KEY,
    ),
}


def quota_rule(subject: str) -> QuotaRule:
    """Règle de comptage d'un sujet — du cœur, ou déclarée par un greffon.

    Raises:
        ValueError: sujet inconnu — une erreur de programmation, jamais un état
            normal : un appel LLM ne doit jamais partir sans compteur.
    """
    # Une règle de greffon l'emporte : elle peut reprendre celle du cœur avec son propre message.
    rule = extra_llm_quota_rules().get(subject) or CORE_RULES.get(subject)
    if rule is None:
        raise ValueError(f"Sujet de quota LLM inconnu : {subject!r}.")
    return rule


async def charge_llm_call(settings: Settings, scope: LlmQuotaScope) -> None:
    """Impute **un** appel LLM hors clé personnelle, avant qu'il ait lieu (ADR-054).

    Compteur d'identité d'abord, budget global ensuite. L'ordre inverse (celui
    d'ADR-047) laissait une identité déjà au plafond incrémenter le compteur
    global à chaque requête refusée : une seule identité insistante épuisait le
    budget du jour pour tout le monde sans qu'aucun appel LLM n'ait lieu.

    Raises:
        TooManyRequestsError: plafond d'identité ou budget global atteint.
        ServiceUnavailableError: Redis injoignable (échec fermé).
        ValueError: sujet de quota inconnu.
    """
    await enforce_quota(settings, scope)
    await enforce_global_budget(settings)


async def enforce_quota(settings: Settings, scope: LlmQuotaScope) -> None:
    """Plafond journalier du sujet de `scope`, sans le budget global."""
    rule = quota_rule(scope.subject)
    await _enforce(
        settings,
        rule.key(scope.identifier),
        rule.limit(settings),
        rule.detail,
        track_set=rule.track_set,
        track_member=rule.member(scope.identifier),
    )


async def peek_quota(settings: Settings, scope: LlmQuotaScope) -> tuple[int, int | None]:
    """`(appels consommés aujourd'hui, plafond ou None)` pour un sujet — sans rien incrémenter."""
    rule = quota_rule(scope.subject)
    used = await get_rate_limiter(settings).peek(rule.key(scope.identifier))
    return used, rule.limit(settings)


async def active_identifiers(settings: Settings, subject: str) -> set[str]:
    """Identifiants ayant consommé ce sujet aujourd'hui, y compris sur un appel refusé."""
    return await get_rate_limiter(settings).members(quota_rule(subject).track_set)


async def enforce_global_budget(settings: Settings) -> None:
    """Coupe-circuit de dépense globale — vérifié après le plafond d'identité (`charge_llm_call`).

    N'est jamais consulté pour un appelant muni de sa propre clé LLM : cette
    fonction n'est appelée que hors clé personnelle, par construction de
    l'appelant (`RetrievalService.chat`).
    """
    max_messages = global_budget_limit(settings)
    await _enforce(
        settings,
        _GLOBAL_BUDGET_KEY,
        max_messages,
        llm_global_budget_detail()
        or "Le budget quotidien de la clé LLM du serveur est épuisé pour aujourd'hui — "
        "réessayez demain, ou renseignez votre propre clé LLM pour un accès illimité.",
    )


async def enforce_user_quota(settings: Settings, user_id: object) -> None:
    """Plafond journalier par compte authentifié sans clé LLM personnelle."""
    await enforce_quota(settings, LlmQuotaScope(QuotaSubject.USER, str(user_id)))


async def enforce_evaluation_quota(settings: Settings, user_id: object) -> None:
    """Plafond journalier par compte, dédié aux évaluations (addendum 2026-09-21 à ADR-054).

    Compteur Redis distinct de `enforce_user_quota` (chat) bien que l'identifiant
    soit le même compte : un run programmé complet (jusqu'à 24 appels sous les
    valeurs par défaut, `Settings.platform_llm_evaluation_daily_limit`) ne doit
    plus ni s'arrêter en route sur le plafond de chat, ni le vider pour le reste
    de la journée. Le budget global (`enforce_global_budget`, appelé juste après
    par `charge_llm_call`) reste la seule barrière commune aux deux usages.
    """
    await enforce_quota(settings, LlmQuotaScope(QuotaSubject.EVALUATION, str(user_id)))


# ─── Lecture des compteurs (exploitation, ADR-047/048) ───────────────────────
# Pas d'incrément : `RedisRateLimiter.peek`, jamais `.hit`.


def global_budget_limit(settings: Settings) -> int | None:
    """Plafond de messages dérivé du budget, `None` sans budget configuré."""
    budget = settings.platform_llm_daily_budget_usd
    if budget is None:
        return None
    cost_per_message = settings.platform_llm_assumed_cost_per_message_usd
    return max(1, int(budget / cost_per_message))


async def peek_global_budget(settings: Settings) -> tuple[int, int | None]:
    """`(messages consommés aujourd'hui, plafond)` hors clé personnelle, tous usages confondus."""
    used = await get_rate_limiter(settings).peek(_GLOBAL_BUDGET_KEY)
    return used, global_budget_limit(settings)


async def peek_user_quota(settings: Settings, user_id: object) -> tuple[int, int | None]:
    """`(messages consommés aujourd'hui, plafond)` pour un compte donné."""
    return await peek_quota(settings, LlmQuotaScope(QuotaSubject.USER, str(user_id)))


async def peek_evaluation_quota(settings: Settings, user_id: object) -> tuple[int, int | None]:
    """`(appels d'évaluation consommés aujourd'hui, plafond)` pour un compte donné."""
    return await peek_quota(settings, LlmQuotaScope(QuotaSubject.EVALUATION, str(user_id)))


async def active_user_ids(settings: Settings) -> set[str]:
    """Identifiants de compte ayant consommé un appel de chat hors clé personnelle aujourd'hui."""
    return await active_identifiers(settings, QuotaSubject.USER)


async def active_evaluation_user_ids(settings: Settings) -> set[str]:
    """Identifiants de compte ayant consommé le plafond d'évaluation aujourd'hui."""
    return await active_identifiers(settings, QuotaSubject.EVALUATION)
