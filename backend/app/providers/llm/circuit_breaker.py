"""Disjoncteur (circuit breaker) `LLMPort` : protège les fournisseurs de génération externes.

Posé **une seule fois**, à la résolution (`app.providers.llm.registry.
resolve_llm_provider`), jamais dans un adaptateur concret
(`anthropic.py`/`openai.py`/`groq.py`) : les trois partagent la même
politique. La dupliquer dans chacun créerait trois compteurs indépendants pour
le même `provider_id` si deux adaptateurs du même fournisseur coexistaient un
jour, et obligerait à répéter la logique à chaque nouveau fournisseur
(CONTRIBUTING.md §3 : « composant substituable » → un seul point de câblage).

**État en mémoire du process, par `provider_id`** — pas de Redis partagé
(CONTRIBUTING.md, brief Phase 9 : « état en mémoire du process suffit »). Un
disjoncteur protège l'aval d'*un* fournisseur en panne pendant la durée de vie
d'*un* worker/process FastAPI ; un état perdu au redémarrage n'est pas un
défaut, c'est le comportement attendu d'un mécanisme de protection à court
terme, pas d'un historique à conserver.

**Fait main plutôt qu'une bibliothèque tierce** (`pybreaker`, `aiobreaker`…) :
le besoin exact — trois états, un compteur d'échecs consécutifs, une horloge —
tient en une trentaine de lignes ; `pybreaker` n'a pas d'API `async` native
(il faudrait l'envelopper dans un thread ou accepter un faux positif sous
concurrence), et `aiobreaker` ajoute une dépendance pour un besoin qui ne
justifie pas d'apprendre les quirks d'une bibliothèque tierce. Décision
documentée : `docs/adr/019-disjoncteur-llm-fait-main.md`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from typing import ClassVar, Final

from app.providers.llm.base import (
    ChatMessage,
    LLMCapabilities,
    LLMError,
    LLMPort,
    LLMRefusalError,
)

_logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD: Final = 5
"""Échecs consécutifs avant ouverture — repris par défaut si `Settings` est absent (tests)."""

DEFAULT_RESET_TIMEOUT_SECONDS: Final = 30.0
"""Délai avant qu'une requête sonde tente de refermer le disjoncteur."""


class CircuitState(StrEnum):
    """Les trois états d'un disjoncteur (motif « circuit breaker » standard)."""

    CLOSED = "closed"
    """Fonctionnement normal : les requêtes passent, les échecs sont comptés."""

    OPEN = "open"
    """Fournisseur jugé en panne : aucune requête n'est envoyée avant le délai de sonde."""

    HALF_OPEN = "half_open"
    """Délai écoulé : une requête de sonde est autorisée à décider de la suite."""


class LLMCircuitOpenError(LLMError):
    """Le disjoncteur est ouvert : la requête n'a pas été envoyée au fournisseur.

    Statut distinct de l'erreur générique (`LLMError`, 502) : ici RAGEngine
    sait déjà, avant toute tentative, que le fournisseur est en panne — ce
    n'est pas un échec constaté à l'instant sur *cette* requête. `503` reflète
    mieux « réessayez plus tard » que « la passerelle a échoué ».
    """

    status: ClassVar[int] = HTTPStatus.SERVICE_UNAVAILABLE
    title: ClassVar[str] = "Service Unavailable"
    type: ClassVar[str] = "/problems/llm-circuit-open"


@dataclass
class _Breaker:
    """État mutable d'un disjoncteur pour un `provider_id`. Protégé par `lock`.

    `lock` sérialise les décisions (`before_call`/`on_success`/`on_failure`)
    sans bloquer le process : FastAPI est mono-thread par worker, mais
    plusieurs requêtes concurrentes vers le même fournisseur restent possibles
    (plusieurs sessions de chat en parallèle) — sans lui, deux échecs
    concurrents pourraient tous deux lire `consecutive_failures` avant que
    l'un des deux ne l'incrémente, sous-comptant les échecs réels.
    """

    failure_threshold: int
    reset_timeout_seconds: float
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _reset_delay_elapsed(self, now: float) -> bool:
        return self.opened_at is not None and (now - self.opened_at) >= self.reset_timeout_seconds

    async def before_call(self, provider_id: str) -> None:
        """Lève `LLMCircuitOpenError` si le circuit est ouvert et le délai de sonde n'est pas passé.

        Fait aussi la transition `OPEN → HALF_OPEN` une fois le délai passé :
        c'est la seule façon dont un disjoncteur ouvert se referme jamais —
        aucune tâche de fond ne le sonde toute seule, c'est la prochaine
        requête réelle qui joue ce rôle.
        """
        async with self.lock:
            if self.state is not CircuitState.OPEN:
                return
            now = time.monotonic()
            if not self._reset_delay_elapsed(now):
                raise LLMCircuitOpenError(
                    f"Le fournisseur « {provider_id} » est temporairement écarté "
                    f"(disjoncteur ouvert après {self.failure_threshold} échecs consécutifs)."
                )
            self.state = CircuitState.HALF_OPEN
            _logger.info(
                "Disjoncteur LLM en sonde", extra={"provider_id": provider_id, "state": "half_open"}
            )

    async def on_success(self, provider_id: str) -> None:
        """Referme le circuit — qu'il ait été à moitié ouvert ou déjà fermé."""
        async with self.lock:
            if self.state is not CircuitState.CLOSED:
                _logger.info(
                    "Disjoncteur LLM refermé", extra={"provider_id": provider_id, "state": "closed"}
                )
            self.state = CircuitState.CLOSED
            self.consecutive_failures = 0
            self.opened_at = None

    async def on_failure(self, provider_id: str) -> None:
        """Compte l'échec ; ouvre le circuit au seuil, ou immédiatement en sonde.

        Une sonde (`HALF_OPEN`) qui échoue rouvre le circuit sur-le-champ, sans
        attendre un nouveau seuil : elle a déjà prouvé que le fournisseur n'est
        pas revenu. `opened_at` est réarmé à chaque ouverture (y compris une
        réouverture immédiate après sonde), pour redonner un plein délai de
        sonde avant la prochaine tentative.
        """
        async with self.lock:
            self.consecutive_failures += 1
            should_open = (
                self.state is CircuitState.HALF_OPEN
                or self.consecutive_failures >= self.failure_threshold
            )
            if not should_open:
                return
            if self.state is not CircuitState.OPEN:
                _logger.warning(
                    "Disjoncteur LLM ouvert",
                    extra={
                        "provider_id": provider_id,
                        "state": "open",
                        "consecutive_failures": self.consecutive_failures,
                    },
                )
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


_BREAKERS: dict[str, _Breaker] = {}
"""Un disjoncteur par `provider_id`, pour la durée de vie du process.

Créé paresseusement (`breaker_for`) : la course entre deux requêtes
concurrentes sur un `provider_id` encore jamais vu (`dict.get` puis
`dict.__setitem__`, sans `await` entre les deux) ne peut créer, au pire, que
deux `_Breaker` dont un seul gagne la place dans le dictionnaire — perte d'un
objet fraîchement alloué, jamais une incohérence d'état, sous le modèle de
concurrence coopérative d'`asyncio` (un seul greenlet actif entre deux
`await`). Un verrou dédié à la seule création n'apporterait rien ici.
"""


def breaker_for(
    provider_id: str, *, failure_threshold: int, reset_timeout_seconds: float
) -> _Breaker:
    """Renvoie le disjoncteur du `provider_id`, en le créant au premier appel.

    Les seuils ne sont lus qu'à la création : ils viennent de `Settings`, figée
    pour la durée de vie du process (`get_settings`, `lru_cache`) — un
    disjoncteur déjà existant garde donc les seuils avec lesquels il a été
    ouvert la première fois, jamais une valeur qui changerait sous ses pieds.
    """
    breaker = _BREAKERS.get(provider_id)
    if breaker is None:
        breaker = _Breaker(
            failure_threshold=failure_threshold, reset_timeout_seconds=reset_timeout_seconds
        )
        _BREAKERS[provider_id] = breaker
    return breaker


def reset_breakers() -> None:
    """Vide le registre des disjoncteurs — réservé aux tests (isolation entre cas)."""
    _BREAKERS.clear()


class CircuitBreakerLLMAdapter:
    """Enveloppe tout `LLMPort` d'un disjoncteur par `provider_id`.

    Structurellement un `LLMPort` (`capabilities`, `complete`, `stream`) : les
    services qui consomment `resolve_llm_provider` (`RetrievalService.chat`)
    n'ont besoin d'aucun changement, le port ne change pas.
    """

    def __init__(self, delegate: LLMPort, *, breaker: _Breaker) -> None:
        self._delegate = delegate
        self._breaker = breaker
        self.capabilities: LLMCapabilities = delegate.capabilities

    @property
    def delegate(self) -> LLMPort:
        """Adaptateur concret enveloppé — introspection réservée aux tests."""
        return self._delegate

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        """Applique le disjoncteur autour de `LLMPort.complete`.

        Un refus (`LLMRefusalError`) n'est ni un succès ni une panne de
        disponibilité : le fournisseur a répondu, la requête a simplement été
        rejetée par son classificateur de sécurité (`app.providers.llm.base`,
        docstring de `LLMRefusalError`). Le compter comme échec ouvrirait le
        circuit sur des requêtes légitimes sans rapport avec une panne — il ne
        compte donc ni pour ni contre l'état du disjoncteur.
        """
        provider_id = self.capabilities.provider_id
        await self._breaker.before_call(provider_id)
        try:
            result = await self._delegate.complete(messages, max_tokens=max_tokens)
        except LLMRefusalError:
            raise
        except Exception:
            await self._breaker.on_failure(provider_id)
            raise
        else:
            await self._breaker.on_success(provider_id)
            return result

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        """Renvoie un itérateur asynchrone sur les fragments de texte générés."""
        return self._stream(messages, max_tokens=max_tokens)

    async def _stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        """Applique le disjoncteur autour de `LLMPort.stream`.

        Le contrôle a lieu **avant** d'entamer le flux : un circuit ouvert
        n'envoie aucune requête au fournisseur. Un échec en cours de flux
        (après que des fragments ont déjà été émis) compte quand même comme un
        échec — politique simple et assumée : un flux interrompu en cours de
        route est un signal de panne au moins aussi fort qu'un échec immédiat,
        et distinguer les deux ajouterait une branche pour un bénéfice
        marginal (`docs/adr/019-disjoncteur-llm-fait-main.md`).
        """
        provider_id = self.capabilities.provider_id
        await self._breaker.before_call(provider_id)
        try:
            async for token in self._delegate.stream(messages, max_tokens=max_tokens):
                yield token
        except LLMRefusalError:
            raise
        except Exception:
            await self._breaker.on_failure(provider_id)
            raise
        else:
            await self._breaker.on_success(provider_id)
