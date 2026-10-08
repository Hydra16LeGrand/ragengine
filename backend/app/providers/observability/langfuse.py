"""Adaptateur `ObservabilityPort` vers Langfuse, via OpenTelemetry standard.

Langfuse est consommé par son **endpoint OTLP standard** (`opentelemetry-sdk` +
`opentelemetry-exporter-otlp-proto-http`), pas par un SDK propriétaire : le jour
où le collecteur change, seule cette classe bouge.

**Authentification.** Langfuse accepte l'en-tête HTTP Basic standard
(`Authorization: Basic base64(public_key:secret_key)`). C'est la variante
retenue ici plutôt que le couple d'en-têtes propriétaires
`x-langfuse-public-key` / `x-langfuse-secret-key` : c'est un mécanisme HTTP
standard, transportable tel quel vers n'importe quel collecteur OTLP protégé par
Basic, et il n'ajoute aucune dépendance au protocole Langfuse.

**Non bloquant, jamais fatal** (CONTRIBUTING.md §3.4). Deux garanties distinctes :

- l'export est asynchrone — `BatchSpanProcessor` bufferise et exporte depuis un
  thread dédié, donc un collecteur lent ne ralentit jamais la requête ;
- toute défaillance du traçage (ouverture de span, fermeture, export
  synchrone-échoué) est capturée et loguée ici même. Aucune exception issue de
  l'observabilité ne remonte à l'appelant.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import Token
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from opentelemetry import context as context_api
from opentelemetry import trace
from opentelemetry.context.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from opentelemetry.util.types import AttributeValue
from pydantic import JsonValue

from app.core.config import Settings
from app.core.errors import ValidationError
from app.domain.enums import JobState

_logger = logging.getLogger(__name__)

OTLP_TRACES_PATH: Final = "/api/public/otel/v1/traces"
"""Chemin de l'endpoint OTLP/HTTP de Langfuse pour le signal « traces »."""

SERVICE_NAME_VALUE: Final = "ragengine-api"
"""Nom de service porté par toutes les traces émises par la façade."""

TRACER_NAME: Final = "app.providers.observability.langfuse"

INGESTION_SPAN_NAME: Final = "ingestion.step"
RETRIEVAL_SPAN_NAME: Final = "retrieval.search"


def otlp_endpoint(host: str) -> str:
    """Construit l'URL OTLP des traces à partir de l'hôte Langfuse configuré."""
    return f"{host.rstrip('/')}{OTLP_TRACES_PATH}"


def basic_auth_header(public_key: str, secret_key: str) -> str:
    """Encode le couple de clés Langfuse en en-tête `Authorization` HTTP Basic."""
    encoded = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _span_attributes(attributes: Mapping[str, JsonValue]) -> dict[str, AttributeValue]:
    """Projette des valeurs JSON arbitraires vers les types d'attributs OpenTelemetry.

    OpenTelemetry n'accepte que des scalaires et des séquences homogènes : les
    structures imbriquées sont sérialisées en JSON (clés triées, donc stable) et
    les valeurs nulles omises plutôt que converties en chaîne « None ».
    """
    encoded: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            encoded[key] = value
        else:
            encoded[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return encoded


@dataclass(frozen=True)
class _ActiveSpan:
    """Span ouvert et son jeton de contexte, à rendre lors de la fermeture."""

    span: Span
    token: Token[Context]


class LangfuseObservability:
    """Traçage réel, exporté vers Langfuse en OTLP/HTTP."""

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer

    @classmethod
    def from_settings(cls, settings: Settings) -> LangfuseObservability:
        """Assemble exporteur, processeur par lots et fournisseur de traces.

        Les clés peuvent manquer alors que l'hôte est renseigné : on construit
        quand même l'exporteur (le collecteur répondra 401, ce qui sera logué et
        absorbé) plutôt que d'échouer au démarrage pour une brique optionnelle.
        C'est ce cas que `observability_provider_status` signale par
        `available=false`.
        """
        if settings.langfuse_host is None:
            raise ValidationError("LANGFUSE_HOST doit être renseigné pour activer Langfuse.")

        headers: dict[str, str] = {}
        if settings.langfuse_public_key is not None and settings.langfuse_secret_key is not None:
            headers["Authorization"] = basic_auth_header(
                settings.langfuse_public_key.get_secret_value(),
                settings.langfuse_secret_key.get_secret_value(),
            )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint(settings.langfuse_host), headers=headers)
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: SERVICE_NAME_VALUE}),
        )
        # `BatchSpanProcessor` : export bufferisé sur un thread dédié — la
        # requête observée n'attend jamais le collecteur.
        provider.add_span_processor(BatchSpanProcessor(exporter))
        return cls(provider.get_tracer(TRACER_NAME))

    def observe_ingestion_step(
        self, *, job_id: UUID, state: JobState, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Encadre une étape du pipeline d'ingestion d'un job."""
        return self._observe(
            INGESTION_SPAN_NAME,
            {"job.id": str(job_id), "job.state": state.value, **attributes},
        )

    def observe_retrieval(
        self, *, knowledge_base_id: UUID, query: str, **attributes: JsonValue
    ) -> AbstractContextManager[None]:
        """Encadre une recherche dans une base de connaissances."""
        return self._observe(
            RETRIEVAL_SPAN_NAME,
            {
                "knowledge_base.id": str(knowledge_base_id),
                "retrieval.query": query,
                **attributes,
            },
        )

    def record_event(self, name: str, **attributes: JsonValue) -> None:
        """Enregistre un événement ponctuel ; toute défaillance est absorbée."""
        try:
            encoded = _span_attributes(attributes)
            current_span = trace.get_current_span()
            if current_span.is_recording():
                current_span.add_event(name, attributes=encoded)
            else:
                # Hors de tout span : un span instantané reste la seule
                # représentation qu'un collecteur OTLP sait ingérer.
                self._tracer.start_span(name, attributes=encoded).end()
        except Exception:
            self._log_failure("enregistrer l'événement", name)

    @contextmanager
    def _observe(self, name: str, attributes: Mapping[str, JsonValue]) -> Iterator[None]:
        """Ouvre un span autour du bloc appelant, sans jamais le faire échouer."""
        active = self._start_span(name, attributes)
        try:
            yield
        # `BaseException` et non `Exception` : une tâche asyncio annulée lève
        # `CancelledError`, qui doit malgré tout refermer le span.
        except BaseException as error:
            self._end_span(active, error)
            raise
        else:
            self._end_span(active, None)

    def _start_span(self, name: str, attributes: Mapping[str, JsonValue]) -> _ActiveSpan | None:
        """Ouvre et active un span ; renvoie `None` si le traçage a échoué."""
        try:
            span = self._tracer.start_span(name, attributes=_span_attributes(attributes))
        except Exception:
            self._log_failure("ouvrir le span", name)
            return None
        try:
            token = context_api.attach(trace.set_span_in_context(span))
        except Exception:
            self._log_failure("activer le span", name)
            self._close_quietly(span)
            return None
        return _ActiveSpan(span=span, token=token)

    def _end_span(self, active: _ActiveSpan | None, error: BaseException | None) -> None:
        """Referme le span en marquant l'échec éventuel du bloc observé."""
        if active is None:
            return
        try:
            if error is not None:
                active.span.record_exception(error)
                active.span.set_status(Status(StatusCode.ERROR, str(error)))
            context_api.detach(active.token)
        except Exception:
            self._log_failure("refermer le span", active.span.__class__.__name__)
        self._close_quietly(active.span)

    def _close_quietly(self, span: Span) -> None:
        try:
            span.end()
        except Exception:
            self._log_failure("clôturer le span", span.__class__.__name__)

    @staticmethod
    def _log_failure(action: str, subject: str) -> None:
        _logger.warning(
            "Observabilité indisponible : impossible de %s « %s ». "
            "L'opération observée n'est pas affectée.",
            action,
            subject,
            exc_info=True,
        )
