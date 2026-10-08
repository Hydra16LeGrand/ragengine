"""Registre d'embeddings : catalogue, disponibilité déclarative, résolution.

Aucun test n'ouvre de connexion. Deux d'entre eux le **prouvent** en rendant la
création d'un client réseau explosive : c'est l'invariant « disponibilité ≠
joignabilité » (`docs/providers-architecture.md` §0) et « ne pas instancier de
connexion à la validation d'une base » (§8).
"""

from __future__ import annotations

from typing import Never

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.embeddings import openai as openai_module
from app.providers.embeddings.base import EmbeddingPort
from app.providers.embeddings.local import LocalEmbeddingAdapter
from app.providers.embeddings.openai import OpenAIEmbeddingAdapter
from app.providers.embeddings.registry import (
    list_embedding_providers,
    resolve_embedding_provider,
)

LOCAL_PROVIDER_ID = "bge-m3"
OPENAI_PROVIDER_ID = "text-embedding-3-large"


def settings_without_openai() -> Settings:
    """Configuration nue : aucune clé d'API tierce."""
    return Settings()


def settings_with_openai() -> Settings:
    """Configuration où seul OpenAI est renseigné (valeur factice, jamais appelée)."""
    return Settings(openai_api_key=SecretStr("sk-test-jamais-appelee"))


def test_catalogue_exposes_the_documented_providers_in_order() -> None:
    """Le défaut du produit est en tête, et rien d'autre n'est enregistré."""
    statuses = list_embedding_providers(settings_without_openai())

    assert [status.provider_id for status in statuses] == [LOCAL_PROVIDER_ID, OPENAI_PROVIDER_ID]


def test_catalogue_reports_the_declared_capabilities() -> None:
    """Les dimensions annoncées sont celles de `GET /api/v1/providers` (§7)."""
    local, openai_status = list_embedding_providers(settings_with_openai())

    assert (local.is_local, local.dimensions, local.languages) == (True, 1024, None)
    assert (openai_status.is_local, openai_status.dimensions) == (False, 3072)


def test_every_provider_carries_a_non_empty_human_label() -> None:
    """Défaut 3 (plan interne) : `GET /api/v1/providers` porte enfin un libellé —
    le frontend n'a plus à le fabriquer depuis `provider_id` (`providerLabels.ts`)."""
    statuses = list_embedding_providers(settings_with_openai())

    labels = {status.provider_id: status.label for status in statuses}
    assert all(len(label.strip()) > 0 for label in labels.values())
    assert labels[LOCAL_PROVIDER_ID] == "BGE-M3 (local)"
    assert labels[OPENAI_PROVIDER_ID] == "OpenAI — Text Embedding 3 Large"


def test_default_embedding_model_id_is_registered() -> None:
    """`settings.embedding_model_id` doit toujours désigner un fournisseur du catalogue."""
    settings = settings_without_openai()

    provider = resolve_embedding_provider(settings.embedding_model_id, settings)

    assert provider.capabilities.provider_id == settings.embedding_model_id


def test_local_provider_is_available_without_any_secret() -> None:
    """Il n'a rien à configurer : sa joignabilité relève de la Phase 3, pas du catalogue."""
    statuses = {status.provider_id: status for status in list_embedding_providers(Settings())}

    assert statuses[LOCAL_PROVIDER_ID].available is True


def test_openai_is_unavailable_without_an_api_key() -> None:
    """Sans `OPENAI_API_KEY`, le fournisseur reste listé mais indisponible."""
    statuses = {
        status.provider_id: status for status in list_embedding_providers(settings_without_openai())
    }

    assert statuses[OPENAI_PROVIDER_ID].available is False


def test_openai_becomes_available_once_the_api_key_is_configured() -> None:
    """La disponibilité ne dépend que de la configuration lue."""
    statuses = {
        status.provider_id: status for status in list_embedding_providers(settings_with_openai())
    }

    assert statuses[OPENAI_PROVIDER_ID].available is True


def test_resolving_an_unknown_provider_is_a_validation_error() -> None:
    """Un `embedding_model_id` fantaisiste échoue en 422, avant toute écriture en base."""
    with pytest.raises(ValidationError) as raised:
        resolve_embedding_provider("modele-inexistant", settings_without_openai())

    assert raised.value.status == 422
    assert raised.value.extensions["provider_id"] == "modele-inexistant"


def test_resolving_an_unconfigured_provider_is_a_validation_error() -> None:
    """Connu mais sans clé : indisponible, donc refusé — et le message dit ce qui reste."""
    with pytest.raises(ValidationError) as raised:
        resolve_embedding_provider(OPENAI_PROVIDER_ID, settings_without_openai())

    assert raised.value.extensions["supported"] == [LOCAL_PROVIDER_ID]


def test_resolving_the_local_provider_builds_its_adapter() -> None:
    """Le registre rend un port utilisable, pas une simple description."""
    provider = resolve_embedding_provider(LOCAL_PROVIDER_ID, settings_without_openai())

    assert isinstance(provider, LocalEmbeddingAdapter)
    assert isinstance(provider, EmbeddingPort)
    assert provider.capabilities.provider_id == LOCAL_PROVIDER_ID


def test_resolving_the_openai_provider_builds_its_adapter() -> None:
    """Construction seule : aucun appel à l'API OpenAI n'est effectué."""
    provider = resolve_embedding_provider(OPENAI_PROVIDER_ID, settings_with_openai())

    assert isinstance(provider, OpenAIEmbeddingAdapter)
    assert isinstance(provider, EmbeddingPort)
    assert provider.capabilities.dimensions == 3072


def test_resolving_the_local_provider_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valider une base de connaissances ne doit ouvrir aucune socket (§8)."""

    def _forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("aucun client HTTP ne doit naître de la résolution")

    monkeypatch.setattr(httpx, "AsyncClient", _forbidden)

    assert resolve_embedding_provider(LOCAL_PROVIDER_ID, settings_without_openai()) is not None


def test_resolving_the_openai_provider_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même invariant côté SDK tiers : le client naît au premier appel, pas avant."""

    def _forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("aucun client OpenAI ne doit naître de la résolution")

    monkeypatch.setattr(openai_module, "AsyncOpenAI", _forbidden)

    assert resolve_embedding_provider(OPENAI_PROVIDER_ID, settings_with_openai()) is not None
