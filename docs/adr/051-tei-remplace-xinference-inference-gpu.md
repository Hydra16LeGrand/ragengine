# ADR-051 — TEI remplace Xinference comme service d'inférence GPU

**Date :** 2026-09-12 · **Statut :** accepté · **Remplace :** ADR-011 (Xinference)

## Résumé

- **Décision** : Xinference → **Text Embeddings Inference** (TEI, HuggingFace), en **deux conteneurs
  mono-modèle** (`inference-embedding` : BGE-M3 ; `inference-reranker` : bge-reranker-v2-m3). Motif :
  Xinference sérialise strictement les requêtes concurrentes d'embedding/rerank (verrou
  `asyncio.Lock`, aucun batching — 4 requêtes simultanées ≈ 4 en séquence), ce qui plafonne le débit
  d'ingestion. TEI fait du batching dynamique natif.
- **ADR-011 avait écarté TEI** précisément parce qu'il impose deux conteneurs. Ce qui a changé :
  l'exigence réelle n'était pas « un seul conteneur » mais « un seul budget VRAM maîtrisé » — deux
  conteneurs mono-modèle le tiennent aussi bien, et mieux (voir VRAM).
- **VRAM mesurée : 2 744 Mio** (1 356 + 1 388) contre **4 768 Mio** sous Xinference, soit **−42 %**.
- **Côté RAGFlow, le fournisseur devient `HuggingFace`** (classes natives `HuggingFaceEmbed` /
  `HuggingfaceRerank`), avec **deux instances** — donc un identifiant de modèle à **trois** segments,
  `<modèle>@<instance>@HuggingFace`. La forme courte à deux segments ne résout plus (RAGFlow ne
  devine l'instance que s'il n'y en a qu'une seule active).
- **Les datasets existants devaient être repointés** : ils stockaient `bge-m3@Xinference`, un
  fournisseur devenu inexistant. `scripts/migrate_ragflow_embedding_provider.py` le fait **sans
  réindexation** (même modèle, mêmes vecteurs). 103 datasets migrés.
- **Le scénario « reranker en cours de rechargement » disparaît** : sans registre dynamique de
  modèles, un conteneur TEI sain sert toujours son modèle. Code de détection retiré.

## Contexte

ADR-002 et CONTRIBUTING.md §2 imposent que les modèles locaux vivent dans un service d'inférence GPU dédié,
consommé à la fois par la façade (`EmbeddingPort`/`RerankerPort`) et par RAGFlow. ADR-011 avait
tranché pour Xinference, et **avait explicitement écarté TEI** au motif qu'« il sert un seul modèle
par instance : héberger BGE-M3 et bge-reranker-v2-m3 imposerait deux conteneurs, donc deux budgets
VRAM à coordonner séparément ».

Le mécanisme de concurrence de Xinference a été mesuré depuis (en-tête de `docker-compose.gpu.yml`
avant cette ADR) : `ModelActor` place tout appel d'inférence derrière `asyncio.Lock` dès que le
modèle ne déclare pas `allow_batch` — ce que ne font jamais les modèles d'embedding et de rerank.
**Mesuré sur cette instance : 4 requêtes d'embedding simultanées sur bge-m3 → 5,66 s, contre 5,56 s
en séquentiel (accélération 0,98×).** Cette sérialisation garantissait le budget VRAM, mais au prix
du débit : elle plafonne l'ingestion dès que plusieurs documents sont en vol, quel que soit
l'appelant (façade, worker Celery ou RAGFlow).

## Décision

**Remplacer Xinference par TEI, en deux conteneurs mono-modèle.** Image officielle
`ghcr.io/huggingface/text-embeddings-inference:89-1.9` (tag `89` = capacité de calcul CUDA 8.9, celle
de la RTX 4080 Laptop / Ada Lovelace), sans image dérivée — contrairement à Xinference qui exigeait
`inference.Dockerfile` pour y ajouter `sentence-transformers` (fichier supprimé).

**L'exigence d'ADR-011 n'est pas violée, elle est relue.** « Un service unique » y servait un but :
un seul budget VRAM à surveiller, sans co-résidents imprévus. Deux conteneurs mono-modèle le
servent aussi bien — chacun charge un poids fixe, une fois, au démarrage — et mieux : l'empreinte
totale mesurée baisse de 42 %, le runtime Rust/candle de TEI étant plus sobre que
PyTorch + sentence-transformers.

**Le mécanisme de protection VRAM change de nature, et c'est assumé.** Sous Xinference il venait de
la sérialisation stricte (aucun batching possible). Sous TEI il vient de deux bornes : un modèle
unique par conteneur (poids fixe, pas de réplique à régler) et `--max-batch-tokens` (16 384 par
défaut, conservé) qui borne la mémoire d'activation du batching dynamique. On échange donc « jamais
d'OOM parce que rien ne se chevauche jamais » contre « batching borné par la valeur par défaut
éprouvée de l'amont » — c'est exactement le compromis recherché, puisque c'est l'absence de
chevauchement qui plafonnait le débit.

**`--max-client-batch-size` relevé de 32 (défaut TEI) à 64** pour ne pas rejeter en 413 un lot à
`RerankerCapabilities.max_documents` (64) ou `BGE_M3_CAPABILITIES.max_batch_size` (64).

## Contrats vérifiés (source réelle + instance réelle, jamais la seule documentation)

| Point | Vérification |
|---|---|
| Port d'écoute | **80**, pas 3000 : l'image officielle pose `ENV PORT=80`, qui surcharge le défaut compilé du binaire (`Dockerfile-cuda`). |
| Cache des poids | `ENV HUGGINGFACE_HUB_CACHE=/data` dans l'image ⇒ un volume par conteneur monté sur `/data`. |
| Healthcheck | `GET /health` natif (200/503). Suffisant ici, contrairement à Xinference : TEI charge son modèle **avant** d'ouvrir le port, il n'existe pas d'état « debout sans modèle ». `curl` est présent dans l'image. |
| `POST /v1/embeddings` | Format OpenAI **inchangé** (`data[].index`/`data[].embedding`, 1024 dimensions) — aucun changement de parsing dans `embeddings/local.py`. Le champ `model` envoyé ("bge-m3" ≠ "BAAI/bge-m3") ne fait que journaliser un avertissement côté TEI, il ne rejette jamais. |
| `POST /rerank` | **Changement réel** : route `/rerank` (pas `/v1/rerank`), requête `{query, texts, raw_scores}` (`texts`, pas `documents` ; aucun champ `model` ni `top_n`), réponse en **tableau nu** `[{index, score}]` (pas `{"results": [{index, relevance_score}]}`). `rerankers/local.py` réécrit en conséquence ; la troncature au `top_n` reste côté adaptateur. |
| Fournisseur RAGFlow | `HuggingFace` existe nativement au catalogue (`conf/llm_factories.json`, tags `TEXT EMBEDDING,TEXT RE-RANK`) et appelle les routes natives TEI (`/embed`, `/rerank`). Aucun contournement. |

## Identifiant de modèle à trois segments — la couture non évidente

RAGFlow résout `<modèle>@<fournisseur>` vers l'unique instance active du fournisseur, et **échoue
s'il y en a plusieurs** : `_resolve_instance_for_model` n'applique son repli que si
`len(active_instances) == 1` (`api/db/joint_services/tenant_model_service.py`). Avec deux instances
sous `HuggingFace`, la forme courte qui suffisait sous Xinference cesse donc de fonctionner. La forme
à trois segments `<modèle>@<instance>@<fournisseur>`, supportée nativement par la même fonction
(`rsplit("@", 2)`), lève l'ambiguïté. Les noms d'instance vivent dans
`app/providers/engine/ragflow/client.py` — seul endroit qui connaît le format attendu par RAGFlow —
et `scripts/bootstrap_ragflow.py` les **importe** au lieu de les redupliquer.

## Migration des datasets existants — sans réindexation

**Le défaut** : RAGFlow stocke l'identifiant complet sur chaque dataset (`embd_id`). Les 103 datasets
créés avant la bascule nommaient `bge-m3@Xinference`, fournisseur devenu inexistant : ingestion et
recherche cassées sur toute base antérieure, alors qu'une base neuve fonctionnait.

**Pourquoi aucune réindexation n'est nécessaire** : le modèle servi est identique (BGE-M3, 1024
dimensions), seul son mode de service change. RAGFlow range les vecteurs dans un champ nommé d'après
la dimension (`q_1024_vec`), pas d'après le fournisseur.

**Ce que RAGFlow accepte, vérifié** : `PUT /api/v1/datasets/{id}` avec un nouvel `embedding_model` ne
pose **aucune** garde sur le nombre de fragments existants — le seul contrôle est
`verify_embedding_availability`, qui résout le modèle auprès des fournisseurs du tenant. Vérifié en
conditions réelles sur un dataset portant déjà des fragments : mise à jour acceptée (`code=0`,
`chunk_count` inchangé), puis `POST /retrieval` renvoie toujours ces fragments, avec et sans
reranking.

**`scripts/migrate_ragflow_embedding_provider.py`** applique ce repointage, idempotent (les datasets
déjà à jour ne sont pas touchés) et doté d'un `--dry-run`. 103 datasets migrés, relance = no-op.

## Empreinte VRAM mesurée

RTX 4080 Laptop, 12 282 Mio, les deux conteneurs sains, poids chargés, avant toute requête
(`nvidia-smi --query-compute-apps`) :

| Conteneur | Modèle | VRAM |
|---|---|---|
| `inference-embedding` | BAAI/bge-m3 | 1 356 Mio |
| `inference-reranker` | BAAI/bge-reranker-v2-m3 | 1 388 Mio |
| **Total** | | **2 744 Mio (2,68 Gio)** |

Contre **4 768 Mio (4,66 Gio)** sous Xinference : **−42 %**. Largement sous le plafond de 10 Go de
CONTRIBUTING.md §2, avec davantage de marge qu'avant pour l'OCR.

## Alternatives écartées

- **Garder Xinference avec un contournement de concurrence.** Aucun levier ne l'offre : la
  sérialisation est structurelle (`allow_batch` n'est défini que par les moteurs LLM, jamais par les
  modèles d'embedding/rerank), et `request_limits` n'est pas un sémaphore mais un plafond
  d'admission qui **rejette** en 429 au-delà. Lancer plusieurs répliques multiplierait l'empreinte
  VRAM pour contourner un verrou par modèle — coûteux et fragile.
- **Un seul conteneur TEI servant les deux modèles.** Impossible : `--model-id` est obligatoire et
  singulier.
- **Réenregistrer un fournisseur nommé `Xinference` pointant vers TEI**, pour éviter de toucher aux
  datasets existants. Écarté : ça figerait un mensonge dans la configuration (un fournisseur nommé
  d'après un service qui n'existe plus), et la migration s'est avérée sûre et vérifiable.

## Conséquences

- `docker/docker-compose.gpu.yml` : services `inference-embedding` et `inference-reranker` ;
  `inference` et `inference-models` supprimés (la boucle de réconciliation n'a plus d'objet — TEI ne
  perd pas son modèle au redémarrage, contrairement au registre volatil de Xinference).
  `inference.Dockerfile` supprimé.
- `Settings.inference_base_url` devient `embedding_inference_base_url` +
  `reranker_inference_base_url` (`EMBEDDING_INFERENCE_BASE_URL` / `RERANKER_INFERENCE_BASE_URL`).
  `INFERENCE_REQUEST_LIMITS` et `INFERENCE_RECONCILE_SECONDS` disparaissent ; nouveaux ports hôtes
  `RAGENGINE_INFERENCE_EMBEDDING_HOST_PORT` (9997) et `RAGENGINE_INFERENCE_RERANKER_HOST_PORT`
  (9998).
- `is_rerank_model_loading` / `RERANK_MODEL_LOADING_MESSAGE` / `_RERANK_TRANSIENT_MARKER` retirés de
  `providers/engine/ragflow/client.py` : **le scénario est devenu impossible par construction**, pas
  seulement rare. Un conteneur TEI n'a ni registre de modèles ni boucle de réconciliation ; il charge
  son unique modèle au démarrage et son healthcheck ne passe qu'ensuite. `is_inference_unreachable`
  reste, son marqueur passant de `Xinference` à `HuggingFace` (nom de classe réellement relayé par
  RAGFlow : `HuggingFaceEmbed`).
- Les scripts de mesure du dépôt qui codaient `bge-m3@Xinference` en dur ont été repointés.
- **Piège de déploiement rencontré** : le fichier compose GPU partage le nom de projet Docker du
  cœur (`ragengine`). Un `docker compose -f docker-compose.gpu.yml up -d --remove-orphans` a
  réellement supprimé les cinq conteneurs du cœur (`api`, `postgres`, `redis`, `worker-default`,
  `worker-beat`), vus comme « orphelins » parce qu'absents de *ce* fichier. Volumes intacts, stack
  relancée sans perte. L'en-tête du fichier — qui affirmait à tort un nom de projet distinct
  `ragengine-gpu` — a été corrigé et porte désormais cet avertissement.
- **Un `docker compose restart` ne suffit pas** après cette bascule : l'image `ragengine/api` doit
  être reconstruite (`up -d --build`), sinon la façade continue d'émettre l'ancien suffixe
  `@Xinference` et le reranking échoue sur `inference:9997` — constaté en test de bout en bout.
