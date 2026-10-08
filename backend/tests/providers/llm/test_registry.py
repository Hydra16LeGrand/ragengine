"""Tests du registre des fournisseurs de génération.

`list_llm_providers` ne doit **jamais** joindre le réseau : sa seule source de
vérité est `Settings` (`docs/providers-architecture.md` §0). Ces tests le
vérifient en n'injectant que des clés factices.
"""

import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.llm.anthropic import ANTHROPIC_CATALOG, AnthropicLLMAdapter
from app.providers.llm.base import LLMPort
from app.providers.llm.circuit_breaker import CircuitBreakerLLMAdapter, reset_breakers
from app.providers.llm.groq import GROQ_CATALOG, GroqLLMAdapter
from app.providers.llm.openai import OPENAI_CATALOG, OpenAILLMAdapter
from app.providers.llm.registry import list_llm_providers, resolve_llm_provider

pytestmark = pytest.mark.usefixtures("_reset_llm_breakers")


@pytest.fixture
def _reset_llm_breakers() -> None:
    """Isole chaque test : l'état des disjoncteurs vit dans un registre de module."""
    reset_breakers()


ANTHROPIC_IDS = tuple(capabilities.provider_id for capabilities in ANTHROPIC_CATALOG)
OPENAI_IDS = tuple(capabilities.provider_id for capabilities in OPENAI_CATALOG)
GROQ_IDS = tuple(capabilities.provider_id for capabilities in GROQ_CATALOG)


def _availability(settings: Settings) -> dict[str, bool]:
    return {status.provider_id: status.available for status in list_llm_providers(settings)}


def test_catalog_lists_every_registered_model() -> None:
    """Le catalogue expose les trois familles enregistrées, capacités comprises."""
    statuses = list_llm_providers(Settings())

    assert [status.provider_id for status in statuses] == list(
        ANTHROPIC_IDS + OPENAI_IDS + GROQ_IDS
    )
    for status in statuses:
        assert status.vendor in {"anthropic", "openai", "groq"}
        assert status.context_window > 0
        assert status.supports_streaming is True


def test_availability_is_false_without_any_api_key() -> None:
    """Sans configuration, aucun fournisseur n'est disponible — mais tous sont listés."""
    assert not any(_availability(Settings()).values())


def test_availability_reflects_the_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ANTHROPIC_API_KEY` n'active que les modèles Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    availability = _availability(Settings())

    assert all(availability[provider_id] for provider_id in ANTHROPIC_IDS)
    assert not any(availability[provider_id] for provider_id in OPENAI_IDS)
    assert not any(availability[provider_id] for provider_id in GROQ_IDS)


def test_availability_reflects_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OPENAI_API_KEY` n'active que les modèles OpenAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    availability = _availability(Settings())

    assert all(availability[provider_id] for provider_id in OPENAI_IDS)
    assert not any(availability[provider_id] for provider_id in ANTHROPIC_IDS)
    assert not any(availability[provider_id] for provider_id in GROQ_IDS)


def test_availability_reflects_the_groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`GROQ_API_KEY` n'active que les modèles Groq."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-test")
    availability = _availability(Settings())

    assert all(availability[provider_id] for provider_id in GROQ_IDS)
    assert not any(availability[provider_id] for provider_id in ANTHROPIC_IDS)
    assert not any(availability[provider_id] for provider_id in OPENAI_IDS)


def test_all_keys_make_every_provider_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les trois clés renseignées : tout le catalogue devient disponible."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-test")

    assert all(_availability(Settings()).values())


def test_every_provider_carries_a_non_empty_human_label() -> None:
    """Défaut 3 (plan interne) : `label` remplace le mapping fait main du frontend."""
    labels = {status.provider_id: status.label for status in list_llm_providers(Settings())}

    assert all(len(label.strip()) > 0 for label in labels.values())
    assert labels["claude-opus-5"] == "Claude Opus 5"
    assert labels["llama-3.3-70b-versatile"] == "Groq — Llama 3.3 70B Versatile"


def test_provider_status_is_immutable() -> None:
    """Les lignes du catalogue sont figées : elles sont sérialisées, pas mutées."""
    status = list_llm_providers(Settings())[0]

    with pytest.raises(Exception, match="frozen"):
        status.available = True


def test_resolve_rejects_an_unknown_model() -> None:
    """Un identifiant inconnu est une erreur de validation (→ 422), pas un `KeyError`."""
    with pytest.raises(ValidationError, match="inconnu"):
        resolve_llm_provider("gpt-inexistant", Settings())


def test_resolve_rejects_a_known_but_unconfigured_model() -> None:
    """Un modèle connu mais sans clé API est refusé avant toute tentative de connexion."""
    with pytest.raises(ValidationError, match="indisponible"):
        resolve_llm_provider(ANTHROPIC_IDS[0], Settings())


def test_resolve_builds_the_anthropic_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le registre instancie bien l'adaptateur Anthropic, sous disjoncteur (ADR-019)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    provider = resolve_llm_provider("claude-opus-5", Settings())

    assert isinstance(provider, CircuitBreakerLLMAdapter)
    assert isinstance(provider, LLMPort)
    assert isinstance(provider.delegate, AnthropicLLMAdapter)
    assert provider.capabilities.provider_id == "claude-opus-5"
    assert provider.capabilities.vendor == "anthropic"


def test_resolve_builds_the_openai_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le registre instancie bien l'adaptateur OpenAI, sous disjoncteur (ADR-019)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    provider = resolve_llm_provider(OPENAI_IDS[0], Settings())

    assert isinstance(provider, CircuitBreakerLLMAdapter)
    assert isinstance(provider.delegate, OpenAILLMAdapter)
    assert provider.capabilities.vendor == "openai"


def test_resolve_builds_the_groq_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le registre instancie bien l'adaptateur Groq, sous disjoncteur (ADR-019)."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-test")

    provider = resolve_llm_provider(GROQ_IDS[0], Settings())

    assert isinstance(provider, CircuitBreakerLLMAdapter)
    assert isinstance(provider.delegate, GroqLLMAdapter)
    assert provider.capabilities.vendor == "groq"


def test_resolve_with_override_key_bypasses_server_availability() -> None:
    """Une clé personnelle rend le fournisseur utilisable même sans clé serveur.

    Chemin des clés LLM personnelles (`UserLlmKeyService`) : contrairement au
    test `..._rejects_a_known_but_unconfigured_model` ci-dessus, aucune
    `ANTHROPIC_API_KEY` n'est fournie ici — c'est précisément le cas que
    `override_api_key` doit couvrir.
    """
    provider = resolve_llm_provider(
        ANTHROPIC_IDS[0], Settings(), override_api_key="sk-ant-personal"
    )

    assert isinstance(provider, CircuitBreakerLLMAdapter)
    assert isinstance(provider.delegate, AnthropicLLMAdapter)
    assert provider.delegate._client.api_key == "sk-ant-personal"


def test_resolve_without_override_key_is_the_unchanged_default_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`override_api_key=None` (défaut) : même résultat qu'avant son introduction.

    Non-régression explicite, demandée pour cette fonctionnalité : un appelant
    qui ne connaît pas ce paramètre (tout le code existant avant cette session)
    continue d'obtenir la clé serveur.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")

    provider = resolve_llm_provider(ANTHROPIC_IDS[0], Settings(), override_api_key=None)

    assert isinstance(provider.delegate, AnthropicLLMAdapter)
    assert provider.delegate._client.api_key == "sk-ant-server"


def test_each_registered_model_can_be_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaque entrée du registre est réellement constructible — pas d'entrée morte."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-test")
    settings = Settings()

    for status in list_llm_providers(settings):
        provider = resolve_llm_provider(status.provider_id, settings)
        assert provider.capabilities.provider_id == status.provider_id
