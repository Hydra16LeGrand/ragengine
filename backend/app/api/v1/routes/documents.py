"""Documents : listing d'une base, ingestion, lecture, restitution, suppression, precheck.

Deux familles de chemins cohabitent ici, et elles ne se protègent pas de la même
façon :

- ceux qui portent `{kb_id}` montent `require_kb_permission`, comme partout ;
- ceux qui n'ont que `{document_id}` ne peuvent pas la monter. La résolution
  « document → base → matrice §4 » est alors faite par `DocumentService`, qui
  appelle `PermissionService.authorize` — jamais un raisonnement d'accès
  réécrit dans ce module.

L'ingestion reçoit un **vrai fichier** en `multipart/form-data` (ADR-014) : le
routeur se contente de lire les octets du corps et de les confier au service, qui
en déduit toutes les métadonnées. Le contrat de réponse — `202` et un job à
suivre — n'a pas changé depuis la Phase 1.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Annotated, Final
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from pydantic import Field

from app.api.v1.dependencies import (
    CurrentPrincipal,
    SettingsDep,
    StorageDep,
    require_kb_permission,
    require_scope,
)
from app.db.session import DbSession
from app.domain.enums import DocumentStatus, MemberRole
from app.domain.pagination import Page, PaginationParams, build_page
from app.domain.schemas.common import MAX_FILENAME_LENGTH, MAX_THEME_LENGTH
from app.domain.schemas.document import (
    DocumentChunksPage,
    DocumentPrecheckDecisionUpdate,
    DocumentRead,
    DocumentUpdate,
)
from app.domain.scopes import ApiKeyScope
from app.services.documents import DocumentService, UploadedDocument, read_upload_content
from app.services.identity.permissions import KnowledgeBaseAccess

router = APIRouter(
    tags=["documents"],
    responses={
        int(HTTPStatus.NOT_FOUND): {
            "description": "Ressource inexistante, ou invisible pour le principal."
        }
    },
)

KbRead = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.VIEWER))]
"""Accès en lecture : membre `viewer`, ou visiteur d'une base `public` (ADR-005)."""

KbWrite = Annotated[KnowledgeBaseAccess, Depends(require_kb_permission(MemberRole.EDITOR))]
"""Accès en écriture de contenu : `owner` ou `editor` (matrice §4)."""

ScopeRead = Depends(require_scope(ApiKeyScope.DOCUMENTS_READ))
"""Portée de clé API exigée en lecture — sans effet hors authentification par clé."""

ScopeWrite = Depends(require_scope(ApiKeyScope.DOCUMENTS_WRITE))
"""Portée de clé API exigée en écriture (ingestion, suppression, decision de precheck)."""

DocumentPath = Annotated[uuid.UUID, Path(description="Identifiant du document.")]

_CHUNKS_MIN_PAGE_SIZE: Final = 1
_CHUNKS_MAX_PAGE_SIZE: Final = 100
"""Plafond RAGFlow lui-même (constaté empiriquement, cf.
`Settings.quality_guard_chunk_sample_limit`)."""
_CHUNKS_DEFAULT_PAGE_SIZE: Final = 20
"""Choix d'ergonomie pour le panneau de fragments — sans équivalent côté moteur."""

_INLINE_CONTENT_TYPES: Final[frozenset[str]] = frozenset({"application/pdf", "text/plain"})
"""Types rendus `inline` par `GET …/content` — tout le reste part en `attachment`.

La liste est **fermée**, et c'est une mesure de sécurité, pas une préférence
d'ergonomie : `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES` accepte `text/html`, et un
document HTML servi `inline` s'exécuterait dans l'origine de l'application, avec
son cookie de session. PDF, images matricielles et texte brut n'exécutent rien —
à la seule condition que le navigateur ne renifle pas un autre type que celui
annoncé, d'où le `X-Content-Type-Options: nosniff` posé systématiquement.
"""

_INLINE_CONTENT_TYPE_PREFIX: Final = "image/"
"""Les images matricielles s'affichent sans exécuter quoi que ce soit."""

_SCRIPTABLE_IMAGE_TYPE: Final = "image/svg+xml"
"""Seule exception au préfixe : un SVG est un document XML, qui peut porter du script."""


def content_headers(filename: str, content_type: str) -> dict[str, str]:
    """En-têtes de restitution du fichier d'origine — nom, mode d'ouverture, anti-sniffing.

    Le nom est encodé selon la RFC 6266/5987 dès qu'il sort de l'ASCII imprimable
    (un corpus francophone en produit à chaque accent) : `filename*=utf-8''…`.
    Le guillemet, qui casserait la forme simple `filename="…"`, déclenche la même
    branche puisque `quote` le remplace — aucun nom ne peut donc s'échapper de
    son en-tête.
    """
    disposition = "inline" if is_safely_renderable(content_type) else "attachment"
    encoded = quote(filename, safe="")
    naming = f'filename="{filename}"' if encoded == filename else f"filename*=utf-8''{encoded}"
    return {
        "Content-Disposition": f"{disposition}; {naming}",
        "X-Content-Type-Options": "nosniff",
    }


def is_safely_renderable(content_type: str) -> bool:
    """`True` si le navigateur peut afficher ce type sans exécuter de code."""
    if content_type == _SCRIPTABLE_IMAGE_TYPE:
        return False
    return content_type in _INLINE_CONTENT_TYPES or content_type.startswith(
        _INLINE_CONTENT_TYPE_PREFIX
    )


class DocumentListParams(PaginationParams):
    """`?limit=&cursor=&status=&theme=` de `GET …/knowledge-bases/{kb_id}/documents`.

    Sous-classe de `PaginationParams`, pas un second paramètre `Query()` posé à
    côté : FastAPI (0.140.7, constaté empiriquement) ne « déplie » qu'un seul
    modèle de requête par route — ajouter un `Query()` frère fait retomber le
    modèle de pagination sur son nom littéral (`?pagination=…` exigé), cassant
    même l'appel sans aucun paramètre.
    """

    status: DocumentStatus | None = Field(
        default=None, description="Ne renvoie que les documents dans cet état."
    )
    theme: str | None = Field(
        default=None,
        max_length=MAX_THEME_LENGTH,
        description="Ne renvoie que les documents portant exactement ce thème.",
    )


@router.get(
    "/knowledge-bases/{kb_id}/documents",
    summary="Lister les documents d'une base",
    description=(
        "Liste paginée par curseur, du plus récent au plus ancien. `status` est "
        "une projection dénormalisée de l'état du job courant : la liste se rend "
        "sans jointure sur les jobs d'ingestion. `status`/`theme` filtrent en base "
        "(correspondance exacte) — un client qui a besoin de tous les documents "
        "d'un statut ou d'un thème donné doit filtrer ici, jamais en tronquant une "
        "page côté client (audit dette technique 2026-09, motif A)."
    ),
    dependencies=[ScopeRead],
)
async def list_documents(
    access: KbRead,
    db: DbSession,
    settings: SettingsDep,
    pagination: Annotated[DocumentListParams, Query()],
) -> Page[DocumentRead]:
    """Liste les documents de la base résolue par la dépendance d'autorisation."""
    rows = await DocumentService(db, settings).list_for_knowledge_base(
        access.knowledge_base,
        cursor=pagination.decoded_cursor(),
        limit=pagination.limit,
        status=pagination.status,
        theme=pagination.theme,
    )
    return build_page(rows, serializer=DocumentRead.model_validate, limit=pagination.limit)


@router.post(
    "/knowledge-bases/{kb_id}/documents/ingest",
    status_code=HTTPStatus.ACCEPTED,
    summary="Ingérer un document",
    description=(
        "Téléverse un fichier en `multipart/form-data` et ouvre son job "
        "d'ingestion : `202`, le suivi se fait sur `GET /api/v1/jobs/{job_id}` "
        "avec le `current_job_id` renvoyé.\n\n"
        "Le corps porte le fichier dans le champ `file` et, facultativement, un "
        "`filename` qui prend le pas sur le nom transporté par l'upload. "
        "`content_type`, `size_bytes` et `checksum_sha256` sont **calculés côté "
        "serveur** à partir des octets reçus : aucune de ces valeurs n'est "
        "acceptée du client.\n\n"
        "**Idempotent par empreinte** : un fichier déjà présent dans la base sous "
        "le même `checksum_sha256` ne crée ni document ni job supplémentaire — la "
        "réponse porte le document d'origine.\n\n"
        "L'analyse préalable démarre aussitôt, puis le job s'arrête sur "
        "`awaiting_review` : c'est "
        "`PUT /api/v1/documents/{document_id}/precheck/decision` qui relance la "
        "suite du pipeline.\n\n"
        "`theme`, facultatif, étiquette le document pour l'organiser par sujet "
        "au sein de la base — saisi par l'utilisateur, jamais déduit. Modifiable "
        "après coup via `PATCH /api/v1/documents/{document_id}`."
    ),
    responses={
        int(HTTPStatus.FORBIDDEN): {
            "description": "Rôle insuffisant, ou quota d'espace éphémère dépassé."
        }
    },
    dependencies=[ScopeWrite],
)
async def ingest_document(
    access: KbWrite,
    db: DbSession,
    settings: SettingsDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File(description="Fichier à ingérer.")],
    filename: Annotated[
        str | None,
        Form(
            max_length=MAX_FILENAME_LENGTH,
            description="Nom à retenir ; à défaut, celui porté par l'upload.",
        ),
    ] = None,
    theme: Annotated[
        str | None,
        Form(
            max_length=MAX_THEME_LENGTH,
            description="Étiquette de thème, pour organiser la base par sujet.",
        ),
    ] = None,
) -> DocumentRead:
    """Persiste le fichier reçu, enregistre le document et ouvre son job d'ingestion."""
    upload = UploadedDocument(
        content=await read_upload_content(file, settings, declared_size=file.size),
        filename=filename or file.filename,
        declared_content_type=file.content_type,
        theme=theme,
    )
    registered = await DocumentService(db, settings, storage=storage).register(access, upload)
    return DocumentRead.model_validate(registered.document)


@router.get(
    "/documents/{document_id}",
    summary="Lire un document",
    description=(
        "Le document appartenant à une base invisible répond `404` : sa "
        "visibilité est celle de sa base de connaissances."
    ),
    dependencies=[ScopeRead],
)
async def read_document(
    document_id: DocumentPath,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> DocumentRead:
    """Renvoie un document, si sa base est lisible par le principal."""
    resolved = await DocumentService(db, settings).resolve_access(
        principal, document_id, minimum=MemberRole.VIEWER
    )
    return DocumentRead.model_validate(resolved.document)


@router.get(
    "/documents/{document_id}/content",
    summary="Télécharger le fichier d'origine",
    description=(
        "Rend les **octets d'origine** du document, tels qu'ils ont été versés — "
        "pas son texte extrait ni ses fragments. Le `Content-Type` est celui "
        "arbitré à l'ingestion, et le nom d'origine est porté par "
        "`Content-Disposition`.\n\n"
        "Accessible dès l'enregistrement, sans attendre l'indexation : c'est "
        "précisément sur un job en échec qu'on veut rouvrir la pièce versée.\n\n"
        "Les types que le navigateur sait rendre sans exécuter de code (PDF, "
        "images, texte brut) sont servis `inline` ; tous les autres en "
        "`attachment`."
    ),
    response_class=Response,
    responses={
        int(HTTPStatus.OK): {
            "description": "Le fichier d'origine.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    dependencies=[ScopeRead],
)
async def read_document_content(
    document_id: DocumentPath,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    storage: StorageDep,
) -> Response:
    """Renvoie le fichier d'origine, si la base qui le porte est lisible par le principal."""
    service = DocumentService(db, settings, storage=storage)
    resolved = await service.resolve_access(principal, document_id, minimum=MemberRole.VIEWER)
    content = await service.read_content(resolved)
    return Response(
        content=content,
        media_type=resolved.document.content_type,
        headers=content_headers(resolved.document.filename, resolved.document.content_type),
    )


@router.get(
    "/documents/{document_id}/chunks",
    summary="Lister les fragments indexés d'un document",
    description=(
        "Fragments tels qu'ils sont réellement stockés par le moteur d'indexation — "
        "contenu, position (page), mots-clés et questions générées, si activés — "
        "avec les métadonnées du document parent (méthode de chunking effective, "
        "nombre total de fragments, tokens indexés).\n\n"
        "Pagination réelle (`page`/`page_size`), déléguée à RAGFlow : `total` "
        "porte le nombre de fragments toutes pages confondues — sauf `parents_only=true`, où "
        "`page`/`total` portent sur le seul sous-ensemble filtré (RAGFlow ne filtre pas côté "
        "serveur).\n\n"
        "`404` si le document n'a pas encore atteint l'indexation (pas de "
        "référence moteur connue) plutôt qu'une erreur moteur confuse — "
        "distinct d'un document vraiment sans fragment (`chunk_count == 0`, "
        "ADR-024), qui répond `200` avec une page vide."
    ),
    dependencies=[ScopeRead],
)
async def list_document_chunks(
    document_id: DocumentPath,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    page: Annotated[int, Query(ge=1, description="Page 1-based.")] = 1,
    page_size: Annotated[
        int,
        Query(
            ge=_CHUNKS_MIN_PAGE_SIZE,
            le=_CHUNKS_MAX_PAGE_SIZE,
            description="Fragments par page, jusqu'au plafond RAGFlow (100).",
        ),
    ] = _CHUNKS_DEFAULT_PAGE_SIZE,
    parents_only: Annotated[
        bool,
        Query(
            description=(
                "N'affiche que les fragments parents (`available=false`) d'un découpage "
                "parent/enfant (ADR-033). RAGFlow ne filtre pas côté serveur : la page et le "
                "total renvoyés portent alors sur l'ensemble filtré, pas sur le document "
                "entier — coûte un aller-retour complet sur le document au premier appel."
            ),
        ),
    ] = False,
) -> DocumentChunksPage:
    """Renvoie une page de fragments, si la base qui porte le document est lisible."""
    service = DocumentService(db, settings)
    resolved = await service.resolve_access(principal, document_id, minimum=MemberRole.VIEWER)
    return await service.list_chunks(
        resolved, page=page, page_size=page_size, parents_only=parents_only
    )


@router.patch(
    "/documents/{document_id}",
    summary="Modifier un document",
    description=(
        "Mise à jour partielle — aujourd'hui `filename` (non vidable) et "
        "`theme` (vidable par `null`, pour organiser une base par sujet). "
        "Réservée aux rôles d'écriture de la base, comme une suppression : "
        "modifier un document est une mutation du contenu."
    ),
    dependencies=[ScopeWrite],
)
async def update_document(
    document_id: DocumentPath,
    payload: DocumentUpdate,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> DocumentRead:
    """Applique la mise à jour partielle, si la base qui porte le document est modifiable."""
    service = DocumentService(db, settings)
    resolved = await service.resolve_access(principal, document_id, minimum=MemberRole.EDITOR)
    document = await service.update(resolved.document, payload)
    return DocumentRead.model_validate(document)


@router.delete(
    "/documents/{document_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Supprimer un document",
    description=(
        "Suppression physique, en cascade sur ses jobs et leur journal, **et "
        "nettoyage complet** : le document est retiré du dataset du moteur "
        "d'ingestion (avec ses fragments indexés) et ses octets d'origine sont "
        "effacés du stockage. Réservée aux rôles d'écriture de la base : "
        "supprimer un document est une mutation du contenu, pas de la base "
        "elle-même.\n\n"
        "Un moteur momentanément injoignable répond `503` sans rien supprimer : "
        "la requête est à rejouer telle quelle."
    ),
    dependencies=[ScopeWrite],
)
async def delete_document(
    document_id: DocumentPath,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    storage: StorageDep,
) -> None:
    """Supprime un document du moteur, de la base et du stockage."""
    service = DocumentService(db, settings, storage=storage)
    resolved = await service.resolve_access(principal, document_id, minimum=MemberRole.EDITOR)
    await service.delete(resolved)


@router.put(
    "/documents/{document_id}/precheck/decision",
    summary="Décider de l'aiguillage du precheck",
    description=(
        "Enregistre le choix human-in-the-loop — `optimize_for_tables` "
        "(découpage tabulaire) et `heavy_ocr` (analyse de mise en page "
        "complète), indépendants et cumulables — **et relance le pipeline** : le "
        "job quitte `awaiting_review` pour `chunking`, puis poursuit jusqu'à "
        "`completed`.\n\n"
        "Rejouer la décision sur un job qui a déjà repris est sans effet : la "
        "tâche de découpage n'agit que depuis `awaiting_review`."
    ),
    dependencies=[ScopeWrite],
)
async def decide_precheck(
    document_id: DocumentPath,
    payload: DocumentPrecheckDecisionUpdate,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> DocumentRead:
    """Persiste la décision de precheck, puis publie la reprise du pipeline."""
    service = DocumentService(db, settings)
    resolved = await service.resolve_access(principal, document_id, minimum=MemberRole.EDITOR)
    document = await service.record_precheck_decision(resolved.document, payload)
    return DocumentRead.model_validate(document)
