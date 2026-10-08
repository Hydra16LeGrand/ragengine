"""Compteur d'octets applicatif sur le corps des requêtes entrantes.

**Pourquoi.** Seules les bornes de Traefik (`docker/traefik/dynamic.yml`,
`maxRequestBodyBytes`, ADR-052) arrêtent un corps de requête trop gros
aujourd'hui : l'API elle-même n'en a aucune. C'est suffisant tant que l'API
n'est joignable que par le proxy, mais c'est une barrière unique (audit
sécurité 2026-09, constat 13) — ce module en est la seconde, indépendante.

**ASGI brut, pas `BaseHTTPMiddleware`.** Ce middleware doit intercepter
`receive` au moment précis où l'application lit le corps (JSON, multipart,
`chunked` sans `Content-Length` connu à l'avance) — un `BaseHTTPMiddleware`
qui construirait un `Request` ne donnerait aucune prise pour compter les
octets *au fil de l'eau* sans d'abord consommer le corps lui-même.

**Stratégie : tamponner jusqu'à la borne, puis rejeter ou rejouer — jamais
lever d'exception vers l'application interne.** Une exception levée depuis
`receive()` (variante plus simple, essayée d'abord) ne remonte **pas**
proprement jusqu'à ce module : vérifié empiriquement, pas supposé — Starlette
enveloppe lui-même la lecture du corps d'une requête `application/json`
(`Request.json()`, dépendance Pydantic de FastAPI) et d'un `multipart/form-data`
(`UploadFile`) dans son propre `try/except`, et recase **toute** exception
survenue pendant cette lecture — y compris une exception maison, sans rapport
avec un JSON malformé — en `400 "There was an error parsing the body"`. Le
`413` voulu n'est jamais atteint ; seul un accès brut à `request.body()` (aucune
route de cette API ne s'en sert) laisse l'exception remonter telle quelle.
Le tampon-puis-rejeu élimine le problème à la racine : `self.app(...)` n'est
jamais appelé tant que la décision (admettre ou refuser) n'est pas prise, donc
aucune exception ne traverse jamais cette frontière ni n'est recatégorisée en
chemin. La mémoire tenue reste bornée à la limite appliquée (jamais plus qu'un
`RAGENGINE_UPLOAD_MAX_BYTES` sur la route d'ingestion, 1 Mo ailleurs) — même
philosophie que `app.services.documents.read_upload_content`, qui borne déjà
sa propre lecture de la même façon.

**Ne touche jamais `send`**, sauf pour construire lui-même sa réponse `413` :
la réponse en flux du chat (`text/event-stream`) est entièrement côté réponse
(`send`), jamais lue ici — ce module ne s'intéresse qu'au corps de la
*requête*, jamais à celui de la réponse.
"""

from __future__ import annotations

import re
from typing import Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import PayloadTooLargeError, problem_response

_INGESTION_ROUTE: Final = re.compile(r"^/api/v1/knowledge-bases/[^/]+/documents/ingest$")
"""Même route, même expression que le routeur `api-upload` de
`docker/traefik/dynamic.yml` — seule à recevoir le plafond d'upload plutôt que
le plafond bas générique. Dupliquée volontairement plutôt qu'importée : ce
module (`app/core/`) ne doit pas dépendre de `app/api/` (sens unique des
couches, CONTRIBUTING.md §3), exactement comme `app.core.rate_limit` duplique déjà
la lecture élémentaire du cookie de session pour la même raison."""

UPLOAD_MULTIPART_ENVELOPE_BYTES: Final = 1024 * 1024
"""Marge pour l'enveloppe multipart (frontières, en-têtes de partie, `name`,
`theme`) au-delà du fichier lui-même — même valeur que `body-limit-upload`
(Traefik, ADR-052). Relever `RAGENGINE_UPLOAD_MAX_BYTES` impose de relever
cette borne applicative comme celle du proxy."""


def _declared_content_length(scope: Scope) -> int | None:
    """Lit `Content-Length` sans faire confiance à sa présence ni à sa véracité.

    Sert uniquement à un rejet rapide, avant tout octet lu, quand le client
    annonce déjà un corps hors bornes — jamais à admettre un corps dont
    l'annonce mentirait par défaut : le compteur réel (`__call__`) reste seul
    juge de ce qui est effectivement reçu, `chunked` compris (aucun
    `Content-Length` dans ce cas).
    """
    raw = Headers(scope=scope).get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class RequestBodyLimitMiddleware:
    """Plafonne les octets réellement reçus du corps d'une requête (audit sécurité
    2026-09, constat 13).

    Plafond de la route d'ingestion (`upload_max_bytes` + enveloppe multipart) :
    reste compatible avec `DocumentService.ensure_upload_within_limits`, qui
    reste le juge final du fichier lui-même — ce module protège la mémoire du
    process contre le corps de la requête tout entier, avant même que
    l'admission ne s'exécute. Toute autre route reçoit `request_body_max_bytes`
    (1 Mo par défaut).
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)

        declared = _declared_content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(scope, send, limit)
            return

        buffered: list[Message] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                # `http.disconnect` avant la fin du corps : rien de plus à lire,
                # l'application interne le verra au même endroit dans le flux.
                break
            received += len(message.get("body", b""))
            if received > limit:
                await self._reject(scope, send, limit)
                return
            if not message.get("more_body", False):
                break

        await self.app(scope, _ReplayReceive(buffered, receive), send)

    def _limit_for(self, scope: Scope) -> int:
        if scope.get("method") == "POST" and _INGESTION_ROUTE.match(scope["path"]):
            return self._settings.upload_max_bytes + UPLOAD_MULTIPART_ENVELOPE_BYTES
        return self._settings.request_body_max_bytes

    async def _reject(self, scope: Scope, send: Send, limit: int) -> None:
        """Construit et envoie le `413` lui-même — jamais une connexion coupée sans
        réponse. Réutilise `PayloadTooLargeError.to_problem()` sans la lever, comme
        `app.core.rate_limit._too_many_requests_response` le fait pour son `429` :
        les deux s'exécutent hors de la portée d'`ExceptionMiddleware`."""
        problem = PayloadTooLargeError(
            f"Le corps de la requête dépasse la taille maximale autorisée ({limit} octets).",
            extensions={"limit_bytes": limit},
        ).to_problem(instance=scope.get("path"))
        response = problem_response(problem)
        await response(scope, _EMPTY_RECEIVE, send)


async def _empty_receive() -> Message:
    """`receive` neutre passé à la réponse `413` : `JSONResponse.__call__` ne lit
    jamais le corps de la requête, mais l'ASGI en exige un appelable valide."""
    return {"type": "http.disconnect"}


_EMPTY_RECEIVE: Final[Receive] = _empty_receive


class _ReplayReceive:
    """`receive` qui rejoue d'abord les messages déjà tamponnés, puis délègue au
    flux réel — l'application interne ne voit aucune différence avec le flux
    d'origine, seulement rejoué depuis un tampon borné plutôt que le socket."""

    __slots__ = ("_index", "_messages", "_real_receive")

    def __init__(self, messages: list[Message], real_receive: Receive) -> None:
        self._messages = messages
        self._real_receive = real_receive
        self._index = 0

    async def __call__(self) -> Message:
        if self._index < len(self._messages):
            message = self._messages[self._index]
            self._index += 1
            return message
        return await self._real_receive()
