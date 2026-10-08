"""Configuration applicative typée, alimentée exclusivement par l'environnement.

Les noms de variables sont ceux de `.env.example` (source de vérité). Aucun
secret ni URL n'est codé en dur ailleurs que dans les valeurs par défaut de
développement de ce module : l'application démarre sans fichier `.env`, tous les
secrets ayant un défaut vide ou optionnel.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Environnement d'exécution de la façade."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DEV_SECRET_KEY = "change-me"
"""Valeur de développement refusée en production par `Settings`."""

_EXPOSED_ENVIRONMENTS: frozenset[Environment] = frozenset(
    {Environment.STAGING, Environment.PRODUCTION}
)
"""Environnements servis à de vrais visiteurs, donc derrière HTTPS."""

_HOST_COOKIE_PREFIX = "__Host-"
"""Préfixe de cookie normalisé (RFC 6265bis) : le navigateur n'accepte le cookie que si
`Secure`, `Path=/` sont posés et `Domain` absent — voir `Settings.session_cookie_effective_name`."""


def _is_exposed_environment(environment: object) -> bool:
    """Vrai pour staging/production.

    Prend un `object` : la `default_factory` de `session_cookie_secure` lui
    passe une entrée du dictionnaire des champs déjà validés, non typé. Si
    `environment` a lui-même échoué, pydantic n'appelle pas la fabrique
    (erreur `default_factory_not_called`, à côté de l'erreur d'origine).
    """
    return environment in _EXPOSED_ENVIRONMENTS


_OPTIONAL_SECRETS = (
    "ragflow_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "groq_api_key",
    "gemini_api_key",
    "langfuse_public_key",
    "langfuse_secret_key",
)
"""Tout champ `SecretStr | None` de `Settings` doit figurer ici : sans quoi une
variable d'environnement vide (`.env.example` en livre plusieurs, dont
`ANTHROPIC_API_KEY=`) est lue comme une `SecretStr('')`
« configurée » plutôt que comme une brique absente (audit 2026-09, constat 1)."""


class Settings(BaseSettings):
    """Réglages de la façade RAGEngine.

    Chaque champ est lié à la variable d'environnement de `.env.example` via son
    `validation_alias`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # --- Façade FastAPI ---
    app_name: str = Field(
        default="RAGEngine",
        min_length=1,
        validation_alias="RAGENGINE_APP_NAME",
        description=(
            "Nom de marque : titre de l'OpenAPI et de la page de référence publique. "
            "Un greffon peut fournir un autre défaut (`Plugin.settings_defaults`)."
        ),
    )
    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        validation_alias="RAGENGINE_ENV",
        description="Environnement d'exécution.",
    )
    secret_key: SecretStr = Field(
        default=SecretStr(DEV_SECRET_KEY),
        validation_alias="RAGENGINE_SECRET_KEY",
        description="Clé de signature des sessions et jetons.",
    )
    log_level: LogLevel = Field(
        default="INFO",
        validation_alias="RAGENGINE_LOG_LEVEL",
        description="Seuil du logger racine.",
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        validation_alias="RAGENGINE_CORS_ORIGINS",
        description="Origines autorisées par CORS (liste séparée par des virgules).",
    )

    # --- Upload de documents (Phase 9 — validation stricte) ---
    upload_max_bytes: int = Field(
        # Plafond par fichier, appliqué à **tout** principal : un quota cumulé
        # éventuel (greffon, `ResourceQuotaPolicy`) s'y ajoute, ce plafond-ci
        # protège le stockage et la mémoire du process contre un unique fichier
        # surdimensionné, quel que soit l'appelant.
        #
        # 90 Mio, pas 100 (audit sécurité 2026-09, liste de contrôle point 6,
        # ADR-066) : le plan Cloudflare gratuit coupe un corps de requête à
        # ~100 000 000 octets décimaux (~95,4 Mio). L'ancien défaut, 100 Mio =
        # 104 857 600 octets, dépassait déjà ce seuil **avant même** d'ajouter
        # l'enveloppe multipart — un envoi légitime proche du maximum recevait
        # la page d'erreur brute de Cloudflare plutôt que ce `413` applicatif.
        # 90 Mio + enveloppe (`UPLOAD_MULTIPART_ENVELOPE_BYTES`,
        # `app.core.body_limit`) laisse une marge d'environ 4,4 Mio sous le
        # seuil Cloudflare.
        default=90 * 1024 * 1024,
        ge=1,
        validation_alias="RAGENGINE_UPLOAD_MAX_BYTES",
        description="Taille maximale d'un document téléversé, en octets (90 Mio par défaut).",
    )
    request_body_max_bytes: int = Field(
        # Défense en profondeur derrière les bornes de Traefik (`docker/traefik/
        # dynamic.yml`, ADR-052) : aujourd'hui la seule barrière tant que l'API
        # n'est joignable que par le proxy (audit sécurité 2026-09, constat 13).
        # Même valeur que `body-limit-api` (Traefik) et le même raisonnement :
        # le corps JSON le plus gros légitime est l'historique d'une conversation
        # (`POST .../chat`, plafonné à 40 messages/64 000 caractères, ADR-053),
        # ~400 Ko au pire en JSON échappé — 1 Mo laisse le `422` applicatif
        # précéder toujours ce `413`. Sans effet sur la route d'ingestion, qui
        # suit `upload_max_bytes` (`RequestBodyLimitMiddleware`).
        default=1_000_000,
        ge=1,
        validation_alias="RAGENGINE_REQUEST_BODY_MAX_BYTES",
        description=(
            "Plafond applicatif par défaut sur le corps d'une requête HTTP entrante, en "
            "octets (1 Mo par défaut) — hors la route d'ingestion, qui suit "
            "`RAGENGINE_UPLOAD_MAX_BYTES`."
        ),
    )
    upload_allowed_content_types: Annotated[list[str], NoDecode] = Field(
        # Formats effectivement exploitables par le pipeline (precheck local
        # pour PDF/tableurs, RAGFlow pour le reste — texte, bureautique OOXML,
        # images pour l'OCR). Configurable pour ajuster sans déploiement.
        default=[
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.macroenabled.12",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-excel",
            "application/vnd.ms-excel.sheet.macroenabled.12",
            "application/msword",
            "application/vnd.ms-powerpoint",
            "text/csv",
            "application/csv",
            "text/plain",
            "text/markdown",
            "text/html",
            "application/json",
            "message/rfc822",
            "image/png",
            "image/jpeg",
            "image/tiff",
            "image/gif",
            "image/bmp",
            "image/webp",
        ],
        validation_alias="RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES",
        description="Types de médias acceptés à l'ingestion (liste séparée par des virgules).",
    )

    # --- Rate limiting par clé API / IP (Phase 9, ADR-018) ---
    rate_limit_enabled: bool = Field(
        default=True,
        validation_alias="RAGENGINE_RATE_LIMIT_ENABLED",
        description="Active le limiteur de débit Redis sur `/api/v1/*` (hors `/health`).",
    )
    rate_limit_requests: int = Field(
        default=120,
        ge=1,
        validation_alias="RAGENGINE_RATE_LIMIT_REQUESTS",
        description="Requêtes autorisées par clé API, par fenêtre glissante fixe.",
    )
    rate_limit_anonymous_requests: int = Field(
        # Plus strict que la limite par clé : une IP peut être partagée (NAT),
        # mais un appelant sans clé n'a par construction aucun autre signal
        # distinctif — c'est aussi le filet de sécurité d'un porteur de jeton
        # invalide qui en changerait à chaque requête pour éviter son propre
        # compteur (voir `app/core/rate_limit.py`).
        default=60,
        ge=1,
        validation_alias="RAGENGINE_RATE_LIMIT_ANONYMOUS_REQUESTS",
        description="Requêtes autorisées par IP pour tout appelant sans clé API.",
    )
    rate_limit_gateway_requests: int = Field(
        # Voir ADR-040 : en déploiement local (le cas par défaut, `docker/
        # docker-compose.yml`), tout le trafic issu de la machine hôte —
        # navigateur compris, tous onglets et sessions confondus — atteint
        # `ragengine-api` sous la même adresse IP, celle de la passerelle du
        # réseau Docker bridge. Un plafond aussi strict que
        # `rate_limit_anonymous_requests` y freinerait un déploiement entier
        # comme s'il s'agissait d'un unique visiteur anonyme. Nettement plus
        # généreux, mais toujours borné : une vraie boucle d'abus locale reste
        # freinée, seulement à un débit dix fois plus élevé.
        default=600,
        ge=1,
        validation_alias="RAGENGINE_RATE_LIMIT_GATEWAY_REQUESTS",
        description=(
            "Requêtes autorisées par fenêtre pour l'IP détectée comme passerelle du "
            "réseau Docker local (déploiement local — ADR-040), à la place de "
            "rate_limit_anonymous_requests."
        ),
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        validation_alias="RAGENGINE_RATE_LIMIT_WINDOW_SECONDS",
        description="Largeur de la fenêtre du compteur de débit, en secondes.",
    )

    # --- Stockage des fichiers téléversés (ADR-014) ---
    storage_root_dir: Path = Field(
        # Chemin **dans le conteneur** : `docker/docker-compose.yml` y monte le
        # volume `ragengine_documents_data`, partagé par `api` (écriture à
        # l'upload) et `worker-default` (lecture au precheck et au chunking).
        default=Path("/data/documents"),
        validation_alias="RAGENGINE_STORAGE_ROOT_DIR",
        description="Racine du stockage local des documents téléversés.",
    )

    # --- Sessions d'interface (ADR-005) ---
    session_cookie_name: str = Field(
        default="ragengine_session",
        validation_alias="SESSION_COOKIE_NAME",
        description=(
            "Nom **configuré** du cookie `HttpOnly` portant le jeton de session. Ce n'est "
            "pas forcément le nom émis sur le fil : voir `session_cookie_effective_name`."
        ),
    )
    session_ttl_hours: int = Field(
        # Réduit de 720 h (30 j) à 168 h (7 j) — audit sécurité 2026-09, constat
        # 4, ADR-064. Pas de renouvellement glissant : chaque valeur plus basse
        # forcerait une reconnexion hebdomadaire à quotidienne pour un usage
        # actif (ingestion, revue de precheck) qui s'étale sur plusieurs jours ;
        # 7 jours borne la fenêtre d'exposition d'un jeton intercepté à un
        # septième de l'ancienne valeur sans dégrader l'expérience d'usage
        # courant.
        default=168,
        ge=1,
        validation_alias="SESSION_TTL_HOURS",
        description="Durée de vie d'une session d'interface, en heures (7 jours par défaut).",
    )
    session_cookie_secure: bool = Field(
        # Sûr par défaut (audit sécurité 2026-09, constat 4) : non renseignée, la
        # valeur se déduit de `RAGENGINE_ENV` — `true` en staging/production,
        # `false` en développement et en test, où l'API est servie en clair sur
        # `http://localhost` et où un cookie `Secure` ne serait jamais renvoyé.
        # Un `false` explicite en staging/production est refusé au démarrage par
        # `_require_secure_session_cookie_when_exposed`.
        default_factory=lambda data: _is_exposed_environment(data.get("environment")),
        validation_alias="SESSION_COOKIE_SECURE",
        description=(
            "Pose l'attribut `Secure` sur le cookie de session (HTTPS obligatoire). "
            "Déduit de l'environnement s'il n'est pas fourni ; jamais `false` en "
            "staging/production."
        ),
    )

    # --- Plafond dédié sur la connexion (audit sécurité 2026-09, constat 8, ADR-059) ---
    login_rate_limit_window_seconds: int = Field(
        # Plus long que `rate_limit_window_seconds` (anti-rafale générique,
        # 60 s) : un bourrage d'identifiants distribué s'étale sur plusieurs
        # minutes, pas sur une seule fenêtre courte.
        default=900,
        ge=1,
        validation_alias="LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        description="Fenêtre du plafond dédié à `POST /api/v1/sessions`, en secondes (15 min).",
    )
    login_rate_limit_ip_attempts: int = Field(
        default=20,
        ge=1,
        validation_alias="LOGIN_RATE_LIMIT_IP_ATTEMPTS",
        description="Tentatives de connexion autorisées par IP normalisée, par fenêtre.",
    )
    login_rate_limit_account_attempts: int = Field(
        # Plus strict que le plafond IP : c'est le compte visé, pas l'IP d'un
        # attaquant potentiellement distribué, qu'il protège en premier —
        # sans lui, un bourrage réparti sur de nombreuses IP contre un même
        # compte ne serait jamais compté par cible.
        default=10,
        ge=1,
        validation_alias="LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS",
        description=(
            "Tentatives de connexion autorisées par compte visé (adresse soumise, que le "
            "compte existe ou non), par fenêtre."
        ),
    )

    # --- PostgreSQL (métadonnées applicatives) ---
    postgres_host: str = Field(default="postgres", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, ge=1, le=65535, validation_alias="POSTGRES_PORT")
    postgres_db: str = Field(default="ragengine", validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="ragengine", validation_alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(
        default=SecretStr(DEV_SECRET_KEY), validation_alias="POSTGRES_PASSWORD"
    )

    # --- Redis / Celery ---
    redis_url: str = Field(default="redis://redis:6379/0", validation_alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://redis:6379/1", validation_alias="CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://redis:6379/2", validation_alias="CELERY_RESULT_BACKEND"
    )

    # --- RAGFlow (moteur d'ingestion/retrieval, ADR-001, ADR-012) ---
    ingestion_engine: Literal["ragflow", "fake", "fake-shared"] = Field(
        default="ragflow",
        validation_alias="INGESTION_ENGINE",
        description=(
            "Adaptateur du moteur d'ingestion (ADR-070). `ragflow` : seule implémentation "
            "de production. `fake` : moteur en mémoire, sans RAGFlow ni GPU, pour les tests "
            "et les démonstrations en un seul process. `fake-shared` : le même, dont l'état "
            "vit dans Redis, partagé entre l'API et les workers (démonstration, ADR-071) — "
            "tous deux refusés en staging/production."
        ),
    )
    ragflow_base_url: str = Field(
        default="http://ragflow-cpu:9380", validation_alias="RAGFLOW_BASE_URL"
    )
    ragflow_api_key: SecretStr | None = Field(default=None, validation_alias="RAGFLOW_API_KEY")
    ragflow_bootstrap_email: str = Field(
        default="service@ragengine.local", validation_alias="RAGFLOW_BOOTSTRAP_EMAIL"
    )
    ragflow_bootstrap_password: SecretStr = Field(
        default=SecretStr(DEV_SECRET_KEY), validation_alias="RAGFLOW_BOOTSTRAP_PASSWORD"
    )
    ragflow_stuck_job_timeout_seconds: int = Field(
        default=900,
        ge=60,
        validation_alias="RAGFLOW_STUCK_JOB_TIMEOUT_SECONDS",
        description="Veille anti-blocage (ADR-012) : RAGFlow peut rester RUNNING indéfiniment.",
    )
    ragflow_poll_interval_seconds: int = Field(
        # Le sondage n'occupe jamais un worker : la tâche se replanifie avec ce
        # délai en `countdown` plutôt que de dormir (ADR-013).
        default=5,
        ge=1,
        validation_alias="RAGFLOW_POLL_INTERVAL_SECONDS",
        description="Délai entre deux sondages de l'état de parsing d'un document.",
    )

    # --- Garde-fou qualité d'extraction (Phase 11 Étape 4, ADR-024) ---
    quality_guard_degenerate_chunk_chars: int = Field(
        default=20,
        ge=1,
        validation_alias="QUALITY_GUARD_DEGENERATE_CHUNK_CHARS",
        description=(
            "Caractères significatifs (espaces exclus) sous lequel un fragment est "
            "considéré dégénéré — même valeur et même justification que "
            "`DEGENERATE_CHUNK_THRESHOLD_CHARS` de `measure_chunking_quality.py` (outil interne) "
            "(fait 6, plan interne : un chunk du seul caractère « Q » a été indexé)."
        ),
    )
    quality_guard_degenerate_chunk_pct_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias="QUALITY_GUARD_DEGENERATE_CHUNK_PCT_THRESHOLD",
        description=(
            "Part de fragments dégénérés au-delà de laquelle l'avertissement "
            "`degenerate_content` se déclenche. 50 % par défaut : la baseline de "
            "l'étape 0 (`run-20260807-104858` (mesure interne)) ne montre "
            "aucun document sain au-dessus de 0 % une fois les documents courts exclus "
            "(cf. `quality_guard_min_significant_chars_for_degenerate_check`) — une "
            "franche majorité de fragments dégénérés reste un seuil conservateur, sans "
            "réagir à un unique fragment isolé dans un document par ailleurs sain."
        ),
    )
    quality_guard_min_significant_chars_for_degenerate_check: int = Field(
        default=100,
        ge=0,
        validation_alias="QUALITY_GUARD_MIN_SIGNIFICANT_CHARS_FOR_DEGENERATE_CHECK",
        description=(
            "Total de caractères significatifs (tous fragments confondus) sous lequel "
            "le contrôle de fragments dégénérés est ignoré. Sans ce plancher, "
            "`25-pdf-single-word.pdf` (10 caractères au total, 100 % dégénéré par "
            "construction, cf. baseline étape 0) déclencherait un faux positif "
            "systématique — un document légitimement court n'est pas un défaut "
            "d'extraction (ADR-024 §3). 100 sépare largement ce cas connu (10) des plus "
            "petits documents sains de la baseline (au moins ~150 caractères)."
        ),
    )
    quality_guard_min_source_coverage: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        validation_alias="QUALITY_GUARD_MIN_SOURCE_COVERAGE",
        description=(
            "Couverture du texte source (PDF uniquement, référence `pdfplumber`) sous "
            "laquelle l'avertissement `low_source_coverage` se déclenche. 30 % par "
            "défaut : nettement sous le plancher légitime le plus bas de la baseline de "
            "l'étape 0 (44,4 %, en-têtes/pieds de page répétés, "
            "`14-pdf-headers-footers-repeated.pdf`), et très au-dessus du cas dégradé "
            "connu (2,2 %, `chunk_method=email` sur un PDF, ADR-024 §Contexte) — attrape "
            "une perte sévère sans faire de bruit sur un document sain."
        ),
    )
    quality_guard_chunk_sample_limit: int = Field(
        default=100,
        ge=1,
        le=100,
        validation_alias="QUALITY_GUARD_CHUNK_SAMPLE_LIMIT",
        description=(
            "Fragments relus via `IngestionEnginePort.list_document_chunks` (page 1 "
            "seulement, le garde-fou ne pagine pas au-delà) pour le calcul du garde-fou "
            "qualité. Plafonné à 100 : taille de page maximale acceptée par RAGFlow "
            "(constaté empiriquement, `measure_chunking_quality.py` (outil interne)). Un document "
            "de plus de 100 fragments n'est donc évalué que sur son premier lot : "
            "compromis v1 assumé, sans effet sur la détection `zero_chunks` (exacte quel "
            "que soit ce plafond). Le port lui-même pagine désormais réellement (Phase 13, "
            "consultation des fragments depuis l'interface) — c'est le garde-fou qui "
            "choisit de rester sur une seule page, pas une limite du port."
        ),
    )

    # --- Garde-fou bombe de décompression `.xlsx`/`.xlsm` (audit sécurité 2026-09,
    # constat 10, ADR-061) ---
    spreadsheet_max_decompressed_bytes: int = Field(
        # Mesuré empiriquement (ADR-061) : le classeur le plus pathologique
        # produit par openpyxl (20 000 lignes identiques) décompresse à ~27 Mio
        # pour 930 Kio compressés. 1 Gio laisse une marge large sur l'usage
        # légitime tout en arrêtant loin en amont d'une bombe visant plusieurs
        # Gio (le cas décrit par l'audit) avant qu'`openpyxl.load_workbook` n'y
        # touche.
        default=1024 * 1024 * 1024,
        ge=1,
        validation_alias="RAGENGINE_SPREADSHEET_MAX_DECOMPRESSED_BYTES",
        description=(
            "Taille décompressée totale au-delà de laquelle un classeur `.xlsx`/`.xlsm` "
            "est refusé au precheck, en octets (1 Gio par défaut)."
        ),
    )
    spreadsheet_max_decompression_ratio: int = Field(
        # Mesuré empiriquement (ADR-061) : ~30:1 pour le classeur le plus
        # compressible produit par openpyxl, ~1029:1 pour un flux DEFLATE
        # pathologique (zlib, niveau 9, sur 200 Mio de zéros) — proche du
        # maximum théorique d'un flux DEFLATE unique (~1032:1). 100 laisse
        # 3x de marge sur l'usage légitime mesuré tout en arrêtant loin en
        # amont du plafond réel d'une bombe.
        default=100,
        ge=1,
        validation_alias="RAGENGINE_SPREADSHEET_MAX_DECOMPRESSION_RATIO",
        description=(
            "Ratio (taille décompressée / taille compressée) au-delà duquel une entrée "
            "de l'archive `.xlsx`/`.xlsm` est jugée suspecte (100:1 par défaut)."
        ),
    )

    # --- Service d'inférence GPU (embeddings + reranker, ADR-002, ADR-051) ---
    # Deux URLs distinctes depuis ADR-051 (TEI) : un conteneur TEI ne sert qu'un
    # seul modèle (`--model-id`), contrairement à Xinference qui en hébergeait
    # plusieurs sous une même URL — `docker-compose.gpu.yml` déclare donc deux
    # services, `inference-embedding` et `inference-reranker`.
    embedding_inference_base_url: str = Field(
        # Port 80 : celui de l'image officielle TEI (`ENV PORT=80` dans
        # `Dockerfile-cuda`, distinct du défaut `3000` codé dans le binaire
        # `text-embeddings-router` lui-même — l'image le surcharge).
        default="http://inference-embedding:80",
        validation_alias="EMBEDDING_INFERENCE_BASE_URL",
    )
    reranker_inference_base_url: str = Field(
        default="http://inference-reranker:80",
        validation_alias="RERANKER_INFERENCE_BASE_URL",
    )
    embedding_model_id: str = Field(default="bge-m3", validation_alias="EMBEDDING_MODEL_ID")
    reranker_model_id: str = Field(
        default="bge-reranker-v2-m3", validation_alias="RERANKER_MODEL_ID"
    )
    embedding_batch_size: int = Field(
        default=16,
        ge=1,
        validation_alias="EMBEDDING_BATCH_SIZE",
        description="Batch conservateur — budget de 12 Go de VRAM (CONTRIBUTING.md §2).",
    )
    rerank_candidate_pool_size: int = Field(
        default=128,
        ge=1,
        # Plafonné à la valeur par défaut elle-même (`.env.example`,
        # `RERANK_CANDIDATE_POOL_SIZE=128`) : c'est la seule valeur dont on sait,
        # par la mesure, qu'elle reste sous le budget VRAM (audit 2026-09,
        # constat 3). Sans `le`, rien n'empêche une valeur d'environnement de
        # se rapprocher à nouveau des ~1024 candidats par défaut de RAGFlow qui
        # ont produit l'incident documenté ci-dessous — jamais revalidée à une
        # valeur supérieure.
        le=128,
        validation_alias="RERANK_CANDIDATE_POOL_SIZE",
        description=(
            "Plafond du vivier de candidats transmis au reranker (paramètre `top_k` "
            "propre à RAGFlow, distinct de celui de RAGEngine — cf. RagflowEngineAdapter."
            "search). Sans plafond, RAGFlow envoie par défaut jusqu'à 1024 candidats en un "
            "seul lot au reranker : sur des passages de longueur réelle, l'allocation CUDA "
            "correspondante dépasse le budget de 12 Go (incident confirmé le 2026-09-06, "
            "6,43 Gio demandés pour un seul lot). Valeur conservatrice, CONTRIBUTING.md §2. "
            "Borne haute (128) alignée sur ce même défaut : au-delà, on retombe sur le risque "
            "de l'incident documenté, jamais revalidé à une valeur supérieure "
            "(`providers/engine/base.py::EngineAdapter.search`)."
        ),
    )

    # --- LLM externes (génération Playground / évaluation) ---
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description=(
            "Utilisée uniquement pour la vision RAGFlow (`RAGFLOW_VISION_PROVIDER=gemini`, "
            "ADR-027) — aucun `LLMPort` Gemini n'existe côté RAGEngine (catalogue "
            "`providers/llm/` : Anthropic, OpenAI, Groq seulement), Gemini n'est pas une "
            "option de génération Playground/évaluation."
        ),
    )

    # --- Évaluation programmée (Phase 12 Étape 4, Celery Beat) ---
    evaluation_scheduled_interval_hours: int = Field(
        default=24,
        ge=1,
        validation_alias="EVALUATION_SCHEDULED_INTERVAL_HOURS",
        description=(
            "Fréquence, par base `evaluation_enabled=true`, des runs d'évaluation "
            "déclenchés par Celery Beat (`app.workers.tasks.evaluations."
            "dispatch_scheduled_evaluations`). 24h (une fois par jour) par défaut : "
            "chaque item d'un run coûte au moins trois appels LLM réels facturés par "
            "le fournisseur (génération de la Q/R, réponse du Playground rejouée pour "
            "juger, jugement) — CONTRIBUTING.md §2, un compte peut s'épuiser (Anthropic, "
            "crédit à sec cette session). Aucune décision produit ne justifie "
            "aujourd'hui une cadence plus rapprochée qu'une fois par jour."
        ),
    )
    evaluation_scheduled_sample_size: int = Field(
        default=8,
        ge=1,
        # Même plafond que `MAX_EVALUATION_SAMPLE_SIZE`
        # (`app.domain.schemas.evaluation`, non importé ici pour ne pas coupler la
        # configuration — module base sans dépendance interne — au domaine) : la
        # contrainte de `EvaluationCreate` la revalide de toute façon à chaque run,
        # ce plafond ne fait qu'échouer plus tôt (démarrage du process) sur une
        # valeur d'environnement aberrante.
        le=20,
        validation_alias="EVALUATION_SCHEDULED_SAMPLE_SIZE",
        description=(
            "Documents échantillonnés par run programmé, par base. 8 par défaut : "
            "ordre de grandeur modéré cohérent avec une cadence quotidienne "
            "(`evaluation_scheduled_interval_hours`) — assez pour un signal de "
            "tendance exploitable par `retrieval_metrics.py`, sans multiplier le "
            "coût LLM réel d'un déclenchement sans intervention humaine. Le plafond "
            "de l'exécution synchrone existante (`MAX_EVALUATION_SAMPLE_SIZE` = 20, "
            "appelée depuis l'UI) reste inchangé et distinct : ce champ ne le "
            "remplace pas, il gouverne un chemin différent (la routine programmée)."
        ),
    )
    evaluation_scheduled_llm_provider_id: str = Field(
        default="llama-3.3-70b-versatile",
        min_length=1,
        validation_alias="EVALUATION_SCHEDULED_LLM_PROVIDER_ID",
        description=(
            "Fournisseur `LLMPort` (catalogue `GET /api/v1/providers`) utilisé par "
            "la routine programmée pour générer et juger — même rôle que "
            "`EvaluationCreate.llm_provider_id` pour un lancement manuel. Groq "
            "(`llama-3.3-70b-versatile`) par défaut : fournisseur déjà utilisé en "
            "pratique sur ce déploiement pour l'évaluation, et surtout distinct "
            "d'Anthropic dont le crédit peut s'épuiser indépendamment (CONTRIBUTING.md "
            "§2, constaté cette session) — une routine sans surveillance humaine ne "
            "doit pas dépendre du même budget que les usages interactifs "
            "prioritaires (Playground)."
        ),
    )

    evaluation_async_max_sample_size: int = Field(
        default=200,
        ge=1,
        validation_alias="EVALUATION_ASYNC_MAX_SAMPLE_SIZE",
        description=(
            "Plafond du chemin **asynchrone** de `POST .../evaluations` (Phase 12 "
            "Étape 4, ADR-028) — au-delà de `MAX_EVALUATION_SAMPLE_SIZE` (20, "
            "toujours synchrone), un `sample_size` plus grand déclenche un job "
            "Celery (`run_async_evaluation`, file `default`) au lieu de bloquer la "
            "requête HTTP. 200 par défaut : un ordre de grandeur cohérent avec le "
            "coût LLM réel (au moins trois appels facturés par item — génération, "
            "réponse rejouée, jugement — CONTRIBUTING.md §2), largement en dessous d'un "
            "volume qui saturerait un run quotidien unique, et assez généreux pour "
            "produire un échantillon représentatif sans intervention manuelle "
            "répétée. Non couplé à `evaluation_scheduled_sample_size` (routine "
            "programmée, 8 par défaut) : deux chemins distincts, deux réglages "
            "distincts, cf. `app.workers.tasks.evaluations`."
        ),
    )

    # --- Balayage de maintenance planifié (Motif C, l'audit de dette 2026-09 (interne)) ---
    maintenance_sweep_interval_minutes: int = Field(
        default=15,
        ge=1,
        validation_alias="MAINTENANCE_SWEEP_INTERVAL_MINUTES",
        description=(
            "Cadence de `app.workers.tasks.maintenance.run_maintenance_sweep` (Celery Beat) : "
            "jobs d'ingestion orphelins, décisions de precheck expirées, purge des espaces "
            "espaces éphémères échus (ADR-055). 15 min par défaut — nettement sous le seuil "
            "d'orphelinage réel (`ragflow_stuck_job_timeout_seconds + "
            "ingestion_job_orphan_grace_seconds`, 45 min par défaut) pour qu'un job devenu "
            "orphelin attende au plus un passage supplémentaire une fois ce seuil franchi."
        ),
    )
    maintenance_sweep_batch_limit: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias="MAINTENANCE_SWEEP_BATCH_LIMIT",
        description=(
            "Candidats traités par passage, **par catégorie** (jobs orphelins, décisions "
            "expirées, espaces échus) — trois plafonds indépendants, jamais cumulés. Borne le "
            "fan-out de chaque catégorie : un retard important se rattrape en plusieurs "
            "passages, jamais en une seule salve non bornée (CONTRIBUTING.md §9)."
        ),
    )
    ingestion_job_orphan_grace_seconds: int = Field(
        default=1800,
        ge=0,
        validation_alias="INGESTION_JOB_ORPHAN_GRACE_SECONDS",
        description=(
            "Marge ajoutée à `ragflow_stuck_job_timeout_seconds` avant qu'un job "
            "`embedding`/`indexing` ne soit considéré orphelin (chaîne de sondage disparue — "
            "worker tué, message perdu, ADR-055) plutôt que simplement en cours de sondage. "
            "Une chaîne vivante réévalue la même immobilité à chaque sondage "
            "(`ragflow_poll_interval_seconds`, 5 s par défaut) et se serait donc déjà "
            "transitée elle-même vers `failed` bien avant "
            "`ragflow_stuck_job_timeout_seconds + ingestion_job_orphan_grace_seconds` : "
            "au-delà de ce total, un job encore visible dans l'un de ces deux états ne peut "
            "provenir que d'une chaîne de sondage effectivement morte, jamais d'une chaîne "
            "active mais lente. 30 min de marge, très généreuse au regard des 5 s de cadence "
            "de sondage par défaut."
        ),
    )
    awaiting_review_decision_timeout_hours: int = Field(
        default=168,
        ge=1,
        validation_alias="AWAITING_REVIEW_DECISION_TIMEOUT_HOURS",
        description=(
            "Délai au-delà duquel un job `awaiting_review` sans décision humaine reprend "
            "automatiquement avec le profil de precheck par défaut — même arête que "
            "`PUT .../precheck/decision` (`docs/domain-model.md` §3). Comportement documenté "
            "de longue date (`domain/state_machine.py`, `app.workers.tasks.ingestion."
            "decision_of`) mais jamais implémenté avant ce réglage (Motif B, "
            "l'audit de dette 2026-09 (interne), ADR-055). 7 jours (168 h) par défaut : "
            "aucune SLA produit n'étant documentée ailleurs, cette valeur reprend le seuil que "
            "l'audit lui-même utilise pour qualifier un job `awaiting_review` d'« accumulé » — "
            "lecture conservatrice à confirmer côté produit, pas une valeur mesurée."
        ),
    )

    # --- Vision RAGFlow (IMAGE2TEXT, Phase 12 étape 7 / ADR-027) ---
    ragflow_vision_provider: Literal["anthropic", "openai", "gemini"] | None = Field(
        default=None,
        validation_alias="RAGFLOW_VISION_PROVIDER",
        description=(
            "Fournisseur externe à enregistrer comme modèle IMAGE2TEXT du tenant "
            "RAGFlow, par `scripts/bootstrap_ragflow.py`. Absent = désactivé : "
            "aucun appel vision, aucun coût, comportement inchangé (ADR-027). "
            "Le mécanisme est global au tenant RAGFlow — pas de portée par base "
            "de connaissances, RAGFlow n'expose qu'un seul `Tenant.img2txt_id` "
            "(vérifié empiriquement, pas supposé). `gemini` : classe `GeminiCV` "
            "native de RAGFlow (`rag/llm/cv_model.py`), confirmée dans le "
            "conteneur déployé — contrairement à Groq, écarté (ADR-027), qui n'a "
            "aucune classe de dispatch vision côté RAGFlow."
        ),
    )
    ragflow_vision_model_id: str | None = Field(
        default=None,
        validation_alias="RAGFLOW_VISION_MODEL_ID",
        description=(
            "Modèle enregistré pour `RAGFLOW_VISION_PROVIDER` (ex. "
            "`claude-haiku-4-5-20251001`). Requis si ce dernier est renseigné."
        ),
    )

    # --- Disjoncteur LLM (Phase 9, ADR-019) ---
    llm_circuit_breaker_failure_threshold: int = Field(
        default=5,
        ge=1,
        validation_alias="LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        description="Échecs consécutifs avant l'ouverture du disjoncteur, par fournisseur LLM.",
    )
    llm_circuit_breaker_reset_timeout_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="LLM_CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS",
        description="Délai avant qu'une requête sonde tente de refermer le disjoncteur.",
    )

    # --- Appels LLM financés par le serveur, hors clé personnelle (ADR-047, ADR-054, ADR-069) ---
    # Préfixe `RAGENGINE_PLATFORM_LLM_*` historique, conservé pour ne pas casser les `.env`
    # déployés. **Cœur seul : aucun plafond, aucun modèle imposé** (`None` par défaut) ; un
    # opérateur qui veut des plafonds les active explicitement. Un greffon peut fournir des
    # valeurs par défaut (`Plugin.settings_defaults`) : appliquées seulement aux réglages
    # que l'environnement ne renseigne pas. `VAR=` (vide) vaut `None` : plafond désactivé.
    platform_llm_model_id: str | None = Field(
        default=None,
        validation_alias="RAGENGINE_PLATFORM_LLM_MODEL_ID",
        description=(
            "Modèle imposé pour tout appel que l'appelant ne paie pas de sa propre clé. "
            "Absent : le modèle demandé est respecté. À renseigner avec un budget global, "
            "dont le coût par appel est calibré sur ce modèle."
        ),
    )
    platform_llm_user_daily_limit: int | None = Field(
        default=None,
        ge=1,
        validation_alias="RAGENGINE_PLATFORM_LLM_USER_DAILY_LIMIT",
        description=(
            "Messages/jour, par compte authentifié sans clé LLM personnelle. Absent : aucun "
            "plafond (et aucun compteur Redis)."
        ),
    )
    platform_llm_evaluation_daily_limit: int | None = Field(
        default=None,
        ge=1,
        validation_alias="RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT",
        description=(
            "Appels/jour, par compte, imputés aux évaluations (génération de Q/R, réponse "
            "rejouée, jugement) — compteur distinct de `platform_llm_user_daily_limit` (chat) "
            "(addendum 2026-09-21 à ADR-054). Un run programmé "
            "(`evaluation_scheduled_sample_size`, "
            "8 par défaut) coûte jusqu'à 3 appels par item : 24 couvre ce pire cas. "
            "Absent : aucun plafond."
        ),
    )
    platform_llm_daily_budget_usd: float | None = Field(
        default=None,
        gt=0,
        validation_alias="RAGENGINE_PLATFORM_LLM_DAILY_BUDGET_USD",
        description=(
            "Coupe-circuit de dépense globale de la clé serveur, sur 24h. N'affecte jamais "
            "un appelant muni de sa propre clé LLM. Absent : aucun coupe-circuit."
        ),
    )
    platform_llm_assumed_cost_per_message_usd: float = Field(
        default=0.01,
        gt=0,
        validation_alias="RAGENGINE_PLATFORM_LLM_ASSUMED_COST_PER_MESSAGE_USD",
        description=(
            "Coût moyen supposé par appel sous le modèle imposé — approximation assumée : "
            "`LLMPort` ne restitue aucun compte de jetons par appel aujourd'hui "
            "(`stream()` ne renvoie que du texte), l'étendre aux trois fournisseurs pour un "
            "vrai suivi de dépense est un chantier séparé. Sert uniquement à convertir "
            "`platform_llm_daily_budget_usd` en un plafond d'appels appliqué au même "
            "compteur Redis que les autres plafonds — pas une réconciliation de facturation."
        ),
    )

    # --- Observabilité (optionnelle — vide = désactivée, no-op) ---
    langfuse_host: str | None = Field(default=None, validation_alias="LANGFUSE_HOST")
    langfuse_public_key: SecretStr | None = Field(
        default=None, validation_alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None, validation_alias="LANGFUSE_SECRET_KEY"
    )

    @model_validator(mode="after")
    def _apply_plugin_settings_defaults(self) -> Settings:
        """Applique les défauts de réglages d'un greffon aux seuls réglages non renseignés.

        Un réglage posé par l'environnement (même vide) l'emporte toujours : le greffon ne
        fournit que des valeurs *par défaut* (`Plugin.settings_defaults`, ADR-069).
        Sans greffon, rien ne change : cœur seul, aucun plafond ni modèle imposé.
        """
        # Import tardif : `app.core.plugins` importe ce module, et le cœur ne nomme aucun greffon.
        from app.core.plugins import settings_defaults

        for name, value in settings_defaults().items():
            if name not in self.model_fields_set:
                setattr(self, name, value)
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        """Accepte `info`, `Info`… en plus des niveaux en majuscules."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("cors_origins", "upload_allowed_content_types", mode="before")
    @classmethod
    def _split_comma_separated_list(cls, value: object) -> object:
        """Découpe une liste fournie sous forme `a,b,c` par l'environnement."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "langfuse_host",
        "ragflow_vision_provider",
        "ragflow_vision_model_id",
        "platform_llm_model_id",
        "platform_llm_user_daily_limit",
        "platform_llm_evaluation_daily_limit",
        "platform_llm_daily_budget_usd",
        *_OPTIONAL_SECRETS,
        mode="before",
    )
    @classmethod
    def _empty_string_means_absent(cls, value: object) -> object:
        """Traite `VAR=` comme non renseignée (brique optionnelle désactivée)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _forbid_dev_secret_outside_development(self) -> Settings:
        """Interdit `change-me` — défaut public partagé par trois secrets — en staging/production.

        `secret_key`, `postgres_password` et `ragflow_bootstrap_password` partagent tous les
        trois `DEV_SECRET_KEY` comme valeur par défaut de développement (audit 2026-09, constat
        2) : sans ce garde-fou sur les deux derniers, un déploiement staging/production qui
        oublie de surcharger `POSTGRES_PASSWORD`/`RAGFLOW_BOOTSTRAP_PASSWORD` démarre quand même,
        avec un mot de passe public connu.
        """
        if self.environment in (Environment.STAGING, Environment.PRODUCTION):
            for env_var, secret in (
                ("RAGENGINE_SECRET_KEY", self.secret_key),
                ("POSTGRES_PASSWORD", self.postgres_password),
                ("RAGFLOW_BOOTSTRAP_PASSWORD", self.ragflow_bootstrap_password),
            ):
                value = secret.get_secret_value()
                if not value or value == DEV_SECRET_KEY:
                    message = (
                        f"{env_var} doit être défini avec une valeur propre "
                        f"lorsque RAGENGINE_ENV vaut '{self.environment}'."
                    )
                    raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _forbid_fake_engine_when_exposed(self) -> Settings:
        """Refuse `INGESTION_ENGINE=fake` en staging/production (ADR-070).

        Le moteur factice ne fait aucune recherche sémantique et perd tout au
        redémarrage : l'y laisser par erreur servirait des réponses vides à de
        vrais utilisateurs. RAGFlow est le moteur unique de production
        (CONTRIBUTING.md §3), erreur de déploiement signalée au démarrage.
        """
        if _is_exposed_environment(self.environment) and self.ingestion_engine != "ragflow":
            message = (
                f"INGESTION_ENGINE={self.ingestion_engine} est refusé lorsque RAGENGINE_ENV "
                f"vaut '{self.environment}' : seul `ragflow` est un moteur de production."
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _forbid_wildcard_origin_with_credentials(self) -> Settings:
        """Refuse `RAGENGINE_CORS_ORIGINS=*` : `main.py` pose `allow_credentials=True`.

        Combiner un joker d'origine avec des identifiants de credentials est une
        faille connue (Starlette reflète alors l'`Origin` de la requête au lieu de
        renvoyer `*`, ce qui revient à autoriser n'importe quel site tiers à
        rejouer les cookies de session d'un visiteur). Comme `allow_credentials`
        n'est pas configurable — la façade en a besoin pour le cookie de session
        (ADR-005) — c'est ici, à la source de la liste d'origines, que le joker
        est fermé.
        """
        if "*" in self.cors_origins:
            message = (
                "RAGENGINE_CORS_ORIGINS ne peut pas contenir '*' : la façade pose "
                "allow_credentials=True, et la combinaison avec un joker d'origine "
                "autoriserait tout site tiers à rejouer le cookie de session."
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_secure_session_cookie_when_exposed(self) -> Settings:
        """Refuse `SESSION_COOKIE_SECURE=false` en staging/production (audit 2026-09, constat 4).

        Le jeton de session vaut une session complète pendant
        `SESSION_TTL_HOURS` : sans `Secure`, le navigateur le renvoie sur toute
        requête `http://` (premier accès, lien partagé, HSTS pas encore
        mémorisé), et une interception passive suffit à le rejouer. La valeur
        omise est déjà déduite à `true` (`default_factory` du champ) : seul un
        `false` **explicite** arrive ici, et c'est une erreur de déploiement à
        signaler au démarrage plutôt qu'un réglage à respecter en silence.
        """
        if _is_exposed_environment(self.environment) and not self.session_cookie_secure:
            message = (
                "SESSION_COOKIE_SECURE ne peut pas valoir false lorsque RAGENGINE_ENV "
                f"vaut '{self.environment}' : le cookie de session serait transmis en "
                "clair sur toute requête http://. Retirez la variable (true est alors "
                "déduit) ou passez-la à true."
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_consistent_vision_provider_config(self) -> Settings:
        """Vérifie la cohérence de la config vision RAGFlow (ADR-027) au démarrage.

        Échoue tôt plutôt qu'au moment du bootstrap RAGFlow : un fournisseur
        renseigné sans modèle, ou sans la clé API correspondante déjà présente
        dans cette même config, est une configuration incomplète — pas un état
        « désactivé » silencieux.
        """
        if self.ragflow_vision_provider is None:
            return self
        if not self.ragflow_vision_model_id:
            message = "RAGFLOW_VISION_PROVIDER nécessite RAGFLOW_VISION_MODEL_ID."
            raise ValueError(message)
        required_var, key_present = {
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
            "gemini": ("GEMINI_API_KEY", self.gemini_api_key),
        }[self.ragflow_vision_provider]
        if key_present is None:
            message = (
                f"RAGFLOW_VISION_PROVIDER={self.ragflow_vision_provider} nécessite {required_var}."
            )
            raise ValueError(message)
        return self

    @property
    def database_url(self) -> str:
        """DSN SQLAlchemy/asyncpg construit à partir des variables `POSTGRES_*`."""
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def session_cookie_effective_name(self) -> str:
        """Nom **réellement émis et lu** pour le cookie de session (audit sécurité 2026-09,
        constat 4, ADR-064).

        Préfixé `__Host-` en staging/production, inchangé sinon — même calcul que
        `session_cookie_secure` (`_is_exposed_environment`), pas une variable
        indépendante à garder synchronisée à la main. `__Host-` impose au
        navigateur `Secure`, `Path=/` et l'absence de `Domain` : les trois tiennent
        déjà (`session_cookie_secure` vaut `true` dès que ce préfixe s'applique,
        `set_session_cookie`/`clear_session_cookie` posent `path="/"` et ne
        déclarent jamais `domain=`). Inapplicable en développement : `__Host-`
        exige `Secure`, donc HTTPS, absent sur `http://localhost`.

        Toute lecture du cookie (poser, effacer, lire — `app.api.v1.dependencies`,
        `app.core.rate_limit`) doit passer par cette propriété, jamais par
        `session_cookie_name` brut : lire l'un et écrire l'autre rendrait le
        cookie invisible au serveur qui l'a lui-même émis.
        """
        if _is_exposed_environment(self.environment) and not self.session_cookie_name.startswith(
            _HOST_COOKIE_PREFIX
        ):
            return f"{_HOST_COOKIE_PREFIX}{self.session_cookie_name}"
        return self.session_cookie_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Renvoie le singleton de configuration (dépendance FastAPI et point d'accès unique)."""
    return Settings()
