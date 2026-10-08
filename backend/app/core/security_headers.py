"""En-têtes de sécurité HTTP standards, posés une seule fois pour toute réponse.

Un middleware dédié plutôt que des en-têtes dispersés route par route (CONTRIBUTING.md
§9) : c'est une politique transverse à l'application, pas une décision propre à
un endpoint.

**Deux émetteurs, mêmes valeurs (ADR-052).** Derrière Traefik, le middleware
`security-headers` de `docker/traefik/dynamic.yml` pose ces en-têtes sur les
routeurs `api` ET `frontend` (le HTML servi par nginx, que ce module ne voit
jamais), et **écrase** ceux de l'API — vérifié sur Traefik v3.5 : une seule
occurrence par en-tête. Ce module reste la défense en profondeur de l'accès
direct à l'API (port publié en boucle locale). Les valeurs doivent rester
identiques des deux côtés : `tests/test_security_headers.py` compare les deux.

`Strict-Transport-Security` n'est posé ici que si la requête est en HTTPS au
sens de `request.url.scheme`. Derrière le proxy, uvicorn y reporte
`X-Forwarded-Proto` **seulement si le pair est approuvé** (ADR-052) : l'en-tête
brut n'est jamais relu ici, un client direct ne peut donc pas le forger. Traefik,
lui, émet HSTS inconditionnellement (`forceSTSHeader`) : un navigateur ignore
HSTS reçu en clair (RFC 6797 §8.1), seule la réponse servie en HTTPS par l'edge
Cloudflare l'engage. Ni `includeSubDomains` ni `preload` : voir ADR-052.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

HSTS_MAX_AGE_SECONDS: Final = 31_536_000
"""Un an. Même valeur que `stsSeconds` du middleware Traefik."""

STATIC_SECURITY_HEADERS: Final[dict[str, str]] = {
    # Empêche un navigateur de deviner un type de média différent du
    # `Content-Type` annoncé — neutralise le détournement d'un upload de
    # document en script exécutable si jamais il était resservi tel quel.
    "X-Content-Type-Options": "nosniff",
    # Aucune page ne doit s'afficher dans une `<iframe>` tierce : ferme le
    # clickjacking (« supprimer la base », « créer une clé API »).
    "X-Frame-Options": "DENY",
    # `frame-ancestors` seul : une politique `script-src`/`default-src` n'a pas
    # été éprouvée sur tous les écrans du build, et `object-src 'none'`
    # risquerait la visionneuse PDF de `/documents/{id}/content` (ADR-052).
    "Content-Security-Policy": "frame-ancestors 'none'",
    # Le chemin d'une requête (ex. `/api/v1/documents/{id}`) ne doit pas fuiter
    # vers un tiers référencé par un lien sortant de l'interface.
    "Referrer-Policy": "no-referrer",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Pose les en-têtes de sécurité standards sur toute réponse de l'API."""

    def __init__(self, app: ASGIApp, *, hsts_max_age_seconds: int = HSTS_MAX_AGE_SECONDS) -> None:
        super().__init__(app)
        self._hsts_value = f"max-age={hsts_max_age_seconds}"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in STATIC_SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", self._hsts_value)
        return response
