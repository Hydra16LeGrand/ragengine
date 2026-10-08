"""Portées (« scopes ») d'une clé API — source de vérité du modèle Phase 9.

**Granularité choisie : `<ressource>:<action>`**, `action` valant `read`/`write`
pour les ressources CRUD classiques, et un verbe métier (`search`, `chat`) pour
le retrieval, qui n'en est pas une (deux endpoints racines, coûts très
différents — voir `RetrievalScope`). C'est le compromis retenu entre un scope
unique par endpoint (trop fin : une clé d'intégration légitime finirait avec
vingt scopes à cocher) et un scope global unique façon `"api"` (trop grossier :
aucune clé ne pourrait jamais être cantonnée à la lecture seule). Documenté en
détail dans `docs/adr/018-scopes-de-cle-api-et-securite.md`.

**`"*"` reste un joker valide**, distinct du catalogue ci-dessous : c'est la
portée par défaut de toute clé émise avant la Phase 9
(`app.domain.schemas.api_key.DEFAULT_SCOPES`), et `require_scope`
(`app.api.v1.dependencies`) la traite comme une autorisation totale — aucune
clé existante ne perd d'accès à l'activation de cette Phase.

**Hors catalogue, donc jamais scopées** : `/health` (sonde de liveness),
`/providers` (catalogue public, déjà sans authentification), et les routes de
gestion du compte lui-même (`GET/PATCH /me`, `/me/credentials`, `/sessions*`)
— un porteur de clé API gère son propre compte quel que soit le périmètre
métier qu'on lui a accordé, exactement comme il peut toujours révoquer sa
propre clé.

`/me/llm-keys*` porte ses scopes (`LLM_KEYS_READ`/`LLM_KEYS_WRITE`) depuis
l'audit sécurité 2026-09 (constat 13, ADR-063) : la première rédaction
d'ADR-018 le classait avec `/me*` par généralisation de préfixe, mais une clé
LLM tierce déclarée sur `/me/llm-keys` est un secret de facturation, pas un
réglage de profil.

**Catalogue extensible** (ADR-069) : un greffon déclare les portées de ses
propres routes (`Plugin.api_key_scopes`) ; `available_scopes()` les ajoute au
catalogue contre lequel une clé est validée.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.core.plugins import extra_api_key_scopes


class ApiKeyScope(StrEnum):
    """Portée exigible par `require_scope` — une valeur par couple ressource/action."""

    KNOWLEDGE_BASES_READ = "knowledge-bases:read"
    KNOWLEDGE_BASES_WRITE = "knowledge-bases:write"
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"
    JOBS_READ = "jobs:read"
    JOBS_WRITE = "jobs:write"
    RETRIEVAL_SEARCH = "retrieval:search"
    RETRIEVAL_CHAT = "retrieval:chat"
    EVALUATIONS_READ = "evaluations:read"
    EVALUATIONS_WRITE = "evaluations:write"
    API_KEYS_READ = "api-keys:read"
    API_KEYS_WRITE = "api-keys:write"
    ORGANIZATIONS_READ = "organizations:read"
    ORGANIZATIONS_WRITE = "organizations:write"
    LLM_KEYS_READ = "llm-keys:read"
    LLM_KEYS_WRITE = "llm-keys:write"


WILDCARD_SCOPE: Final = "*"
"""Portée joker : satisfait toute exigence de `require_scope` (rétrocompatibilité)."""

CORE_SCOPES: Final[frozenset[str]] = frozenset(scope.value for scope in ApiKeyScope)
"""Portées du cœur."""


def available_scopes() -> frozenset[str]:
    """Catalogue complet : portées du cœur et des greffons — c'est contre cet ensemble
    (union `{WILDCARD_SCOPE}`) que `ApiKeyCreate.scopes` est validé
    (`app.domain.schemas.api_key`)."""
    return CORE_SCOPES | extra_api_key_scopes()


def scope_satisfied(granted: list[str], required: str) -> bool:
    """`True` si `granted` (portées d'une clé API) couvre `required`.

    Le joker `"*"` couvre tout ; sinon il faut une correspondance exacte —
    aucune hiérarchie implicite (`knowledge-bases:write` ne couvre pas
    `knowledge-bases:read` automatiquement, une clé qui a besoin des deux les
    déclare toutes les deux). Ce choix simple évite d'avoir à maintenir un
    graphe de portées, au prix d'une déclaration un peu plus verbeuse à la
    création d'une clé.
    """
    return WILDCARD_SCOPE in granted or required in granted
