"""Moteur d'ingestion factice : complet, déterministe, en mémoire. PAS un moteur de production.

Deux usages, un seul code :

1. **Double de test unique** du port `IngestionEnginePort`. Il remplace les
   doubles partiels que chaque module de tests réécrivait pour n'exercer qu'une
   ou deux méthodes. Un comportement particulier se demande par une *option*
   (voir « Scénarisation » plus bas), pas par un nouveau double.
2. **Exemple d'adaptateur** pour qui veut brancher un autre moteur
   (`docs/extending.md`) : la moitié haute de la classe est un adaptateur
   complet — chaque méthode du port, chaque état de parsing, chaque erreur —
   lisible de bout en bout.

Ce qu'il fait : `ensure_dataset` idempotent sur `name_seed`, téléversement en
mémoire, parsing déterministe (le texte UTF-8 du fichier est découpé en
paragraphes), progression `chunking → embedding → indexing → done` répartie sur
un nombre configurable de sondages, `cancel_parsing`, suppression, listing des
fragments paginé, et une **recherche lexicale** (part des mots de la requête
retrouvés dans le fragment, accents et casse ignorés).

Ce qu'il ne fait pas, et ne fera pas : aucune recherche sémantique (le score
dense vaut toujours `0.0`, aucun embedding n'est calculé), aucun OCR (un fichier
qui n'est pas du texte UTF-8 fait échouer le parsing), aucun reranking, aucune
persistance. L'état vit dans le process : un worker Celery et une API lancés
dans deux process distincts ne partagent pas ce moteur. Pour ce cas, la
sous-classe `SharedStateFakeEngine` (`fake_shared.py`, ADR-071) range l'état
dans Redis — c'est le moteur du chemin d'essai `scripts/quickstart.sh demo`.
Ni l'un ni l'autre n'est un moteur de déploiement.

CONTRIBUTING.md §3 fait de RAGFlow le moteur unique : un pipeline maison concurrent
est interdit. Ce module n'en est pas un — il ne chunke pas « mieux » ni
autrement, il simule le contrat — et `Settings` **refuse** de le sélectionner
en staging/production (`INGESTION_ENGINE=fake`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Concatenate, Final

from pydantic import BaseModel, ConfigDict

from app.domain.enums import JobState
from app.providers.engine.base import (
    DEFAULT_CHUNK_METHOD,
    MAX_SEARCH_RESULTS,
    ChunkingProfile,
    EngineChunk,
    EngineDatasetRef,
    EngineDocumentChunksMeta,
    EngineDocumentChunksPage,
    EngineDocumentRef,
    EngineError,
    EngineParseStatus,
    EngineSearchResult,
    EngineValidationError,
)

FAKE_ENGINE_ID: Final = "fake"
"""Valeur de `INGESTION_ENGINE` qui sélectionne ce moteur."""

MAX_CHUNK_CHARACTERS: Final = 1000
"""Taille maximale d'un fragment ; un paragraphe plus long est coupé aux espaces."""

_PHASES: Final = (JobState.CHUNKING, JobState.EMBEDDING, JobState.INDEXING)
_PARAGRAPH_BREAK: Final = re.compile(r"\n\s*\n")
_WORD: Final = re.compile(r"\w+")

NOT_TEXT_MESSAGE: Final = "Le moteur factice ne lit que du texte UTF-8 : ce fichier n'en est pas."


def _words(text: str) -> list[str]:
    """Mots en minuscules, accents retirés — la seule « sémantique » de la recherche factice."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    return _WORD.findall("".join(c for c in folded if not unicodedata.combining(c)))


def split_into_chunks(text: str) -> list[str]:
    """Découpe un texte en fragments : un par paragraphe, coupé aux espaces au-delà de la borne."""
    chunks: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        remaining = " ".join(paragraph.split())
        while len(remaining) > MAX_CHUNK_CHARACTERS:
            cut = remaining.rfind(" ", 0, MAX_CHUNK_CHARACTERS)
            cut = cut if cut > 0 else MAX_CHUNK_CHARACTERS
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
    return chunks


@dataclass(frozen=True, slots=True)
class RecordedSearch:
    """Un appel à `search`, tel que reçu — pour qu'un test vérifie ce que le service a demandé."""

    datasets: list[EngineDatasetRef]
    query: str
    top_k: int
    rerank_provider_id: str | None
    similarity_threshold: float | None
    document_refs: list[EngineDocumentRef] | None
    rerank_candidate_pool_size: int | None


@dataclass(slots=True)
class _Document:
    filename: str
    content: bytes
    run: str = "unstart"
    profile: ChunkingProfile | None = None
    chunks: list[EngineChunk] | None = None
    """Fragments **indexés** — `None` tant que le parsing n'est pas `done`."""
    pending: list[str] | None = None
    pending_error: str | None = None
    polls: int = 0


@dataclass(slots=True)
class _Dataset:
    name_seed: str
    embedding_provider_id: str
    reranker_provider_id: str | None
    documents: dict[str, _Document]


class _State(BaseModel):
    """Instantané sérialisable de l'état « réel » du moteur (hors scénarisation et journaux)."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    counter: int
    dataset_by_seed: dict[str, str]
    datasets: dict[str, _Dataset]


def _operation[**P, R](
    method: Callable[Concatenate[FakeIngestionEngine, P], Coroutine[Any, Any, R]],
) -> Callable[Concatenate[FakeIngestionEngine, P], Coroutine[Any, Any, R]]:
    """Entoure une opération du port de `FakeIngestionEngine._operation` (point d'extension).

    `Coroutine[Any, Any, R]` : la forme exacte que le port déclare pour ses méthodes.
    """

    @wraps(method)
    async def wrapper(self: FakeIngestionEngine, /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._operation():
            return await method(self, *args, **kwargs)

    return wrapper


class FakeIngestionEngine:
    """Adaptateur en mémoire du port `IngestionEnginePort`.

    Args:
        polls_before_done: nombre de sondages `running` avant `done` (0 : le
            premier sondage rend déjà `done`). Les sondages intermédiaires
            parcourent `chunking`, `embedding`, `indexing`.
        strict: `True` (défaut) rejette en `EngineError` tout dataset ou
            document que le moteur n'a pas lui-même créé, comme RAGFlow
            (`code=102`). `False` adopte silencieusement le dataset inconnu et
            ignore le document inconnu : pour un test dont la base de données
            porte déjà des identifiants externes que ce moteur n'a jamais vus.
        dataset_id: impose l'identifiant de tout dataset créé (au lieu de
            `fake-dataset-<n>`).
        document_id: impose l'identifiant de tout document téléversé.
        search_results, chunks_by_document, chunks, statuses: raccourcis de
            scénarisation, voir les attributs du même nom ci-dessous.
    """

    def __init__(
        self,
        *,
        polls_before_done: int = 0,
        strict: bool = True,
        dataset_id: str | None = None,
        document_id: str | None = None,
        search_results: list[EngineSearchResult] | None = None,
        chunks_by_document: dict[str, list[EngineChunk]] | None = None,
        chunks: list[EngineChunk] | None = None,
        statuses: list[EngineParseStatus] | None = None,
    ) -> None:
        self.polls_before_done = polls_before_done
        self.strict = strict
        self.dataset_id = dataset_id
        self.document_id = document_id
        self._datasets: dict[str, _Dataset] = {}
        self._dataset_by_seed: dict[str, str] = {}
        self._counter = 0

        # ── Scénarisation (tests) ──────────────────────────────────────────
        self.failures: dict[str, Exception] = {}
        """`{nom de méthode du port: exception}` : la méthode lève cette exception."""
        self.parse_failure: str | None = None
        """Message d'échec : tout parsing lancé ensuite finit en `run="fail"` avec ce texte."""
        self.statuses: list[EngineParseStatus] | None = statuses
        """Si posé, `get_parse_status` rejoue cette file (le dernier état est répété)."""
        self.search_results: list[EngineSearchResult] | None = search_results
        """Si posé, `search` rend exactement ces résultats au lieu de chercher."""
        self.chunks: list[EngineChunk] | None = chunks
        """Si posé, `list_document_chunks` rend ces fragments pour tout document."""
        self.chunks_by_document: dict[str, list[EngineChunk]] = chunks_by_document or {}
        """Fragments rendus par document (identifiant externe), prioritaires sur l'état réel."""
        self.chunk_pages: list[EngineDocumentChunksPage] | None = None
        """Si posé, `list_document_chunks` rend ces pages dans l'ordre des appels
        (la dernière est répétée), quels que soient `page`/`page_size`."""

        # ── Journaux ───────────────────────────────────────────────────────
        self.ensured: list[EngineDatasetRef | None] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.started: list[ChunkingProfile] = []
        self.polls = 0
        self.cancellations = 0
        self.search_calls: list[RecordedSearch] = []
        self.chunk_list_calls: list[tuple[str, str, int, int]] = []
        """`(dataset, document, page, page_size)` par appel à `list_document_chunks`."""
        self.documents_deleted: list[tuple[str, str]] = []
        self.datasets_deleted: list[str] = []

    # ─── Opérations du port ──────────────────────────────────────────────────

    @_operation
    async def ensure_dataset(
        self,
        *,
        ref: EngineDatasetRef | None,
        name_seed: str,
        embedding_provider_id: str,
        reranker_provider_id: str | None,
    ) -> EngineDatasetRef:
        """Réutilise `ref`, sinon crée le dataset de `name_seed` — idempotent sur ce nom."""
        self._maybe_fail("ensure_dataset")
        self.ensured.append(ref)
        if ref is not None:
            self._datasets.setdefault(
                ref.external_id,
                _Dataset(name_seed, embedding_provider_id, reranker_provider_id, {}),
            )
            return ref
        known = self._dataset_by_seed.get(name_seed)
        if known is not None:
            return EngineDatasetRef(external_id=known)
        external_id = self.dataset_id or self._next_id("fake-dataset")
        self._datasets[external_id] = _Dataset(
            name_seed, embedding_provider_id, reranker_provider_id, {}
        )
        self._dataset_by_seed[name_seed] = external_id
        return EngineDatasetRef(external_id=external_id)

    @_operation
    async def upload_document(
        self, dataset: EngineDatasetRef, *, filename: str, content: bytes
    ) -> EngineDocumentRef:
        """Enregistre le fichier sans lancer son traitement (`run="unstart"`)."""
        self._maybe_fail("upload_document")
        self.uploads.append((filename, content))
        external_id = self.document_id or self._next_id("fake-document")
        self._dataset(dataset).documents[external_id] = _Document(filename, content)
        return EngineDocumentRef(external_id=external_id)

    @_operation
    async def start_parsing(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef, profile: ChunkingProfile
    ) -> None:
        """Applique le profil puis lance le parsing ; le résultat vient au fil des sondages."""
        self._maybe_fail("start_parsing")
        self.started.append(profile)
        doc = self._document(dataset, document)
        if doc is None:
            return
        doc.profile = profile
        doc.run, doc.polls, doc.chunks = "running", 0, None
        doc.pending, doc.pending_error = None, self.parse_failure
        if doc.pending_error is None:
            try:
                doc.pending = split_into_chunks(doc.content.decode("utf-8"))
            except UnicodeDecodeError:
                doc.pending_error = NOT_TEXT_MESSAGE

    @_operation
    async def get_parse_status(
        self, dataset: EngineDatasetRef, document: EngineDocumentRef
    ) -> EngineParseStatus:
        """Projette l'état du document ; chaque appel le fait avancer d'un sondage."""
        self._maybe_fail("get_parse_status")
        if self.statuses:
            index = min(self.polls, len(self.statuses) - 1)
            self.polls += 1
            return self.statuses[index]
        self.polls += 1
        doc = self._document(dataset, document)
        if doc is None or doc.run == "unstart":
            return _status("unstart", None, 0.0, 0, None)
        if doc.run == "running":
            if doc.polls < self.polls_before_done:
                phase = _PHASES[min(doc.polls, len(_PHASES) - 1)]
                doc.polls += 1
                return _status(
                    "running", phase, doc.polls / (self.polls_before_done + 1), 0, phase.value
                )
            if doc.pending_error is not None:
                doc.run = "fail"
            else:
                doc.run = "done"
                doc.chunks = [
                    EngineChunk(chunk_id=f"{document.external_id}-chunk-{index}", content=text)
                    for index, text in enumerate(doc.pending or [])
                ]
        if doc.run == "done":
            return _status("done", JobState.COMPLETED, 1.0, len(doc.chunks or []), "Done.")
        if doc.run == "fail":
            return _status("fail", None, -1.0, 0, doc.pending_error, error=doc.pending_error)
        return _status("cancel", None, 0.0, 0, None)

    @_operation
    async def cancel_parsing(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Interrompt un parsing en cours : le document passe à `cancel`."""
        self._maybe_fail("cancel_parsing")
        self.cancellations += 1
        doc = self._document(dataset, document)
        if doc is not None and doc.run == "running":
            doc.run = "cancel"

    @_operation
    async def search(
        self,
        datasets: Sequence[EngineDatasetRef],
        *,
        query: str,
        top_k: int,
        rerank_provider_id: str | None,
        similarity_threshold: float | None,
        document_refs: Sequence[EngineDocumentRef] | None = None,
        rerank_candidate_pool_size: int | None = None,
    ) -> list[EngineSearchResult]:
        """Recherche lexicale : score = part des mots de la requête présents dans le fragment."""
        self._maybe_fail("search")
        self.search_calls.append(
            RecordedSearch(
                datasets=list(datasets),
                query=query,
                top_k=top_k,
                rerank_provider_id=rerank_provider_id,
                similarity_threshold=similarity_threshold,
                document_refs=list(document_refs) if document_refs is not None else None,
                rerank_candidate_pool_size=rerank_candidate_pool_size,
            )
        )
        if self.search_results is not None:
            return list(self.search_results)
        if not datasets or (document_refs is not None and not document_refs):
            return []
        if top_k < 1 or top_k > MAX_SEARCH_RESULTS:
            message = f"`top_k` doit être compris entre 1 et {MAX_SEARCH_RESULTS} (reçu {top_k})."
            raise EngineValidationError(message)
        wanted = {ref.external_id for ref in document_refs} if document_refs is not None else None
        terms = set(_words(query))
        scored: list[tuple[float, int, EngineSearchResult]] = []
        for dataset in datasets:
            for document_id, doc in self._dataset(dataset).documents.items():
                if wanted is not None and document_id not in wanted:
                    continue
                for chunk in doc.chunks or []:
                    score = len(terms & set(_words(chunk.content))) / len(terms) if terms else 0.0
                    if score <= 0.0 or (
                        similarity_threshold is not None and score < similarity_threshold
                    ):
                        continue
                    scored.append(
                        (
                            score,
                            len(scored),
                            EngineSearchResult(
                                chunk_id=chunk.chunk_id,
                                document_external_id=document_id,
                                content=chunk.content,
                                similarity=score,
                                vector_similarity=0.0,
                                term_similarity=score,
                            ),
                        )
                    )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [result for _, _, result in scored[:top_k]]

    @_operation
    async def delete_document(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> None:
        """Retire le document. Pas idempotent, comme le moteur réel : un inconnu lève."""
        self._maybe_fail("delete_document")
        self.documents_deleted.append((dataset.external_id, document.external_id))
        documents = self._dataset(dataset).documents
        if document.external_id in documents:
            del documents[document.external_id]
        elif self.strict:
            raise EngineError(f"Document introuvable ou interdit : {document.external_id}.")

    @_operation
    async def delete_dataset(self, ref: EngineDatasetRef) -> None:
        """Supprime le dataset et ses documents."""
        self._maybe_fail("delete_dataset")
        self.datasets_deleted.append(ref.external_id)
        if ref.external_id in self._datasets:
            seed = self._datasets.pop(ref.external_id).name_seed
            self._dataset_by_seed.pop(seed, None)
        elif self.strict:
            raise EngineError(f"Dataset introuvable ou interdit : {ref.external_id}.")

    @_operation
    async def list_document_chunks(
        self,
        dataset: EngineDatasetRef,
        document: EngineDocumentRef,
        *,
        page: int = 1,
        page_size: int,
    ) -> EngineDocumentChunksPage:
        """Fragments indexés d'un document, une page à la fois ; vide tant que rien n'est indexé."""
        self._maybe_fail("list_document_chunks")
        self.chunk_list_calls.append((dataset.external_id, document.external_id, page, page_size))
        if self.chunk_pages:
            return self.chunk_pages[min(len(self.chunk_list_calls), len(self.chunk_pages)) - 1]
        doc = self._document(dataset, document)
        chunks = self.chunks_by_document.get(document.external_id)
        if chunks is None:
            chunks = self.chunks if self.chunks is not None else (doc.chunks if doc else None) or []
        start = (page - 1) * page_size
        method = doc.profile.chunk_method if doc and doc.profile else None
        return EngineDocumentChunksPage(
            chunks=chunks[start : start + page_size],
            document=EngineDocumentChunksMeta(
                chunk_method=method or DEFAULT_CHUNK_METHOD,
                chunk_count=len(chunks),
                token_count=sum(len(_words(chunk.content)) for chunk in chunks),
            ),
            total=len(chunks),
            page=page,
            page_size=page_size,
        )

    # ─── Mécanique interne ───────────────────────────────────────────────────

    def _operation(self) -> AbstractAsyncContextManager[None]:
        """Enveloppe chaque opération du port ; ne fait rien ici.

        Point d'extension de `SharedStateFakeEngine` : il y recharge l'état
        depuis un stockage partagé avant l'opération, et l'y réécrit après.
        """
        return _no_operation()

    def export_state(self) -> bytes:
        """État du moteur (datasets, documents, fragments, compteur d'identifiants), en JSON."""
        state = _State(
            counter=self._counter,
            dataset_by_seed=self._dataset_by_seed,
            datasets=self._datasets,
        )
        return state.model_dump_json().encode()

    def import_state(self, payload: bytes) -> None:
        """Remplace l'état du moteur par un instantané produit par `export_state`."""
        state = _State.model_validate_json(payload)
        self._counter = state.counter
        self._dataset_by_seed = state.dataset_by_seed
        self._datasets = state.datasets

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def _maybe_fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def _dataset(self, ref: EngineDatasetRef) -> _Dataset:
        found = self._datasets.get(ref.external_id)
        if found is not None:
            return found
        if self.strict:
            raise EngineError(f"Dataset introuvable ou interdit : {ref.external_id}.")
        return self._datasets.setdefault(ref.external_id, _Dataset(ref.external_id, "", None, {}))

    def _document(self, dataset: EngineDatasetRef, document: EngineDocumentRef) -> _Document | None:
        found = self._dataset(dataset).documents.get(document.external_id)
        if found is None and self.strict:
            raise EngineError(f"Document introuvable ou interdit : {document.external_id}.")
        return found


@asynccontextmanager
async def _no_operation() -> AsyncIterator[None]:
    yield


def _status(
    run: str,
    phase: JobState | None,
    progress: float,
    chunk_count: int,
    message: str | None,
    *,
    error: str | None = None,
) -> EngineParseStatus:
    return EngineParseStatus(
        run=run,  # type: ignore[arg-type]  # les appelants ne passent que les littéraux du port.
        inferred_phase=phase,
        progress=progress,
        last_message=message,
        chunk_count=chunk_count,
        error=error,
    )


_SHARED: FakeIngestionEngine | None = None


def shared_fake_engine() -> FakeIngestionEngine:
    """Instance unique du process, rendue par la fabrique (`INGESTION_ENGINE=fake`).

    L'API et les tâches Celery résolvent le moteur à chaque appel : sans
    instance partagée, le document téléversé par l'une serait introuvable par
    l'autre. Valable dans **un seul process** — voir l'en-tête du module.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = FakeIngestionEngine()
    return _SHARED


def reset_shared_fake_engine() -> None:
    """Oublie l'instance partagée (isolation entre deux tests)."""
    global _SHARED
    _SHARED = None
