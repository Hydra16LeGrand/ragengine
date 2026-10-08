"""Plafonds d'usage de la clé LLM plateforme (`app/services/identity/platform_llm_budget.py`,
ADR-047) — comptage réel contre Redis, même politique que `tests/test_rate_limit.py` : skip
si aucun Redis n'est joignable, un `INCR`/`EXPIRE` atomique ne se simule pas fidèlement sans
un vrai serveur.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError, TooManyRequestsError
from app.core.rate_limit import close_rate_limiter
from app.services.identity.platform_llm_budget import (
    LlmQuotaScope,
    QuotaSubject,
    active_user_ids,
    charge_llm_call,
    enforce_evaluation_quota,
    enforce_global_budget,
    enforce_user_quota,
    global_budget_limit,
    peek_evaluation_quota,
    peek_global_budget,
    peek_user_quota,
)

pytestmark = pytest.mark.usefixtures("explicit_llm_caps")

_HOST_PORT = os.environ.get("RAGENGINE_REDIS_HOST_PORT", "6380")
_EXPLICIT_URL = os.environ.get("TEST_REDIS_URL", "")
_TEST_DB_INDEX = 9


def _resolve_test_redis_url() -> str:
    if _EXPLICIT_URL:
        return _EXPLICIT_URL
    return f"redis://localhost:{_HOST_PORT}/{_TEST_DB_INDEX}"


@pytest.fixture
async def redis_test_url() -> AsyncIterator[str]:
    """URL d'un Redis réellement joignable, ou `skip` sinon — même fixture que `test_rate_limit`."""
    url = _resolve_test_redis_url()
    probe = redis_asyncio.from_url(url)  # type: ignore[no-untyped-call]
    try:
        await probe.ping()
    except RedisError:
        pytest.skip(f"Redis injoignable sur {url} — démarrer la stack Docker ou TEST_REDIS_URL.")
    finally:
        await probe.aclose()

    yield url

    cleanup = redis_asyncio.from_url(url)  # type: ignore[no-untyped-call]
    try:
        await cleanup.flushdb()
    finally:
        await cleanup.aclose()


@pytest.fixture(autouse=True)
async def _fresh_rate_limiter_singleton(
    redis_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Force le singleton du limiteur sur la base de test (9), isolée de l'app réelle."""
    monkeypatch.setenv("REDIS_URL", redis_test_url)
    await close_rate_limiter()
    yield
    await close_rate_limiter()


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


async def test_global_budget_limit_is_the_budget_divided_by_the_assumed_cost() -> None:
    """Arithmétique pure — aucun Redis requis."""
    settings = _settings(
        platform_llm_daily_budget_usd=10.0, platform_llm_assumed_cost_per_message_usd=0.02
    )
    assert global_budget_limit(settings) == 500


async def test_global_budget_limit_never_reaches_zero() -> None:
    """Un budget minuscule reste au moins 1 message, pas un plafond inutilisable."""
    settings = _settings(
        platform_llm_daily_budget_usd=0.001, platform_llm_assumed_cost_per_message_usd=1.0
    )
    assert global_budget_limit(settings) == 1


async def test_enforce_user_quota_allows_up_to_the_limit_then_blocks(redis_test_url: str) -> None:
    settings = _settings(platform_llm_user_daily_limit=2)
    user_id = uuid.uuid4()

    await enforce_user_quota(settings, user_id)
    await enforce_user_quota(settings, user_id)
    with pytest.raises(TooManyRequestsError, match="Plafond quotidien de messages"):
        await enforce_user_quota(settings, user_id)


async def test_enforce_global_budget_blocks_once_the_derived_message_cap_is_reached(
    redis_test_url: str,
) -> None:
    settings = _settings(
        platform_llm_daily_budget_usd=0.02, platform_llm_assumed_cost_per_message_usd=0.01
    )
    assert global_budget_limit(settings) == 2

    await enforce_global_budget(settings)
    await enforce_global_budget(settings)
    with pytest.raises(TooManyRequestsError, match="budget quotidien"):
        await enforce_global_budget(settings)


async def test_peek_does_not_increment_the_counter(redis_test_url: str) -> None:
    """Une lecture admin ne doit jamais elle-même consommer un message du plafond."""
    settings = _settings(platform_llm_user_daily_limit=5)
    user_id = uuid.uuid4()

    used_before, limit = await peek_user_quota(settings, user_id)
    assert (used_before, limit) == (0, 5)

    await enforce_user_quota(settings, user_id)

    used_after, _ = await peek_user_quota(settings, user_id)
    used_after_again, _ = await peek_user_quota(settings, user_id)
    assert used_after == 1
    assert used_after_again == 1  # la deuxième lecture ne l'a pas fait grimper à 2


async def test_enforce_user_quota_tracks_the_active_user_id(redis_test_url: str) -> None:
    """Le tableau de bord admin (ADR-048) doit pouvoir découvrir qui a consommé, pas
    seulement combien — sans identifiant connu à l'avance, `peek_user_quota` seul ne le
    permettrait pas."""
    settings = _settings(platform_llm_user_daily_limit=5)
    user_id = uuid.uuid4()

    assert user_id not in {uuid.UUID(raw) for raw in await active_user_ids(settings)}
    await enforce_user_quota(settings, user_id)
    assert str(user_id) in await active_user_ids(settings)


async def test_enforce_user_quota_tracks_even_on_a_blocked_call(redis_test_url: str) -> None:
    """Un compte qui vient de heurter son plafond doit rester visible — c'est justement
    celui que l'admin doit voir, pas un compte qui disparaît de la liste au pire moment."""
    settings = _settings(platform_llm_user_daily_limit=1)
    user_id = uuid.uuid4()

    await enforce_user_quota(settings, user_id)
    with pytest.raises(TooManyRequestsError):
        await enforce_user_quota(settings, user_id)

    assert str(user_id) in await active_user_ids(settings)


# ─── ADR-054 : imputation par appel LLM, ordre des compteurs, échec fermé ────


async def test_concurrent_calls_never_exceed_the_cap(redis_test_url: str) -> None:
    """Aucune fenêtre « vérifier puis agir » : 20 imputations simultanées sur un plafond de 5
    → exactement 5 passent, et le budget global n'en voit que 5."""
    settings = _settings(platform_llm_user_daily_limit=5)
    scope = LlmQuotaScope(QuotaSubject.USER, str(uuid.uuid4()))

    outcomes = await asyncio.gather(
        *(charge_llm_call(settings, scope) for _ in range(20)), return_exceptions=True
    )

    assert sum(outcome is None for outcome in outcomes) == 5
    assert all(
        isinstance(outcome, TooManyRequestsError) for outcome in outcomes if outcome is not None
    )
    assert (await peek_global_budget(settings))[0] == 5


async def test_an_unreachable_redis_refuses_the_call_rather_than_letting_it_through(
    redis_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échec fermé, à l'inverse du rate limiting anti-rafale : ce compteur protège une dépense
    réelle. Port 1 : connexion refusée immédiatement, sans attente DNS."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    await close_rate_limiter()
    settings = _settings()

    with pytest.raises(ServiceUnavailableError):
        await charge_llm_call(settings, LlmQuotaScope(QuotaSubject.USER, str(uuid.uuid4())))


# ─── Addendum 2026-09-21 à ADR-054 : plafond dédié aux évaluations ───────────


async def test_enforce_evaluation_quota_allows_up_to_the_limit_then_blocks(
    redis_test_url: str,
) -> None:
    settings = _settings(platform_llm_evaluation_daily_limit=2)
    user_id = uuid.uuid4()

    await enforce_evaluation_quota(settings, user_id)
    await enforce_evaluation_quota(settings, user_id)
    with pytest.raises(TooManyRequestsError, match="évaluation"):
        await enforce_evaluation_quota(settings, user_id)


async def test_enforce_evaluation_quota_is_independent_of_the_chat_quota_for_the_same_account(
    redis_test_url: str,
) -> None:
    """Même compte, deux compteurs : épuiser celui du chat ne touche pas celui des
    évaluations, et réciproquement — c'est tout l'objet de cet addendum."""
    settings = _settings(platform_llm_user_daily_limit=1, platform_llm_evaluation_daily_limit=1)
    user_id = uuid.uuid4()

    await enforce_user_quota(settings, user_id)
    with pytest.raises(TooManyRequestsError, match="Plafond quotidien"):
        await enforce_user_quota(settings, user_id)

    # Le plafond d'évaluation, séparé, n'a pas été entamé par le chat.
    await enforce_evaluation_quota(settings, user_id)
    with pytest.raises(TooManyRequestsError, match="évaluation"):
        await enforce_evaluation_quota(settings, user_id)


async def test_a_full_default_scheduled_evaluation_run_fits_under_the_new_default(
    redis_test_url: str,
) -> None:
    """Le défaut (24) est calibré sur le pire cas d'un run programmé complet sous les
    valeurs par défaut : `evaluation_scheduled_sample_size` (8) x 3 appels/item."""
    settings = _settings()
    assert settings.platform_llm_evaluation_daily_limit == 24
    assert settings.evaluation_scheduled_sample_size == 8
    user_id = uuid.uuid4()

    worst_case_calls = settings.evaluation_scheduled_sample_size * 3
    for _ in range(worst_case_calls):
        await enforce_evaluation_quota(settings, user_id)

    used, limit = await peek_evaluation_quota(settings, user_id)
    assert (used, limit) == (worst_case_calls, 24)


async def test_charge_llm_call_routes_the_evaluation_subject_to_its_own_counter(
    redis_test_url: str,
) -> None:
    settings = _settings()
    user_id = uuid.uuid4()

    await charge_llm_call(settings, LlmQuotaScope(QuotaSubject.EVALUATION, str(user_id)))

    assert (await peek_evaluation_quota(settings, user_id))[0] == 1
    assert (await peek_user_quota(settings, user_id))[0] == 0
    assert (await peek_global_budget(settings))[0] == 1


async def test_evaluation_quota_still_fails_closed_when_redis_is_unreachable(
    redis_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même politique d'échec fermé que le reste des compteurs de dépense (ADR-054)."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    await close_rate_limiter()
    settings = _settings()

    with pytest.raises(ServiceUnavailableError):
        await charge_llm_call(settings, LlmQuotaScope(QuotaSubject.EVALUATION, str(uuid.uuid4())))
