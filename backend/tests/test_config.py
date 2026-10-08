"""Tests de la configuration : démarrage sans `.env`, DSN et briques optionnelles."""

import pytest
from pydantic import ValidationError

from app.core.config import DEV_SECRET_KEY, Environment, Settings, get_settings


def test_settings_load_without_env_file() -> None:
    """L'application doit démarrer sans fichier `.env` : tous les secrets ont un défaut."""
    settings = Settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.embedding_model_id == "bge-m3"
    assert settings.ragflow_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.langfuse_host is None


def test_get_settings_is_a_singleton() -> None:
    """`get_settings` met la configuration en cache pour tout le process."""
    assert get_settings() is get_settings()


def test_database_url_uses_asyncpg_and_escapes_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le DSN est construit depuis les `POSTGRES_*`, mot de passe échappé."""
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_USER", "ragengine")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("POSTGRES_DB", "ragengine")

    assert (
        Settings().database_url
        == "postgresql+asyncpg://ragengine:p%40ss%2Fword@db.internal:5433/ragengine"
    )


def test_empty_optional_secret_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LANGFUSE_*=` vide vaut brique désactivée, pas secret vide (CONTRIBUTING.md §3.4)."""
    monkeypatch.setenv("LANGFUSE_HOST", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "   ")

    settings = Settings()

    assert settings.langfuse_host is None
    assert settings.langfuse_secret_key is None


def test_log_level_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le niveau de log accepte une casse quelconque."""
    monkeypatch.setenv("RAGENGINE_LOG_LEVEL", "debug")

    assert Settings().log_level == "DEBUG"


def test_cors_origins_accept_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les origines CORS se déclarent en liste séparée par des virgules."""
    monkeypatch.setenv("RAGENGINE_CORS_ORIGINS", "https://app.example, https://admin.example")

    assert Settings().cors_origins == ["https://app.example", "https://admin.example"]


def test_development_secret_key_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """La clé de signature de développement ne peut pas atteindre la production."""
    monkeypatch.setenv("RAGENGINE_ENV", "production")
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", DEV_SECRET_KEY)

    with pytest.raises(ValidationError, match="RAGENGINE_SECRET_KEY"):
        Settings()


def test_default_password_is_accepted_in_development() -> None:
    """`change-me` reste le défaut utilisable sans configuration en développement."""
    settings = Settings()

    assert settings.secret_key.get_secret_value() == DEV_SECRET_KEY
    assert settings.postgres_password.get_secret_value() == DEV_SECRET_KEY
    assert settings.ragflow_bootstrap_password.get_secret_value() == DEV_SECRET_KEY


@pytest.mark.parametrize(
    ("env_var", "match"),
    [
        ("POSTGRES_PASSWORD", "POSTGRES_PASSWORD"),
        ("RAGFLOW_BOOTSTRAP_PASSWORD", "RAGFLOW_BOOTSTRAP_PASSWORD"),
    ],
)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_default_password_is_refused_outside_development(
    monkeypatch: pytest.MonkeyPatch, environment: str, env_var: str, match: str
) -> None:
    """Audit 2026-09, constat 2 : `postgres_password`/`ragflow_bootstrap_password` partagent
    le défaut public `change-me` de `secret_key` — même garde-fou en staging/production."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")
    monkeypatch.setenv(env_var, DEV_SECRET_KEY)

    with pytest.raises(ValidationError, match=match):
        Settings()


def test_session_cookie_is_not_secure_by_default_in_development() -> None:
    """En développement, l'API est servie en clair : rien à configurer, pas de `Secure`."""
    assert Settings().session_cookie_secure is False


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_session_cookie_secure_is_derived_when_exposed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Sans `SESSION_COOKIE_SECURE`, staging/production posent `Secure` d'office (constat 4)."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")

    assert Settings().session_cookie_secure is True


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_explicit_insecure_session_cookie_is_refused_when_exposed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Un `false` explicite hors développement fait échouer le démarrage, message à l'appui."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        Settings()


def test_explicit_session_cookie_secure_is_honoured_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un développeur derrière un HTTPS local peut toujours l'activer."""
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    assert Settings().session_cookie_secure is True


def test_session_cookie_effective_name_is_unprefixed_in_development() -> None:
    """`__Host-` impose `Secure` (donc HTTPS) : inapplicable sur `http://localhost`."""
    settings = Settings()
    assert settings.session_cookie_effective_name == settings.session_cookie_name
    assert not settings.session_cookie_effective_name.startswith("__Host-")


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_session_cookie_effective_name_is_prefixed_when_exposed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Audit sécurité 2026-09, constat 4 (ADR-064) : même calcul que `session_cookie_secure`."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")

    assert Settings().session_cookie_effective_name == "__Host-ragengine_session"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_session_cookie_effective_name_does_not_double_prefix(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Un nom déjà préfixé par l'opérateur n'est pas préfixé deux fois."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")
    monkeypatch.setenv("SESSION_COOKIE_NAME", "__Host-deja-prefixe")

    assert Settings().session_cookie_effective_name == "__Host-deja-prefixe"


def test_session_ttl_default_is_a_week() -> None:
    """Réduit de 720 h (30 j) à 168 h (7 j) — audit sécurité 2026-09, constat 4 (ADR-064)."""
    assert Settings().session_ttl_hours == 168


def test_wildcard_cors_origin_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`allow_credentials=True` est en dur dans `main.py` : `*` resterait une faille (ADR-018)."""
    monkeypatch.setenv("RAGENGINE_CORS_ORIGINS", "*")

    with pytest.raises(ValidationError, match="RAGENGINE_CORS_ORIGINS"):
        Settings()


def test_wildcard_cors_origin_is_refused_even_mixed_with_real_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le joker reste refusé même combiné à des origines explicites."""
    monkeypatch.setenv("RAGENGINE_CORS_ORIGINS", "https://app.example,*")

    with pytest.raises(ValidationError, match="RAGENGINE_CORS_ORIGINS"):
        Settings()


def test_upload_allowed_content_types_accept_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comme les origines CORS, la liste blanche d'upload se déclare en CSV."""
    monkeypatch.setenv("RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES", "application/pdf, text/plain")

    assert Settings().upload_allowed_content_types == ["application/pdf", "text/plain"]


def test_upload_and_rate_limit_defaults_are_conservative() -> None:
    """Les nouveaux réglages de la Phase 9 ont des valeurs par défaut sans `.env`."""
    settings = Settings()

    assert settings.upload_max_bytes == 90 * 1024 * 1024
    assert "application/pdf" in settings.upload_allowed_content_types


def test_vision_provider_is_disabled_by_default() -> None:
    """Sans `RAGFLOW_VISION_PROVIDER`, aucune vision configurée — aucun coût (ADR-027)."""
    settings = Settings()

    assert settings.ragflow_vision_provider is None
    assert settings.ragflow_vision_model_id is None


def test_vision_provider_requires_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un fournisseur vision sans modèle est une config incomplète, refusée au démarrage."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    with pytest.raises(ValidationError, match="RAGFLOW_VISION_MODEL_ID"):
        Settings()


def test_vision_provider_requires_the_matching_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RAGFLOW_VISION_PROVIDER=anthropic` sans `ANTHROPIC_API_KEY` est refusé."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "claude-haiku-4-5-20251001")

    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        Settings()


def test_vision_provider_openai_requires_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même garde-fou pour `openai` — la clé Anthropic ne doit pas suffire."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "openai")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "gpt-5-mini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings()


def test_vision_provider_gemini_requires_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même garde-fou pour `gemini` (ADR-027 mis à jour) — la clé Groq ne doit pas suffire."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "gemini-3.6-flash")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        Settings()


def test_vision_provider_accepted_when_fully_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fournisseur + modèle + clé cohérents : la configuration passe."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    settings = Settings()

    assert settings.ragflow_vision_provider == "anthropic"
    assert settings.ragflow_vision_model_id == "claude-haiku-4-5-20251001"


def test_vision_provider_gemini_accepted_when_fully_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gemini` suit la même règle que les deux autres fournisseurs (ADR-027 mis à jour)."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

    settings = Settings()

    assert settings.ragflow_vision_provider == "gemini"
    assert settings.gemini_api_key is not None


def test_vision_provider_empty_string_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`RAGFLOW_VISION_PROVIDER=` vide vaut désactivé, même règle que Langfuse."""
    monkeypatch.setenv("RAGFLOW_VISION_PROVIDER", "")
    monkeypatch.setenv("RAGFLOW_VISION_MODEL_ID", "")

    settings = Settings()

    assert settings.ragflow_vision_provider is None
    assert settings.ragflow_vision_model_id is None
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_requests == 120
    assert settings.rate_limit_anonymous_requests == 60
    assert settings.rate_limit_gateway_requests == 600
    assert settings.rate_limit_window_seconds == 60


def test_rerank_candidate_pool_size_default_matches_env_example() -> None:
    """`.env.example` livre 128 : c'est aussi la borne haute (constat 3)."""
    assert Settings().rerank_candidate_pool_size == 128


def test_rerank_candidate_pool_size_rejects_above_the_vetted_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-09, constat 3 : au-delà de 128, on retombe sur le risque OOM CUDA de
    l'incident du 2026-09-06 (`providers/engine/base.py::EngineAdapter.search`)."""
    monkeypatch.setenv("RERANK_CANDIDATE_POOL_SIZE", "129")

    with pytest.raises(ValidationError, match="RERANK_CANDIDATE_POOL_SIZE"):
        Settings()


def test_the_ingestion_engine_defaults_to_ragflow() -> None:
    """ADR-070 : RAGFlow reste le moteur par défaut, donc celui de la production actuelle."""
    assert Settings().ingestion_engine == "ragflow"


@pytest.mark.parametrize("engine", ["fake", "fake-shared"])
def test_the_fake_ingestion_engines_are_allowed_in_development(
    monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    monkeypatch.setenv("INGESTION_ENGINE", engine)

    assert Settings().ingestion_engine == engine


@pytest.mark.parametrize("engine", ["fake", "fake-shared"])
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_fake_ingestion_engines_are_refused_when_exposed(
    monkeypatch: pytest.MonkeyPatch, environment: str, engine: str
) -> None:
    """ADR-070 : le moteur factice n'est pas un moteur de production (CONTRIBUTING.md §3)."""
    monkeypatch.setenv("RAGENGINE_ENV", environment)
    monkeypatch.setenv("RAGENGINE_SECRET_KEY", "une-vraie-cle-de-signature")
    monkeypatch.setenv("POSTGRES_PASSWORD", "un-vrai-mot-de-passe-postgres")
    monkeypatch.setenv("RAGFLOW_BOOTSTRAP_PASSWORD", "un-vrai-mot-de-passe-ragflow")
    monkeypatch.setenv("INGESTION_ENGINE", engine)

    with pytest.raises(ValidationError, match="INGESTION_ENGINE"):
        Settings()
