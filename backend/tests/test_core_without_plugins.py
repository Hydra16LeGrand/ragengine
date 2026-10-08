"""Le cœur seul (ADR-069) : complet et sûr sans aucun greffon.

Ces tests construisent l'application sous `plugins_overridden(())` : ils prouvent,
dans le dépôt qui porte aussi des greffons, ce que sert un moteur auto-hébergé qui
n'en a aucun — pas de route de greffon, et une connexion exigée pour tout.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins import admits_anonymous, get_plugins
from app.domain.scopes import CORE_SCOPES, available_scopes
from app.main import API_PREFIX
from app.services.identity.errors import AuthenticationRequiredError
from app.services.identity.principal import ANONYMOUS
from app.services.llm_funding import LlmFundingPolicy, LlmKeySource, get_llm_funding_policy
from app.services.resource_quotas import NoResourceQuotas, resolve_resource_quotas

PLATFORM_ROUTE_MARKERS = ("chatbot", "guest", "/admin", "credentials")


def test_the_core_alone_serves_no_platform_route(core_app: FastAPI) -> None:
    paths = set(core_app.openapi()["paths"])

    assert f"{API_PREFIX}/knowledge-bases" in paths
    assert f"{API_PREFIX}/retrieval/chat" in paths
    assert not [path for path in paths if any(m in path for m in PLATFORM_ROUTE_MARKERS)]


def test_every_extension_point_falls_back_to_the_core_default(without_plugins: None) -> None:
    assert get_plugins() == ()
    assert admits_anonymous() is False
    assert available_scopes() == CORE_SCOPES
    assert type(get_llm_funding_policy()) is LlmFundingPolicy
    assert isinstance(resolve_resource_quotas(None, Settings()), NoResourceQuotas)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/knowledge-bases", None),
        ("GET", "/evaluations/summary", None),
        (
            "POST",
            "/retrieval/search",
            {"knowledge_base_id": "00000000-0000-0000-0000-000000000000", "query": "q"},
        ),
    ],
)
async def test_an_anonymous_visitor_must_log_in_for_everything(
    core_client: AsyncClient, method: str, path: str, body: object
) -> None:
    response = await core_client.request(method, f"{API_PREFIX}{path}", json=body)

    assert response.status_code == 401
    assert response.json()["type"] == "/problems/authentication-required"


async def test_the_essential_account_journey_needs_no_plugin(core_client: AsyncClient) -> None:
    """Inscription, connexion, création et lecture d'une base : rien n'y dépend d'un greffon."""
    credentials = {"email": "premier@example.com", "password": "mot-de-passe-solide-2026"}
    registered = await core_client.post(
        f"{API_PREFIX}/users", json={**credentials, "display_name": "Premier"}
    )
    assert registered.status_code == 201
    logged_in = await core_client.post(f"{API_PREFIX}/sessions", json=credentials)
    assert logged_in.status_code == 201
    assert "guest_quota" not in logged_in.json()

    created = await core_client.post(
        f"{API_PREFIX}/knowledge-bases", json={"name": "Contrats", "embedding_model_id": "bge-m3"}
    )
    assert created.status_code == 201
    assert not [key for key in created.json() if key.startswith("chatbot")]

    listed = await core_client.get(f"{API_PREFIX}/knowledge-bases")
    assert [kb["name"] for kb in listed.json()["data"]] == ["Contrats"]


def test_the_core_funding_policy_never_funds_a_caller_without_an_account() -> None:
    policy = LlmFundingPolicy()

    assert policy.shared_funding(Settings(), "claude-opus-5").source == LlmKeySource.SERVER
    with pytest.raises(AuthenticationRequiredError):
        policy.quota_scope(ANONYMOUS, via_evaluation=False, client_ip="203.0.113.1")


async def test_the_core_funding_policy_uses_the_server_key_and_forces_the_model(
    db_session: AsyncSession,
) -> None:
    from tests.services.test_knowledge_bases import account, principal_of

    owner = await account(db_session)
    settings = Settings(platform_llm_model_id="claude-haiku-4-5")

    funding = await LlmFundingPolicy().resolve(
        db_session, settings, principal_of(owner), "claude-opus-5"
    )

    assert (funding.source, funding.provider_id, funding.override_api_key) == (
        LlmKeySource.SERVER,
        "claude-haiku-4-5",
        None,
    )
    assert not funding.is_personal


def test_the_core_alone_caps_nothing_and_forces_no_model(
    without_plugins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un auto-hébergeur ne subit ni plafond, ni budget, ni modèle imposé qu'il n'a pas demandés."""
    for name in (
        "RAGENGINE_PLATFORM_LLM_MODEL_ID",
        "RAGENGINE_PLATFORM_LLM_USER_DAILY_LIMIT",
        "RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT",
        "RAGENGINE_PLATFORM_LLM_DAILY_BUDGET_USD",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.platform_llm_model_id is None
    assert settings.platform_llm_user_daily_limit is None
    assert settings.platform_llm_evaluation_daily_limit is None
    assert settings.platform_llm_daily_budget_usd is None
    funding = LlmFundingPolicy().shared_funding(settings, "claude-opus-5")
    assert funding.provider_id == "claude-opus-5"


async def test_without_any_cap_a_call_never_touches_the_counters(
    without_plugins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.identity import platform_llm_budget as budget

    def unreachable(*_: object, **__: object) -> None:
        raise AssertionError("aucun compteur ne doit être sollicité sans plafond")

    monkeypatch.setattr(budget, "get_rate_limiter", unreachable)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    for subject in (budget.QuotaSubject.USER, budget.QuotaSubject.EVALUATION):
        await budget.charge_llm_call(settings, budget.LlmQuotaScope(subject, "u"))


async def test_an_operator_can_still_enable_the_caps_explicitly(
    without_plugins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(_env_file=None, RAGENGINE_PLATFORM_LLM_USER_DAILY_LIMIT=3)  # type: ignore[call-arg]

    assert settings.platform_llm_user_daily_limit == 3
    assert settings.platform_llm_daily_budget_usd is None


def test_the_core_alone_is_branded_ragengine_and_the_brand_is_a_setting(
    without_plugins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAGENGINE_APP_NAME", raising=False)
    assert Settings(_env_file=None).app_name == "RAGEngine"  # type: ignore[call-arg]

    monkeypatch.setenv("RAGENGINE_APP_NAME", "Mon Moteur")
    assert Settings(_env_file=None).app_name == "Mon Moteur"  # type: ignore[call-arg]


async def test_the_catalog_tells_the_client_that_no_model_is_forced(
    core_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("RAGENGINE_PLATFORM_LLM_MODEL_ID",):
        monkeypatch.delenv(name, raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()

    response = await core_client.get(f"{API_PREFIX}/providers")

    assert response.json()["llm_forced_model_id"] is None
