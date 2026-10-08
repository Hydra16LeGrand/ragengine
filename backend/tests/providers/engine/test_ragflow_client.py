"""`RagflowEngineAdapter` : traduction des réponses réelles de RAGFlow, sans réseau.

Aucune requête ne quitte cette suite : le client `httpx` est remplacé par un
`MockTransport` qui rejoue des réponses **relevées sur une instance réelle**
(RAGFlow v0.26.4). Les corps et les codes utilisés ici ne sont pas inventés — ils
proviennent de sondes documentées dans `docs/ingestion-engine-architecture.md` et
de l'exécution de la Phase 4 :

    GET  /datasets/{inconnu}/documents  → HTTP 200 {"code":102,"message":"You don't own…"}
    POST /datasets  (nom vide)          → HTTP 200 {"code":101,"message":"Field: <name>…"}
    POST /datasets/{id}/chunks (deux fois) → HTTP 200 {"code":102,"message":"Can't parse…"}
    GET  /datasets  (clé invalide)      → HTTP 401 {"code":401,…}

C'est le point le plus important de tout l'adaptateur : **le statut HTTP ment**.
Chaque test d'erreur ci-dessous renvoie donc `200 OK`, comme le vrai moteur.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from app.core.config import Settings
from app.domain.enums import JobState
from app.providers.engine.base import (
    ChunkingProfile,
    EngineChunkPosition,
    EngineDatasetRef,
    EngineDocumentChunksMeta,
    EngineDocumentRef,
    EngineError,
    EngineTransientError,
    EngineValidationError,
)
from app.providers.engine.ragflow import client as ragflow_client_module
from app.providers.engine.ragflow.client import (
    _UPLOAD_INTEGRITY_MAX_ATTEMPTS as UPLOAD_INTEGRITY_MAX_ATTEMPTS,
)
from app.providers.engine.ragflow.client import (
    _UPLOAD_RETRY_BACKOFF_SECONDS as UPLOAD_RETRY_BACKOFF_SECONDS,
)
from app.providers.engine.ragflow.client import (
    DATASET_NAME_PREFIX,
    INFERENCE_UNREACHABLE_MESSAGE,
    MAX_SEARCH_RESULTS,
    RagflowEngineAdapter,
    dataset_name_for,
    infer_phase,
    is_inference_unreachable,
    last_progress_line,
    normalize_chunk_whitespace,
)

BASE_URL = "http://ragflow.invalid"
API_BASE = f"{BASE_URL}/api/v1"

DATASET = EngineDatasetRef(external_id="ds-1234")
DOCUMENT = EngineDocumentRef(external_id="doc-5678")
KB_UUID = "275a9759-c48c-4c6e-8ece-0b44fc781b6a"

# Journal de progression réel d'un PDF mené à terme (relevé en audit), tronqué
# aux lignes qui portent les marqueurs de l'heuristique du §3.
DONE_PROGRESS_MSG = (
    "\n20:53:56 Task has been received."
    "\n20:53:58 Page(1~2): Start to parse."
    "\n20:54:03 Page(1~2): Generate 1 chunks"
    "\n20:54:03 Page(1~2): Embedding chunks (0.16s)"
    "\n20:54:04 Page(1~2): Indexing done (1.23s)."
    "\n20:54:09 Page(1~2): Task done (11.20s)"
)


@pytest.fixture
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Remplace l'attente entre deux tentatives de téléversement par un enregistrement.

    Le rejeu de `upload_document` attend réellement entre deux tentatives
    (`asyncio.sleep`, attente croissante) : indispensable en production, mais
    ces tests doivent rester rapides et déterministes — même patron que
    `test_circuit_breaker.py` pour l'horloge du disjoncteur.
    """
    delays: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(ragflow_client_module.asyncio, "sleep", _instant_sleep)
    return delays


Responses = list[httpx.Response] | Callable[[httpx.Request], httpx.Response]
"""Script de réponses, ou fabrique de réponse pilotée par la requête reçue."""


class Recorder:
    """Transport de test : enregistre les requêtes et rejoue des réponses scriptées."""

    def __init__(self, responses: Responses) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # Force la lecture du corps : `httpx` le stream sinon, et les assertions
        # sur `request.content` renverraient du vide.
        request.read()
        self.requests.append(request)
        if callable(self._responses):
            return self._responses(request)
        if not self._responses:
            raise AssertionError(f"requête non prévue par le test : {request.url}")
        return self._responses.pop(0)


def build(responses: Responses) -> tuple[RagflowEngineAdapter, Recorder]:
    """Assemble un adaptateur branché sur un transport de test."""
    recorder = Recorder(responses)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder), base_url=API_BASE)
    return RagflowEngineAdapter(base_url=BASE_URL, api_key="clé-de-test", client=client), recorder


def exploding() -> RagflowEngineAdapter:
    """Adaptateur dont toute requête est une erreur de test, jamais un appel."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"appel réseau interdit : {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=API_BASE)
    return RagflowEngineAdapter(base_url=BASE_URL, api_key="clé-de-test", client=client)


def ok(data: JsonValue) -> httpx.Response:
    """Réponse de succès telle que RAGFlow l'émet : `code` à zéro, HTTP 200."""
    return httpx.Response(200, json={"code": 0, "data": data})


def business_error(code: int, message: str = "refus du moteur") -> httpx.Response:
    """Échec métier tel que RAGFlow l'émet : **HTTP 200**, l'erreur est dans `code`."""
    return httpx.Response(200, json={"code": code, "message": message})


# ─── Traduction du champ `code` ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (100, EngineTransientError),  # EXCEPTION_ERROR — exception serveur générique
        (101, EngineValidationError),  # ARGUMENT_ERROR — argument invalide
        (102, EngineError),  # DATA_ERROR — « introuvable ou interdit », indiscernables
        (105, EngineTransientError),  # CONNECTION_ERROR
        (106, EngineTransientError),  # RUNNING — traitement déjà en cours
    ],
)
async def test_each_engine_code_maps_to_its_error_class(
    code: int, expected: type[EngineError]
) -> None:
    """Le catalogue du §2, cas par cas — et toujours sur un HTTP 200."""
    adapter, recorder = build([business_error(code)])

    with pytest.raises(expected) as raised:
        await adapter.delete_dataset(DATASET)

    assert recorder.requests[0].method == "DELETE"
    assert raised.value.extensions["engine_code"] == code
    assert type(raised.value) is expected


async def test_a_business_error_arrives_with_http_200() -> None:
    """Le fait qui façonne tout l'adaptateur, rendu explicite par un test dédié.

    Un adaptateur bâti sur `raise_for_status()` laisserait passer cette réponse
    comme un succès : c'est exactement le piège que `_call` évite.
    """
    response = business_error(102, "You don't own the dataset 0000.")
    assert response.status_code == 200

    adapter, _ = build([response])

    with pytest.raises(EngineError) as raised:
        await adapter.get_parse_status(DATASET, DOCUMENT)

    assert "You don't own the dataset" in str(raised.value)


EMBEDDING_UNREACHABLE_VERBATIM = (
    "EmbeddingError('Embedding request failed for HuggingFaceEmbed. Error: Connection error.')"
)
"""Forme attendue depuis ADR-051 (TEI) : `HuggingFaceEmbed` est le nom de classe
réel que RAGFlow relaie (`rag/llm/embedding_model.py`, `_FACTORY_NAME =
"HuggingFace"`) — remplace `XinferenceEmbed`, observé sous Xinference (ADR-011)."""


async def test_an_inference_outage_during_search_gets_a_clean_message() -> None:
    """`code=100` sur `/retrieval`, message verbatim de l'échec de connexion.

    Bout en bout via `search()` (pas seulement la fonction pure) : c'est ce
    chemin, Playground compris, qui montrait la trace brute à l'utilisateur.
    """
    adapter, _ = build([business_error(100, EMBEDDING_UNREACHABLE_VERBATIM)])

    with pytest.raises(EngineTransientError) as raised:
        await adapter.search(
            [DATASET],
            query="q",
            top_k=5,
            rerank_provider_id="bge-reranker-v2-m3",
            similarity_threshold=None,
        )

    assert INFERENCE_UNREACHABLE_MESSAGE in str(raised.value)
    # La trace d'origine reste disponible pour qui diagnostique.
    assert EMBEDDING_UNREACHABLE_VERBATIM in str(raised.value)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (EMBEDDING_UNREACHABLE_VERBATIM, True),
        ("Le moteur a rejeté la requête.", False),
        # Le nom du fournisseur seul, sans échec de connexion, ne doit pas
        # requalifier une erreur de modèle sans rapport avec une panne réseau.
        ("Model HuggingFace introuvable pour ce type.", False),
    ],
)
def test_the_inference_outage_heuristic_needs_both_markers(message: str, expected: bool) -> None:
    """Le nom du fournisseur *et* un échec de connexion, jamais l'un seul."""
    assert is_inference_unreachable(message) is expected


async def test_code_102_stays_a_generic_engine_error() -> None:
    """« Introuvable » et « interdit » sont confondus côté RAGFlow : l'ambiguïté ne fuit pas."""
    adapter, _ = build([business_error(102)])

    with pytest.raises(EngineError) as raised:
        await adapter.delete_dataset(DATASET)

    assert type(raised.value) is EngineError
    assert not isinstance(raised.value, EngineTransientError)
    assert not isinstance(raised.value, EngineValidationError)


@pytest.mark.parametrize("code", [10, 103, 108, 109, 400, 401, 403, 404, 409, 500])
async def test_an_uncatalogued_code_is_never_treated_as_retryable(code: int) -> None:
    """Hors des trois codes transitoires connus, on ne rejoue pas : on remonte l'échec."""
    adapter, _ = build([business_error(code)])

    with pytest.raises(EngineError) as raised:
        await adapter.delete_dataset(DATASET)

    assert not isinstance(raised.value, EngineTransientError)


async def test_the_engine_message_is_carried_into_the_problem_detail() -> None:
    """Le message du moteur est l'information de diagnostic : il ne doit pas être perdu."""
    adapter, _ = build([business_error(101, "Field: <name> - Message: <at least 1 character>")])

    with pytest.raises(EngineValidationError) as raised:
        await adapter.delete_dataset(DATASET)

    problem = raised.value.to_problem()
    assert problem.status == 422
    assert problem.detail is not None
    assert "at least 1 character" in problem.detail


# ─── Repli sur le statut HTTP, uniquement hors contrat ───────────────────────


async def test_an_unreachable_engine_is_transient() -> None:
    """Panne réseau : rejouable, c'est tout l'intérêt de la distinction."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée", request=request)

    adapter, _ = build(_handler)

    with pytest.raises(EngineTransientError):
        await adapter.delete_dataset(DATASET)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, EngineError),
        (404, EngineError),
        (500, EngineTransientError),
        (502, EngineTransientError),
    ],
)
async def test_a_body_without_code_falls_back_to_the_http_status(
    status_code: int, expected: type[EngineError]
) -> None:
    """Réponse hors contrat (401 émis par le framework, panne de proxy…) : là, le statut parle."""
    adapter, _ = build([httpx.Response(status_code, text="<html>Unauthorized</html>")])

    with pytest.raises(expected) as raised:
        await adapter.delete_dataset(DATASET)

    assert raised.value.extensions["status_code"] == status_code


async def test_a_non_json_success_is_still_an_error() -> None:
    """Un HTTP 200 sans corps JSON n'est pas un succès : c'est une réponse illisible."""
    adapter, _ = build([httpx.Response(200, text="pas du json")])

    with pytest.raises(EngineError):
        await adapter.delete_dataset(DATASET)


# ─── Heuristique de projection d'état (§3) ───────────────────────────────────


@pytest.mark.parametrize(
    ("progress_msg", "expected"),
    [
        # Aucun marqueur : le journal vide du bug de blocage, et tout message imprévu.
        ("", None),
        ("Un message d'une future version de RAGFlow", None),
        ("\n20:54:03 Page(1~2): OCR started", None),
        # Tronc commun observé avant toute phase reconnaissable.
        ("Task is queued...", JobState.CHUNKING),
        ("\n20:53:56 Task has been received.", JobState.CHUNKING),
        ("\n20:53:58 Page(1~2): Start to parse.", JobState.CHUNKING),
        # « Embedding chunks » vu, « Indexing done » pas encore.
        ("\n20:53:58 Start to parse.\n20:54:03 Embedding chunks (0.16s)", JobState.EMBEDDING),
        ("Embedding chunks (0.33s)", JobState.EMBEDDING),
        # « Indexing done » vu : il prime, quels que soient les marqueurs antérieurs.
        (
            "\n20:54:03 Embedding chunks (0.16s)\n20:54:04 Indexing done (1.23s).",
            JobState.INDEXING,
        ),
        ("Indexing done (8.78s).", JobState.INDEXING),
    ],
)
def test_the_phase_heuristic_reads_markers_and_never_invents(
    progress_msg: str, expected: JobState | None
) -> None:
    """Chaque combinaison du tableau du §3, y compris l'absence de marqueur → `None`."""
    assert infer_phase("running", progress_msg) is expected


@pytest.mark.parametrize(
    "progress_msg", ["", "message inconnu", "Embedding chunks", DONE_PROGRESS_MSG]
)
def test_a_finished_run_is_completed_whatever_the_log_says(progress_msg: str) -> None:
    """`run == "done"` tranche sans ambiguïté : le journal ne peut pas le contredire (§3)."""
    assert infer_phase("done", progress_msg) is JobState.COMPLETED


@pytest.mark.parametrize("run", ["unstart", "running", "cancel", "fail"])
def test_an_unknown_log_yields_none_and_never_raises(run: str) -> None:
    """L'incertitude est une valeur de retour, jamais une exception à rattraper."""
    assert infer_phase(run, "⚠ journal totalement inattendu") is None


@pytest.mark.parametrize(
    ("progress_msg", "expected"),
    [
        ("", None),
        ("\n", None),
        ("   \n  \n", None),
        ("\n20:53:56 Task has been received.", "20:53:56 Task has been received."),
        (DONE_PROGRESS_MSG, "20:54:09 Page(1~2): Task done (11.20s)"),
    ],
)
def test_the_last_message_is_the_last_non_empty_line(
    progress_msg: str, expected: str | None
) -> None:
    """`progress_msg` s'ouvre sur un saut de ligne et accumule : seule la fin intéresse."""
    assert last_progress_line(progress_msg) == expected


# ─── `normalize_chunk_whitespace` (double espaces, Phase 13) ────────────────


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("un  texte  espacé", "un texte espacé"),
        ("triple   espace", "triple espace"),
        ("tabulation\t\tdouble", "tabulation double"),
        ("mix\t \tespace-tab", "mix espace-tab"),
        ("déjà propre, un seul espace", "déjà propre, un seul espace"),
        ("", ""),
    ],
)
def test_repeated_spaces_and_tabs_collapse_to_one(content: str, expected: str) -> None:
    """Le cœur du correctif : la Phase 13 a mesuré jusqu'à 97,6 % d'espacements doublés."""
    assert normalize_chunk_whitespace(content) == expected


def test_newlines_are_never_touched() -> None:
    """La mise en forme verticale (paragraphes, listes) doit rester intacte."""
    content = "premier paragraphe\n\ndeuxième  paragraphe\nligne  suivante"
    assert normalize_chunk_whitespace(content) == (
        "premier paragraphe\n\ndeuxième paragraphe\nligne suivante"
    )


@pytest.mark.parametrize(
    "content",
    [
        "<table><tr><td>a  b</td></tr></table>",
        "<TABLE><TR><TD>a  b</TD></TR></TABLE>",
        "texte avant  un <table>a   b</table> texte  après",
    ],
)
def test_table_like_content_is_left_untouched(content: str) -> None:
    """L'espacement dans un tableau HTML peut porter du sens visuel/alignement —
    diagnostic précédent, exclu délibérément. Détection large : `<table` suffit
    à désactiver la normalisation pour tout le fragment, y compris le texte
    hors tableau qui l'entoure."""
    assert normalize_chunk_whitespace(content) == content


# ─── `get_parse_status` ──────────────────────────────────────────────────────


def document_payload(**overrides: JsonValue) -> httpx.Response:
    """Réponse de `GET /datasets/{id}/documents?id=…`, dans sa forme réelle."""
    doc: dict[str, JsonValue] = {
        "id": DOCUMENT.external_id,
        "run": "RUNNING",
        "progress": 0.0410019,
        "progress_msg": "",
        "chunk_count": 0,
    }
    doc.update(overrides)
    return ok({"total": 1, "docs": [doc]})


@pytest.mark.parametrize(
    ("engine_run", "expected"),
    [
        ("UNSTART", "unstart"),
        ("RUNNING", "running"),
        ("CANCEL", "cancel"),
        ("DONE", "done"),
        ("FAIL", "fail"),
    ],
)
async def test_engine_states_are_lowered_to_the_port_vocabulary(
    engine_run: str, expected: str
) -> None:
    """RAGFlow répond en majuscules ; le port expose des minuscules."""
    adapter, _ = build([document_payload(run=engine_run)])

    status = await adapter.get_parse_status(DATASET, DOCUMENT)

    assert status.run == expected


async def test_an_unknown_engine_state_is_refused_rather_than_projected() -> None:
    """`SCHEDULE` n'a jamais été observé (§7) : le rencontrer doit se voir, pas se deviner."""
    adapter, _ = build([document_payload(run="SCHEDULE")])

    with pytest.raises(EngineError) as raised:
        await adapter.get_parse_status(DATASET, DOCUMENT)

    assert "SCHEDULE" in str(raised.value)


async def test_the_status_request_filters_on_the_document_id() -> None:
    """Aucun endpoint de métadonnées par identifiant n'existe : la liste filtrée en tient lieu."""
    adapter, recorder = build([document_payload()])

    await adapter.get_parse_status(DATASET, DOCUMENT)

    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/api/v1/datasets/{DATASET.external_id}/documents"
    assert request.url.params["id"] == DOCUMENT.external_id


async def test_a_stuck_document_is_reported_without_a_phase() -> None:
    """Le bug de blocage vu en audit : `RUNNING`, `progress=0.0`, journal vide.

    C'est ce triplet que la veille (§5) doit pouvoir reconnaître ; l'adaptateur
    ne doit surtout pas le maquiller en `chunking`.
    """
    adapter, _ = build([document_payload(run="RUNNING", progress=0.0, progress_msg="")])

    status = await adapter.get_parse_status(DATASET, DOCUMENT)

    assert status.run == "running"
    assert status.inferred_phase is None
    assert status.progress == 0.0
    assert status.last_message is None
    assert status.error is None


async def test_a_finished_document_carries_its_chunks_and_no_error() -> None:
    """Cas nominal, relevé tel quel sur une instance réelle."""
    adapter, _ = build(
        [document_payload(run="DONE", progress=1.0, progress_msg=DONE_PROGRESS_MSG, chunk_count=15)]
    )

    status = await adapter.get_parse_status(DATASET, DOCUMENT)

    assert status.run == "done"
    assert status.inferred_phase is JobState.COMPLETED
    assert status.chunk_count == 15
    assert status.last_message == "20:54:09 Page(1~2): Task done (11.20s)"
    assert status.error is None


async def test_an_error_is_reported_only_on_a_failed_run() -> None:
    """`error` non vide seulement si `run == "fail"` — le contrat du port l'exige."""
    failed, _ = build([document_payload(run="FAIL", progress=-1.0, progress_msg="\nboom")])
    status = await failed.get_parse_status(DATASET, DOCUMENT)
    assert status.error == "boom"

    running, _ = build([document_payload(run="RUNNING", progress_msg="\nboom")])
    assert (await running.get_parse_status(DATASET, DOCUMENT)).error is None


async def test_an_out_of_range_progress_is_clamped_rather_than_fatal() -> None:
    """Une future version qui déborderait ne doit pas faire tomber tout le polling."""
    adapter, _ = build([document_payload(progress=7.5)])

    assert (await adapter.get_parse_status(DATASET, DOCUMENT)).progress == 1.0


async def test_an_empty_document_list_is_an_explicit_error() -> None:
    """Document absent du dataset : on le dit plutôt que de renvoyer un état fabriqué."""
    adapter, _ = build([ok({"total": 0, "docs": []})])

    with pytest.raises(EngineError) as raised:
        await adapter.get_parse_status(DATASET, DOCUMENT)

    assert DOCUMENT.external_id in str(raised.value)


# ─── `ensure_dataset` ────────────────────────────────────────────────────────


def test_the_dataset_name_is_derived_from_the_uuid_alone() -> None:
    """Nommer par UUID rend la création idempotente sans vérifier l'unicité."""
    assert dataset_name_for(KB_UUID) == f"{DATASET_NAME_PREFIX}{KB_UUID}"


async def test_ensure_dataset_names_from_the_uuid_never_from_a_readable_name() -> None:
    """Un nom dupliqué n'est pas une erreur côté RAGFlow : il est renommé en silence.

    Le port n'expose donc **aucun** paramètre de nom lisible, et le corps envoyé
    ne doit contenir que le nom dérivé de l'UUID — jamais le libellé humain de la
    base de connaissances, quel qu'il soit.
    """
    adapter, recorder = build([ok({"id": "ds-neuf"})])

    ref = await adapter.ensure_dataset(
        ref=None,
        name_seed=KB_UUID,
        # Identifiant nu, tel que le porte `KnowledgeBase.embedding_model_id` —
        # c'est à l'adaptateur d'ajouter `@<instance>@HuggingFace`, jamais à
        # l'appelant (fait vérifié en conditions réelles sous Xinference,
        # ADR-011 : `POST /datasets` refuse le nom nu ; forme à 3 segments
        # depuis ADR-051, deux instances actives sous le même fournisseur).
        embedding_provider_id="bge-m3",
        reranker_provider_id="bge-reranker-v2-m3",
    )

    assert ref == EngineDatasetRef(external_id="ds-neuf")
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets"
    body = request.content.decode()
    assert f'"{DATASET_NAME_PREFIX}{KB_UUID}"' in body
    assert "bge-m3@ragengine-inference-embedding@HuggingFace" in body
    # Un nom lisible de base de connaissances ne doit jamais atteindre le moteur.
    for readable in ("Base RH", "documentation-interne", "Ma base"):
        assert readable not in body


async def test_ensure_dataset_reuses_a_known_reference_without_any_call() -> None:
    """Reprise après panne : la référence connue suffit, aucun doublon n'est créé."""
    adapter = exploding()

    ref = await adapter.ensure_dataset(
        ref=DATASET,
        name_seed=KB_UUID,
        embedding_provider_id="bge-m3",
        reranker_provider_id=None,
    )

    assert ref is DATASET


async def test_a_dataset_creation_without_an_id_is_an_error() -> None:
    """Réponse hors contrat : on échoue plutôt que de fabriquer une référence vide."""
    adapter, _ = build([ok({"name": "sans identifiant"})])

    with pytest.raises(EngineError):
        await adapter.ensure_dataset(
            ref=None,
            name_seed=KB_UUID,
            embedding_provider_id="bge-m3",
            reranker_provider_id=None,
        )


# ─── Téléversement, lancement, annulation ────────────────────────────────────


async def test_upload_document_posts_the_file_and_returns_its_reference(
    no_real_sleep: list[float],
) -> None:
    """Multipart, l'identifiant vient du premier élément renvoyé, la taille stockée
    est confirmée."""
    content = b"contenu"
    adapter, recorder = build(
        [
            ok([{"id": "doc-neuf", "run": "UNSTART"}]),
            document_payload(id="doc-neuf", run="UNSTART", size=len(content)),
        ]
    )

    ref = await adapter.upload_document(DATASET, filename="note.txt", content=content)

    assert ref == EngineDocumentRef(external_id="doc-neuf")
    request = recorder.requests[0]
    assert request.url.path == f"/api/v1/datasets/{DATASET.external_id}/documents"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"note.txt" in request.content
    assert b"contenu" in request.content
    assert no_real_sleep == []


async def test_an_upload_returning_nothing_is_an_error() -> None:
    """Sans document en retour, il n'y a rien à suivre : autant le dire tout de suite."""
    adapter, _ = build([ok([])])

    with pytest.raises(EngineError):
        await adapter.upload_document(DATASET, filename="note.txt", content=b"x")


async def test_upload_document_retries_and_cleans_up_on_a_corrupted_upload(
    no_real_sleep: list[float],
) -> None:
    """ADR-044 §5.1 : taille stockée ≠ taille envoyée ⇒ le document corrompu est supprimé
    avant nouvel essai, pour ne jamais laisser un doublon derrière un rejeu."""
    content = b"contenu"
    adapter, recorder = build(
        [
            ok([{"id": "doc-corrompu", "run": "UNSTART"}]),
            document_payload(id="doc-corrompu", run="UNSTART", size=len(content) + 2),
            ok({"deleted": 1}),
            ok([{"id": "doc-propre", "run": "UNSTART"}]),
            document_payload(id="doc-propre", run="UNSTART", size=len(content)),
        ]
    )

    ref = await adapter.upload_document(DATASET, filename="note.txt", content=content)

    assert ref == EngineDocumentRef(external_id="doc-propre")
    assert [(request.method, request.url.path) for request in recorder.requests] == [
        ("POST", f"/api/v1/datasets/{DATASET.external_id}/documents"),
        ("GET", f"/api/v1/datasets/{DATASET.external_id}/documents"),
        ("DELETE", f"/api/v1/datasets/{DATASET.external_id}/documents"),
        ("POST", f"/api/v1/datasets/{DATASET.external_id}/documents"),
        ("GET", f"/api/v1/datasets/{DATASET.external_id}/documents"),
    ]
    assert b"doc-corrompu" in recorder.requests[2].content
    assert no_real_sleep == [UPLOAD_RETRY_BACKOFF_SECONDS[0]]


async def test_upload_document_gives_up_after_the_attempt_budget(
    no_real_sleep: list[float],
) -> None:
    """Corruption persistante : abandon borné en `EngineTransientError`, jamais une
    boucle sans fin."""
    content = b"contenu"
    responses: list[httpx.Response] = []
    for index in range(UPLOAD_INTEGRITY_MAX_ATTEMPTS):
        doc_id = f"doc-{index}"
        responses.append(ok([{"id": doc_id, "run": "UNSTART"}]))
        responses.append(document_payload(id=doc_id, run="UNSTART", size=len(content) + 2))
        responses.append(ok({"deleted": 1}))
    adapter, recorder = build(responses)

    with pytest.raises(EngineTransientError):
        await adapter.upload_document(DATASET, filename="note.txt", content=content)

    documents_path = f"/api/v1/datasets/{DATASET.external_id}/documents"
    posts = [r for r in recorder.requests if r.method == "POST" and r.url.path == documents_path]
    deletes = [r for r in recorder.requests if r.method == "DELETE"]
    assert len(posts) == UPLOAD_INTEGRITY_MAX_ATTEMPTS
    assert len(deletes) == UPLOAD_INTEGRITY_MAX_ATTEMPTS
    assert no_real_sleep == list(UPLOAD_RETRY_BACKOFF_SECONDS)


async def test_upload_document_accepts_when_the_engine_does_not_report_a_size(
    no_real_sleep: list[float],
) -> None:
    """`size` absent de la réponse : vérification désactivée, pas un échec de l'upload.

    Une version de RAGFlow qui cesserait de rendre ce champ ne doit pas casser
    tout téléversement — cf. la docstring de `_RagflowDocument.size`.
    """
    adapter, recorder = build(
        [
            ok([{"id": "doc-neuf", "run": "UNSTART"}]),
            document_payload(id="doc-neuf", run="UNSTART"),
        ]
    )

    ref = await adapter.upload_document(DATASET, filename="note.txt", content=b"contenu")

    assert ref == EngineDocumentRef(external_id="doc-neuf")
    assert len(recorder.requests) == 2
    assert no_real_sleep == []


async def test_upload_document_retries_when_the_integrity_check_itself_is_transient(
    no_real_sleep: list[float],
) -> None:
    """Une panne pendant la relecture de la taille n'est jamais confondue avec un envoi intact."""
    content = b"contenu"
    adapter, _recorder = build(
        [
            ok([{"id": "doc-1", "run": "UNSTART"}]),
            business_error(100, "exception serveur générique pendant la relecture"),
            ok({"deleted": 1}),
            ok([{"id": "doc-2", "run": "UNSTART"}]),
            document_payload(id="doc-2", run="UNSTART", size=len(content)),
        ]
    )

    ref = await adapter.upload_document(DATASET, filename="note.txt", content=content)

    assert ref == EngineDocumentRef(external_id="doc-2")
    assert no_real_sleep == [UPLOAD_RETRY_BACKOFF_SECONDS[0]]


async def test_upload_document_retries_even_if_cleaning_up_the_corrupted_document_fails(
    no_real_sleep: list[float],
) -> None:
    """Le nettoyage est best-effort : son échec ne doit pas bloquer la nouvelle tentative."""
    content = b"contenu"
    adapter, _recorder = build(
        [
            ok([{"id": "doc-corrompu", "run": "UNSTART"}]),
            document_payload(id="doc-corrompu", run="UNSTART", size=len(content) + 2),
            business_error(102, "document introuvable pour le nettoyage"),
            ok([{"id": "doc-propre", "run": "UNSTART"}]),
            document_payload(id="doc-propre", run="UNSTART", size=len(content)),
        ]
    )

    ref = await adapter.upload_document(DATASET, filename="note.txt", content=content)

    assert ref == EngineDocumentRef(external_id="doc-propre")


async def test_start_parsing_applies_the_profile_before_launching() -> None:
    """Deux appels dans cet ordre : sans le PATCH, le moteur garderait le template du dataset."""
    adapter, recorder = build([ok({}), ok({})])

    await adapter.start_parsing(
        DATASET,
        DOCUMENT,
        ChunkingProfile(chunk_method="table", parser_config={"layout_recognize": "DeepDOC"}),
    )

    patch, launch = recorder.requests
    assert patch.method == "PATCH"
    assert patch.url.path == (
        f"/api/v1/datasets/{DATASET.external_id}/documents/{DOCUMENT.external_id}"
    )
    assert b'"chunk_method":"table"' in patch.content.replace(b" ", b"")
    assert b"layout_recognize" in patch.content

    assert launch.method == "POST"
    assert launch.url.path == f"/api/v1/datasets/{DATASET.external_id}/chunks"
    assert DOCUMENT.external_id.encode() in launch.content


async def test_start_parsing_omits_an_empty_parser_config() -> None:
    """Rien d'imposé au parseur quand le profil n'a rien à dire."""
    adapter, recorder = build([ok({}), ok({})])

    await adapter.start_parsing(DATASET, DOCUMENT, ChunkingProfile())

    assert b"parser_config" not in recorder.requests[0].content


async def test_a_refused_profile_stops_before_launching() -> None:
    """`code=102` sur le PATCH : on ne lance surtout pas le parsing derrière."""
    adapter, recorder = build([business_error(102, "`chunk_method` general doesn't exist")])

    with pytest.raises(EngineError):
        await adapter.start_parsing(DATASET, DOCUMENT, ChunkingProfile(chunk_method="naive"))

    assert len(recorder.requests) == 1


async def test_cancel_parsing_calls_the_stop_endpoint() -> None:
    """Le geste dont dépend la veille anti-blocage (ADR-012, décision 2)."""
    adapter, recorder = build([ok({})])

    await adapter.cancel_parsing(DATASET, DOCUMENT)

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/api/v1/datasets/{DATASET.external_id}/documents/stop"
    assert DOCUMENT.external_id.encode() in request.content


async def test_delete_document_targets_the_dataset_collection() -> None:
    """Forme relevée sur l'instance réelle : `DELETE /datasets/{id}/documents`, `{"ids": [...]}`.

    Le corps de réponse est celui observé en sonde — `{"deleted": 1}` — et non
    le `{"success_count": …}` de la suppression de dataset : deux endpoints
    voisins, deux enveloppes différentes.
    """
    adapter, recorder = build([ok({"deleted": 1})])

    await adapter.delete_document(DATASET, DOCUMENT)

    request = recorder.requests[0]
    assert request.method == "DELETE"
    assert request.url.path == f"/api/v1/datasets/{DATASET.external_id}/documents"
    assert DOCUMENT.external_id.encode() in request.content


async def test_deleting_an_unknown_document_raises_the_generic_engine_error() -> None:
    """Réponse réelle d'un rejeu : `code=102`, sans distinction entre absent et interdit."""
    adapter, _ = build(
        [
            business_error(
                102,
                f"These documents do not belong to dataset {DATASET.external_id} "
                f"or Document not found: {DOCUMENT.external_id}",
            )
        ]
    )

    with pytest.raises(EngineError) as excinfo:
        await adapter.delete_document(DATASET, DOCUMENT)

    # Ni validation ni transitoire : rejouer donnerait exactement le même refus.
    assert not isinstance(excinfo.value, EngineTransientError | EngineValidationError)


async def test_delete_dataset_sends_the_identifier_in_a_list() -> None:
    """La suppression RAGFlow est une opération de masse, même pour un seul dataset."""
    adapter, recorder = build([ok({"success_count": 1})])

    await adapter.delete_dataset(DATASET)

    request = recorder.requests[0]
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/datasets"
    assert DATASET.external_id.encode() in request.content


# ─── `search` ────────────────────────────────────────────────────────────────


def search_payload(positions: object | None = None) -> httpx.Response:
    """Réponse de `POST /retrieval` réduite aux champs consommés, forme réelle.

    `positions` reproduit la forme relevée sur l'instance déployée : des
    quintuplets `[page, x0, x1, haut, bas]`, ici deux rectangles à cheval sur
    les pages 13 et 14 — le cas courant, un fragment ne s'arrêtant pas au bord
    d'une page.
    """
    chunk: dict[str, object] = {
        "id": "f97dc8f6780bdc5d",
        "document_id": DOCUMENT.external_id,
        "dataset_id": DATASET.external_id,
        "content": "Le teletravail est autorise jusqu'a trois jours par semaine.",
        "similarity": 0.4677896615622995,
        "vector_similarity": 0.8536679,
        "term_similarity": 0.30241327366042786,
        "content_ltks": "ignoré",
        "positions": [[13, 57, 372, 60, 75], [14, 54, 540, 400, 461]]
        if positions is None
        else positions,
    }
    return ok({"total": 1, "chunks": [chunk]})


async def test_search_maps_the_engine_fields_onto_the_port() -> None:
    """`id` → `chunk_id`, `document_id` → `document_external_id`, scores rendus tels quels."""
    adapter, _ = build([search_payload()])

    results = await adapter.search(
        [DATASET],
        query="teletravail",
        top_k=5,
        rerank_provider_id=None,
        similarity_threshold=None,
    )

    assert len(results) == 1
    assert results[0].chunk_id == "f97dc8f6780bdc5d"
    assert results[0].document_external_id == DOCUMENT.external_id
    assert results[0].similarity == 0.4677896615622995
    assert results[0].vector_similarity == 0.8536679
    assert results[0].term_similarity == 0.30241327366042786


async def test_search_normalizes_repeated_spaces_in_content() -> None:
    """Le chemin recherche (Playground) rend un contenu sans double espace (Phase 13)."""
    adapter, _ = build(
        [
            ok(
                {
                    "total": 1,
                    "chunks": [
                        {
                            "id": "f97dc8f6780bdc5d",
                            "document_id": DOCUMENT.external_id,
                            "content": "Le  télétravail  est  autorisé.",
                            "similarity": 0.5,
                            "vector_similarity": 0.5,
                            "term_similarity": 0.5,
                            "positions": [],
                        }
                    ],
                }
            )
        ]
    )

    results = await adapter.search(
        [DATASET], query="télétravail", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert results[0].content == "Le télétravail est autorisé."


async def test_search_leaves_table_content_untouched() -> None:
    """Un fragment de type tableau n'est pas normalisé, même sur le chemin recherche."""
    table_content = "<table><tr><td>a  b</td></tr></table>"
    adapter, _ = build(
        [
            ok(
                {
                    "total": 1,
                    "chunks": [
                        {
                            "id": "c1",
                            "document_id": DOCUMENT.external_id,
                            "content": table_content,
                            "positions": [],
                        }
                    ],
                }
            )
        ]
    )

    results = await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert results[0].content == table_content


async def test_search_exposes_the_page_span_a_chunk_covers() -> None:
    """`positions` → intervalle de pages : un fragment déborde souvent sur la suivante."""
    adapter, _ = build([search_payload()])

    results = await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert (results[0].page_start, results[0].page_end) == (13, 14)


async def test_a_chunk_confined_to_one_page_reports_that_page_twice() -> None:
    """Pas de `page_end` nul dans ce cas : l'intervalle reste un intervalle, fermé sur lui-même."""
    adapter, _ = build([search_payload(positions=[[7, 54, 521, 100, 440]])])

    results = await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert (results[0].page_start, results[0].page_end) == (7, 7)


@pytest.mark.parametrize(
    ("positions", "reason"),
    [
        ([], "champ vide"),
        ([[1, 0, 0, 0, 0]], "boîte d'aire nulle — rang de fragment Word, pas une page"),
        ([[5, 4, 4, 4, 4]], "même ersatz, cinquième fragment d'un document sans pages"),
        ([[3, 10, 2, 40, 80]], "largeur négative : boîte incohérente"),
        ([[3, 10, 40, 80, 20]], "hauteur négative : boîte incohérente"),
        ([[0, 10, 40, 20, 80]], "page 0 : hors de la numérotation 1-based"),
        ([[2, 10, 40]], "quintuplet tronqué"),
    ],
)
async def test_an_unreliable_position_yields_no_page_rather_than_a_wrong_one(
    positions: object, reason: str
) -> None:
    """Aucune page vaut mieux qu'une fausse : les formats sans pagination en rendent une.

    Le cas décisif est `[[1, 0, 0, 0, 0]]`, relevé tel quel sur un document Word
    réel : RAGFlow y range le **rang** du fragment, avec une boîte d'aire nulle.
    Le prendre pour une page afficherait « page 1 » sous chaque premier
    paragraphe d'un document qui n'a pas de pages.
    """
    adapter, _ = build([search_payload(positions=positions)])

    results = await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert results[0].page_start is None, reason
    assert results[0].page_end is None, reason


async def test_a_response_without_the_positions_field_still_parses() -> None:
    """Le champ n'est pas contractuel : son absence ne doit pas casser la recherche."""
    adapter, _ = build(
        [
            ok(
                {
                    "total": 1,
                    "chunks": [{"id": "c1", "document_id": DOCUMENT.external_id, "content": "x"}],
                }
            )
        ]
    )

    results = await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    assert results[0].page_start is None


async def test_top_k_drives_page_size_not_the_engine_candidate_pool() -> None:
    """Confondre les deux plafonnerait silencieusement les résultats à 30."""
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET], query="q", top_k=7, rerank_provider_id=None, similarity_threshold=None
    )

    body = recorder.requests[0].content.replace(b" ", b"")
    assert b'"page_size":7' in body
    assert b'"top_k"' not in body


async def test_no_similarity_threshold_is_sent_when_the_caller_gives_none() -> None:
    """Aucun défaut caché : `None` laisse le moteur appliquer le sien."""
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    body = recorder.requests[0].content
    assert b"similarity_threshold" not in body
    assert b"rerank_id" not in body


@pytest.mark.parametrize("threshold", [0.0, 0.05, 0.2, 1.0])
async def test_an_explicit_threshold_is_transmitted_verbatim(threshold: float) -> None:
    """Y compris `0.0` : c'est justement la valeur qui sauve une recherche avec reranking."""
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        # Nu, tel que porté par `KnowledgeBase.reranker_model_id` — c'est
        # l'adaptateur qui doit lui appliquer le suffixe d'instance, jamais
        # l'appelant.
        rerank_provider_id="bge-reranker-v2-m3",
        similarity_threshold=threshold,
    )

    body = recorder.requests[0].content.replace(b" ", b"")
    assert f'"similarity_threshold":{threshold}'.encode() in body
    assert b"bge-reranker-v2-m3@ragengine-inference-reranker@HuggingFace" in body


async def test_the_rerank_provider_id_is_suffixed_by_the_adapter() -> None:
    """Le défaut corrigé : un `rerank_id` nu est refusé par RAGFlow (fait vérifié).

    `POST /retrieval` avec `rerank_id="bge-reranker-v2-m3"` répond
    `LookupError('Provider  not found for model bge-reranker-v2-m3.')` ; seul un
    identifiant suffixé est accepté. Depuis ADR-051 (TEI, deux instances actives
    sous le même fournisseur `HuggingFace`), la forme à 3 segments
    `<modèle>@<instance>@HuggingFace` désigne l'instance sans ambiguïté — la
    forme courte à 2 segments (encore valide sous Xinference, ADR-011, qui
    n'avait qu'une instance) échouerait désormais. Le suffixe doit être ajouté
    par l'adaptateur, exactement comme pour `embedding_model`.
    """
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id="bge-reranker-v2-m3",
        similarity_threshold=None,
    )

    body = recorder.requests[0].content
    assert (
        b'"rerank_id":"bge-reranker-v2-m3@ragengine-inference-reranker@HuggingFace"'
        in body.replace(b" ", b"")
    )


async def test_the_candidate_pool_is_capped_only_when_reranking() -> None:
    """Incident réel du 2026-09-06 : sans plafond, RAGFlow soumet jusqu'à 1024
    candidats en un seul lot au reranker externe — sur des passages de longueur
    réelle, l'allocation CUDA correspondante dépasse le budget de 12 Go
    (`CUDA out of memory`, 6,43 Gio demandés pour un seul lot). Le `top_k`
    RAGFlow (distinct de celui de RAGEngine, cf. docstring de `search`) est
    l'unique levier qui le borne.
    """
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id="bge-reranker-v2-m3",
        similarity_threshold=None,
        rerank_candidate_pool_size=128,
    )

    body = json.loads(recorder.requests[0].content)
    assert body["top_k"] == 128
    # `page_size` (sortie RAGEngine) et `top_k` (vivier RAGFlow) sont deux
    # champs distincts — le premier ne doit jamais se confondre avec le second.
    assert body["page_size"] == 5


async def test_the_candidate_pool_is_never_sent_without_reranking() -> None:
    """Sans reranker, RAGFlow n'a rien à plafonner : un `top_k` fantôme
    tronquerait silencieusement le vivier de la recherche vectorielle elle-même
    (défaut RAGFlow 1024), pas seulement celui du reranking.
    """
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id=None,
        similarity_threshold=None,
        rerank_candidate_pool_size=128,
    )

    body = json.loads(recorder.requests[0].content)
    assert "top_k" not in body


async def test_document_refs_become_document_ids_in_the_payload() -> None:
    """Le filtre par documents (fait vérifié) part dans la requête `/retrieval` elle-même."""
    other = EngineDocumentRef(external_id="doc-9999")
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id=None,
        similarity_threshold=None,
        document_refs=[DOCUMENT, other],
    )

    body = recorder.requests[0].content
    assert DOCUMENT.external_id.encode() in body
    assert other.external_id.encode() in body
    assert b'"document_ids"' in body


async def test_no_document_refs_key_when_the_caller_gives_none() -> None:
    """`None` ne filtre rien : aucune clé `document_ids` n'est envoyée."""
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id=None,
        similarity_threshold=None,
        document_refs=None,
    )

    assert b"document_ids" not in recorder.requests[0].content


async def test_an_empty_document_refs_short_circuits_without_a_call() -> None:
    """Symétrique du dataset vide : rien à chercher, on ne dérange pas le moteur."""
    assert (
        await exploding().search(
            [DATASET],
            query="q",
            top_k=5,
            rerank_provider_id=None,
            similarity_threshold=None,
            document_refs=[],
        )
        == []
    )


async def test_search_over_several_datasets_sends_them_all() -> None:
    """La recherche multi-bases est native côté moteur, pas assemblée ici."""
    other = EngineDatasetRef(external_id="ds-9999")
    adapter, recorder = build([search_payload()])

    await adapter.search(
        [DATASET, other], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
    )

    body = recorder.requests[0].content
    assert DATASET.external_id.encode() in body
    assert other.external_id.encode() in body


async def test_searching_no_dataset_short_circuits_without_a_call() -> None:
    """Aucune base à interroger : la réponse est vide, sans déranger le moteur."""
    assert (
        await exploding().search(
            [], query="q", top_k=5, rerank_provider_id=None, similarity_threshold=None
        )
        == []
    )


@pytest.mark.parametrize("top_k", [0, -1, MAX_SEARCH_RESULTS + 1, 1000])
async def test_an_out_of_bounds_top_k_is_refused_before_any_call(top_k: int) -> None:
    """Au-delà du plafond, RAGFlow lève hors de son bloc de garde : on refuse localement."""
    with pytest.raises(EngineValidationError) as raised:
        await exploding().search(
            [DATASET],
            query="q",
            top_k=top_k,
            rerank_provider_id=None,
            similarity_threshold=None,
        )

    assert str(MAX_SEARCH_RESULTS) in str(raised.value)


async def test_a_search_that_returns_nothing_is_not_an_error() -> None:
    """Le piège du reranking : les résultats se vident **sans** erreur (fait vérifié)."""
    adapter, _ = build([ok({"total": 0, "chunks": [], "doc_aggs": []})])

    results = await adapter.search(
        [DATASET],
        query="q",
        top_k=5,
        rerank_provider_id="bge-reranker-v2-m3",
        similarity_threshold=0.2,
    )

    assert results == []


# ─── Construction ────────────────────────────────────────────────────────────


# ─── `list_document_chunks` (Phase 8, génération d'évaluation) ──────────────


def chunk_list_payload(**overrides: JsonValue) -> httpx.Response:
    """Réponse de `GET /datasets/{id}/documents/{doc}/chunks`, forme réelle relevée en Phase 8."""
    body: dict[str, JsonValue] = {
        "chunks": [
            {
                "id": "488c773b19bf0338",
                "content": "Un extrait du document.",
                "document_id": DOCUMENT.external_id,
                "dataset_id": DATASET.external_id,
                "docnm_kwd": "politique.pdf",
                "image_id": f"{DATASET.external_id}-488c773b19bf0338",
                "important_keywords": [],
                "positions": [[1, 60, 483, 53, 84]],
                "questions": [],
                "available": True,
                "tag_kwd": [],
            }
        ],
        "doc": {"id": DOCUMENT.external_id, "chunk_count": 1},
        "total": 1,
    }
    body.update(overrides)
    return ok(body)


async def test_list_document_chunks_maps_id_and_content() -> None:
    """`id` → `chunk_id`, `content`, et les champs ajoutés en Phase 13 (fragments UI)."""
    adapter, recorder = build([chunk_list_payload()])

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert len(page.chunks) == 1
    chunk = page.chunks[0]
    assert chunk.chunk_id == "488c773b19bf0338"
    assert chunk.content == "Un extrait du document."
    assert chunk.important_keywords == []
    assert chunk.questions == []
    assert chunk.available is True
    assert chunk.positions == [EngineChunkPosition(page=1, x0=60, x1=483, top=53, bottom=84)]
    assert page.document == EngineDocumentChunksMeta(
        chunk_method=None, chunk_count=1, token_count=0
    )
    assert page.total == 1
    assert page.page == 1
    assert page.page_size == 20
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == (
        f"/api/v1/datasets/{DATASET.external_id}/documents/{DOCUMENT.external_id}/chunks"
    )
    assert request.url.params["page_size"] == "20"
    assert request.url.params["page"] == "1"


async def test_list_document_chunks_normalizes_repeated_spaces_in_content() -> None:
    """Le panneau des fragments, le garde-fou qualité et la génération d'évaluation
    reçoivent un contenu sans double espace (Phase 13)."""
    adapter, _ = build(
        [chunk_list_payload(chunks=[{"id": "c1", "content": "Un  extrait   du  document."}])]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert page.chunks[0].content == "Un extrait du document."


async def test_list_document_chunks_leaves_table_content_untouched() -> None:
    """Un fragment de type tableau n'est pas normalisé non plus sur ce chemin."""
    table_content = "<table><tr><td>a  b</td></tr></table>"
    adapter, _ = build([chunk_list_payload(chunks=[{"id": "c1", "content": table_content}])])

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert page.chunks[0].content == table_content


async def test_list_document_chunks_reorders_by_page_and_top() -> None:
    """Le moteur peut rendre les fragments dans le désordre (Phase 13, fait établi) :
    l'adaptateur retrie par position réelle plutôt que de faire confiance à l'ordre
    de la réponse."""
    adapter, _ = build(
        [
            chunk_list_payload(
                chunks=[
                    {
                        "id": "third",
                        "content": "Troisième.",
                        "positions": [[2, 60, 483, 200, 230]],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                    {
                        "id": "first",
                        "content": "Premier.",
                        "positions": [[1, 60, 483, 53, 84]],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                    {
                        "id": "second",
                        "content": "Deuxième.",
                        "positions": [[1, 60, 483, 300, 330]],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                ],
                doc={"id": DOCUMENT.external_id, "chunk_count": 3},
                total=3,
            )
        ]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert [chunk.chunk_id for chunk in page.chunks] == ["first", "second", "third"]


async def test_list_document_chunks_without_positions_keeps_engine_order() -> None:
    """`positions` vide (systématique sous `layout_recognize: Plain Text`, fait établi
    Phase 13) : aucun signal fiable, l'adaptateur n'invente pas d'ordre — il conserve
    celui rendu par le moteur (tri stable)."""
    adapter, _ = build(
        [
            chunk_list_payload(
                chunks=[
                    {
                        "id": "rendu-en-premier",
                        "content": "Rendu en premier par le moteur.",
                        "positions": [],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                    {
                        "id": "rendu-en-second",
                        "content": "Rendu en second par le moteur.",
                        "positions": [],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                ],
                doc={"id": DOCUMENT.external_id, "chunk_count": 2},
                total=2,
            )
        ]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert [chunk.chunk_id for chunk in page.chunks] == [
        "rendu-en-premier",
        "rendu-en-second",
    ]


async def test_list_document_chunks_positions_sort_before_positionless_ones() -> None:
    """Un fragment positionné passe avant tout fragment sans position fiable, même si
    le moteur le rend après — cf. docstring de `_document_order_key`."""
    adapter, _ = build(
        [
            chunk_list_payload(
                chunks=[
                    {
                        "id": "sans-position",
                        "content": "Sans position.",
                        "positions": [],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                    {
                        "id": "avec-position",
                        "content": "Avec position.",
                        "positions": [[1, 60, 483, 53, 84]],
                        "important_keywords": [],
                        "questions": [],
                        "available": True,
                    },
                ],
                doc={"id": DOCUMENT.external_id, "chunk_count": 2},
                total=2,
            )
        ]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert [chunk.chunk_id for chunk in page.chunks] == ["avec-position", "sans-position"]


async def test_list_document_chunks_requests_the_page_asked() -> None:
    """`page` est transmis tel quel — c'est ce qui rend la pagination réelle côté UI."""
    adapter, recorder = build([chunk_list_payload()])

    await adapter.list_document_chunks(DATASET, DOCUMENT, page=3, page_size=20)

    assert recorder.requests[0].url.params["page"] == "3"


async def test_list_document_chunks_reads_the_document_metadata() -> None:
    """`doc.chunk_method`/`chunk_count`/`token_count` alimentent `EngineDocumentChunksMeta`."""
    adapter, _ = build(
        [
            chunk_list_payload(
                doc={
                    "id": DOCUMENT.external_id,
                    "chunk_method": "naive",
                    "chunk_count": 42,
                    "token_count": 512,
                },
                total=42,
            )
        ]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert page.document == EngineDocumentChunksMeta(
        chunk_method="naive", chunk_count=42, token_count=512
    )
    assert page.total == 42


async def test_list_document_chunks_on_an_empty_document_is_an_empty_page() -> None:
    """Aucun fragment : page vide, pas une erreur — à l'appelant d'en tirer les conséquences."""
    adapter, _ = build(
        [chunk_list_payload(chunks=[], doc={"id": DOCUMENT.external_id, "chunk_count": 0}, total=0)]
    )

    page = await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert page.chunks == []
    assert page.total == 0


async def test_list_document_chunks_on_an_unknown_document_is_a_generic_engine_error() -> None:
    """`code=102` : même ambiguïté « introuvable/interdit » que le reste de l'adaptateur."""
    adapter, _ = build([business_error(102, "You don't own the document doc-5678.")])

    with pytest.raises(EngineError) as raised:
        await adapter.list_document_chunks(DATASET, DOCUMENT, page_size=20)

    assert type(raised.value) is EngineError
    assert "doc-5678" in str(raised.value)


def test_the_adapter_is_built_from_settings_alone() -> None:
    """Aucune URL ni aucun secret en dur (CONTRIBUTING.md §6)."""
    settings = Settings(ragflow_api_key=SecretStr("ragflow-clé-de-test"))

    adapter = RagflowEngineAdapter.from_settings(settings)

    assert isinstance(adapter, RagflowEngineAdapter)


def test_a_missing_api_key_fails_at_construction_not_at_the_first_call() -> None:
    """Sans clé, RAGFlow répondrait `code=102` : la vraie cause serait masquée."""
    with pytest.raises(EngineError) as raised:
        RagflowEngineAdapter.from_settings(Settings(ragflow_api_key=None))

    assert "bootstrap_ragflow" in str(raised.value)


async def test_the_self_built_client_carries_the_bearer_token() -> None:
    """La clé est portée par le client, pas recollée à chaque appel."""
    adapter = RagflowEngineAdapter(base_url=f"{BASE_URL}/", api_key="ragflow-abc")

    client = adapter._http_client()
    try:
        assert client.headers["Authorization"] == "Bearer ragflow-abc"
        # La barre oblique finale est ajoutée par `httpx` ; la garde utile ici
        # est qu'aucune barre double ne subsiste malgré l'URL passée avec `/`.
        assert str(client.base_url).rstrip("/") == API_BASE
    finally:
        await adapter.aclose()
