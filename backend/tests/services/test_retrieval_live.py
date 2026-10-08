"""Test de pertinence réel — contre la stack RAGFlow + TEI réellement déployée (ADR-051).

Contrairement au reste de la suite, ce test ne mocke **ni** le moteur **ni** le
réseau : il pilote `RagflowEngineAdapter` directement (sans Celery — Phase 5 ne
teste pas le pipeline d'ingestion, déjà couvert en Phase 4) pour ingérer trois
documents synthétiques thématiquement distincts, puis interroge
`RetrievalService` avec ce même adaptateur réel, une fois sans reranking et une
fois avec.

**Piège vérifié, deux fois** : `Settings.model_config.env_file` (`".env"`) est
résolu relativement au **cwd du process**, jamais à l'emplacement de ce
fichier. Une chaîne relative pour `_env_file` hérite donc du même piège selon
que `pytest` tourne depuis `backend/` ou depuis la racine du dépôt (l'usage
réel de ce dépôt) — mesuré : avec `"../.env"` et un cwd à la racine, aucune
erreur n'est levée (le chemin, hors du dépôt, n'existe simplement pas), mais
`ragflow_api_key` retombe silencieusement à `None`. `_REPO_ROOT_ENV` (chemin
**absolu**, dérivé de `__file__`) élimine cette dépendance au cwd. L'URL, elle,
est réécrite via le kwarg explicite `ragflow_base_url` : les kwargs priment
toujours sur le fichier, donc la clé API vient bien de `.env` tandis que le
port mappé sur l'hôte remplace le nom `ragflow-cpu` du `.env`, résoluble
seulement depuis l'intérieur du réseau Docker.

Le dataset créé est nettoyé en fin de test (`delete_dataset`), pour ne rien
laisser sur l'instance partagée — y compris si les assertions échouent.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.schemas.retrieval import RetrievalSearchRequest
from app.providers.engine.base import ChunkingProfile, EngineDatasetRef, EngineDocumentRef
from app.providers.engine.ragflow.client import RagflowEngineAdapter
from app.services.retrieval import RetrievalService
from tests.db import factories
from tests.ingestion.precheck.documents import text_pdf
from tests.services.test_knowledge_bases import account, principal_of

pytestmark = pytest.mark.live_ragflow

POLL_INTERVAL_SECONDS = 1.5
PARSE_TIMEOUT_SECONDS = 90.0

# Trois sujets nettement distincts (CONTRIBUTING.md : corpus francophone) : une
# requête ciblée sur l'un ne doit raisonnablement matcher aucun des deux autres.
BEEKEEPING_PARAGRAPH = (
    "Les colonies d'abeilles occupent des ruches disposees en bordure du verger.",
    "Chaque printemps l'apiculteur inspecte les cadres pour verifier la ponte de la reine.",
    "Le miel recolte a l'automne provient exclusivement de la floraison locale des acacias.",
    "Un frelon asiatique repere pres du rucher a impose la pose de pieges selectifs.",
    "La production annuelle de cire est transformee en bougies vendues au marche local.",
    "Les essaims trop peuples sont divises avant la periode de forte chaleur estivale.",
)

NAVAL_ARCHITECTURE_PARAGRAPH = (
    "La coque du voilier est dessinee pour reduire la gite par vent de travers soutenu.",
    "Le chantier naval a choisi un bordage en contreplaque marine colle a la resine epoxy.",
    "Le plan de voilure prevoit un genois recouvrant largement le mat en carbone.",
    "Les calculs de stabilite verifient que le centre de carene reste sous le centre de gravite.",
    "La quille lestee en plomb abaisse le centre de gravite pour limiter le risque de chavirage.",
    "Le tirant d'eau final autorise l'acces aux ports peu profonds de la cote atlantique.",
)

LOCAL_TAXATION_PARAGRAPH = (
    "Le conseil municipal a vote une revalorisation moderee du taux de taxe fonciere.",
    "La base d'imposition des locaux commerciaux integre desormais la valeur locative actualisee.",
    "Les contribuables redevables de la taxe d'habitation sur les residences secondaires",
    "restent concernes malgre les exonerations recemment votees par le conseil.",
    "Un abattement specifique s'applique aux menages dont le quotient familial reste modeste.",
    "La commission des finances locales examine chaque annee les taux votes par les communes.",
    "Le produit de la fiscalite directe locale finance en priorite les ecoles et la voirie.",
)

DOCUMENTS: dict[str, tuple[str, ...]] = {
    "apiculture.pdf": BEEKEEPING_PARAGRAPH,
    "architecture-navale.pdf": NAVAL_ARCHITECTURE_PARAGRAPH,
    "fiscalite-locale.pdf": LOCAL_TAXATION_PARAGRAPH,
}

QUERY = "Quelles mesures prendre contre le frelon asiatique repere pres du rucher ?"
EXPECTED_FILENAME = "apiculture.pdf"


_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
"""Chemin **absolu** du `.env` racine, immunisé contre le cwd du process.

`"../.env"` (chaîne relative) supposait un `pytest` lancé depuis `backend/` —
faux dès que la suite tourne depuis la racine du dépôt (l'invocation réelle de
cette session, `cd <racine> && pytest backend/tests/...`), où `"../.env"`
pointe hors du dépôt. Résultat mesuré : `Settings(_env_file="../.env")` ne
levait alors aucune erreur (le fichier absent n'est pas requis), mais
`ragflow_api_key` retombait silencieusement à `None`, faisant échouer l'appel
moteur avec un message qui ne pointait pas vers la vraie cause.
"""


def live_settings() -> Settings:
    """Configuration de test pointée vers l'instance RAGFlow réellement déployée.

    Vérifié en conditions réelles : les kwargs explicites (`ragflow_base_url`)
    priment sur `.env`, dont seule la clé API est donc effectivement reprise.
    """
    return Settings(_env_file=_REPO_ROOT_ENV, ragflow_base_url="http://127.0.0.1:9390")


async def _wait_until_done(
    adapter: RagflowEngineAdapter, dataset: EngineDatasetRef, document: EngineDocumentRef
) -> None:
    """Sonde `get_parse_status` jusqu'à `run == "done"`, ou échoue explicitement au timeout."""
    deadline = time.monotonic() + PARSE_TIMEOUT_SECONDS
    while True:
        status = await adapter.get_parse_status(dataset, document)
        if status.run == "done":
            return
        if status.run == "fail":
            pytest.fail(f"Parsing RAGFlow en échec pour {document.external_id} : {status.error}")
        if time.monotonic() >= deadline:
            pytest.fail(
                f"Timeout de {PARSE_TIMEOUT_SECONDS}s dépassé en attendant la fin du "
                f"parsing de {document.external_id} (dernier état observé : {status.run})."
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def test_reranking_does_not_change_the_top_result_on_distinct_topics(
    db_session: AsyncSession,
) -> None:
    """Trois documents à sujets distincts ; la requête ciblée isole le bon, avec et sans rerank."""
    settings = live_settings()
    adapter = RagflowEngineAdapter.from_settings(settings)
    seed = uuid.uuid4().hex[:12]
    dataset = await adapter.ensure_dataset(
        ref=None,
        name_seed=f"retrieval-live-{seed}",
        embedding_provider_id="bge-m3",
        reranker_provider_id=None,
    )

    try:
        engine_refs: dict[str, EngineDocumentRef] = {}
        for filename, paragraph in DOCUMENTS.items():
            content = text_pdf(pages=1, paragraph=paragraph)
            engine_document = await adapter.upload_document(
                dataset, filename=filename, content=content
            )
            await adapter.start_parsing(dataset, engine_document, ChunkingProfile())
            engine_refs[filename] = engine_document

        for engine_document in engine_refs.values():
            await _wait_until_done(adapter, dataset, engine_document)

        owner = await account(db_session)
        knowledge_base = factories.make_knowledge_base(owner, slug=f"retrieval-live-{seed}")
        knowledge_base.ragflow_dataset_external_id = dataset.external_id
        db_session.add(knowledge_base)
        await db_session.flush()

        expected_document_id: uuid.UUID | None = None
        for filename, engine_document in engine_refs.items():
            document = factories.make_document(knowledge_base, filename=filename)
            document.ragflow_document_external_id = engine_document.external_id
            db_session.add(document)
            await db_session.flush()
            if filename == EXPECTED_FILENAME:
                expected_document_id = document.id
        assert expected_document_id is not None

        service = RetrievalService(db_session, settings, engine=adapter)
        principal = principal_of(owner)

        started = time.monotonic()
        without_rerank = await service.search(
            principal,
            RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query=QUERY, rerank=False),
        )
        latency_without_rerank = time.monotonic() - started

        started = time.monotonic()
        with_rerank = await service.search(
            principal,
            RetrievalSearchRequest(knowledge_base_id=knowledge_base.id, query=QUERY, rerank=True),
        )
        latency_with_rerank = time.monotonic() - started

        print(
            f"\n[retrieval-live] requête : {QUERY!r}\n"
            f"[retrieval-live] rerank=False  latence={latency_without_rerank:.3f}s  "
            f"top={without_rerank.results[0].filename if without_rerank.results else None}\n"
            f"[retrieval-live] rerank=True   latence={latency_with_rerank:.3f}s  "
            f"top={with_rerank.results[0].filename if with_rerank.results else None}"
        )

        assert without_rerank.results, "Aucun résultat sans reranking."
        assert without_rerank.results[0].document_id == expected_document_id
        assert with_rerank.results, "Aucun résultat avec reranking."
        assert with_rerank.results[0].document_id == expected_document_id
    finally:
        await adapter.delete_dataset(dataset)
        await adapter.aclose()
