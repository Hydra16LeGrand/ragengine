"""Cas d'usage des bases de connaissances : cycle de vie et gestion des membres.

Ce service n'arbitre **jamais** un droit d'accès. La matrice §4 de
`docs/domain-model.md` a un point d'application unique — `require_kb_permission`
et `PermissionService` — et le refaire ici le ferait dériver. Les méthodes
supposent donc l'autorisation déjà acquise : elles exécutent.

Deux invariants sont en revanche portés ici, faute de pouvoir l'être par le
schéma :

- l'unicité `(owner_id, slug)` est arbitrée par **la base** ; la lecture
  préalable ne sert qu'à produire un message clair dans le cas courant, la
  violation de contrainte fait foi ;
- **une base garde toujours au moins un `owner`** (`docs/domain-model.md` §1).
  La règle porte sur un ensemble de lignes, pas sur une ligne : aucun `CHECK`
  simple ne l'exprime. Le retrait comme la rétrogradation du dernier `owner`
  sont donc refusés ici, et nulle part ailleurs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import KnowledgeBase, KnowledgeBaseMember, OrganizationMember, User
from app.domain.enums import MemberRole, Visibility
from app.domain.pagination import DEFAULT_PAGE_SIZE, Cursor, apply_cursor_pagination
from app.domain.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseMemberUpdate,
    KnowledgeBaseUpdate,
)
from app.domain.slug import slugify
from app.providers.embeddings.registry import resolve_embedding_provider
from app.providers.engine import IngestionEnginePort, resolve_ingestion_engine
from app.providers.engine.base import EngineDatasetRef, EngineError, EngineTransientError
from app.providers.rerankers.registry import resolve_reranker_provider
from app.services.identity.principal import Principal
from app.services.resource_quotas import resolve_resource_quotas

_logger = logging.getLogger(__name__)

SLUG_UNIQUE_CONSTRAINT: Final = "uq_knowledge_bases_owner_id_slug"
"""Contrainte d'unicité `(owner_id, slug)` — la contradiction remonte sous ce nom."""

_CLEARABLE_FIELDS: Final[frozenset[str]] = frozenset({"description", "reranker_model_id"})
"""Seuls champs qu'un `PATCH` peut remettre à `null` : les colonnes nullables."""

_UNKNOWN_KB: Final = "Base de connaissances introuvable."
_UNKNOWN_MEMBER: Final = "Cet utilisateur n'est pas membre de cette base."
_UNKNOWN_USER: Final = "Utilisateur introuvable."
_UNKNOWN_ORGANIZATION: Final = "Organisation introuvable, ou vous n'en êtes pas membre."
_ORGANIZATION_ID_REQUIRED: Final = (
    "Vous appartenez à plusieurs organisations (ou à aucune) : "
    "indiquez `organization_id` explicitement."
)
_SLUG_TAKEN: Final = "Vous possédez déjà une base de connaissances portant cet identifiant."
_UNDERIVABLE_SLUG: Final = (
    "Le nom ne permet de dériver aucun identifiant lisible : fournissez un `slug` explicite."
)
_LAST_OWNER: Final = (
    "Une base de connaissances doit conserver au moins un `owner` : "
    "promouvez un autre membre avant de retirer celui-ci."
)
_OWNER_IS_IMMUTABLE: Final = (
    "Le propriétaire de la base ne peut être ni rétrogradé ni retiré : "
    "`knowledge_bases.owner_id` est la source de vérité de la propriété."
)


@dataclass(frozen=True, slots=True)
class MemberEntry:
    """Une appartenance **et** le nom affiché du compte qui la porte.

    Le nom voyage à côté de la ligne plutôt que dessus : `knowledge_base_members`
    ne porte aucune colonne de ce genre, et la relation `KnowledgeBaseMember.user`
    est déclarée `lazy="raise_on_sql"` — la lire depuis la sérialisation ferait
    lever, ou pire, un N+1 silencieux si l'on relâchait ce garde-fou. Le service
    joint donc `users` une seule fois et rend le couple ; le routeur assemble.
    """

    member: KnowledgeBaseMember
    display_name: str


@dataclass(frozen=True, slots=True)
class MemberAssignment:
    """Résultat d'un `PUT …/members/{user_id}` : la ligne, et si elle vient de naître.

    `created` n'existe que pour le code de statut (`201` contre `200`) : la
    décision métier, elle, est identique dans les deux cas.
    """

    member: KnowledgeBaseMember
    display_name: str
    created: bool


class KnowledgeBaseService:
    """Cas d'usage portant sur `knowledge_bases` et `knowledge_base_members`."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        *,
        engine: IngestionEnginePort | None = None,
    ) -> None:
        """Construit le service.

        `engine` n'est **pas** résolu ici : `resolve_ingestion_engine` refuse de
        construire l'adaptateur quand `RAGFLOW_API_KEY` est absente (erreur de
        déploiement signalée au plus tôt, `providers/engine/__init__.py`), et
        seule la suppression s'en sert. Le résoudre dans le constructeur ferait
        échouer toutes les routes de bases sur un déploiement non appairé au
        moteur. Il est donc résolu à la demande, et l'argument reste là pour
        l'injection d'un double de test — même schéma que `DocumentService`.
        """
        self._db = db
        self._settings = settings if settings is not None else get_settings()
        self._quotas = resolve_resource_quotas(db, self._settings)
        self._engine = engine

    async def create(self, user: User, payload: KnowledgeBaseCreate) -> KnowledgeBase:
        """Crée une base et inscrit son créateur comme `owner`.

        L'appartenance explicite est écrite en plus de `owner_id` : elle rend la
        propriété lisible dans la même table que les autres rôles, ce dont
        dépendra tout futur transfert de propriété.

        Raises:
            RAGEngineError: refus de la politique de plafonds de création
                (`app.services.resource_quotas`), s'il en est une.
            ValidationError: le nom ne produit aucun slug exploitable, le modèle
                d'embedding/reranker demandé est inconnu ou indisponible, ou
                `organization_id` est omis alors que le compte appartient à
                zéro ou plusieurs organisations (ADR-022).
            NotFoundError: `organization_id` fourni mais le principal n'en est
                pas membre.
            ConflictError: ce propriétaire possède déjà ce slug.
        """
        await self._quotas.ensure_can_create_knowledge_base(user)
        self._ensure_providers_are_available(
            embedding_model_id=payload.embedding_model_id,
            reranker_model_id=payload.reranker_model_id,
        )
        organization_id = await self._resolve_organization(user, payload)

        slug = payload.slug or slugify(payload.name)
        if not slug:
            raise ValidationError(_UNDERIVABLE_SLUG, extensions={"name": payload.name})
        await self._ensure_slug_is_free(user.id, slug)

        knowledge_base = KnowledgeBase(
            slug=slug,
            name=payload.name,
            description=payload.description,
            owner_id=user.id,
            organization_id=organization_id,
            visibility=payload.visibility,
            embedding_model_id=payload.embedding_model_id,
            reranker_model_id=payload.reranker_model_id,
            provider_config=payload.provider_config.model_dump(mode="json"),
            observability_enabled=payload.observability_enabled,
            evaluation_enabled=payload.evaluation_enabled,
        )
        await self._flush_unique(knowledge_base)

        self._db.add(
            KnowledgeBaseMember(
                knowledge_base_id=knowledge_base.id, user_id=user.id, role=MemberRole.OWNER
            )
        )
        await self._db.flush()
        return knowledge_base

    async def list_visible(
        self,
        principal: Principal,
        *,
        cursor: str | Cursor | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Sequence[KnowledgeBase]:
        """Liste les bases que `principal` peut lire — `GET /api/v1/knowledge-bases`.

        Le périmètre est exactement celui de la lecture unitaire : une base
        figure dans la liste si et seulement si `GET /knowledge-bases/{id}` lui
        répondrait `200`. Tout autre découpage ferait diverger la liste de la
        matrice §4, et une base absente de la liste mais lisible à l'unité (ou
        l'inverse) serait un défaut d'interface impossible à expliquer.
        """
        statement = select(KnowledgeBase)
        visibility = self._visibility_filter(principal)
        if visibility is not None:
            statement = statement.where(visibility)
        return await self._paginate(statement, cursor=cursor, limit=limit)

    async def update(
        self, knowledge_base: KnowledgeBase, payload: KnowledgeBaseUpdate
    ) -> KnowledgeBase:
        """Applique une mise à jour partielle — `PATCH /api/v1/knowledge-bases/{kb_id}`.

        Le slug n'est **pas** re-dérivé quand le nom change : il fait partie de
        l'identité lisible de la base, et le muter au détour d'un renommage
        casserait tout lien déjà partagé. Le client qui le veut le demande.

        Raises:
            ValidationError: un champ non nullable est explicitement mis à `null`,
                ou le modèle de reranker demandé est inconnu ou indisponible.
            ConflictError: le slug demandé est déjà pris par ce propriétaire.
        """
        data: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if payload.provider_config is not None:
            data["provider_config"] = payload.provider_config.model_dump(mode="json")

        for field, value in data.items():
            if value is None and field not in _CLEARABLE_FIELDS:
                raise ValidationError(
                    f"Le champ « {field} » ne peut pas être vidé.", extensions={"field": field}
                )

        # Seul un champ explicitement présent dans le PATCH est validé :
        # `reranker_model_id: null` (désactivation) n'a rien à valider.
        # `embedding_model_id` n'existe pas sur `KnowledgeBaseUpdate` (docstring
        # du schéma) : rien à résoudre ici pour ce champ.
        self._ensure_providers_are_available(reranker_model_id=data.get("reranker_model_id"))

        slug = data.get("slug")
        if slug is not None and slug != knowledge_base.slug:
            await self._ensure_slug_is_free(
                knowledge_base.owner_id, slug, exclude=knowledge_base.id
            )

        for field, value in data.items():
            setattr(knowledge_base, field, value)
        await self._flush_unique(knowledge_base)
        return knowledge_base

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        """Supprime une base, **partout** — `DELETE /api/v1/knowledge-bases/{kb_id}`.

        Deux emplacements portent la trace d'une base versée, et les deux sont
        nettoyés : le dataset RAGFlow (ce que le moteur a indexé) et la ligne
        `knowledge_bases` dont les `ON DELETE CASCADE` emportent appartenances,
        documents, jobs et journal. N'en nettoyer qu'un laissait des datasets
        orphelins qui s'accumulaient sans qu'aucune interface ne les montre
        (ADR-039).

        **L'ordre est un invariant, pas une préférence.**

        1. *Le moteur d'abord.* Son identifiant de dataset ne vit que dans la
           ligne qu'on s'apprête à effacer : échouer après le `COMMIT`
           rendrait l'orphelin RAGFlow définitivement inatteignable. Échouer
           avant ne coûte qu'une suppression à rejouer — même raisonnement que
           `DocumentService.delete`.

        **Ce qu'une panne du moteur signifie.** Une erreur *permanente* (`102`,
        « introuvable ou interdit ») est avalée : c'est exactement ce que RAGFlow
        répond pour un dataset déjà retiré, et une base jamais versée n'a de
        toute façon aucun équivalent chez lui. Une erreur *transitoire*, elle,
        est propagée (`503`) : le moteur est joignable un instant plus tard, et
        supprimer quand même créerait l'orphelin que cette méthode existe pour
        éviter.

        Raises:
            EngineTransientError: moteur momentanément indisponible — rien n'est
                supprimé, la requête est à rejouer.
        """
        await self._forget_in_engine(knowledge_base)
        await self._db.delete(knowledge_base)
        await self._db.flush()

    async def _forget_in_engine(self, knowledge_base: KnowledgeBase) -> None:
        """Retire le dataset du moteur, s'il y a jamais été créé.

        Sans l'identifiant externe, il n'y a rien à retirer : une base créée
        mais jamais ingérée n'a pas encore de dataset RAGFlow (les identifiants
        externes ne sont écrits qu'au moment du chunking,
        `app.workers.tasks.ingestion`).

        L'échec permanent est journalisé plutôt que propagé — y compris celui
        d'un déploiement sans `RAGFLOW_API_KEY`, où `resolve_ingestion_engine`
        refuse de construire l'adaptateur : dans tous ces cas, l'utilisateur ne
        peut rien réparer en rejouant, et lui refuser la suppression de sa propre
        base serait une impasse.
        """
        dataset_external_id = knowledge_base.ragflow_dataset_external_id
        if dataset_external_id is None:
            return
        try:
            engine = self._ingestion_engine()
            await engine.delete_dataset(EngineDatasetRef(external_id=dataset_external_id))
        except EngineTransientError:
            raise
        except EngineError:
            _logger.warning(
                "Dataset introuvable ou refusé par le moteur à la suppression de base",
                exc_info=True,
                extra={
                    "knowledge_base_id": str(knowledge_base.id),
                    "engine_dataset_id": dataset_external_id,
                },
            )

    def _ingestion_engine(self) -> IngestionEnginePort:
        """Résout le moteur à la demande, et une seule fois par service."""
        if self._engine is None:
            self._engine = resolve_ingestion_engine(self._settings)
        return self._engine

    async def list_members(self, knowledge_base: KnowledgeBase) -> Sequence[MemberEntry]:
        """Liste tous les membres d'une base — `GET …/members`.

        Pas de pagination par curseur : l'appartenance à une base est une
        composition d'équipe humaine, bornée en pratique, contrairement aux
        bases ou aux documents. `created_at` ordonne par ancienneté, l'`owner`
        créateur (toujours le premier membre écrit, cf. `create`) en tête.

        La jointure sur `users` est **interne** : la clé étrangère porte un
        `ON DELETE CASCADE`, une appartenance orpheline n'existe donc pas — un
        `LEFT JOIN` ne couvrirait qu'un cas impossible, au prix d'un nom
        optionnel à porter dans tout le reste de la chaîne.
        """
        statement = (
            select(KnowledgeBaseMember, User.display_name)
            .join(User, User.id == KnowledgeBaseMember.user_id)
            .where(KnowledgeBaseMember.knowledge_base_id == knowledge_base.id)
            .order_by(KnowledgeBaseMember.created_at.asc())
        )
        rows = (await self._db.execute(statement)).tuples().all()
        return [MemberEntry(member=member, display_name=name) for member, name in rows]

    async def resolve_principal_roles(
        self, principal: Principal, knowledge_bases: Sequence[KnowledgeBase]
    ) -> dict[uuid.UUID, MemberRole | None]:
        """Rôle de `principal` sur chaque base d'une page — `GET /knowledge-bases`.

        Reproduit **exactement** la sémantique de `PermissionService.resolve_access`
        (`role=None` pour un accès par la seule visibilité `public`, un
        administrateur plateforme reçoit toujours au moins `owner`) sans jamais
        résoudre un rôle par ligne : un `SELECT ... WHERE user_id = :principal AND
        knowledge_base_id IN (:page)` unique couvre toute la page, quelle que
        soit sa taille — l'alternative (un `resolve_access` par item) ferait un
        N+1 sur l'endpoint le plus consulté du produit.
        """
        if not knowledge_bases:
            return {}
        if principal.user is None:
            return {knowledge_base.id: None for knowledge_base in knowledge_bases}

        statement = select(KnowledgeBaseMember.knowledge_base_id, KnowledgeBaseMember.role).where(
            KnowledgeBaseMember.user_id == principal.user.id,
            KnowledgeBaseMember.knowledge_base_id.in_(
                [knowledge_base.id for knowledge_base in knowledge_bases]
            ),
        )
        result = await self._db.execute(statement)
        member_roles: dict[uuid.UUID, MemberRole] = dict(result.tuples().all())

        roles: dict[uuid.UUID, MemberRole | None] = {}
        for knowledge_base in knowledge_bases:
            role = member_roles.get(knowledge_base.id)
            # Filet de sécurité, comme `PermissionService.member_role` : la
            # ligne d'appartenance de l'`owner` existe toujours en pratique
            # (`create` l'écrit), mais `owner_id` reste la source de vérité.
            if role is None and knowledge_base.owner_id == principal.user.id:
                role = MemberRole.OWNER
            if role is None and principal.is_admin:
                role = MemberRole.OWNER
            roles[knowledge_base.id] = role
        return roles

    async def get_member(self, knowledge_base: KnowledgeBase, user_id: uuid.UUID) -> MemberEntry:
        """Lit une appartenance — `GET …/members/{user_id}`.

        Même jointure que `list_members` : le frontend enchaîne la recherche
        unitaire et l'annuaire sur le même écran, les deux réponses doivent donc
        porter exactement les mêmes champs.

        Raises:
            NotFoundError: cet utilisateur n'a pas de rôle sur cette base.
        """
        statement = (
            select(KnowledgeBaseMember, User.display_name)
            .join(User, User.id == KnowledgeBaseMember.user_id)
            .where(
                KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
                KnowledgeBaseMember.user_id == user_id,
            )
        )
        row = (await self._db.execute(statement)).tuples().first()
        if row is None:
            raise NotFoundError(_UNKNOWN_MEMBER)
        member, display_name = row
        return MemberEntry(member=member, display_name=display_name)

    async def set_member(
        self,
        knowledge_base: KnowledgeBase,
        user_id: uuid.UUID,
        payload: KnowledgeBaseMemberUpdate,
    ) -> MemberAssignment:
        """Ajoute un membre ou change son rôle — `PUT …/members/{user_id}`.

        Raises:
            NotFoundError: le compte visé n'existe pas.
            ConflictError: rétrogradation du propriétaire, ou du dernier `owner`.
        """
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError(_UNKNOWN_USER)
        # Lu avant toute écriture : après le `flush`, une relecture d'attribut
        # expiré déclencherait une I/O implicite depuis un contexte synchrone.
        display_name = user.display_name
        if payload.role is not MemberRole.OWNER and user_id == knowledge_base.owner_id:
            raise ConflictError(_OWNER_IS_IMMUTABLE)

        member = await self._find_member(knowledge_base, user_id)
        created = member is None
        if member is None:
            member = KnowledgeBaseMember(
                knowledge_base_id=knowledge_base.id, user_id=user_id, role=payload.role
            )
            self._db.add(member)
        else:
            if member.role is MemberRole.OWNER and payload.role is not MemberRole.OWNER:
                await self._ensure_not_the_last_owner(knowledge_base)
            member.role = payload.role

        await self._db.flush()
        await self._db.refresh(member)
        return MemberAssignment(member=member, display_name=display_name, created=created)

    async def remove_member(self, knowledge_base: KnowledgeBase, user_id: uuid.UUID) -> None:
        """Retire un membre — `DELETE …/members/{user_id}`.

        Raises:
            NotFoundError: cet utilisateur n'a pas de rôle sur cette base.
            ConflictError: retrait du propriétaire, ou du dernier `owner`
                (`docs/domain-model.md` §1 — invariant applicatif).
        """
        if user_id == knowledge_base.owner_id:
            raise ConflictError(_OWNER_IS_IMMUTABLE)
        # `_find_member` plutôt que `get_member` : le retrait n'a que faire du
        # nom affiché, une jointure sur `users` serait payée pour rien.
        member = await self._find_member(knowledge_base, user_id)
        if member is None:
            raise NotFoundError(_UNKNOWN_MEMBER)
        if member.role is MemberRole.OWNER:
            await self._ensure_not_the_last_owner(knowledge_base)
        await self._db.delete(member)
        await self._db.flush()

    def _visibility_filter(self, principal: Principal) -> ColumnElement[bool] | None:
        """Filtre de visibilité du principal, ou `None` s'il voit tout (admin §4)."""
        if principal.is_admin:
            return None
        conditions: list[ColumnElement[bool]] = [KnowledgeBase.visibility == Visibility.PUBLIC]
        user = principal.user
        if user is not None:
            conditions.append(KnowledgeBase.owner_id == user.id)
            conditions.append(
                KnowledgeBase.id.in_(
                    select(KnowledgeBaseMember.knowledge_base_id).where(
                        KnowledgeBaseMember.user_id == user.id
                    )
                )
            )
        return or_(*conditions)

    async def _paginate(
        self,
        statement: Select[tuple[KnowledgeBase]],
        *,
        cursor: str | Cursor | None,
        limit: int,
    ) -> Sequence[KnowledgeBase]:
        """Applique le curseur et exécute — `limit + 1` lignes pour `build_page`."""
        paginated = apply_cursor_pagination(statement, KnowledgeBase, cursor=cursor, limit=limit)
        return (await self._db.execute(paginated)).scalars().all()

    async def _find_member(
        self, knowledge_base: KnowledgeBase, user_id: uuid.UUID
    ) -> KnowledgeBaseMember | None:
        """Charge une appartenance par sa clé primaire composite."""
        return await self._db.get(KnowledgeBaseMember, (knowledge_base.id, user_id))

    async def _ensure_not_the_last_owner(self, knowledge_base: KnowledgeBase) -> None:
        """Refuse de laisser une base sans `owner`.

        Raises:
            ConflictError: la base n'a plus qu'un seul `owner`.
        """
        owners = await self._db.scalar(
            select(func.count())
            .select_from(KnowledgeBaseMember)
            .where(
                KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
                KnowledgeBaseMember.role == MemberRole.OWNER,
            )
        )
        if (owners or 0) <= 1:
            raise ConflictError(_LAST_OWNER, extensions={"owners": owners or 0})

    def _ensure_providers_are_available(
        self, *, reranker_model_id: str | None, embedding_model_id: str | None = None
    ) -> None:
        """Vérifie l'existence et la disponibilité des modèles demandés (§8).

        Résout l'adaptateur via le registre — la construction est paresseuse
        (aucune connexion n'est ouverte, cf. `docs/providers-architecture.md`
        §8) — plutôt que de comparer les identifiants à une liste : c'est le
        registre qui fait foi, une seule fois, pas une copie locale de son
        catalogue. `reranker_model_id=None` n'est pas une erreur : c'est le
        passthrough (§2), rien à valider.

        `embedding_model_id` n'a de sens qu'à la création (`create()`) : RAGFlow
        fixe le modèle d'embedding du dataset une seule fois, à sa création
        (docstring de `KnowledgeBaseUpdate`) — `update()` ne le passe donc
        jamais, d'où le défaut `None`.

        Raises:
            ValidationError: modèle inconnu ou indisponible (clé API absente).
        """
        if embedding_model_id is not None:
            resolve_embedding_provider(embedding_model_id, self._settings)
        if reranker_model_id is not None:
            resolve_reranker_provider(reranker_model_id, self._settings)

    async def _resolve_organization(self, user: User, payload: KnowledgeBaseCreate) -> uuid.UUID:
        """Détermine l'organisation (tenant) propriétaire de la base à créer (ADR-022).

        Explicite (`payload.organization_id`) : le principal doit en être
        membre — même rationale de confidentialité qu'une base privée (§4),
        jamais l'occasion de confirmer l'existence d'une organisation à
        laquelle on n'appartient pas. Omis : l'organisation par défaut si le
        compte n'en a qu'une, sinon une erreur explicite plutôt qu'un choix
        arbitraire et silencieux — un compte fraîchement inscrit en a toujours
        exactement une (organisation personnelle, `OrganizationService.create_personal`).

        Raises:
            NotFoundError: `organization_id` fourni mais le principal n'en est
                pas membre.
            ValidationError: `organization_id` omis et le compte appartient à
                zéro ou plusieurs organisations.
        """
        if payload.organization_id is not None:
            is_member = await self._db.scalar(
                select(OrganizationMember.organization_id).where(
                    OrganizationMember.organization_id == payload.organization_id,
                    OrganizationMember.user_id == user.id,
                )
            )
            if is_member is None:
                raise NotFoundError(_UNKNOWN_ORGANIZATION)
            return payload.organization_id

        memberships = (
            (
                await self._db.execute(
                    select(OrganizationMember.organization_id).where(
                        OrganizationMember.user_id == user.id
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(memberships) == 1:
            return memberships[0]
        raise ValidationError(
            _ORGANIZATION_ID_REQUIRED, extensions={"organization_count": len(memberships)}
        )

    async def _ensure_slug_is_free(
        self, owner_id: uuid.UUID, slug: str, *, exclude: uuid.UUID | None = None
    ) -> None:
        """Vérifie l'unicité `(owner_id, slug)` avant écriture, pour un message clair.

        Raises:
            ConflictError: ce propriétaire possède déjà une base sous ce slug.
        """
        statement = select(KnowledgeBase.id).where(
            KnowledgeBase.owner_id == owner_id, KnowledgeBase.slug == slug
        )
        if exclude is not None:
            statement = statement.where(KnowledgeBase.id != exclude)
        if (await self._db.execute(statement)).first() is not None:
            raise ConflictError(_SLUG_TAKEN, extensions={"slug": slug})

    async def _flush_unique(self, knowledge_base: KnowledgeBase) -> None:
        """Écrit la ligne en traduisant la violation d'unicité en `ConflictError`.

        Le `begin_nested()` isole l'écriture dans un point de sauvegarde : après
        l'échec, la session reste utilisable et le routeur peut répondre
        proprement au lieu d'être condamné au rollback complet.

        Le `refresh` final relit les colonnes écrites par PostgreSQL
        (`created_at`, `updated_at`, `document_count`) : elles sont expirées côté
        ORM après l'écriture, et leur relecture par Pydantic déclencherait sinon
        un chargement paresseux depuis un contexte synchrone.
        """
        try:
            async with self._db.begin_nested():
                self._db.add(knowledge_base)
                await self._db.flush([knowledge_base])
        except IntegrityError as exc:
            if SLUG_UNIQUE_CONSTRAINT in str(exc.orig):
                raise ConflictError(_SLUG_TAKEN, extensions={"slug": knowledge_base.slug}) from exc
            raise
        await self._db.refresh(knowledge_base)


async def get_knowledge_base(db: AsyncSession, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
    """Charge une base par identifiant, sans arbitrer aucun droit.

    Réservé aux chemins qui ne portent pas `{kb_id}` (`/documents/{id}`,
    `/jobs/{id}`) : la base ainsi chargée est immédiatement soumise à
    `PermissionService.authorize`, qui décide seul de sa visibilité.

    Raises:
        NotFoundError: aucune base sous cet identifiant.
    """
    knowledge_base = await db.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:  # pragma: no cover - la clé étrangère l'interdit
        raise NotFoundError(_UNKNOWN_KB)
    return knowledge_base
