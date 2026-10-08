"""Rate limiting par clé API / session (ou par IP à défaut) — compteur Redis, fenêtre fixe.

**Pourquoi pas une nouvelle dépendance.** `redis>=5.2` est déjà dans
`pyproject.toml` (Celery) et expose un client asynchrone (`redis.asyncio`) tout
à fait suffisant pour un compteur `INCR`/`EXPIRE` : ajouter `slowapi` ou
`fastapi-limiter` pour ça aurait été la « nouvelle dépendance lourde » que
CONTRIBUTING.md §9 demande d'éviter quand l'existant suffit. `REDIS_URL` (base Redis
`0`) n'est consommée par aucun autre composant — Celery vit sur les bases `1`
(broker) et `2` (backend de résultats) — donc aucune collision de clés.

**Trois compteurs indépendants, pas un seul.** Un appelant est toujours limité
par IP, *et* additionnellement par un compteur plus généreux (`rate_limit_requests`)
dès qu'il porte un identifiant : clé API (en-tête `Authorization: Bearer`) ou,
sinon, cookie de session d'interface (`ratelimit:session:{empreinte}`, ADR-036)
— l'un ou l'autre, jamais les deux, dans le même ordre de priorité que
`PrincipalResolver.resolve` (§`app.services.identity.principal`). Sans ce
troisième compteur, un utilisateur connecté au navigateur (authentifié par
cookie, jamais par `Authorization`) restait plafonné à la limite anonyme —
un chargement de plusieurs documents (un suivi de job par document) la
dépassait vite.

Limiter uniquement par empreinte de jeton/cookie laisserait un trou : un
client qui ferait varier son identifiant à chaque requête (garbage aléatoire,
**jamais vérifié en base ici** — ce middleware ne fait aucune requête SQL,
exactement comme pour la clé API déjà) obtiendrait un compartiment neuf à
chaque fois et ne serait jamais freiné par ce compteur-là. La couche IP, elle,
reste incontournable et n'est jamais affaiblie : c'est elle qui absorbe ce
cas. Un cookie de session présent mais invalide/expiré en base n'est donc pas
distingué d'un cookie valide *par ce compteur* (le distinguer exigerait soit
une requête DB par appel, exclue par conception, soit un cache de validité
dupliquant la logique d'expiration de `PrincipalResolver.resolve_session`) ;
il obtient tout de même son propre compartiment généreux plutôt que de
retomber sur celui, plus restrictif, de son IP — sans jamais court-circuiter
le plafond IP qui, lui, s'applique toujours en premier et ne dépend d'aucun
identifiant. Voir `docs/adr/036-rate-limit-cookie-session.md`.

**Fenêtre fixe, pas glissante.** Une seule commande Redis par requête (un
script Lua `INCR`+`EXPIRE` conditionnel, exécuté atomiquement) contre les
`ZADD`/`ZREMRANGEBYSCORE` d'une fenêtre glissante. L'imprécision qui en résulte
— jusqu'au double du débit nominal à la frontière de deux fenêtres — est sans
conséquence pour un garde-fou anti-abus, pas un compteur de facturation.

**Échec ouvert — pour ce middleware, jamais pour tout appelant.** Un Redis
injoignable laisse passer la requête (avec un avertissement journalisé) plutôt
que de renvoyer `503` sur toute l'API : la disponibilité prime sur l'exactitude
d'un débit, dans le même esprit que les briques optionnelles no-op de
CONTRIBUTING.md §3.4, même si le rate limiting n'en est pas une au sens strict (il
est actif par défaut, `RAGENGINE_RATE_LIMIT_ENABLED`).

Ce n'est **pas** la politique du port lui-même : `RedisRateLimiter.hit`/`peek`
ne décident jamais d'échouer ouvert ou fermé, et ne laissent jamais fuir
`redis.exceptions.RedisError` (le SDK concret resterait alors visible d'un
appelant hors adaptateur, CONTRIBUTING.md §3). Un Redis injoignable devient
`RateLimiterUnavailableError`, une erreur de ce module, que chaque appelant
attrape et traite selon ce qu'il protège : ce middleware échoue ouvert (un
débit anti-rafale, ADR-036/040) ; les compteurs de dépense réelle — quotas LLM
(`app.services.identity.platform_llm_budget`), création d'espace invité
(`app.services.identity.sessions`) — échouent **fermés** (`ServiceUnavailableError`,
ADR-054). La politique est donc toujours explicite au point d'appel, jamais
recopiée en silence d'un site à l'autre.

**Clé d'identité IP normalisée (ADR-052).** `normalize_client_ip` ramène une
IPv6 à son préfixe `/64` avant qu'elle ne serve de clé de plafond ou de
quota : une IPv6 individuelle n'a aucun coût pour un attaquant qui en change à
volonté dans son propre `/64`, contrairement à une IPv4. Une IPv4 mappée en
IPv6 (`::ffff:a.b.c.d`, double pile) est ramenée à l'IPv4 qu'elle encode, pour
ne pas lui ouvrir un compartiment distinct de la même adresse vue en IPv4 pure.
Cette normalisation ne s'applique **jamais** à `sessions.ip_address` (valeur
forensique exacte, inchangée) — seulement aux clés Redis construites à partir
de `request.client.host`.

**Le compteur IP a un troisième plafond, pour la passerelle Docker locale
(ADR-040).** `rate_limit_anonymous_requests` (défaut 60/min) est calibré pour
*un* visiteur anonyme isolé. En déploiement local (`docker/docker-compose.yml`,
le cas par défaut), `api` n'est jamais contactée directement par le
navigateur : tout trafic issu de la machine hôte — tous onglets, toutes
sessions confondues — traverse la publication de port Docker et arrive avec
la même adresse source, celle de la passerelle du réseau bridge. Ce plafond
strict, pensé pour un inconnu, freinait alors *tout le déploiement local* et
pouvait bloquer une requête sans aucun rapport avec celle qui a rempli le
compartiment (ex. une tentative de connexion coupée par un burst de polling
de suivi de jobs parti d'un autre onglet). `_detect_docker_gateway_ip()`
détecte cette adresse dynamiquement (route par défaut du conteneur lui-même,
`/proc/net/route` — c'est structurellement la même passerelle que celle vue
en NAT sortant, cf. docstring de la fonction) plutôt que de la coder en dur :
Docker peut attribuer un sous-réseau et donc une passerelle différents d'un
déploiement à l'autre. Cette IP reçoit `rate_limit_gateway_requests` (défaut
600/min, dix fois plus généreux) au lieu de `rate_limit_anonymous_requests` —
généreux mais toujours borné, jamais illimité : une vraie boucle d'abus
locale reste freinée, seulement à un débit plus élevé.

Détection gardée par la présence de `/.dockerenv` : en dehors d'un conteneur
Docker (déploiement bare-metal, hors du périmètre canonique de ce dépôt mais
pas interdit), la route par défaut du process est celle de l'infrastructure
réseau réelle (box internet, routeur d'entreprise) — potentiellement partagée
par de nombreux vrais visiteurs anonymes derrière un NAT. Lui accorder le
plafond généreux affaiblirait alors la protection pour un cas que ce correctif
ne vise pas. Un Redis/`/proc/net/route` injoignable ou absent (plateforme non
Linux, conteneur exotique) fait échouer la détection ouvert : `None`, retour
silencieux au plafond anonyme strict — jamais de plafond élevé accordé « par
défaut » à une IP non confirmée comme passerelle Docker.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol, cast

import redis.asyncio as redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.core.errors import TooManyRequestsError, problem_response
from app.core.security import hash_token

_logger = logging.getLogger(__name__)

_DOCKERENV_MARKER: Final = Path("/.dockerenv")
_PROC_NET_ROUTE: Final = Path("/proc/net/route")
"""Constante plutôt que littéral en dur dans `_detect_docker_gateway_ip` : les
tests substituent un fichier temporaire à ce chemin (`monkeypatch.setattr`)
plutôt que de dépendre du `/proc` réel de la machine qui exécute la suite."""

_DEFAULT_ROUTE_DESTINATION: Final = "00000000"
"""Colonne `Destination` de `/proc/net/route` pour la route par défaut (`0.0.0.0/0`)."""

_BEARER_SCHEME: Final = "bearer"

_EXCLUDED_PATH_SUFFIXES: Final = ("/health",)
"""Jamais limité : sonde de liveness Docker, sondée toutes les quelques secondes
sans dépendance externe (`app/api/v1/routes/health.py`) — la coupler au rate
limiting romprait cette garantie et ferait échouer le healthcheck si Redis est
momentanément indisponible, pour un endpoint qui ne coûte rien à laisser ouvert.
"""


class _UnsetGatewayIp:
    """Sentinel distinct de `None` pour le paramètre `gateway_ip` de `RateLimitMiddleware`.

    `None` est une valeur valide et volontaire (« pas de passerelle Docker,
    même plafond pour tout le monde ») — un simple défaut `None` ne pourrait
    pas distinguer ce cas de « non précisé, détecte-la toi-même ».
    """


_UNSET_GATEWAY_IP: Final = _UnsetGatewayIp()

_INCR_WITH_TTL_SCRIPT: Final = """
local current = redis.call("INCR", KEYS[1])
if tonumber(current) == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""
"""`INCR` + pose du TTL **une seule fois**, au premier coup de la fenêtre — sinon
chaque requête repousserait l'expiration et la fenêtre ne se viderait jamais."""


class RateLimiterUnavailableError(Exception):
    """Le compteur (Redis) est injoignable — la politique revient à l'appelant.

    Le port ne décide jamais lui-même d'échouer ouvert ou fermé : ce choix
    dépend de ce que le compteur protège (débit anti-rafale, fail-open,
    ADR-036/040 ; dépense réelle — quota LLM, création d'espace invité —,
    fail-closed, ADR-054). Chaque appelant attrape **cette** erreur, jamais
    `redis.exceptions.RedisError` directement : c'est elle qui ferait fuir le
    SDK concret hors de l'adaptateur (CONTRIBUTING.md §3). Voir le docstring de
    module pour la table des politiques par compteur.
    """


def normalize_client_ip(host: str) -> str:
    """Ramène `host` à la clé d'identité utilisée pour un plafond/quota (ADR-052).

    IPv6 → préfixe `/64` (un attaquant change d'adresse à volonté dans son
    propre `/64` — un compteur par adresse individuelle ne le freine pas).
    IPv4 → inchangée. IPv4 mappée en IPv6 (`::ffff:a.b.c.d`, socket double
    pile) → l'IPv4 qu'elle encode, pour ne pas lui ouvrir un compartiment
    distinct de la même adresse vue en IPv4 pure.

    **Jamais** appliqué à `sessions.ip_address` (valeur forensique exacte) —
    seulement aux clés Redis de plafond/quota construites à partir de
    `request.client.host`.

    Une valeur non parseable (`"unknown"`, adresse de test malformée) est
    renvoyée telle quelle : un compteur par valeur littérale reste un
    compteur valide, seulement moins précis qu'une vraie IP.
    """
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(parsed, ipaddress.IPv4Address):
        return str(parsed)
    mapped = parsed.ipv4_mapped
    if mapped is not None:
        return str(mapped)
    network = ipaddress.ip_network(f"{parsed}/64", strict=False)
    return str(network.network_address)


@dataclass(frozen=True, slots=True)
class RateLimitOutcome:
    """Résultat d'un `hit()` : autorisé ou non, et le délai avant réessai."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    """Port minimal attendu par `RateLimitMiddleware` — `RedisRateLimiter` l'implémente.

    Un `Protocol` plutôt qu'exiger `RedisRateLimiter` littéralement : les tests
    substituent des doubles en mémoire (toujours autorisé, indisponibilité
    simulée) sans dépendre de l'implémentation Redis — même logique qu'un port
    `providers/` (CONTRIBUTING.md §3), à ceci près que le rate limiting n'a qu'un seul
    adaptateur réel et ne justifie donc pas un module `providers/` dédié.
    """

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome: ...


class RedisRateLimiter:
    """Compteur à fenêtre fixe adossé à Redis, un `EVAL` atomique par requête."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self._script = client.register_script(_INCR_WITH_TTL_SCRIPT)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitOutcome:
        """Incrémente le compteur de `key` et indique s'il dépasse `limit`.

        Raises:
            RateLimiterUnavailableError: Redis injoignable — la politique
                (fail-open ou fail-closed) revient entièrement à l'appelant.
        """
        try:
            current = int(await self._script(keys=[key], args=[window_seconds]))
            if current > limit:
                ttl = await self._client.ttl(key)
                retry_after = ttl if ttl and ttl > 0 else window_seconds
                return RateLimitOutcome(allowed=False, remaining=0, retry_after_seconds=retry_after)
            return RateLimitOutcome(
                allowed=True, remaining=max(limit - current, 0), retry_after_seconds=0
            )
        except RedisError as exc:
            raise RateLimiterUnavailableError(str(exc)) from exc

    async def peek(self, key: str) -> int:
        """Lit le compteur courant de `key` sans l'incrémenter (lecture admin, ADR-047).

        `0` si la clé n'existe pas (fenêtre pas encore ouverte) — indistinguable
        d'une fenêtre tout juste expirée, ce qui est sans conséquence pour une
        lecture d'observation.

        Raises:
            RateLimiterUnavailableError: Redis injoignable — laissé à l'appelant.
        """
        try:
            value = await self._client.get(key)
        except RedisError as exc:
            raise RateLimiterUnavailableError(str(exc)) from exc
        return int(value) if value is not None else 0

    async def remember_member(self, set_key: str, member: str, *, window_seconds: int) -> None:
        """Ajoute `member` à l'ensemble `set_key`, avec la même fenêtre d'expiration qu'un compteur.

        Permet de retrouver *qui* a consommé un plafond journalier, pas seulement
        *combien* — le tableau de bord admin (ADR-048) lit ces ensembles pour
        lister les comptes/chatbots/IP actifs sans scanner tout l'espace de clés.
        `SADD` puis `EXPIRE` (pas `SET ... EX` atomique, indisponible sur un set) :
        une fenêtre qui expire une fraction de seconde plus tôt ne fait perdre
        qu'une entrée de liste, jamais l'exactitude d'un compteur de plafond.
        """
        # `redis-py` type `sadd`/`expire`/`smembers` en `Awaitable[X] | X` dans ses
        # stubs (support pipeline synchrone) — toujours un `Awaitable` ici, client
        # asynchrone (`redis.asyncio`) : cast, pas un vrai flou de type.
        await cast("Awaitable[int]", self._client.sadd(set_key, member))
        await cast("Awaitable[bool]", self._client.expire(set_key, window_seconds))

    async def members(self, set_key: str) -> set[str]:
        """Lit l'ensemble `set_key` — vide si la fenêtre n'est pas ouverte ou a expiré."""
        raw = await cast("Awaitable[set[bytes | str]]", self._client.smembers(set_key))
        return {member.decode() if isinstance(member, bytes) else member for member in raw}

    async def close(self) -> None:
        """Ferme la connexion Redis sous-jacente."""
        await self._client.aclose()


_limiter: RedisRateLimiter | None = None


def get_rate_limiter(settings: Settings) -> RedisRateLimiter:
    """Renvoie le limiteur du process — singleton paresseux, comme le moteur DB.

    Paresseux pour la même raison que `app.db.session.get_engine` : importer ce
    module ne doit pas, à lui seul, ouvrir une connexion Redis.
    """
    global _limiter
    if _limiter is None:
        # `redis-py` ne type pas `from_url` (aucune annotation de retour dans la
        # bibliothèque elle-même — pas un stub manquant, cf. `redis/asyncio/client.py`).
        client = redis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        _limiter = RedisRateLimiter(client)
    return _limiter


async def close_rate_limiter() -> None:
    """Ferme la connexion Redis du limiteur et réinitialise le singleton.

    À appeler à l'arrêt de l'application (`lifespan`), comme `dispose_engine`.
    """
    global _limiter
    if _limiter is not None:
        await _limiter.close()
    _limiter = None


def _bearer_token(header_value: str | None) -> str | None:
    """Extrait un jeton `Bearer` bien formé, sans jamais lever.

    Contrairement à `app.services.identity.principal.parse_bearer_token`, un
    schéma absent ou malformé renvoie `None` plutôt qu'une erreur : ce
    middleware s'exécute hors de la portée d'`ExceptionMiddleware` (ajouté via
    `add_middleware`), une `RAGEngineError` levée ici ne serait donc **pas**
    traduite en Problem Details par les handlers de `app.core.errors` mais
    remonterait telle quelle jusqu'à `ServerErrorMiddleware` (réponse `500`
    brute). Un jeton absent ou mal formé retombe simplement sur le compteur IP.
    """
    if not header_value or not header_value.strip():
        return None
    scheme, _, credentials = header_value.strip().partition(" ")
    if scheme.lower() != _BEARER_SCHEME or not credentials.strip():
        return None
    return credentials.strip()


def _session_token(request: Request, settings: Settings) -> str | None:
    """Lit le jeton de session brut porté par le cookie, sans le valider.

    Symétrique de `_bearer_token` : un cookie absent ou vide retombe sur le
    compteur IP. Pas d'appel à `PrincipalResolver.resolve_session` ici — cette
    fonction ne fait qu'un accès dict (`request.cookies`), jamais de requête
    SQL (voir docstring de module). Duplique volontairement la lecture
    élémentaire de `app.api.v1.dependencies.read_session_token` plutôt que de
    l'importer : `app/core/` ne doit pas dépendre de `app/api/` (sens unique
    des couches, CONTRIBUTING.md §3), et il n'y a rien à valider ici, donc rien à
    dupliquer côté logique.

    Sous `session_cookie_effective_name`, comme `read_session_token` (audit
    sécurité 2026-09, constat 4, ADR-064) : lire `session_cookie_name` brut
    reviendrait à ne jamais trouver le cookie une fois préfixé `__Host-`, et
    ce compteur retomberait silencieusement sur l'IP pour 100 % du trafic
    authentifié par cookie en staging/production.
    """
    raw = request.cookies.get(settings.session_cookie_effective_name)
    if not raw or not raw.strip():
        return None
    return raw.strip()


@lru_cache(maxsize=1)
def _detect_docker_gateway_ip() -> str | None:
    """Adresse de la passerelle du réseau Docker bridge courant, ou `None`.

    **Pas codée en dur** (ADR-040) : lit la route par défaut du conteneur
    lui-même dans `/proc/net/route` plutôt que de supposer `172.18.0.1` — le
    sous-réseau et donc la passerelle sont attribués par Docker et peuvent
    différer d'un déploiement à l'autre (autre réseau `bridge`, plusieurs
    projets Compose sur la même machine, etc.).

    C'est fiable *par construction* du driver `bridge` de Docker : tout
    conteneur qui y est attaché reçoit une route par défaut vers l'adresse
    `.1` du sous-réseau, et c'est cette même adresse que le hairpin NAT du
    port publié (`ports:` de `docker-compose.yml`) substitue à la source
    réelle pour tout appelant qui contacte le port publié depuis l'hôte —
    donc, entre autres, tout onglet de navigateur qui parle à l'API en
    déploiement local. Il ne s'agit pas d'une coïncidence à revalider à
    chaque déploiement : c'est le même mécanisme réseau des deux côtés.

    Gardée par `/.dockerenv` (marqueur standard posé par le moteur Docker
    dans tout conteneur) : hors d'un conteneur, la route par défaut du
    process est celle de l'infrastructure réseau réelle, pas une passerelle
    Docker locale — un cas que cette détection ne doit jamais capturer (voir
    docstring de module). `@lru_cache` : la route par défaut d'un conteneur
    ne change pas en cours de vie du process, inutile de rouvrir
    `/proc/net/route` à chaque requête sur le chemin chaud.

    Toute condition d'échec (fichier absent, format inattendu, plateforme non
    Linux) renvoie `None` plutôt que de lever — jamais de plafond élevé
    accordé sur la foi d'une détection qui a échoué.
    """
    if not _DOCKERENV_MARKER.exists():
        return None
    try:
        with _PROC_NET_ROUTE.open(encoding="ascii") as route_file:
            next(route_file)  # en-tête de colonnes, pas une route
            for line in route_file:
                fields = line.split()
                if len(fields) < 3:
                    continue
                destination, gateway_hex = fields[1], fields[2]
                if destination != _DEFAULT_ROUTE_DESTINATION:
                    continue
                # `/proc/net/route` encode l'adresse en hexadécimal, octets en
                # ordre machine (little-endian sur toute plateforme Linux
                # pertinente ici) — `"<L"` reflète cet ordre, pas celui du
                # réseau (`inet_ntoa` attend déjà l'ordre réseau une fois
                # `struct.pack` appliqué).
                return socket.inet_ntoa(struct.pack("<L", int(gateway_hex, 16)))
    except (OSError, StopIteration, ValueError):
        _logger.warning(
            "Détection de la passerelle Docker impossible (/proc/net/route) — le "
            "plafond IP anonyme s'applique même au trafic local (ADR-040)."
        )
        return None
    return None


def _is_excluded(path: str) -> bool:
    return path.endswith(_EXCLUDED_PATH_SUFFIXES)


def _too_many_requests_response(request: Request, retry_after_seconds: int) -> Response:
    """Construit la réponse `429` — Problem Details, comme toute autre erreur.

    Réutilise `TooManyRequestsError.to_problem()` sans jamais lever
    l'exception (voir son docstring dans `app.core.errors`).
    """
    problem = TooManyRequestsError(
        "Trop de requêtes : réessayez plus tard.",
        extensions={"retry_after_seconds": retry_after_seconds},
    ).to_problem(instance=request.url.path)
    return problem_response(problem, headers={"Retry-After": str(retry_after_seconds)})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applique le compteur IP (toujours) + un compteur généreux (clé API ou session)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: RateLimiter | None = None,
        gateway_ip: str | _UnsetGatewayIp | None = _UNSET_GATEWAY_IP,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._limiter = limiter if limiter is not None else get_rate_limiter(settings)
        # `gateway_ip` explicite (tests) court-circuite la détection réelle —
        # `_UNSET_GATEWAY_IP` distingue « non fourni » de « détection ayant
        # échoué, `None` volontaire » (ADR-040) : un test qui veut vérifier le
        # comportement *sans* passerelle Docker passe `gateway_ip=None`
        # explicitement plutôt que de dépendre du système de fichiers réel.
        self._gateway_ip = (
            _detect_docker_gateway_ip() if gateway_ip is _UNSET_GATEWAY_IP else gateway_ip
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._settings.rate_limit_enabled or _is_excluded(request.url.path):
            return await call_next(request)

        client_ip = normalize_client_ip(
            request.client.host if request.client is not None else "unknown"
        )
        window = self._settings.rate_limit_window_seconds
        ip_limit = (
            self._settings.rate_limit_gateway_requests
            if self._gateway_ip is not None and client_ip == self._gateway_ip
            else self._settings.rate_limit_anonymous_requests
        )

        try:
            ip_outcome = await self._limiter.hit(
                f"ratelimit:ip:{client_ip}",
                limit=ip_limit,
                window_seconds=window,
            )
        except RateLimiterUnavailableError:
            _logger.warning("Compteur IP injoignable — requête laissée passer (fail-open).")
            return await call_next(request)

        if not ip_outcome.allowed:
            return _too_many_requests_response(request, ip_outcome.retry_after_seconds)

        generous_key = self._generous_counter_key(request)
        if generous_key is not None:
            try:
                generous_outcome = await self._limiter.hit(
                    generous_key,
                    limit=self._settings.rate_limit_requests,
                    window_seconds=window,
                )
            except RateLimiterUnavailableError:
                _logger.warning(
                    "Compteur clé/session injoignable — requête laissée passer (fail-open)."
                )
                generous_outcome = None
            if generous_outcome is not None and not generous_outcome.allowed:
                return _too_many_requests_response(request, generous_outcome.retry_after_seconds)

        return await call_next(request)

    def _generous_counter_key(self, request: Request) -> str | None:
        """Clé du compteur généreux (`rate_limit_requests`), ou `None` sans identifiant.

        Même ordre de priorité que `PrincipalResolver.resolve` : une clé API
        (en-tête `Authorization: Bearer`) prime sur le cookie de session — un
        appelant qui présente les deux est un client machine qui a aussi une
        session navigateur active, son intention est la clé API. Sinon, le
        cookie de session (utilisateur connecté au navigateur, ADR-036). Sans
        les deux, `None` : seul le compteur IP s'applique.
        """
        token = _bearer_token(request.headers.get("authorization"))
        if token is not None:
            return f"ratelimit:key:{hash_token(token)}"
        session_token = _session_token(request, self._settings)
        if session_token is not None:
            return f"ratelimit:session:{hash_token(session_token)}"
        return None
