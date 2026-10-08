#!/usr/bin/env python3
"""Amorçage du moteur RAGFlow — idempotent, à lancer après `docker-compose.ragflow.yml`.

    docker compose --env-file .env -f docker/docker-compose.ragflow.yml up -d
    ./venv/bin/python scripts/bootstrap_ragflow.py        # depuis la racine du dépôt

Trois étapes, chacune sautée si elle est déjà faite :

1. **Compte de service et clé d'API.** Si `RAGFLOW_API_KEY` est renseignée et
   fonctionne (`GET /api/v1/datasets` répond `code=0`), rien n'est touché. Sinon
   le compte `RAGFLOW_BOOTSTRAP_EMAIL` est créé (ou reconnecté s'il existe), et
   une clé d'API est émise puis **affichée**. Elle n'est jamais écrite dans un
   fichier : aucun secret généré ne s'auto-persiste (CONTRIBUTING.md §6). C'est à toi
   de la recopier dans `.env`.
2. **Fournisseur `HuggingFace`.** RAGFlow n'embarque aucun modèle depuis la v0.22 :
   il faut lui désigner les nôtres (ADR-051, remplace le fournisseur `Xinference`
   d'ADR-011). L'enregistrement se fait par API HTTP, sans passer par
   l'interface web, et déclare **deux instances** — un conteneur TEI ne sert
   qu'un seul modèle, contrairement à Xinference qui les hébergeait tous les
   deux sous une même URL — pointant `settings.embedding_inference_base_url` et
   `settings.reranker_inference_base_url` : les URLs telles que **RAGFlow** les
   voit (`http://inference-embedding:80`, `http://inference-reranker:80`,
   les conteneurs partageant le réseau `ragengine`), pas telles que l'hôte les
   voit.
3. **Vision `IMAGE2TEXT` (optionnelle, ADR-027).** Sautée si
   `RAGFLOW_VISION_PROVIDER` n'est pas défini — aucun coût, aucun changement de
   comportement. Si renseignée (`anthropic`, `openai` ou `gemini`), enregistre le
   fournisseur, un modèle `image2text`, et le fixe comme modèle vision par
   défaut du **tenant** — RAGFlow n'expose qu'un seul `Tenant.img2txt_id`,
   vérifié empiriquement : il n'y a pas de portée par base de connaissances
   possible ici, contrairement à `embedding_model_id`/`reranker_model_id` qui
   sont des choix par KB côté RAGEngine. Activer cette étape s'applique donc à
   **toutes** les bases du déploiement à la fois.

Ce script encapsule le seul détail cryptographique de l'intégration : RAGFlow
attend un mot de passe **chiffré RSA côté client**, avec la clé publique de son
image. `IngestionEnginePort` n'en sait rien et n'a pas à en savoir quoi que ce
soit — c'est une étape de déploiement, au même titre qu'une migration Alembic.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import httpx  # noqa: E402  — après l'ajustement de `sys.path` ci-dessus.

from app.core.config import Settings  # noqa: E402
from app.providers.engine.ragflow.client import (  # noqa: E402
    HUGGINGFACE_EMBEDDING_INSTANCE_NAME,
    HUGGINGFACE_PROVIDER,
    HUGGINGFACE_RERANKER_INSTANCE_NAME,
)

RAGFLOW_PUBLIC_KEY_PEM: Final = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/
z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp
2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOO
UEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVK
RNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK
6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs
2wIDAQAB
-----END PUBLIC KEY-----"""
"""Clé **publique** de `conf/public.pem` de l'image `infiniflow/ragflow:v0.26.4`.

Ce n'est pas un secret : RAGFlow embarque littéralement la même valeur dans son
propre client web (`web/src/utils/index.ts`, fonction `rsaPsw`), et la clé
privée correspondante reste dans l'image. La reproduire ici évite d'exiger un
`docker exec` depuis un script d'amorçage. **À revérifier à chaque montée de
version de RAGFlow** : `docker exec ragflow-cpu cat /ragflow/conf/public.pem`.
"""

HUGGINGFACE_API_KEY: Final = "huggingface"
"""Espace réservé : nos conteneurs TEI tournent sans authentification (aucune
option d'API key côté `text-embeddings-inference`, cf. `docker-compose.gpu.
yml`). RAGFlow exige néanmoins un `api_key` non vide pour créer une instance de
fournisseur."""

MAX_TOKENS: Final = 8192
"""Longueur d'entrée de BGE-M3 et de bge-reranker-v2-m3 (cf. `BGE_M3_CAPABILITIES`)."""

VISION_FACTORY_BY_PROVIDER: Final[dict[str, str]] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Gemini",
}
"""Nom de fournisseur RAGFlow (`FACTORY_LLM_INFOS`) pour chaque valeur acceptée
de `RAGFLOW_VISION_PROVIDER`. Les trois existent nativement dans le catalogue
RAGFlow v0.26.4 déployé (`conf/llm_factories.json`), avec des modèles tagués
`IMAGE2TEXT` — confirmé en lisant le conteneur `ragflow-cpu`, pas supposé.
Groq a été évalué et écarté (ADR-027, non supporté par RAGFlow pour la vision,
aucune classe `GroqCV` dans `rag/llm/cv_model.py`) — ne pas l'ajouter ici sans
revérifier que ça a changé côté RAGFlow."""

VISION_INSTANCE_NAME: Final = "ragengine-vision"
"""Nom d'instance dédié à l'enregistrement vision — distinct de toute instance
que l'utilisateur pourrait créer à la main pour le même fournisseur."""

DEFAULT_HOST_PORT: Final = "9390"
"""Port hôte publié par `docker-compose.ragflow.yml` (`RAGENGINE_RAGFLOW_HOST_PORT`)."""

TIMEOUT_SECONDS: Final = 120.0
"""Généreux : créer l'instance de fournisseur déclenche une vraie sonde des
modèles côté RAGFlow, qui charge BGE-M3 sur le GPU si ce n'est pas déjà fait."""


class BootstrapError(RuntimeError):
    """Échec d'amorçage — message destiné à l'opérateur, pas à un client HTTP."""


# ─── Chiffrement RSA du mot de passe (PKCS#1 v1.5, stdlib seule) ─────────────
# Écrit à la main plutôt qu'avec une bibliothèque : `pyproject.toml` ne déclare
# aucune dépendance cryptographique, et en ajouter une pour dix lignes de
# chiffrement RSA à clé publique — une exponentiation modulaire — ne se justifie
# pas. Aucune donnée n'est déchiffrée ni signée ici : la clé privée reste chez
# RAGFlow.


def _read_der_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """Lit un triplet DER (tag, contenu, position suivante) à partir d'`offset`."""
    tag = data[offset]
    length = data[offset + 1]
    cursor = offset + 2
    if length & 0x80:
        length_bytes = length & 0x7F
        length = int.from_bytes(data[cursor : cursor + length_bytes], "big")
        cursor += length_bytes
    return tag, data[cursor : cursor + length], cursor + length


def parse_rsa_public_key(pem: str) -> tuple[int, int]:
    """Extrait `(modulus, exponent)` d'une clé publique PEM au format SubjectPublicKeyInfo.

    Structure attendue : `SEQUENCE { SEQUENCE { OID, NULL }, BIT STRING { SEQUENCE
    { INTEGER n, INTEGER e } } }`.
    """
    body = "".join(line for line in pem.strip().splitlines() if "-----" not in line)
    der = base64.b64decode(body)

    _, outer, _ = _read_der_tlv(der, 0)
    _, _, after_algorithm = _read_der_tlv(outer, 0)
    tag, bit_string, _ = _read_der_tlv(outer, after_algorithm)
    if tag != 0x03:
        raise BootstrapError("Clé publique RAGFlow illisible : BIT STRING attendu.")

    # Le premier octet d'un BIT STRING compte les bits de bourrage inutilisés.
    _, key_sequence, _ = _read_der_tlv(bit_string[1:], 0)
    _, modulus, after_modulus = _read_der_tlv(key_sequence, 0)
    _, exponent, _ = _read_der_tlv(key_sequence, after_modulus)
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def encrypt_password(password: str, pem: str = RAGFLOW_PUBLIC_KEY_PEM) -> str:
    """Chiffre un mot de passe comme le fait le client web de RAGFlow.

    Reproduit `api/utils/crypt.py::crypt` : le mot de passe est d'abord encodé en
    base64, *puis* chiffré en RSA/PKCS#1 v1.5, et le chiffré est rendu en base64.
    L'ordre importe — le serveur déchiffre et attend du base64.
    """
    modulus, exponent = parse_rsa_public_key(pem)
    key_size = (modulus.bit_length() + 7) // 8

    message = base64.b64encode(password.encode("utf-8"))
    if len(message) > key_size - 11:
        raise BootstrapError("Mot de passe trop long pour un chiffrement RSA en un bloc.")

    # Bourrage PKCS#1 v1.5 de type 2 : 0x00 0x02 <octets non nuls> 0x00 <message>.
    padding_length = key_size - len(message) - 3
    padding = bytearray()
    while len(padding) < padding_length:
        padding.extend(byte for byte in secrets.token_bytes(padding_length) if byte)
    encoded = b"\x00\x02" + bytes(padding[:padding_length]) + b"\x00" + message

    cipher = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return base64.b64encode(cipher.to_bytes(key_size, "big")).decode("ascii")


# ─── Client HTTP ─────────────────────────────────────────────────────────────


class RagflowBootstrapClient:
    """Client minimal de l'API RAGFlow, qui lit `code` avant le statut HTTP.

    Même règle que l'adaptateur (`app/providers/engine/ragflow/client.py`) :
    RAGFlow répond `200 OK` y compris sur échec métier, le seul indicateur
    fiable est le champ `code` du corps.
    """

    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/api/v1", timeout=TIMEOUT_SECONDS
        )
        self._auth: str | None = None

    def __enter__(self) -> RagflowBootstrapClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def authenticate(self, token: str) -> None:
        """Fixe le jeton porté par toutes les requêtes suivantes.

        RAGFlow accepte indifféremment un jeton de session (JWT) et une clé d'API
        sur l'en-tête `Authorization`, avec ou sans préfixe `Bearer`.
        """
        self._auth = token

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        expected_failures: Sequence[str] = (),
    ) -> tuple[bool, Any, httpx.Response]:
        """Exécute une requête et renvoie `(succès, data, réponse)`.

        `expected_failures` liste des fragments de message que l'appelant sait
        gérer (« existe déjà »…) : ils rendent `succès=False` sans lever, ce qui
        est exactement ce qu'il faut pour rendre l'amorçage idempotent face à une
        API sans opération « créer si absent ».
        """
        headers = {"Authorization": self._auth} if self._auth else None
        response = self._client.request(method, path, json=json_body, headers=headers)
        try:
            body = response.json()
        except ValueError as error:
            raise BootstrapError(
                f"{method} {path} : réponse non-JSON (HTTP {response.status_code})."
            ) from error
        code = body.get("code")
        if code == 0:
            return True, body.get("data"), response
        message = str(body.get("message", ""))
        if any(fragment.lower() in message.lower() for fragment in expected_failures):
            return False, None, response
        raise BootstrapError(f"{method} {path} : code={code} — {message}")


# ─── Étapes d'amorçage ───────────────────────────────────────────────────────


def resolve_base_url(settings: Settings, override: str | None) -> str:
    """Choisit une URL de base réellement joignable **depuis l'hôte**.

    `RAGFLOW_BASE_URL` vaut `http://ragflow-cpu:9380` : c'est le nom DNS interne
    au réseau Docker, correct pour l'API et les workers, mais introuvable depuis
    l'hôte où tourne ce script. On essaie donc l'URL configurée puis le port
    publié en boucle locale, et on annonce celle qui répond.
    """
    candidates = (
        [override]
        if override
        else [settings.ragflow_base_url, f"http://127.0.0.1:{DEFAULT_HOST_PORT}"]
    )
    for candidate in candidates:
        try:
            response = httpx.get(f"{candidate.rstrip('/')}/api/v1/system/healthz", timeout=10.0)
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return candidate.rstrip("/")
        raise BootstrapError(
            f"{candidate} répond mais n'est pas sain (HTTP {response.status_code}) : "
            f"{response.text[:300]}"
        )
    raise BootstrapError(
        "Aucune instance RAGFlow joignable parmi "
        + ", ".join(candidates)
        + ". Démarre `docker/docker-compose.ragflow.yml`, ou passe --base-url."
    )


def existing_key_works(base_url: str, api_key: str) -> bool:
    """Vérifie qu'une clé déjà en place ouvre bien l'API (`GET /datasets`)."""
    with RagflowBootstrapClient(base_url) as client:
        client.authenticate(f"Bearer {api_key}")
        try:
            ok, _, _ = client.request("GET", "/datasets")
        except BootstrapError:
            # Une clé refusée ressort en `code=102` : c'est une réponse, pas une
            # panne — on la lit comme « clé à réémettre », sans faire échouer.
            return False
        return ok


def open_session(client: RagflowBootstrapClient, settings: Settings) -> None:
    """Crée le compte de service s'il n'existe pas, puis ouvre une session.

    Le jeton de session se lit dans l'en-tête **de réponse** `Authorization`,
    jamais dans le corps — c'est vrai de l'inscription comme de la connexion.
    """
    email = settings.ragflow_bootstrap_email
    encrypted = encrypt_password(settings.ragflow_bootstrap_password.get_secret_value())

    created, _, response = client.request(
        "POST",
        "/users",
        json_body={"nickname": "ragengine", "email": email, "password": encrypted},
        expected_failures=("already registered",),
    )
    if not created:
        print(f"→ Compte {email} déjà présent : connexion.")
        _, _, response = client.request(
            "POST", "/auth/login", json_body={"email": email, "password": encrypted}
        )
    else:
        print(f"→ Compte de service {email} créé.")

    token = response.headers.get("Authorization")
    if not token:
        raise BootstrapError(
            "RAGFlow n'a pas renvoyé d'en-tête `Authorization` : session impossible."
        )
    client.authenticate(token)


def issue_api_key(client: RagflowBootstrapClient) -> str:
    """Émet une clé d'API pour le compte de service et la renvoie."""
    _, data, _ = client.request("POST", "/system/tokens")
    if not isinstance(data, dict) or not data.get("token"):
        raise BootstrapError("RAGFlow n'a pas renvoyé de jeton dans `POST /system/tokens`.")
    return str(data["token"])


def ensure_huggingface_provider(client: RagflowBootstrapClient, settings: Settings) -> None:
    """Déclare le fournisseur `HuggingFace` et ses deux instances — vérifie avant de créer.

    Aucune des deux routes RAGFlow n'est idempotente : elles répondent « existe
    déjà » (en HTTP 200, `code=102`). L'existence est donc vérifiée d'abord, et
    le message d'échec est toléré en second rideau pour le cas où deux exécutions
    se croiseraient.

    **Deux instances**, pas une (contrairement à Xinference, ADR-011) : un
    conteneur TEI ne sert qu'un seul modèle (`--model-id`), donc deux `base_url`
    distinctes côté RAGFlow — une par conteneur (`docker-compose.gpu.yml`,
    services `inference-embedding`/`inference-reranker`). Les noms d'instance
    viennent de `app.providers.engine.ragflow.client`, seul endroit qui connaît
    le format `<modèle>@<instance>@HuggingFace` attendu en retour par
    `ensure_dataset`/`search` (ADR-051 : la forme courte `<modèle>@HuggingFace`
    ne suffit plus dès que deux instances actives coexistent sous le même
    fournisseur).
    """
    _, providers, _ = client.request("GET", "/providers")
    names = {entry.get("name") for entry in providers or [] if isinstance(entry, dict)}
    if HUGGINGFACE_PROVIDER not in names:
        client.request(
            "PUT",
            "/providers",
            json_body={"provider_name": HUGGINGFACE_PROVIDER},
            expected_failures=("already exists",),
        )
        print(f"→ Fournisseur {HUGGINGFACE_PROVIDER} déclaré.")
    else:
        print(f"→ Fournisseur {HUGGINGFACE_PROVIDER} déjà déclaré.")

    _, instances, _ = client.request("GET", f"/providers/{HUGGINGFACE_PROVIDER}/instances")
    known = {
        str(entry.get("instance_name") or entry.get("name"))
        for entry in instances or []
        if isinstance(entry, dict)
    }

    # `base_url` est l'URL vue **par RAGFlow**, pas par l'hôte : les conteneurs
    # partagent le réseau `ragengine`. RAGFlow sonde réellement le modèle à la
    # création de chaque instance.
    if HUGGINGFACE_EMBEDDING_INSTANCE_NAME in known:
        print(f"→ Instance {HUGGINGFACE_EMBEDDING_INSTANCE_NAME} déjà enregistrée.")
    else:
        client.request(
            "POST",
            f"/providers/{HUGGINGFACE_PROVIDER}/instances",
            json_body={
                "instance_name": HUGGINGFACE_EMBEDDING_INSTANCE_NAME,
                "api_key": HUGGINGFACE_API_KEY,
                "base_url": settings.embedding_inference_base_url,
                "region": "",
                "model_info": [
                    {
                        "model_type": ["embedding"],
                        "model_name": settings.embedding_model_id,
                        "max_tokens": MAX_TOKENS,
                    },
                ],
            },
            expected_failures=("already exists",),
        )
        print(
            f"→ Instance {HUGGINGFACE_EMBEDDING_INSTANCE_NAME} enregistrée sur "
            f"{settings.embedding_inference_base_url} ({settings.embedding_model_id})."
        )

    if HUGGINGFACE_RERANKER_INSTANCE_NAME in known:
        print(f"→ Instance {HUGGINGFACE_RERANKER_INSTANCE_NAME} déjà enregistrée.")
    else:
        client.request(
            "POST",
            f"/providers/{HUGGINGFACE_PROVIDER}/instances",
            json_body={
                "instance_name": HUGGINGFACE_RERANKER_INSTANCE_NAME,
                "api_key": HUGGINGFACE_API_KEY,
                "base_url": settings.reranker_inference_base_url,
                "region": "",
                "model_info": [
                    {
                        "model_type": ["rerank"],
                        "model_name": settings.reranker_model_id,
                        "max_tokens": MAX_TOKENS,
                    },
                ],
            },
            expected_failures=("already exists",),
        )
        print(
            f"→ Instance {HUGGINGFACE_RERANKER_INSTANCE_NAME} enregistrée sur "
            f"{settings.reranker_inference_base_url} ({settings.reranker_model_id})."
        )


def ensure_vision_provider(client: RagflowBootstrapClient, settings: Settings) -> None:
    """Enregistre (optionnellement) un modèle `IMAGE2TEXT` — global au tenant (ADR-027).

    Sauté sans effet si `RAGFLOW_VISION_PROVIDER` n'est pas défini : aucun coût,
    aucun changement de comportement pour un déploiement existant — même
    discipline que le reste de ce script (chaque étape est un choix explicite).

    `Settings._require_consistent_vision_provider_config` a déjà validé au
    chargement que la clé API correspondante est présente et qu'un modèle est
    renseigné : on peut supposer les deux ici sans re-vérifier.

    **Portée** : RAGFlow n'expose qu'un seul `Tenant.img2txt_id` — pas de
    granularité par base de connaissances (vérifié en lisant
    `api/db/joint_services/tenant_model_service.py::get_tenant_default_model_by_type`
    et les points d'appel réels du pipeline `naive` que RAGEngine utilise
    déjà : `deepdoc/parser/figure_parser.py`, `rag/app/picture.py`,
    `rag/app/naive.py` — tous lisent le défaut tenant, aucun ne prend un
    identifiant de modèle vision par dataset/document). Fixer ce modèle
    l'active donc pour **toutes** les bases de connaissances existantes et
    futures du déploiement, pas seulement celles qui en ont besoin.
    """
    provider = settings.ragflow_vision_provider
    if provider is None:
        print(
            "→ Vision (IMAGE2TEXT) non configurée (RAGFLOW_VISION_PROVIDER absent) : "
            "sautée, aucun coût engagé."
        )
        return

    factory = VISION_FACTORY_BY_PROVIDER[provider]
    api_key_secret = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }[provider]
    assert api_key_secret is not None  # garanti par la validation de Settings
    model_name = settings.ragflow_vision_model_id
    assert model_name  # idem

    _, providers, _ = client.request("GET", "/providers")
    names = {entry.get("name") for entry in providers or [] if isinstance(entry, dict)}
    if factory not in names:
        client.request(
            "PUT",
            "/providers",
            json_body={"provider_name": factory},
            expected_failures=("already exists",),
        )
        print(f"→ Fournisseur {factory} déclaré (vision).")
    else:
        print(f"→ Fournisseur {factory} déjà déclaré.")

    _, instances, _ = client.request("GET", f"/providers/{factory}/instances")
    known = {
        str(entry.get("instance_name") or entry.get("name"))
        for entry in instances or []
        if isinstance(entry, dict)
    }
    if VISION_INSTANCE_NAME not in known:
        # "image2text" == LLMType.IMAGE2TEXT.value côté RAGFlow — c'est cette
        # valeur exacte que `get_tenant_default_model_by_type` recherche,
        # vérifié dans `common/constants.py`.
        vision_model_info = {
            "model_type": ["image2text"],
            "model_name": model_name,
            "max_tokens": MAX_TOKENS,
        }
        client.request(
            "POST",
            f"/providers/{factory}/instances",
            json_body={
                "instance_name": VISION_INSTANCE_NAME,
                "api_key": api_key_secret.get_secret_value(),
                "model_info": [vision_model_info],
            },
            expected_failures=("already exists",),
        )
        print(f"→ Instance {VISION_INSTANCE_NAME} enregistrée sur {factory} ({model_name}).")
    else:
        print(f"→ Instance {VISION_INSTANCE_NAME} déjà enregistrée sur {factory}.")

    # `PATCH /users/me/models` exige les 4 champs (asr/embd/img2txt/llm) même
    # si un seul change : on relit l'état courant pour ne pas écraser une
    # config tenant existante à l'aveugle (CONTRIBUTING.md §3 — RAGFlow tel qu'il est
    # conçu, pas de mutation d'un champ voisin non demandée).
    _, tenant_info, _ = client.request("GET", "/users/me/models")
    target_img2txt_id = f"{model_name}@{VISION_INSTANCE_NAME}@{factory}"
    if tenant_info.get("img2txt_id") == target_img2txt_id:
        print("→ Modèle vision déjà défini par défaut sur le tenant.")
        return
    client.request(
        "PATCH",
        "/users/me/models",
        json_body={
            "tenant_id": tenant_info["tenant_id"],
            "asr_id": tenant_info.get("asr_id") or "",
            "embd_id": tenant_info.get("embd_id") or "",
            "llm_id": tenant_info.get("llm_id") or "",
            "img2txt_id": target_img2txt_id,
        },
    )
    print(
        f"→ Modèle vision par défaut du tenant fixé à {target_img2txt_id} — "
        "s'applique à TOUTES les bases de connaissances (mécanisme global RAGFlow, "
        "pas par KB, cf. ADR-027)."
    )


def announce_key(api_key: str) -> None:
    """Affiche la clé émise — et ne l'écrit nulle part.

    Aucun secret généré ne s'auto-persiste (CONTRIBUTING.md §6) : un script qui édite
    `.env` tout seul recopie un secret dans un fichier que l'opérateur n'a pas
    relu, et le fait silencieusement à chaque exécution.
    """
    print()
    print("=" * 72)
    print("  Clé d'API RAGFlow émise. Recopie-la dans `.env` :")
    print()
    print(f"    RAGFLOW_API_KEY={api_key}")
    print()
    print("  Elle n'est écrite dans aucun fichier — à toi de jouer.")
    print("=" * 72)


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : deux étapes idempotentes, journalisées ligne à ligne."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="URL de l'API RAGFlow vue depuis l'hôte (par défaut : RAGFLOW_BASE_URL, "
        f"puis http://127.0.0.1:{DEFAULT_HOST_PORT}).",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        base_url = resolve_base_url(settings, args.base_url)
        print(f"→ RAGFlow joignable sur {base_url}.")

        configured = settings.ragflow_api_key
        api_key: str | None = None
        if configured is not None and existing_key_works(base_url, configured.get_secret_value()):
            print("→ RAGFLOW_API_KEY déjà valide : compte et clé inchangés.")
            api_key = configured.get_secret_value()
        else:
            if configured is not None:
                print("→ RAGFLOW_API_KEY présente mais refusée par le moteur : réémission.")
            with RagflowBootstrapClient(base_url) as client:
                open_session(client, settings)
                api_key = issue_api_key(client)
                announce_key(api_key)

        with RagflowBootstrapClient(base_url) as client:
            client.authenticate(f"Bearer {api_key}")
            ensure_huggingface_provider(client, settings)
            ensure_vision_provider(client, settings)
    except BootstrapError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"✗ RAGFlow injoignable : {error}", file=sys.stderr)
        return 1

    print("→ Amorçage terminé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
