"""Registre de reranking : catalogue, disponibilité déclarative, résolution.

Miroir des tests du registre d'embeddings — mêmes invariants (§0, §6, §8), y
compris l'absence de toute connexion, prouvée en rendant explosive la création
d'un client HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ValidationError
from app.providers.rerankers.base import RerankerPort
from app.providers.rerankers.local import LocalRerankerAdapter
from app.providers.rerankers.passthrough import PassthroughReranker
from app.providers.rerankers.registry import (
    list_reranker_providers,
    resolve_reranker_provider,
)

LOCAL_PROVIDER_ID = "bge-reranker-v2-m3"
PASSTHROUGH_PROVIDER_ID = "passthrough"


def settings_without_openai() -> Settings:
    """Configuration nue : aucune clé d'API tierce."""
    return Settings()


def settings_with_openai() -> Settings:
    """Configuration où une clé OpenAI est présente (sans effet sur ce catalogue)."""
    return Settings(openai_api_key=SecretStr("sk-test-jamais-appelee"))


def test_catalogue_exposes_the_documented_providers_in_order() -> None:
    """Le défaut du produit est en tête, et rien d'autre n'est enregistré."""
    statuses = list_reranker_providers(settings_without_openai())

    assert [status.provider_id for status in statuses] == [
        LOCAL_PROVIDER_ID,
        PASSTHROUGH_PROVIDER_ID,
    ]


def test_catalogue_reports_the_declared_capabilities() -> None:
    """`is_local` distingue le modèle GPU du Null Object."""
    local, passthrough = list_reranker_providers(settings_without_openai())

    assert (local.is_local, local.languages) == (True, None)
    assert local.max_documents > 0
    assert passthrough.is_local is False


def test_every_provider_carries_a_non_empty_human_label() -> None:
    """Défaut 3 (plan interne) : `label` remplace le mapping fait main du frontend."""
    statuses = list_reranker_providers(settings_without_openai())

    labels = {status.provider_id: status.label for status in statuses}
    assert all(len(label.strip()) > 0 for label in labels.values())
    assert labels[LOCAL_PROVIDER_ID] == "BGE Reranker v2-M3 (local)"
    assert labels[PASSTHROUGH_PROVIDER_ID] == "Passthrough"


def test_default_reranker_model_id_is_registered() -> None:
    """`settings.reranker_model_id` doit toujours désigner un fournisseur du catalogue."""
    settings = settings_without_openai()

    provider = resolve_reranker_provider(settings.reranker_model_id, settings)

    assert provider.capabilities.provider_id == settings.reranker_model_id


@pytest.mark.parametrize(
    "build_settings",
    [settings_without_openai, settings_with_openai],
    ids=["sans-cle-openai", "avec-cle-openai"],
)
def test_every_reranker_is_available_whatever_the_third_party_keys(
    build_settings: Callable[[], Settings],
) -> None:
    """Aucun reranker ne dépend d'un secret : ni le local, ni le passthrough.

    Les configurations sont construites **dans** le test : à la collecte, la
    fixture qui neutralise l'environnement n'a pas encore joué.
    """
    statuses = list_reranker_providers(build_settings())

    assert all(status.available for status in statuses)


def test_resolving_an_unknown_provider_is_a_validation_error() -> None:
    """Un `reranker_model_id` fantaisiste échoue en 422, avant toute écriture en base."""
    with pytest.raises(ValidationError) as raised:
        resolve_reranker_provider("reranker-inexistant", settings_without_openai())

    assert raised.value.status == 422
    assert raised.value.extensions["provider_id"] == "reranker-inexistant"
    assert raised.value.extensions["supported"] == [LOCAL_PROVIDER_ID, PASSTHROUGH_PROVIDER_ID]


def test_resolving_the_local_provider_builds_its_adapter() -> None:
    """Le registre rend un port utilisable, pas une simple description."""
    provider = resolve_reranker_provider(LOCAL_PROVIDER_ID, settings_without_openai())

    assert isinstance(provider, LocalRerankerAdapter)
    assert isinstance(provider, RerankerPort)


def test_resolving_the_passthrough_provider_builds_the_null_object() -> None:
    """« Pas de reranking » est un fournisseur comme un autre, pas un cas particulier caché."""
    provider = resolve_reranker_provider(PASSTHROUGH_PROVIDER_ID, settings_without_openai())

    assert isinstance(provider, PassthroughReranker)
    assert isinstance(provider, RerankerPort)


def test_resolving_the_local_provider_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valider une base de connaissances ne doit ouvrir aucune socket (§8)."""

    def _forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("aucun client HTTP ne doit naître de la résolution")

    monkeypatch.setattr(httpx, "AsyncClient", _forbidden)

    assert resolve_reranker_provider(LOCAL_PROVIDER_ID, settings_without_openai()) is not None
