"""Greffons : extensions du cœur, découvertes dans le paquet `app.plugins`.

Le cœur ne nomme **jamais** un greffon. Tout sous-paquet de `app.plugins` qui
expose un attribut `plugin` (instance de `Plugin`) est importé au premier appel
de `get_plugins()` ; son absence ne laisse rien derrière elle — ni drapeau, ni
branche morte à réactiver. C'est ce qui permet de livrer un cœur autonome en
retirant un dossier, sans patcher le code (ADR-069).

Le sens des dépendances est fixe : un greffon dépend du cœur, jamais
l'inverse. Le cœur n'expose ici que des **points d'extension** typés, chacun
avec un comportement par défaut complet et sûr quand aucun greffon ne s'y
branche :

| Point d'extension      | Sans greffon (cœur seul)                                     |
|------------------------|--------------------------------------------------------------|
| `routers`              | Seuls les routeurs du cœur sont montés.                      |
| `admits_anonymous`     | Tout appel sans identifiant reçoit `401` : connexion exigée. |
| `api_key_scopes`       | Catalogue de portées du cœur (`app.domain.scopes`).          |
| `task_modules`/`routes`| Tâches Celery du cœur.                                       |
| `maintenance_steps`    | Balayage de maintenance du cœur (ADR-055).                   |
| `llm_funding_policy`   | Clé personnelle, sinon clé serveur, plafonds par compte.     |
| `llm_quota_rules`      | Compteurs de dépense par compte et par évaluation.           |
| `settings_defaults`    | Défauts du cœur : aucun plafond ni modèle imposé, marque RAGEngine. |
| `llm_global_budget_detail` | Message neutre quand le budget global est épuisé.        |
| `resource_quotas`      | Aucun plafond de création propre au compte.                  |
| `knowledge_base_fields`| `KnowledgeBaseRead` du cœur, sans champ supplémentaire.      |
| `principal_fields`     | `PrincipalRead` du cœur, sans champ supplémentaire.          |

**Découverte paresseuse, greffon léger à l'import.** `get_plugins()` est appelé
depuis des modules de bas niveau (schémas, dépendances, services) : le module
`__init__` d'un greffon ne doit donc importer que ce module-ci et des schémas
Pydantic purs. Tout le reste (routeurs, services) se charge à la demande, dans
les fabriques passées au `Plugin`. Une erreur d'import d'un greffon présent
n'est **jamais** avalée : un greffon cassé doit faire échouer le démarrage,
pas disparaître en silence.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from fastapi import APIRouter
    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import KnowledgeBase, User
    from app.services.identity.platform_llm_budget import QuotaRule
    from app.services.llm_funding import LlmFundingPolicy
    from app.services.resource_quotas import ResourceQuotaPolicy
    from app.workers.tasks.maintenance import MaintenanceStep

PLUGINS_PACKAGE: Final = "app.plugins"
"""Paquet scanné à la découverte : chaque sous-paquet est un greffon candidat."""


def _no_routers() -> Sequence[APIRouter]:
    return ()


def _no_maintenance_steps() -> Sequence[MaintenanceStep]:
    return ()


def _no_quota_rules() -> Mapping[str, QuotaRule]:
    return {}


@dataclass(frozen=True, slots=True)
class PrincipalFields:
    """Champs ajoutés par un greffon à `PrincipalRead` (`GET /me`, ouverture de session).

    `model` décrit les champs (documentation OpenAPI) et doit pouvoir être
    construit sans argument (valeurs d'un principal sans compte) ; `build` les
    calcule pour un compte donné, et rend une instance de `model`.
    """

    model: type[BaseModel]
    build: Callable[[AsyncSession, Settings, User], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class Plugin:
    """Contribution d'un greffon au cœur — voir la table de la docstring du module."""

    name: str
    routers: Callable[[], Sequence[APIRouter]] = _no_routers
    """Routeurs montés sous `/api/v1`, après ceux du cœur."""
    admits_anonymous: bool = False
    """Le greffon sert des visiteurs sans compte : un appel anonyme n'est plus refusé
    d'office, il traverse la matrice d'accès (`PermissionService`) comme tel."""
    api_key_scopes: tuple[str, ...] = ()
    """Portées de clé API supplémentaires, exigées par les routes du greffon."""
    task_modules: tuple[str, ...] = ()
    """Modules de tâches Celery à importer au démarrage du worker."""
    task_routes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """Routage Celery des tâches du greffon (même forme que `TASK_ROUTES`)."""
    maintenance_steps: Callable[[], Sequence[MaintenanceStep]] = _no_maintenance_steps
    """Catégories ajoutées au balayage de maintenance (ADR-055)."""
    llm_funding_policy: Callable[[], LlmFundingPolicy] | None = None
    """Remplace la politique de financement des appels LLM — un seul greffon au plus."""
    llm_quota_rules: Callable[[], Mapping[str, QuotaRule]] = _no_quota_rules
    """Compteurs de dépense supplémentaires, par sujet (`LlmQuotaScope.subject`)."""
    settings_defaults: Mapping[str, object] = field(default_factory=dict)
    """Valeurs par défaut de réglages `Settings` (plafonds de dépense LLM `platform_llm_*`, marque
    `app_name`), appliquées seulement quand l'environnement ne les renseigne pas — jamais
    par-dessus lui."""
    llm_global_budget_detail: str | None = None
    """Message du `429` quand le budget global est épuisé, à la place du message neutre."""
    resource_quotas: Callable[[AsyncSession, Settings], ResourceQuotaPolicy] | None = None
    """Plafonds de création (bases, documents) — un seul greffon au plus."""
    knowledge_base_fields: Callable[[], type[BaseModel]] | None = None
    """Champs ajoutés à `KnowledgeBaseRead`, lus sur la ligne `KnowledgeBase`."""
    principal_fields: Callable[[], PrincipalFields] | None = None
    """Champs ajoutés à `PrincipalRead`."""


@lru_cache(maxsize=1)
def _discover() -> tuple[Plugin, ...]:
    package = importlib.import_module(PLUGINS_PACKAGE)
    found: list[Plugin] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{PLUGINS_PACKAGE}.{info.name}")
        candidate = getattr(module, "plugin", None)
        if not isinstance(candidate, Plugin):
            raise RuntimeError(
                f"Greffon « {info.name} » invalide : `{PLUGINS_PACKAGE}.{info.name}.plugin` "
                "doit être une instance de `app.core.plugins.Plugin`."
            )
        found.append(candidate)
    return tuple(found)


_override: tuple[Plugin, ...] | None = None


def get_plugins() -> tuple[Plugin, ...]:
    """Greffons actifs, dans l'ordre de leur nom de paquet."""
    return _override if _override is not None else _discover()


@contextmanager
def plugins_overridden(plugins: Sequence[Plugin]) -> Iterator[None]:
    """Remplace les greffons découverts le temps d'un bloc.

    Sert aux tests (prouver le comportement du cœur seul dans le dépôt qui porte
    aussi des greffons) et à l'outillage (`app.cli.openapi --core`, contrat du
    cœur seul). Jamais appelé par le code de production.
    """
    global _override
    previous = _override
    _override = tuple(plugins)
    _reset_settings()
    try:
        yield
    finally:
        _override = previous
        _reset_settings()


def _reset_settings() -> None:
    """Oublie les réglages mis en cache : leurs défauts dépendent des greffons actifs."""
    from app.core.config import get_settings  # import tardif : `config` importe ce module

    get_settings.cache_clear()


def _single[T](name: str, values: Iterator[tuple[str, T]]) -> T | None:
    provided = list(values)
    if len(provided) > 1:
        owners = ", ".join(owner for owner, _ in provided)
        raise RuntimeError(f"Point d'extension `{name}` fourni par plusieurs greffons : {owners}.")
    return provided[0][1] if provided else None


def plugin_routers() -> list[APIRouter]:
    """Routeurs de tous les greffons, à monter après ceux du cœur."""
    return [router for plugin in get_plugins() for router in plugin.routers()]


def admits_anonymous() -> bool:
    """Un greffon sert-il des visiteurs sans compte ? Le cœur seul exige une connexion."""
    return any(plugin.admits_anonymous for plugin in get_plugins())


def extra_api_key_scopes() -> frozenset[str]:
    """Portées de clé API apportées par les greffons."""
    return frozenset(scope for plugin in get_plugins() for scope in plugin.api_key_scopes)


def extra_task_modules() -> tuple[str, ...]:
    """Modules de tâches Celery apportés par les greffons."""
    return tuple(module for plugin in get_plugins() for module in plugin.task_modules)


def extra_task_routes() -> dict[str, dict[str, str]]:
    """Routage Celery des tâches apportées par les greffons."""
    return {
        name: dict(route) for plugin in get_plugins() for name, route in plugin.task_routes.items()
    }


def extra_maintenance_steps() -> list[MaintenanceStep]:
    """Catégories du balayage de maintenance apportées par les greffons."""
    return [step for plugin in get_plugins() for step in plugin.maintenance_steps()]


def extra_llm_quota_rules() -> dict[str, QuotaRule]:
    """Compteurs de dépense LLM apportés par les greffons, par sujet."""
    return {
        subject: rule
        for plugin in get_plugins()
        for subject, rule in plugin.llm_quota_rules().items()
    }


def settings_defaults() -> dict[str, object]:
    """Défauts de réglages fournis par les greffons (cœur seul : aucun)."""
    return {
        name: value for plugin in get_plugins() for name, value in plugin.settings_defaults.items()
    }


def llm_global_budget_detail() -> str | None:
    """Message de budget global épuisé d'un greffon, ou `None` (message neutre du cœur)."""
    return next(
        (p.llm_global_budget_detail for p in get_plugins() if p.llm_global_budget_detail), None
    )


def llm_funding_policy_factory() -> Callable[[], LlmFundingPolicy] | None:
    """Fabrique de la politique de financement LLM d'un greffon, ou `None`."""
    return _single(
        "llm_funding_policy",
        (
            (plugin.name, plugin.llm_funding_policy)
            for plugin in get_plugins()
            if plugin.llm_funding_policy is not None
        ),
    )


def resource_quotas_factory() -> Callable[[AsyncSession, Settings], ResourceQuotaPolicy] | None:
    """Fabrique des plafonds de création d'un greffon, ou `None`."""
    return _single(
        "resource_quotas",
        (
            (plugin.name, plugin.resource_quotas)
            for plugin in get_plugins()
            if plugin.resource_quotas is not None
        ),
    )


def knowledge_base_field_models() -> list[type[BaseModel]]:
    """Modèles des champs ajoutés à `KnowledgeBaseRead` par les greffons."""
    return [
        plugin.knowledge_base_fields()
        for plugin in get_plugins()
        if plugin.knowledge_base_fields is not None
    ]


def principal_field_providers() -> list[PrincipalFields]:
    """Champs ajoutés à `PrincipalRead` par les greffons."""
    return [
        plugin.principal_fields() for plugin in get_plugins() if plugin.principal_fields is not None
    ]


def knowledge_base_extra_fields(knowledge_base: KnowledgeBase) -> dict[str, Any]:
    """Valeurs des champs de greffon pour une base, prêtes à fusionner dans `KnowledgeBaseRead`."""
    extra: dict[str, Any] = {}
    for model in knowledge_base_field_models():
        extra.update(model.model_validate(knowledge_base, from_attributes=True).model_dump())
    return extra


async def principal_extra_fields(
    db: AsyncSession, settings: Settings, user: User | None
) -> dict[str, Any]:
    """Valeurs des champs de greffon pour le principal courant.

    Sans compte (`user is None`), les valeurs par défaut du modèle : un champ de
    greffon est toujours présent dans la réponse, jamais tantôt absent.
    """
    extra: dict[str, Any] = {}
    for provider in principal_field_providers():
        fields = provider.model() if user is None else await provider.build(db, settings, user)
        extra.update(fields.model_dump())
    return extra


def _inline_refs(node: object, definitions: Mapping[str, object]) -> object:
    """Remplace les `$ref` locaux (`#/$defs/...`) par leur définition, récursivement."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline_refs(definitions[ref.removeprefix("#/$defs/")], definitions)
        return {key: _inline_refs(value, definitions) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, definitions) for item in node]
    return node


def _document_extra_fields(schema: dict[str, Any], models: Sequence[type[BaseModel]]) -> None:
    """Ajoute à un schéma JSON les propriétés des modèles de greffon, sans `$ref` pendante.

    Les modèles de greffon sont générés à part : leurs sous-modèles sont
    inlinés, pour que le schéma OpenAPI final ne référence aucun composant
    qu'il ne déclare pas. Le contrat reste fermé (`additionalProperties`
    retiré) : les champs de greffon y sont nommés, rien d'autre n'est promis.
    """
    schema.pop("additionalProperties", None)
    properties = schema.setdefault("properties", {})
    for model in models:
        generated = model.model_json_schema(mode="serialization")
        definitions = generated.get("$defs", {})
        for name, prop in generated.get("properties", {}).items():
            properties[name] = _inline_refs(prop, definitions)


def document_knowledge_base_fields(schema: dict[str, Any]) -> None:
    """`json_schema_extra` de `KnowledgeBaseRead` : champs de greffon dans l'OpenAPI."""
    _document_extra_fields(schema, knowledge_base_field_models())


def document_principal_fields(schema: dict[str, Any]) -> None:
    """`json_schema_extra` de `PrincipalRead` : champs de greffon dans l'OpenAPI."""
    _document_extra_fields(schema, [provider.model for provider in principal_field_providers()])
