"""Disjoncteur `LLMPort` : ouverture au seuil, sonde après délai, refermeture (ADR-019).

Le délai de sonde est piloté par une horloge factice (`time.monotonic`
monkeypatché) plutôt que par un vrai `sleep` : ces tests doivent rester
déterministes et rapides, pas dépendre d'un vrai écoulement du temps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from app.providers.llm.base import ChatMessage, LLMCapabilities, LLMError, LLMPort, LLMRefusalError
from app.providers.llm.circuit_breaker import (
    CircuitBreakerLLMAdapter,
    CircuitState,
    LLMCircuitOpenError,
    breaker_for,
    reset_breakers,
)

PROVIDER_ID = "scripted-llm"


@pytest.fixture(autouse=True)
def _isolated_breakers() -> None:
    """Chaque test démarre avec un registre de disjoncteurs vide."""
    reset_breakers()


class _FakeClock:
    """Horloge monotone factice : avance uniquement quand le test le décide."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr("app.providers.llm.circuit_breaker.time.monotonic", fake)
    return fake


class _ScriptedLLM:
    """`LLMPort` dont chaque appel rejoue le prochain élément d'un scénario.

    Un élément est soit une chaîne (réponse), soit une exception à lever.
    `calls` compte les appels **réellement transmis au délégué** — c'est ce
    qui prouve qu'un circuit ouvert n'atteint jamais cette classe.
    """

    def __init__(self, script: list[str | Exception]) -> None:
        self.capabilities = LLMCapabilities(
            provider_id=PROVIDER_ID,
            vendor="anthropic",
            context_window=100_000,
            supports_streaming=True,
        )
        self._script = list(script)
        self.calls = 0

    def _next(self) -> str:
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> str:
        return self._next()

    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> AsyncIterator[str]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        yield self._next()


def _adapter(
    script: list[str | Exception], *, threshold: int = 3, reset_seconds: float = 30.0
) -> tuple[CircuitBreakerLLMAdapter, _ScriptedLLM]:
    delegate = _ScriptedLLM(script)
    breaker = breaker_for(
        PROVIDER_ID, failure_threshold=threshold, reset_timeout_seconds=reset_seconds
    )
    return CircuitBreakerLLMAdapter(delegate, breaker=breaker), delegate


def _conversation() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="Bonjour")]


async def test_satisfies_the_llm_port() -> None:
    """Le disjoncteur reste structurellement un `LLMPort` : rien ne change côté appelant."""
    adapter, _ = _adapter(["ok"])
    assert isinstance(adapter, LLMPort)


async def test_a_closed_circuit_passes_calls_through() -> None:
    """Fonctionnement normal : la réponse du délégué traverse sans y toucher."""
    adapter, delegate = _adapter(["Bonjour !"])

    answer = await adapter.complete(_conversation(), max_tokens=64)

    assert answer == "Bonjour !"
    assert delegate.calls == 1


async def test_fewer_failures_than_the_threshold_keep_the_circuit_closed() -> None:
    """En dessous du seuil, chaque échec reste isolé : le circuit ne s'ouvre pas."""
    adapter, delegate = _adapter(
        [LLMError("panne 1"), LLMError("panne 2"), "ça repart"], threshold=3
    )

    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)
    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)
    answer = await adapter.complete(_conversation(), max_tokens=64)

    assert answer == "ça repart"
    assert delegate.calls == 3


async def test_reaching_the_threshold_opens_the_circuit() -> None:
    """Le énième échec consécutif ouvre le circuit — le prochain appel n'atteint plus le délégué."""
    adapter, delegate = _adapter(
        [LLMError("panne 1"), LLMError("panne 2"), LLMError("panne 3")], threshold=3
    )
    for _ in range(3):
        with pytest.raises(LLMError):
            await adapter.complete(_conversation(), max_tokens=64)
    assert delegate.calls == 3

    with pytest.raises(LLMCircuitOpenError):
        await adapter.complete(_conversation(), max_tokens=64)

    assert delegate.calls == 3, "le délégué ne doit pas être appelé quand le circuit est ouvert"


async def test_a_refusal_does_not_count_towards_the_threshold() -> None:
    """Un refus du modèle n'est pas une panne de disponibilité (docstring du port)."""
    adapter, delegate = _adapter(
        [LLMRefusalError("refusé"), LLMRefusalError("refusé"), LLMRefusalError("refusé"), "ok"],
        threshold=3,
    )
    for _ in range(3):
        with pytest.raises(LLMRefusalError):
            await adapter.complete(_conversation(), max_tokens=64)

    # Le circuit doit être resté fermé : le quatrième appel atteint le délégué.
    answer = await adapter.complete(_conversation(), max_tokens=64)
    assert answer == "ok"
    assert delegate.calls == 4


async def test_a_success_resets_the_failure_counter() -> None:
    """Le compteur d'échecs ne survit pas à un succès intercalé."""
    adapter, delegate = _adapter(
        [LLMError("a"), LLMError("b"), "ok", LLMError("c"), LLMError("d")], threshold=3
    )
    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)
    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)
    await adapter.complete(_conversation(), max_tokens=64)  # "ok" : referme le compteur

    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)
    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)

    # Deux échecs seulement depuis le succès : le circuit doit être encore fermé.
    assert delegate.calls == 5
    breaker = breaker_for(PROVIDER_ID, failure_threshold=3, reset_timeout_seconds=30.0)
    assert breaker.state is CircuitState.CLOSED


async def test_the_circuit_reopens_after_the_reset_delay_and_a_successful_probe(
    clock: _FakeClock,
) -> None:
    """Après le délai, une sonde réussie referme le circuit."""
    adapter, delegate = _adapter(
        [LLMError("a"), LLMError("b"), LLMError("c"), "je suis de retour"],
        threshold=3,
        reset_seconds=30.0,
    )
    for _ in range(3):
        with pytest.raises(LLMError):
            await adapter.complete(_conversation(), max_tokens=64)

    with pytest.raises(LLMCircuitOpenError):
        await adapter.complete(_conversation(), max_tokens=64)

    clock.advance(31.0)
    answer = await adapter.complete(_conversation(), max_tokens=64)

    assert answer == "je suis de retour"
    assert delegate.calls == 4


async def test_a_failed_probe_reopens_the_circuit_immediately(clock: _FakeClock) -> None:
    """Une sonde qui échoue rouvre sur-le-champ, sans attendre un nouveau seuil."""
    adapter, delegate = _adapter(
        [LLMError("a"), LLMError("b"), LLMError("c"), LLMError("toujours en panne"), "enfin"],
        threshold=3,
        reset_seconds=30.0,
    )
    for _ in range(3):
        with pytest.raises(LLMError):
            await adapter.complete(_conversation(), max_tokens=64)
    clock.advance(31.0)

    with pytest.raises(LLMError):
        await adapter.complete(_conversation(), max_tokens=64)  # sonde ratée
    assert delegate.calls == 4

    # Immédiatement après une sonde ratée, le circuit est de nouveau ouvert.
    with pytest.raises(LLMCircuitOpenError):
        await adapter.complete(_conversation(), max_tokens=64)
    assert delegate.calls == 4

    clock.advance(31.0)
    answer = await adapter.complete(_conversation(), max_tokens=64)
    assert answer == "enfin"


async def test_stream_is_protected_by_the_circuit_too() -> None:
    """Le flux de streaming obéit à la même politique que `complete`."""
    adapter, delegate = _adapter([LLMError("a"), LLMError("b"), LLMError("c")], threshold=3)
    for _ in range(3):
        with pytest.raises(LLMError):
            async for _chunk in adapter.stream(_conversation(), max_tokens=64):
                pass
    assert delegate.calls == 3

    with pytest.raises(LLMCircuitOpenError):
        async for _chunk in adapter.stream(_conversation(), max_tokens=64):
            pass
    assert delegate.calls == 3


async def test_a_successful_stream_closes_the_circuit() -> None:
    """Un flux qui va au bout compte comme un succès, comme `complete`."""
    adapter, delegate = _adapter(["un seul morceau"], threshold=3)

    chunks = [chunk async for chunk in adapter.stream(_conversation(), max_tokens=64)]

    assert chunks == ["un seul morceau"]
    breaker = breaker_for(PROVIDER_ID, failure_threshold=3, reset_timeout_seconds=30.0)
    assert breaker.state is CircuitState.CLOSED
    assert delegate.calls == 1


async def test_breaker_for_is_stable_across_calls_for_the_same_provider() -> None:
    """Le même `provider_id` retrouve le même disjoncteur — c'est ce qui fait persister l'état."""
    first = breaker_for(PROVIDER_ID, failure_threshold=5, reset_timeout_seconds=30.0)
    second = breaker_for(PROVIDER_ID, failure_threshold=5, reset_timeout_seconds=30.0)
    assert first is second


async def test_different_providers_have_independent_breakers() -> None:
    """Un fournisseur en panne n'ouvre pas le circuit d'un autre (isolation par `provider_id`)."""
    a = breaker_for("provider-a", failure_threshold=1, reset_timeout_seconds=30.0)
    b = breaker_for("provider-b", failure_threshold=1, reset_timeout_seconds=30.0)
    assert a is not b


def test_delegate_property_exposes_the_wrapped_adapter() -> None:
    """Introspection utilisée par les tests du registre (`test_registry.py`)."""
    adapter, delegate = _adapter(["ok"])
    assert adapter.delegate is delegate
    assert adapter.capabilities is delegate.capabilities
