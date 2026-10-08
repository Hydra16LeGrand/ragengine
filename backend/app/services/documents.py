"""Cas d'usage des documents : enregistrement, lecture, suppression, precheck.

**L'ingestion possède réellement le fichier** (ADR-014). Le router reçoit un
`multipart/form-data`, ce module en calcule les métadonnées — `content_type`,
`size_bytes` et surtout `checksum_sha256` — **à partir des octets reçus**, jamais
d'une valeur déclarée par le client, puis confie le contenu à `StoragePort`. Le
`storage_uri` renvoyé par le port est la seule adresse que connaîtront ensuite le
precheck et le chunking : c'est ce qui permet à un job repris après un crash de
retrouver ses octets sans dépendre du process qui a reçu l'upload.

L'idempotence par empreinte encadre le tout : elle est évaluée **avant** toute
écriture sur disque et avant toute consommation de quota, si bien qu'un
téléversement rejoué ne coûte ni fichier, ni job, ni quota.

`delete` est le **miroir exact** de cette possession : un document versé laisse
une trace à trois endroits (dataset RAGFlow, ligne PostgreSQL, octets sur le
volume) et la suppression les défait tous les trois, dans un ordre choisi pour
qu'aucune panne intermédiaire ne rende un résidu inatteignable.

`Document.status` n'est jamais écrit ici : son écrivain unique est
`app.services.jobs` (`docs/domain-model.md` §2).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import (
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from app.db.models import Document, KnowledgeBase
from app.domain.enums import DocumentStatus, MemberRole
from app.domain.pagination import DEFAULT_PAGE_SIZE, Cursor, apply_cursor_pagination
from app.domain.schemas.document import (
    DocumentChunkRead,
    DocumentChunksMeta,
    DocumentChunksPage,
    DocumentPrecheckDecisionUpdate,
    DocumentUpdate,
)
from app.providers.engine import IngestionEnginePort, resolve_ingestion_engine
from app.providers.engine.base import (
    EngineChunk,
    EngineDatasetRef,
    EngineDocumentRef,
    EngineError,
    EngineTransientError,
)
from app.providers.storage import StoragePort, resolve_storage
from app.services.file_signatures import ensure_content_matches_signature
from app.services.identity.permissions import KnowledgeBaseAccess, PermissionService
from app.services.identity.principal import Principal
from app.services.jobs import IngestionJobService
from app.services.knowledge_bases import get_knowledge_base
from app.services.resource_quotas import resolve_resource_quotas
from app.workers.celery_app import CHUNKING_TASK, PRECHECK_TASK, task_by_name

_logger = logging.getLogger(__name__)

_UNKNOWN_DOCUMENT: Final = "Document introuvable."
_DOCUMENT_NOT_INDEXED: Final = (
    "Ce document n'a pas encore de fragments : l'indexation n'a pas atteint le moteur."
)

FALLBACK_FILENAME: Final = "document"
"""Nom retenu quand le client n'en fournit aucun — le contenu prime sur son étiquette."""

FALLBACK_CONTENT_TYPE: Final = "application/octet-stream"
"""Type de média retenu quand ni l'extension ni le client ne renseignent mieux."""

_PARENTS_ONLY_FETCH_SIZE: Final = 100
"""Taille de lot pour récupérer tout un document (`list_chunks`, `parents_only`) —
doit rester égale à `_CHUNKS_MAX_PAGE_SIZE` (`api/v1/routes/documents.py`), le
plafond que RAGFlow accepte réellement sur `page_size` ; deux constantes plutôt
qu'un import croisé route → service qui inverserait la dépendance établie."""


@dataclass(frozen=True, slots=True)
class UploadedDocument:
    """Octets reçus par la façade, et les deux seules indications du client.

    Tout le reste (`content_type` retenu, taille, empreinte, emplacement) est
    **calculé** par le service : le nom et le type annoncés ne sont que des
    indices de format, jamais des métadonnées de confiance.
    """

    content: bytes
    filename: str | None
    declared_content_type: str | None
    theme: str | None = None
    """Étiquette de thème saisie par l'utilisateur, facultative — jamais déduite."""


@dataclass(frozen=True, slots=True)
class RegisteredDocument:
    """Document accepté à l'ingestion, et s'il vient d'être créé.

    `created` vaut `False` quand l'empreinte était déjà connue de la base : la
    requête est un rejeu, la réponse reste `202` et porte le document d'origine.
    """

    document: Document
    created: bool


def resolve_filename(filename: str | None) -> str:
    """Nom retenu pour le document, jamais vide.

    Seul le **nom de base** est conservé : un client qui enverrait
    `../../etc/passwd` ne doit pas pouvoir influencer un chemin, même si le
    stockage adresse par empreinte et ne se sert du nom que pour son extension.
    """
    candidate = (filename or "").strip().replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    return candidate or FALLBACK_FILENAME


def resolve_content_type(filename: str, declared: str | None) -> str:
    """Type de média retenu : l'extension d'abord, la déclaration du client ensuite.

    L'extension prime pour la même raison que dans le precheck : un navigateur
    envoie régulièrement `application/octet-stream` pour un fichier parfaitement
    identifiable par son nom, alors que l'inverse est bien plus rare.

    Toujours renvoyé en minuscules (ADR-060, défaut trouvé en chemin) :
    `mimetypes.guess_type(".xlsm")` renvoie `macroEnabled` en casse mixte sur
    certaines tables système, alors que `Settings.upload_allowed_content_types`
    est déclarée en minuscules — un `.xlsm` légitime pouvait donc être rejeté
    par la liste blanche avant tout autre contrôle. Normaliser ici, au point
    unique par lequel transitent la liste blanche, la vérification d'octets
    magiques et `Document.content_type`, évite de dupliquer ce `.lower()` à
    chaque point de comparaison.
    """
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed.lower()
    candidate = (declared or "").split(";", maxsplit=1)[0].strip().lower()
    if candidate and candidate != FALLBACK_CONTENT_TYPE:
        return candidate
    return FALLBACK_CONTENT_TYPE


def ensure_upload_within_limits(settings: Settings, *, size_bytes: int, content_type: str) -> None:
    """Rejette un téléversement hors des bornes de sécurité — taille et type de média.

    S'applique à **tout** principal : plafond par fichier et liste blanche,
    distincts d'un éventuel quota *cumulé* propre au compte
    (`ResourceQuotaPolicy.ensure_can_ingest_document`, qui reste évalué en plus,
    pas à la place). Appelée avant l'empreinte SHA-256 et avant toute écriture
    sur disque, pour ne payer ni le calcul ni le stockage d'un fichier refusé.

    Raises:
        PayloadTooLargeError: `size_bytes` dépasse `RAGENGINE_UPLOAD_MAX_BYTES` (`413`).
        UnsupportedMediaTypeError: `content_type` hors de
            `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES` (`415`).
    """
    if size_bytes > settings.upload_max_bytes:
        raise _upload_too_large(settings, size_bytes=size_bytes)
    if content_type not in settings.upload_allowed_content_types:
        raise UnsupportedMediaTypeError(
            f"Le type de média « {content_type} » n'est pas accepté à l'ingestion.",
            extensions={
                "content_type": content_type,
                "allowed_content_types": list(settings.upload_allowed_content_types),
            },
        )


def _upload_too_large(settings: Settings, *, size_bytes: int | None) -> PayloadTooLargeError:
    """Le `413` d'upload, unique pour les deux chemins qui peuvent le lever.

    `size_bytes` vaut `None` quand la lecture a été interrompue au dépassement
    sans que la taille totale soit connue : on n'annonce alors que la limite,
    plutôt qu'un nombre d'octets lus qui passerait pour la taille du fichier.
    """
    limit = settings.upload_max_bytes
    if size_bytes is None:
        return PayloadTooLargeError(
            f"Le fichier dépasse la taille maximale autorisée ({limit} octets).",
            extensions={"limit_bytes": limit},
        )
    return PayloadTooLargeError(
        f"Le fichier ({size_bytes} octets) dépasse la taille maximale autorisée ({limit} octets).",
        extensions={"size_bytes": size_bytes, "limit_bytes": limit},
    )


UPLOAD_READ_CHUNK_BYTES: Final = 1024 * 1024
"""Taille des morceaux lus par `read_upload_content` — celle du tampon mémoire
de Starlette (`MultiPartParser.spool_max_size`), au-delà duquel la partie
fichier est déjà sur disque."""


class UploadStream(Protocol):
    """Ce que `read_upload_content` attend d'un fichier téléversé.

    `fastapi.UploadFile` en a exactement la forme ; le service n'importe donc
    rien du framework HTTP, et un test peut lui passer n'importe quel flux.
    """

    async def read(self, size: int = -1) -> bytes:
        """Lit au plus `size` octets ; `b""` en fin de flux."""
        ...


async def read_upload_content(
    stream: UploadStream, settings: Settings, *, declared_size: int | None = None
) -> bytes:
    """Lit un fichier téléversé par morceaux, en s'arrêtant **dès** le plafond franchi.

    Remplace un `await file.read()` d'un seul tenant (audit sécurité 2026-09,
    constat 3), qui chargeait tout le fichier en mémoire **avant** que
    `ensure_upload_within_limits` ne le mesure : un envoi de plusieurs Gio
    faisait grossir le process jusqu'à l'OOM. Ici la mémoire tenue ne dépasse
    jamais `RAGENGINE_UPLOAD_MAX_BYTES` plus un morceau.

    Ce que la fonction **ne borne pas**, et ne peut pas borner : Starlette
    analyse le corps `multipart/form-data` avant d'appeler l'endpoint, et met la
    partie fichier dans un `SpooledTemporaryFile` (disque au-delà de 1 Mio). Le
    disque et la bande passante d'un envoi démesuré sont donc déjà consommés
    quand on arrive ici — c'est le plafond de corps du reverse proxy qui les
    protège, pas cette lecture.

    `declared_size` (la taille mesurée par Starlette en écrivant la partie,
    `UploadFile.size`) permet de refuser sans lire un octet. Elle ne sert
    qu'à refuser, jamais à accepter : la lecture reste bornée même si elle
    était fausse ou absente.

    Le contenu est rendu en `bytes`, borné par le plafond : `StoragePort.save`
    prend des octets (ADR-014), et le precheck comme l'envoi au moteur relisent
    de toute façon le fichier entier.

    Raises:
        PayloadTooLargeError: `RAGENGINE_UPLOAD_MAX_BYTES` dépassé (`413`), même
            erreur que `ensure_upload_within_limits`.
    """
    limit = settings.upload_max_bytes
    if declared_size is not None and declared_size > limit:
        raise _upload_too_large(settings, size_bytes=declared_size)

    chunks: list[bytes] = []
    received = 0
    while chunk := await stream.read(UPLOAD_READ_CHUNK_BYTES):
        received += len(chunk)
        if received > limit:
            raise _upload_too_large(settings, size_bytes=None)
        chunks.append(chunk)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


@dataclass(frozen=True, slots=True)
class DocumentAccess:
    """Document résolu depuis `/documents/{id}` et l'accès à la base qui le porte."""

    document: Document
    access: KnowledgeBaseAccess

    @property
    def knowledge_base(self) -> KnowledgeBase:
        """Base de connaissances propriétaire du document, déjà chargée."""
        return self.access.knowledge_base


class DocumentService:
    """Cas d'usage portant sur la table `documents`."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        *,
        storage: StoragePort | None = None,
        engine: IngestionEnginePort | None = None,
    ) -> None:
        """Construit le service.

        `engine` n'est **pas** résolu ici, contrairement à `storage` : seule la
        suppression s'en sert, alors que `resolve_ingestion_engine` refuse de
        construire l'adaptateur quand `RAGFLOW_API_KEY` est absente (erreur de
        déploiement signalée au plus tôt, `providers/engine/__init__.py`). Le
        résoudre dans le constructeur ferait échouer *toutes* les routes de
        documents — lister, lire, téléverser — sur un déploiement qui n'a pas
        encore été appairé au moteur. Il est donc résolu à la demande, et
        l'argument reste là pour l'injection d'un double de test.
        """
        self._db = db
        self._settings = settings if settings is not None else get_settings()
        self._quotas = resolve_resource_quotas(db, self._settings)
        self._jobs = IngestionJobService(db)
        self._storage = storage if storage is not None else resolve_storage(self._settings)
        self._engine = engine

    async def register(
        self, access: KnowledgeBaseAccess, upload: UploadedDocument
    ) -> RegisteredDocument:
        """Enregistre un document téléversé et ouvre son job — `POST …/documents/ingest`.

        Les métadonnées sont **déduites des octets reçus** (ADR-014) : la taille
        et l'empreinte sont mesurées ici, le type de média est arbitré à partir
        du nom, et le `storage_uri` vient du port de stockage. Aucune valeur
        n'est reprise du client — une empreinte déclarée ruinerait l'idempotence
        qu'elle est censée porter.

        **Idempotence par empreinte.** Un fichier déjà présent dans la base sous
        le même `checksum_sha256` (index
        `ix_documents_knowledge_base_id_checksum_sha256`) ne produit ni nouvelle
        ligne, ni nouveau job, ni écriture sur disque, ni consommation de quota :
        le document existant est renvoyé tel quel, et la réponse reste `202`. Ce
        choix plutôt qu'un `409` : un téléversement rejoué après une coupure
        réseau est le cas normal, pas une erreur, et le client n'a alors rien à
        réparer — il retrouve le même `id` et suit le même job. Un `409`
        obligerait chaque client à écrire une branche de récupération pour un cas
        où il a déjà ce qu'il demandait.

        Raises:
            AuthenticationRequiredError: principal anonyme.
            PayloadTooLargeError: `RAGENGINE_UPLOAD_MAX_BYTES` dépassé (`413`).
            UnsupportedMediaTypeError: type de média hors liste blanche (`415`), ou
                contenu incohérent avec le type retenu (`415`, audit sécurité
                2026-09 constat 9 — un fichier renommé ne passe plus sur la
                seule foi de son extension).
            RAGEngineError: refus de la politique de plafonds de création
                (`app.services.resource_quotas`), s'il en est une.
            StorageError: le contenu n'a pas pu être persisté.
        """
        user = access.principal.require_user()
        knowledge_base = access.knowledge_base

        filename = resolve_filename(upload.filename)
        content_type = resolve_content_type(filename, upload.declared_content_type)
        size_bytes = len(upload.content)

        # Validation de sécurité (§9) **avant** l'empreinte SHA-256 : un rejet
        # ne doit payer ni le hachage du fichier entier, ni la moindre écriture.
        ensure_upload_within_limits(
            self._settings, size_bytes=size_bytes, content_type=content_type
        )
        # Le contenu réel, pas seulement l'extension/le type déclaré (constat 9) :
        # même emplacement, même raison de passer avant l'empreinte.
        ensure_content_matches_signature(upload.content, content_type)

        checksum = hashlib.sha256(upload.content).hexdigest()

        existing = await self.find_by_checksum(knowledge_base, checksum)
        if existing is not None:
            # Le rejeu republie quand même : c'est ce qui rattrape un dispatch
            # perdu (broker momentanément indisponible), sans quoi le document
            # resterait `queued` pour toujours. La garde d'idempotence de
            # `run_precheck` rend la republication inoffensive sur un job déjà parti.
            await self._publish_after_commit(PRECHECK_TASK, existing.current_job_id)
            return RegisteredDocument(document=existing, created=False)

        # Le quota est arbitré **avant** l'écriture : un invité au plafond ne
        # doit pas laisser un fichier orphelin sur le volume.
        await self._quotas.ensure_can_ingest_document(user, size_bytes=size_bytes)

        storage_uri = await self._storage.save(
            upload.content, checksum_sha256=checksum, filename=filename
        )

        document = Document(
            knowledge_base_id=knowledge_base.id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum,
            storage_uri=storage_uri,
            theme=upload.theme,
        )
        self._db.add(document)
        await self._db.flush([document])

        # Le job est ouvert par son service dédié : c'est lui qui écrit
        # `Document.status` et le journal, jamais ce module.
        await self._jobs.open_for(document)

        # Compteur dénormalisé maintenu par le service d'ingestion (§2).
        await self._adjust_document_count(knowledge_base, 1)
        await self._db.flush()
        await self._db.refresh(document)
        await self._publish_after_commit(PRECHECK_TASK, document.current_job_id)
        return RegisteredDocument(document=document, created=True)

    async def find_by_checksum(
        self, knowledge_base: KnowledgeBase, checksum_sha256: str
    ) -> Document | None:
        """Cherche un document par empreinte dans une base — support de l'idempotence."""
        statement = select(Document).where(
            Document.knowledge_base_id == knowledge_base.id,
            Document.checksum_sha256 == checksum_sha256,
        )
        return (await self._db.execute(statement)).scalars().first()

    async def list_for_knowledge_base(
        self,
        knowledge_base: KnowledgeBase,
        *,
        cursor: str | Cursor | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        status: DocumentStatus | None = None,
        theme: str | None = None,
    ) -> Sequence[Document]:
        """Liste les documents d'une base — `GET …/knowledge-bases/{kb_id}/documents`.

        `status` étant une projection dénormalisée, la liste se rend sans aucune
        jointure sur `ingestion_jobs` (§2).

        `status`/`theme` filtrent en base (correspondance exacte) — audit
        dette technique 2026-09, motif A : un client qui a besoin de « tous les
        documents indexés » ou « tous les documents d'un thème » ne doit pas
        reconstituer ce sous-ensemble en tronquant une page côté client, ce qui
        rend invisible tout élément au-delà de la première page.
        """
        statement = select(Document).where(Document.knowledge_base_id == knowledge_base.id)
        if status is not None:
            statement = statement.where(Document.status == status)
        if theme is not None:
            statement = statement.where(Document.theme == theme)
        statement = apply_cursor_pagination(statement, Document, cursor=cursor, limit=limit)
        return (await self._db.execute(statement)).scalars().all()

    async def resolve_access(
        self, principal: Principal, document_id: uuid.UUID, *, minimum: MemberRole
    ) -> DocumentAccess:
        """Résout `/documents/{document_id}` : le document, puis la matrice §4.

        Le chemin ne porte pas `{kb_id}`, donc `require_kb_permission` ne peut pas
        s'y monter : on remonte à la base par la clé étrangère et on délègue la
        décision à `PermissionService.authorize`. Une base invisible produit un
        `404`, qui masque du même coup l'existence du document.

        Raises:
            NotFoundError: document inconnu, ou base invisible pour ce principal.
            AuthenticationRequiredError | ForbiddenError: droits insuffisants.
        """
        document = await self._db.get(Document, document_id)
        if document is None:
            raise NotFoundError(_UNKNOWN_DOCUMENT)
        knowledge_base = await get_knowledge_base(self._db, document.knowledge_base_id)
        access = await PermissionService(self._db).authorize(
            principal, knowledge_base, minimum=minimum
        )
        return DocumentAccess(document=document, access=access)

    async def record_precheck_decision(
        self, document: Document, payload: DocumentPrecheckDecisionUpdate
    ) -> Document:
        """Enregistre l'aiguillage human-in-the-loop et relance le pipeline.

        La décision est écrite en JSONB, commitée, **puis seulement** convertie en
        reprise du traitement (`_publish_after_commit`). Aucune garde d'état
        n'est réécrite ici : `run_chunking` exige elle-même `awaiting_review` en
        entrée et no-ope sinon (`app.workers.tasks.ingestion`, garde
        d'idempotence). La dupliquer créerait une seconde source de vérité pour
        la même règle, et une fenêtre de course entre les deux.
        """
        document.precheck_decision = payload.decision.model_dump(mode="json")
        await self._db.flush()
        await self._db.refresh(document)
        await self._publish_after_commit(CHUNKING_TASK, document.current_job_id)
        return document

    async def update(self, document: Document, payload: DocumentUpdate) -> Document:
        """Applique une mise à jour partielle — `PATCH /api/v1/documents/{document_id}`.

        Contrairement à `theme`, `filename` ne peut pas être vidé : un document
        garde toujours un nom d'affichage, `null` explicite y est donc refusé
        (même garde que `KnowledgeBaseService.update` pour ses propres champs
        non clairables).

        `filename` retraverse `resolve_filename` (audit sécurité 2026-09,
        constat 13) : sans quoi ce chemin contournait le seul point qui réduit
        un nom au nom de base (`register`, à l'admission), laissant un
        `../../etc/passwd` survivre en base puis partir vers RAGFlow
        (`app.workers.tasks.ingestion`, `filename=document.filename`) sur la
        seule foi d'un `PATCH`. Même fonction, même garantie, sur les deux
        chemins d'écriture du champ.

        Raises:
            ValidationError: `filename` est explicitement mis à `null`.
        """
        data = payload.model_dump(exclude_unset=True)
        if "filename" in data and data["filename"] is None:
            raise ValidationError(
                "Le champ « filename » ne peut pas être vidé.", extensions={"field": "filename"}
            )
        if "filename" in data:
            data["filename"] = resolve_filename(data["filename"])
        for field, value in data.items():
            setattr(document, field, value)
        await self._db.flush()
        await self._db.refresh(document)
        return document

    async def _adjust_document_count(self, knowledge_base: KnowledgeBase, delta: int) -> None:
        """Incrémente/décrémente `document_count` par un `UPDATE` atomique, pas un `+=` Python.

        `knowledge_base.document_count += 1` (et son miroir `- 1` côté
        suppression) lit la valeur en mémoire puis réécrit la ligne entière :
        sous des ingestions concurrentes sur la même base, chaque requête HTTP a
        sa propre session/transaction, chacune lit le même compteur de départ,
        et la dernière à commiter écrase silencieusement les autres — une perte
        de mise à jour classique, jamais visible sur un seul upload à la fois.
        Trouvé en campagne E2E (9 documents envoyés en parallèle dans une base
        neuve : `document_count` valait 1 à la fin, pas 9).

        `UPDATE ... SET document_count = document_count + :delta` est évalué
        **côté serveur**, sur la ligne verrouillée par Postgres le temps de
        l'écriture : deux transactions concurrentes sur la même base se
        sérialisent naturellement à ce niveau, sans verrou applicatif
        supplémentaire — contrairement à la résolution du dataset RAGFlow
        (`app.workers.tasks.ingestion._chunk_document`), il n'y a ici aucune
        décision à prendre à partir de la valeur lue, seulement une
        incrémentation, exactement le cas que `SET x = x + n` couvre nativement.
        `func.greatest(..., 0)` reproduit le clampage `max(count - 1, 0)`
        d'origine — la `CheckConstraint` `document_count >= 0` refuserait sinon
        la ligne sur une désynchronisation préexistante.

        `refresh` remet l'objet en mémoire en phase avec la ligne : sans lui,
        toute lecture ultérieure de `knowledge_base.document_count` dans la même
        session verrait encore l'ancienne valeur (l'`UPDATE` ci-dessus ne
        touche pas l'état de l'objet ORM, seulement la ligne).
        """
        await self._db.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base.id)
            .values(document_count=func.greatest(KnowledgeBase.document_count + delta, 0))
        )
        await self._db.refresh(knowledge_base)

    async def _publish_after_commit(self, task_name: str, job_id: uuid.UUID | None) -> None:
        """Commit la transaction courante, **puis** publie la tâche qui va la relire.

        L'ordre est un invariant, pas une préférence. Une tâche publiée avant le
        `COMMIT` est une tâche qu'un worker décroche en quelques millisecondes,
        dans son propre process et sur sa propre connexion : sous READ COMMITTED
        il y lit l'état **précédent**. Le job n'existe pas encore, ou
        `precheck_decision` vaut toujours `NULL` — et rien ne le signale. Pas
        d'exception, pas de journal : simplement un document découpé avec le
        profil par défaut, la décision de l'utilisateur perdue en silence.

        **Ni `BackgroundTasks`, ni la fermeture des dépendances `yield` ne
        donnent cette garantie** (contrairement à ce qu'on pourrait croire) :
        FastAPI exécute les tâches de fond *à l'intérieur* de la pile de sortie
        de la requête — `await response(scope, receive, send)`, qui les déclenche,
        précède la fermeture de `request_stack`, où vit le `commit` de
        `get_db_session`. Vérifié sur la version installée, pas déduit d'un
        journal des modifications. D'où ce commit explicite, au seul endroit qui
        connaisse à la fois la mutation et la tâche qui en dépend.

        `expire_on_commit=False` sur la fabrique de sessions : les objets restent
        lisibles après ce commit, la réponse se sérialise sans relecture. Le
        commit final de `get_db_session` devient un no-op inoffensif.
        """
        if job_id is None:  # pragma: no cover - un document porte toujours un job courant
            return
        await self._db.commit()
        task_by_name(task_name).delay(str(job_id))
        _logger.info(
            "Étape d'ingestion publiée",
            extra={"job_id": str(job_id), "celery_task": task_name},
        )

    async def read_content(self, resolved: DocumentAccess) -> bytes:
        """Relit les octets d'origine d'un document — `GET …/documents/{id}/content`.

        Passe par `StoragePort` comme tout le reste : le routeur ne connaît ni
        chemin, ni système de fichiers (ADR-014). Aucune vérification d'état
        n'est faite ici — le fichier d'origine existe dès l'enregistrement, bien
        avant que l'indexation n'aboutisse, et c'est justement quand un job a
        échoué qu'on veut pouvoir rouvrir la pièce versée.

        Raises:
            StorageError: contenu introuvable ou illisible (`500`) — le cas d'un
                fichier disparu du volume alors que sa ligne existe encore.
        """
        return await self._storage.read(resolved.document.storage_uri)

    async def list_chunks(
        self, resolved: DocumentAccess, *, page: int, page_size: int, parents_only: bool = False
    ) -> DocumentChunksPage:
        """Liste les fragments indexés d'un document — `GET …/documents/{id}/chunks`.

        Contrairement à `read_content`, cette lecture **suppose l'indexation
        acquise** : sans les deux identifiants externes (dataset, document),
        il n'y a aucun fragment à lire côté moteur — un document encore en
        precheck, en cours de chunking, ou dont le job a échoué avant
        d'atteindre RAGFlow. `NotFoundError` plutôt qu'une erreur moteur
        confuse (`EngineError` générique sur un identifiant vide n'aurait pas
        de sens) : l'absence de fragments est une propriété du document, pas
        un accroc du moteur.

        `parents_only` (ADR-033) filtre aux fragments `available=False` d'un
        découpage parent/enfant. RAGFlow ne sait pas filtrer côté serveur —
        seuls `page`/`page_size` existent sur son endpoint — donc ce mode
        récupère **tout** le document par lots de `_PARENTS_ONLY_FETCH_SIZE`
        (son propre plafond), filtre, puis repagine ici. Coût assumé : un
        aller-retour occasionnel de consultation, pas un chemin chaud. Sans
        ça, une pagination calculée sur l'ensemble complet (parents + enfants)
        pouvait annoncer une page inexistante ou en rendre une vide dès que
        les enfants d'une tranche de `page_size` masquaient tous ses parents
        — constaté en conditions réelles, 2026-09-07.

        Raises:
            NotFoundError: document jamais parvenu à l'indexation.
            EngineTransientError: moteur momentanément indisponible.
        """
        knowledge_base = resolved.knowledge_base
        document = resolved.document
        dataset_external_id = knowledge_base.ragflow_dataset_external_id
        document_external_id = document.ragflow_document_external_id
        if dataset_external_id is None or document_external_id is None:
            raise NotFoundError(_DOCUMENT_NOT_INDEXED)

        engine = self._ingestion_engine()
        dataset_ref = EngineDatasetRef(external_id=dataset_external_id)
        document_ref = EngineDocumentRef(external_id=document_external_id)
        uses_parent_child = _uses_parent_child(document.precheck_decision)

        if not parents_only:
            engine_page = await engine.list_document_chunks(
                dataset_ref, document_ref, page=page, page_size=page_size
            )
            return DocumentChunksPage(
                data=[_chunk_read_of(chunk) for chunk in engine_page.chunks],
                document=DocumentChunksMeta(
                    chunk_method=engine_page.document.chunk_method,
                    chunk_count=engine_page.document.chunk_count,
                    token_count=engine_page.document.token_count,
                    uses_parent_child=uses_parent_child,
                ),
                total=engine_page.total,
                page=engine_page.page,
                page_size=engine_page.page_size,
            )

        all_chunks: list[EngineChunk] = []
        loop_page = 1
        while True:
            fetched = await engine.list_document_chunks(
                dataset_ref, document_ref, page=loop_page, page_size=_PARENTS_ONLY_FETCH_SIZE
            )
            # Réassigné à chaque itération plutôt que lu une seule fois avant
            # la boucle : `while True` garantit au moins un passage, donc
            # toujours affecté avant les deux sorties ci-dessous — mypy le
            # prouve sans avoir besoin d'un `Optional` ni d'un `assert`.
            meta = fetched.document
            if not fetched.chunks:
                break  # garde-fou : y compris si le tout premier appel est déjà vide
            all_chunks.extend(fetched.chunks)
            if len(all_chunks) >= fetched.total:
                break
            loop_page += 1

        parents = [chunk for chunk in all_chunks if not chunk.available]
        start = (page - 1) * page_size
        return DocumentChunksPage(
            data=[_chunk_read_of(chunk) for chunk in parents[start : start + page_size]],
            document=DocumentChunksMeta(
                chunk_method=meta.chunk_method,
                chunk_count=meta.chunk_count,
                token_count=meta.token_count,
                uses_parent_child=uses_parent_child,
            ),
            total=len(parents),
            page=page,
            page_size=page_size,
        )

    async def delete(self, resolved: DocumentAccess) -> None:
        """Supprime un document, **partout** — `DELETE /api/v1/documents/{document_id}`.

        Trois emplacements portent la trace d'un document versé, et les trois
        sont nettoyés : le dataset RAGFlow (ses fragments indexés), la ligne
        `documents` avec ses jobs en cascade (§0), et les octets d'origine sur
        le volume de stockage. N'en nettoyer qu'un laissait des orphelins qui
        s'accumulaient sans qu'aucune interface ne les montre.

        **L'ordre est un invariant, pas une préférence.**

        1. *Le moteur d'abord.* Son identifiant de document ne vit que dans la
           ligne qu'on s'apprête à effacer : échouer après le `COMMIT` rendrait
           l'orphelin RAGFlow définitivement inatteignable. Échouer avant ne
           coûte qu'une suppression à rejouer.
        2. *Le `COMMIT` ensuite, les octets après.* Effacer le fichier avant que
           la transaction ne soit acquise laisserait, si elle était annulée, une
           ligne `Document` dont les octets ont disparu — un document qui
           échouerait à chaque relecture. Dans l'autre sens, une panne entre les
           deux ne laisse qu'un fichier inutilisé, sans conséquence
           fonctionnelle : le stockage étant adressé par empreinte, un
           re-téléversement du même contenu réécrit simplement au même endroit.

        **Ce qu'une panne du moteur signifie.** Une erreur *permanente* (`102`,
        « introuvable ou interdit ») est avalée : c'est exactement ce que RAGFlow
        répond pour un document déjà retiré, et un document jamais parsé n'a de
        toute façon aucun équivalent chez lui. Une erreur *transitoire*, elle,
        est propagée (`503`) : le moteur est joignable un instant plus tard, et
        supprimer quand même créerait l'orphelin que cette méthode existe pour
        éviter.

        Le compteur dénormalisé de la base est décrémenté dans la même
        transaction, sans jamais passer sous zéro : la `CheckConstraint`
        `document_count >= 0` refuserait la ligne, et une liste vide affichant
        « -1 document » serait pire que le compteur légèrement optimiste que
        cette borne préserve.

        Raises:
            EngineTransientError: moteur momentanément indisponible — rien n'est
                supprimé, la requête est à rejouer.
            StorageError: le volume a refusé l'effacement des octets (la ligne,
                elle, est déjà commitée).
        """
        document = resolved.document
        knowledge_base = resolved.knowledge_base
        storage_uri = document.storage_uri
        # Évalué **avant** le DELETE : la ligne courante compte encore, et c'est
        # sur les *autres* qu'on s'interroge.
        content_is_shared = await self._content_is_shared(document)

        await self._forget_in_engine(knowledge_base, document)

        await self._adjust_document_count(knowledge_base, -1)
        # La référence circulaire `documents.current_job_id → ingestion_jobs` est
        # coupée avant le DELETE : sans cela l'ORM devrait charger `current_job`,
        # déclaré `lazy="raise_on_sql"`.
        document.current_job_id = None
        await self._db.flush()
        await self._db.delete(document)
        await self._db.flush()
        await self._db.commit()

        if not content_is_shared:
            await self._storage.delete(storage_uri)

    async def _content_is_shared(self, document: Document) -> bool:
        """`True` si une **autre** ligne pointe encore sur les mêmes octets.

        Le stockage est adressé par empreinte (ADR-014) tandis que l'idempotence
        de `register` est scopée à une base : le même fichier versé dans deux
        bases produit deux `Document` et **un seul** `storage_uri`. Effacer les
        octets à la première suppression casserait silencieusement le second
        document, qui ne s'en apercevrait qu'au prochain precheck.
        """
        statement = (
            select(func.count())
            .select_from(Document)
            .where(Document.storage_uri == document.storage_uri, Document.id != document.id)
        )
        return bool(await self._db.scalar(statement))

    async def _forget_in_engine(self, knowledge_base: KnowledgeBase, document: Document) -> None:
        """Retire le document du dataset RAGFlow, s'il y est jamais arrivé.

        Sans les deux identifiants externes, il n'y a rien à retirer : un
        document refusé au precheck, annulé, ou encore en file n'a jamais été
        téléversé au moteur (`app.workers.tasks.ingestion` ne les écrit qu'au
        moment du chunking).

        L'échec permanent est journalisé plutôt que propagé — y compris celui
        d'un déploiement sans `RAGFLOW_API_KEY`, où `resolve_ingestion_engine`
        refuse de construire l'adaptateur : dans tous ces cas, l'utilisateur ne
        peut rien réparer en rejouant, et lui refuser la suppression de sa propre
        ligne serait une impasse.
        """
        dataset_external_id = knowledge_base.ragflow_dataset_external_id
        document_external_id = document.ragflow_document_external_id
        if dataset_external_id is None or document_external_id is None:
            return
        try:
            engine = self._ingestion_engine()
            await engine.delete_document(
                EngineDatasetRef(external_id=dataset_external_id),
                EngineDocumentRef(external_id=document_external_id),
            )
        except EngineTransientError:
            raise
        except EngineError:
            _logger.warning(
                "Document introuvable ou refusé par le moteur à la suppression",
                exc_info=True,
                extra={
                    "document_id": str(document.id),
                    "engine_document_id": document_external_id,
                    "engine_dataset_id": dataset_external_id,
                },
            )

    def _ingestion_engine(self) -> IngestionEnginePort:
        """Résout le moteur à la demande, et une seule fois par service."""
        if self._engine is None:
            self._engine = resolve_ingestion_engine(self._settings)
        return self._engine


def _uses_parent_child(precheck_decision: dict[str, Any] | None) -> bool:
    """Relit `use_parent_child` dans la décision de precheck conservée en base.

    **Pourquoi la base plutôt que le moteur** : l'API de listage des fragments de
    RAGFlow (`GET /datasets/{id}/documents/{doc}/chunks`) n'expose ni `mom_id` ni
    aucun marqueur de filiation — vérifié champ par champ sur l'instance déployée
    (`id`, `content`, `document_id`, `dataset_id`, `docnm_kwd`, `positions`,
    `image_id`, `important_keywords`, `questions`, `available`, `tag_kwd`). Le
    seul signe visible d'un fragment parent y est `available=false`, qui ne se
    distingue pas d'un fragment désactivé à la main. La décision de l'utilisateur,
    elle, est sans ambiguïté et déjà persistée : c'est elle qui arbitre.

    Tolérant par construction : décision absente (client REST qui n'a rien
    envoyé), clé absente, ou valeur non booléenne héritée d'une version
    antérieure du schéma ⇒ `False`, le comportement d'avant la Phase 13. Une
    liste de fragments ne doit jamais échouer pour un défaut de métadonnée.
    """
    if not precheck_decision:
        return False
    return precheck_decision.get("use_parent_child") is True


def _chunk_read_of(chunk: EngineChunk) -> DocumentChunkRead:
    """Traduit un `EngineChunk` du port en fragment exposé par l'API.

    `page_range` fait le tri entre rectangles fiables et dégénérés — voir sa
    docstring (`app/providers/engine/base.py`) : rien à refaire ici.
    """
    page_range = chunk.page_range
    return DocumentChunkRead(
        chunk_id=chunk.chunk_id,
        content=chunk.content,
        page_start=None if page_range is None else page_range[0],
        page_end=None if page_range is None else page_range[1],
        important_keywords=chunk.important_keywords,
        questions=chunk.questions,
        available=chunk.available,
    )
