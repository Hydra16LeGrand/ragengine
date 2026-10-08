"""Contrat d'erreur transverse : RFC 9457 « Problem Details for HTTP APIs ».

Toute erreur quittant l'API — métier, validation, HTTP ou inattendue — est
sérialisée en `application/problem+json` par les handlers enregistrés ici, afin
que les clients n'aient qu'une seule enveloppe à connaître (CONTRIBUTING.md §4).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import ClassVar, Final

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
"""Type de média imposé par la RFC 9457 pour les réponses d'erreur."""

DEFAULT_PROBLEM_TYPE: Final = "about:blank"
"""Valeur par défaut du membre `type` (RFC 9457 §4.2.1)."""

_logger = logging.getLogger(__name__)

_STANDARD_MEMBERS: Final = frozenset({"type", "title", "status", "detail", "instance"})


class ProblemDetail(BaseModel):
    """Document « Problem Details » (RFC 9457).

    Les membres standards sont typés ; les extensions spécifiques à un problème
    (par exemple `errors` pour une erreur de validation) sont autorisées à la
    racine du document, comme le prévoit la RFC 9457 §3.2.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        default=DEFAULT_PROBLEM_TYPE,
        description="Référence d'URI identifiant le type de problème.",
    )
    title: str = Field(description="Résumé court, lisible et stable du type de problème.")
    status: int = Field(ge=100, le=599, description="Code de statut HTTP de la réponse.")
    detail: str | None = Field(
        default=None, description="Explication propre à cette occurrence du problème."
    )
    instance: str | None = Field(
        default=None, description="Référence d'URI identifiant cette occurrence."
    )


def build_problem(
    *,
    status: int,
    title: str,
    type_: str = DEFAULT_PROBLEM_TYPE,
    detail: str | None = None,
    instance: str | None = None,
    extensions: Mapping[str, JsonValue] | None = None,
) -> ProblemDetail:
    """Assemble un `ProblemDetail`, les extensions ne pouvant écraser les membres standards."""
    payload: dict[str, JsonValue] = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    if extensions:
        payload.update(
            {key: value for key, value in extensions.items() if key not in _STANDARD_MEMBERS}
        )
    return ProblemDetail.model_validate(payload)


def problem_response(
    problem: ProblemDetail, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Sérialise un `ProblemDetail` en réponse `application/problem+json`."""
    return JSONResponse(
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=dict(headers) if headers else None,
    )


class RAGEngineError(Exception):
    """Erreur applicative traduisible en Problem Details.

    Les sous-classes fixent le triplet (`status`, `title`, `type`) ; l'appelant
    fournit le `detail` de l'occurrence et, si besoin, des extensions.
    """

    status: ClassVar[int] = HTTPStatus.INTERNAL_SERVER_ERROR
    title: ClassVar[str] = "Internal Server Error"
    type: ClassVar[str] = "/problems/internal-error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extensions: Mapping[str, JsonValue] = dict(extensions) if extensions else {}

    def to_problem(self, instance: str | None = None) -> ProblemDetail:
        """Projette l'erreur en document Problem Details."""
        return build_problem(
            status=self.status,
            title=self.title,
            type_=self.type,
            detail=self.detail,
            instance=instance,
            extensions=self.extensions,
        )


class NotFoundError(RAGEngineError):
    """Ressource demandée inexistante ou hors du périmètre de l'appelant."""

    status: ClassVar[int] = HTTPStatus.NOT_FOUND
    title: ClassVar[str] = "Not Found"
    type: ClassVar[str] = "/problems/not-found"


class ConflictError(RAGEngineError):
    """Conflit avec l'état courant de la ressource (état de job, unicité…)."""

    status: ClassVar[int] = HTTPStatus.CONFLICT
    title: ClassVar[str] = "Conflict"
    type: ClassVar[str] = "/problems/conflict"


class ValidationError(RAGEngineError):
    """Requête syntaxiquement correcte mais sémantiquement invalide."""

    status: ClassVar[int] = HTTPStatus.UNPROCESSABLE_ENTITY
    title: ClassVar[str] = "Unprocessable Entity"
    type: ClassVar[str] = "/problems/validation-error"


class UnauthorizedError(RAGEngineError):
    """Authentification absente ou invalide."""

    status: ClassVar[int] = HTTPStatus.UNAUTHORIZED
    title: ClassVar[str] = "Unauthorized"
    type: ClassVar[str] = "/problems/unauthorized"


class ForbiddenError(RAGEngineError):
    """Authentification valide mais privilèges insuffisants."""

    status: ClassVar[int] = HTTPStatus.FORBIDDEN
    title: ClassVar[str] = "Forbidden"
    type: ClassVar[str] = "/problems/forbidden"


class PayloadTooLargeError(RAGEngineError):
    """Corps de requête excédant une limite déclarée (upload, notamment)."""

    status: ClassVar[int] = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: ClassVar[str] = "Payload Too Large"
    type: ClassVar[str] = "/problems/payload-too-large"


class UnsupportedMediaTypeError(RAGEngineError):
    """Type de média non accepté par l'endpoint (liste blanche d'upload, §9)."""

    status: ClassVar[int] = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    title: ClassVar[str] = "Unsupported Media Type"
    type: ClassVar[str] = "/problems/unsupported-media-type"


class TooManyRequestsError(RAGEngineError):
    """Débit ou quota dépassé.

    Deux chemins. Le middleware de rate limiting (`app.core.rate_limit`) la
    construit et la sérialise lui-même, sans la lever : un middleware ASGI
    ajouté via `add_middleware` s'exécute hors de la portée
    d'`ExceptionMiddleware`. Les services, eux, la **lèvent** (plafonds de
    dépense LLM, ADR-047/054 ; rate limit d'un chatbot publié ; plafond de
    créations d'espaces invités) et `ragengine_error_handler` la traduit.
    L'extension `retry_after_seconds`, quand elle est présente, devient aussi
    l'en-tête `Retry-After` sur les deux chemins.
    """

    status: ClassVar[int] = HTTPStatus.TOO_MANY_REQUESTS
    title: ClassVar[str] = "Too Many Requests"
    type: ClassVar[str] = "/problems/rate-limit-exceeded"


class ServiceUnavailableError(RAGEngineError):
    """Dépendance indispensable momentanément injoignable — on refuse plutôt que d'agir à l'aveugle.

    Réservée aux garde-fous qui protègent une dépense réelle (compteurs de quota
    LLM, plafond de créations d'espaces invités, ADR-054) : là, laisser passer
    faute de pouvoir compter ouvrirait la dépense sans plafond. Le rate limiting
    anti-rafale, lui, échoue ouvert (`app.core.rate_limit`).
    """

    status: ClassVar[int] = HTTPStatus.SERVICE_UNAVAILABLE
    title: ClassVar[str] = "Service Unavailable"
    type: ClassVar[str] = "/problems/service-unavailable"


def _title_for_status(status: int) -> str:
    """Libellé standard associé à un code HTTP, avec repli sur un libellé générique."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


async def ragengine_error_handler(request: Request, exc: Exception) -> Response:
    """Traduit une `RAGEngineError` en réponse Problem Details."""
    if not isinstance(exc, RAGEngineError):  # pragma: no cover - garde-fou de typage
        raise exc
    headers: dict[str, str] | None = None
    retry_after = exc.extensions.get("retry_after_seconds")
    if isinstance(exc, TooManyRequestsError) and isinstance(retry_after, int):
        # Même en-tête que le middleware de rate limiting (`_too_many_requests_response`) :
        # un client ne doit pas avoir à distinguer d'où vient le `429` pour savoir quand
        # réessayer.
        headers = {"Retry-After": str(retry_after)}
    return problem_response(exc.to_problem(instance=request.url.path), headers=headers)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """Traduit les `HTTPException` (dont les 404 de routage) en Problem Details."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - garde-fou de typage
        raise exc
    title = _title_for_status(exc.status_code)
    detail = str(exc.detail) if exc.detail is not None else None
    return problem_response(
        build_problem(
            status=exc.status_code,
            title=title,
            detail=None if detail == title else detail,
            instance=request.url.path,
        ),
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    """Traduit l'échec de validation Pydantic d'une requête en Problem Details."""
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - garde-fou de typage
        raise exc
    return problem_response(
        build_problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            title=ValidationError.title,
            type_=ValidationError.type,
            detail="La requête ne respecte pas le schéma attendu.",
            instance=request.url.path,
            # `jsonable_encoder` neutralise les objets non sérialisables du contexte Pydantic.
            extensions={"errors": jsonable_encoder(exc.errors())},
        )
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    """Filet de sécurité : aucune erreur inattendue ne sort hors enveloppe RFC 9457."""
    _logger.exception(
        "Erreur non gérée", extra={"path": request.url.path, "method": request.method}
    )
    return problem_response(
        build_problem(
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            title=RAGEngineError.title,
            type_=RAGEngineError.type,
            detail="Une erreur interne est survenue.",
            instance=request.url.path,
        )
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Branche les handlers Problem Details sur l'application."""
    app.add_exception_handler(RAGEngineError, ragengine_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
