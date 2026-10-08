"""Double en mémoire de `RedisRateLimiter`, installé par défaut pour toute la suite.

Pourquoi par défaut (`tests/conftest.py::_reset_rate_limiter`) : les plafonds de dépense LLM
(ADR-047, ADR-054) et le plafond global de créations d'espaces invités passent par le singleton
`app.core.rate_limit.get_rate_limiter`. Sans ce double, un test de service qui les traverse
retomberait sur `redis://redis:6379/0` (nom Docker non résolvable depuis l'hôte, plusieurs
secondes par tentative), et un test d'API sur la base Redis `0` de la stack de développement
réelle (`tests/conftest.py::app`) — il y incrémenterait de vrais compteurs, visibles dans le
tableau de bord admin et opposables aux vrais visiteurs.

Les tests qui éprouvent le vrai Redis (`tests/test_rate_limit.py`,
`tests/services/test_platform_llm_budget.py`, `tests/api/v1/test_admin_routes.py`) appellent
`close_rate_limiter()` dans leur propre fixture : le singleton repasse à `None` et le vrai
client est recréé sur leur base isolée.

Même sémantique que `RedisRateLimiter.hit` : incrément **puis** comparaison (jamais
« lire, comparer, écrire »), fenêtre fixe ouverte au premier coup.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.rate_limit import RateLimitOutcome


@dataclass(slots=True)
class InMemoryRateLimiter:
    """Compteurs à fenêtre fixe en mémoire — `hit`, `peek`, `remember_member`, `members`."""

    counters: dict[str, tuple[int, float]] = field(default_factory=dict)
    sets: dict[str, set[str]] = field(default_factory=dict)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome:
        now = time.monotonic()
        count, expires_at = self.counters.get(key, (0, now + window_seconds))
        if now >= expires_at:
            count, expires_at = 0, now + window_seconds
        count += 1
        self.counters[key] = (count, expires_at)
        if count > limit:
            retry_after = max(1, int(expires_at - now))
            return RateLimitOutcome(allowed=False, remaining=0, retry_after_seconds=retry_after)
        return RateLimitOutcome(allowed=True, remaining=limit - count, retry_after_seconds=0)

    async def peek(self, key: str) -> int:
        count, expires_at = self.counters.get(key, (0, 0.0))
        return count if time.monotonic() < expires_at else 0

    async def remember_member(self, set_key: str, member: str, *, window_seconds: int) -> None:
        self.sets.setdefault(set_key, set()).add(member)

    async def members(self, set_key: str) -> set[str]:
        return set(self.sets.get(set_key, set()))

    async def close(self) -> None:
        return None
