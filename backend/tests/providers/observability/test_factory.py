"""Tests de la factory d'observabilité : la bascule binaire, prise une seule fois."""

import pytest

from app.core.config import Settings
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.factory import (
    get_observability,
    observability_provider_status,
)
from app.providers.observability.langfuse import LangfuseObservability
from app.providers.observability.noop import NoOpObservability


def _configure_langfuse(monkeypatch: pytest.MonkeyPatch, *, with_keys: bool = True) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.interne.test")
    if with_keys:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


def test_returns_the_noop_implementation_without_configuration() -> None:
    """Sans `LANGFUSE_HOST`, l'observabilité est un no-op — jamais `None`."""
    observability = get_observability(Settings())

    assert isinstance(observability, NoOpObservability)
    assert isinstance(observability, ObservabilityPort)


def test_returns_the_langfuse_implementation_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avec `LANGFUSE_HOST`, c'est l'adaptateur réel qui est renvoyé."""
    _configure_langfuse(monkeypatch)

    observability = get_observability(Settings())

    assert isinstance(observability, LangfuseObservability)
    assert isinstance(observability, ObservabilityPort)


def test_status_is_unavailable_without_configuration() -> None:
    """Le catalogue annonce la brique comme indisponible et non configurée."""
    status = observability_provider_status(Settings())

    assert status.provider_id == "langfuse"
    assert status.available is False


def test_status_carries_a_non_empty_human_label() -> None:
    """Défaut 3 (plan interne) : `label` remplace le mapping fait main du frontend."""
    status = observability_provider_status(Settings())

    assert status.label == "Langfuse"


def test_status_is_available_when_host_and_keys_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hôte et deux clés renseignés : la brique est annoncée disponible."""
    _configure_langfuse(monkeypatch)

    assert observability_provider_status(Settings()).available is True


def test_status_is_unavailable_when_keys_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un hôte sans clés produirait un export systématiquement rejeté (401).

    `get_observability` dégrade en no-op plutôt que de construire un
    adaptateur voué à l'échec à chaque étape observée : cela n'empêche pas
    davantage le démarrage qu'un adaptateur réel qui échouerait en boucle, et
    ça évite le bruit de logs et le travail réseau perdu. « activé » et
    « disponible » restent donc alignés sur la même configuration complète.
    """
    _configure_langfuse(monkeypatch, with_keys=False)
    settings = Settings()

    assert observability_provider_status(settings).available is False
    assert isinstance(get_observability(settings), NoOpObservability)


def test_empty_environment_variables_are_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`LANGFUSE_HOST=` équivaut à non renseigné : retour au no-op."""
    monkeypatch.setenv("LANGFUSE_HOST", "   ")

    assert isinstance(get_observability(Settings()), NoOpObservability)
