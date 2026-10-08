"""Factory de l'observabilité : une bascule binaire, pas un registre.

`observability/` n'a pas de registre à choix multiple
(`docs/providers-architecture.md` §6) : Langfuse est configuré ou il ne l'est
pas. La branche est prise **ici et nulle part ailleurs** — les services reçoivent
toujours un `ObservabilityPort`, jamais un `ObservabilityPort | None`
(CONTRIBUTING.md §3.4).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.providers.observability.base import ObservabilityPort
from app.providers.observability.langfuse import LangfuseObservability
from app.providers.observability.noop import NoOpObservability

OBSERVABILITY_PROVIDER_ID: Final = "langfuse"
OBSERVABILITY_PROVIDER_LABEL: Final = "Langfuse"


class ObservabilityProviderStatus(BaseModel):
    """Ligne « observability » du catalogue `GET /api/v1/providers`."""

    model_config = ConfigDict(frozen=True)

    provider_id: Literal["langfuse"] = OBSERVABILITY_PROVIDER_ID
    label: str = OBSERVABILITY_PROVIDER_LABEL
    available: bool


def _is_fully_configured(settings: Settings) -> bool:
    """Vrai si l'hôte **et** les deux clés sont renseignés.

    C'est aussi la condition d'activation de `get_observability` : un hôte sans
    clés produirait un exporteur systématiquement rejeté par le collecteur
    (401) — l'échec resterait absorbé (CONTRIBUTING.md §3.4, aucune requête ne
    casse), mais chaque étape observée tenterait un export voué à l'échec, en
    pure perte et en bruit de logs. Une configuration partielle dégrade donc en
    no-op, comme une absence totale de configuration.
    """
    return (
        settings.langfuse_host is not None
        and settings.langfuse_public_key is not None
        and settings.langfuse_secret_key is not None
    )


_cached_settings: Settings | None = None
_cached_instance: ObservabilityPort | None = None
"""Mémoïsation du dernier `(settings, instance)` — bug corrigé le 2026-07-29.

`LangfuseObservability.from_settings` construit un `BatchSpanProcessor`, qui
démarre un thread d'export **dès sa construction** (`opentelemetry-sdk`,
vérifié empiriquement : `BatchProcessor.__init__` appelle
`self._worker_thread.start()`). Sans mémoïsation, chaque appelant — une tâche
Celery à chaque étape du pipeline d'ingestion, une requête de retrieval à
chaque recherche — en ouvrirait un nouveau sans jamais le refermer : fuite de
threads et de connexions non bornée sur un process worker/API de longue durée.
`resolve_ingestion_engine`/`resolve_storage` peuvent se permettre d'être
reconstruits à chaque appel (construction documentée comme sans coût, aucune
socket ouverte avant le premier usage réel) ; `get_observability` ne peut pas
suivre le même principe tant que Langfuse est configuré, d'où cette
mémoïsation, propre à cette fonction.

Comparaison par **identité** (`is`), pas par contenu : `Settings` n'est pas
`frozen`, donc pas hashable, et `functools.lru_cache` est donc inapplicable
tel quel. Garder une référence forte vers l'objet `Settings` mémoïsé
(`_cached_settings`) élimine tout risque de collision d'`id()` réutilisé après
ramasse-miettes — tant que la référence vit, CPython ne peut pas réattribuer
son adresse à un autre objet. En production, `settings` provient toujours de
`get_settings()` (`@lru_cache(maxsize=1)`) : un seul objet vit pour tout le
process, donc un seul appel réel à `LangfuseObservability.from_settings`. En
test, chaque `Settings()` fraîchement construit invalide trivialement le
cache (comparaison `is` fausse), sans effet de bord entre tests.
"""


def get_observability(settings: Settings) -> ObservabilityPort:
    """Renvoie l'implémentation d'observabilité correspondant à la configuration.

    Mémoïsée pour l'objet `settings` fourni (voir `_cached_settings` ci-dessus).
    """
    global _cached_settings, _cached_instance
    if _cached_settings is settings and _cached_instance is not None:
        return _cached_instance

    instance: ObservabilityPort = (
        LangfuseObservability.from_settings(settings)
        if _is_fully_configured(settings)
        else NoOpObservability()
    )
    _cached_settings, _cached_instance = settings, instance
    return instance


def observability_provider_status(settings: Settings) -> ObservabilityProviderStatus:
    """Décrit l'état de la brique d'observabilité, sans aucun appel réseau."""
    return ObservabilityProviderStatus(available=_is_fully_configured(settings))
