# ADR-027 — Vision RAGFlow (`IMAGE2TEXT`) : mécanisme global au tenant, opt-in par bootstrap

**Date :** 2026-08-07, complétée 2026-08-08 · **Statut :** accepté (mécanisme) — **vérifié
bout-en-bout avec Gemini (`gemini-3.6-flash`)**, Groq écarté (non supporté par RAGFlow pour la
vision, vérifié)

## Mise à jour 2026-08-08 — vérification bout-en-bout complétée avec Gemini

Anthropic restait à sec, `OPENAI_API_KEY` toujours vide. Gemini a été testé — RAGFlow le supporte
nativement pour la vision (`GeminiCV`, `rag/llm/cv_model.py`, `_FACTORY_NAME = "Gemini"`, SDK
`google-genai`), confirmé en lisant le conteneur `ragflow-cpu` déployé avant tout enregistrement.
`Settings.ragflow_vision_provider` étendu à `Literal["anthropic", "openai", "gemini"]`,
`Settings.gemini_api_key` ajouté (vision uniquement — aucun `LLMPort` Gemini côté RAGEngine),
`scripts/bootstrap_ragflow.py` généralisé en table `{provider: (factory, clé)}` plutôt qu'un
branchement binaire anthropic/openai.

**Bootstrap réel exécuté** contre l'instance déployée : fournisseur `Gemini` déclaré, instance
`ragengine-vision` enregistrée (`gemini-3.6-flash`), `Tenant.img2txt_id` fixé à
`gemini-3.6-flash@ragengine-vision@Gemini` — effectif pour tout le déploiement (mécanisme global,
cf. décision ci-dessous).

**Trois documents d'essai, trois enseignements distincts** (le corpus interne) :

1. `05-pdf-mixed-text-table-diagram.pdf` (schéma dessiné en primitives vectorielles reportlab,
   pas une image intégrée) : `GeminiCV` s'initialise (`img2txt_id` correctement résolu jusqu'au
   bout), mais `figure_parser.py::vision_figure_parser_pdf_wrapper` ne trouve aucun élément
   `is_image_like` dans `tbls` — un schéma **vectoriel** n'est pas classé comme figure par DeepDOC,
   contrairement à une image **matricielle** intégrée. Le mécanisme est correct ; ce document-là
   n'est simplement pas le bon cas de test pour la vision.
2. `06-image-text-only.png` (image autonome, texte lisible) : `rag/app/picture.py` fait d'abord
   son propre OCR DeepDOC, et **saute délibérément l'appel vision** si le texte OCR est jugé
   suffisant (log RAGFlow exact : « OCR results is too long to use CV LLM »). Comportement voulu
   de RAGFlow (économie de coût), pas un défaut de configuration.
3. `07-image-shapes-only.png` (image autonome, formes géométriques, sans texte) : OCR ne produit
   rien d'exploitable → RAGFlow **appelle réellement Gemini** (log : « Use CV LLM to describe the
   picture » puis « CV LLM respond: ### Image Description... »). Chunk produit, verbatim :
   > *"### **Image Description** — **What is shown:** The image is a simple graphic illustration
   > featuring three basic geometric shapes with black outlines on a plain white background,
   > arranged horizontally from left to right: 1. A **square** (left) 2. A **circle** (middle)
   > 3. A **triangle** (right) [...]"*

   Description exacte, aucune hallucination, indexée comme chunk unique du document
   (`chunk_method=picture`). **DoD de cette ADR atteint** : un document à contenu non textuel
   porteur de sens produit un chunk descriptif indexable, vérifié de bout en bout contre
   l'instance réelle.

**Coût observé** : ~4,5s de bout en bout pour l'appel vision (log RAGFlow, du déclenchement à la
réponse), un seul appel pour ce document d'un seul chunk. Pas de mesure de tokens/prix exposée par
ce log ; à instrumenter si le volume réel le justifie plus tard.

**Portée confirmée en pratique, pas seulement en théorie** : le changement de `Tenant.img2txt_id`
s'applique immédiatement à toute nouvelle ingestion sur **tout le déploiement**, conforme à la
décision ci-dessous — aucune KB n'a eu besoin d'un réglage particulier pour en bénéficier.

Tests ajoutés (`backend/tests/test_config.py`) : `gemini` suit exactement les mêmes garde-fous que
`anthropic`/`openai` (clé requise, modèle requis, configuration complète acceptée).

## Résumé (lire le corps si tu touches un de ces points)

- **Fait établi, pas supposé** : RAGFlow n'expose qu'un seul `Tenant.img2txt_id`. Tous les points
  d'appel du pipeline `naive` que RAGEngine utilise déjà (`deepdoc/parser/figure_parser.py`,
  `rag/app/picture.py`, `rag/app/naive.py` chemin markdown/MinerU) lisent le **défaut du tenant**
  (`get_tenant_default_model_by_type`), aucun ne prend un identifiant de modèle vision par
  dataset/document. **Il n'y a donc pas de granularité par base de connaissances possible** — à la
  différence d'`embedding_model_id`/`reranker_model_id` qui sont des choix par KB côté RAGEngine.
  Ceci contredit l'hypothèse initiale du plan interne (Phase 13, Étape 7) qui anticipait un
  pattern « optionnel par KB, comme `reranker_model_id` » — corrigé ici après vérification empirique.
- **Décision** : activer la vision est un choix de **déploiement entier**, pas de KB — porté par
  deux variables d'environnement lues au bootstrap RAGFlow (`RAGFLOW_VISION_PROVIDER`,
  `RAGFLOW_VISION_MODEL_ID`), absentes par défaut (aucun coût, aucun changement de comportement
  pour un déploiement existant). Aucune colonne `KnowledgeBase.vision_*` n'est ajoutée : ce serait
  une granularité que le moteur ne permet pas réellement.
- **Fournisseur** : Anthropic et OpenAI sont tous deux nativement reconnus par RAGFlow pour
  `IMAGE2TEXT` (classes dédiées `AnthropicCV`/`GptV4`, pas de passage générique LiteLLM). Les deux
  restent une option côté implémentation (`Literal["anthropic", "openai"]`) — **le choix définitif
  entre les deux n'est pas tranché par cette ADR**, cf. « Question ouverte » ci-dessous.
- **Coût récurrent** : un appel API vision par image/figure détectée à l'indexation (naive
  chunking) — externalisé, cohérent CONTRIBUTING.md §2, mais réel et à surveiller une fois activé.
- **Vérification de bout en bout non complétée** : la clé `ANTHROPIC_API_KEY` configurée dans cet
  environnement a un solde de crédit épuisé (`credit balance is too low`, confirmé par un appel
  direct à l'API Anthropic) et `OPENAI_API_KEY` est vide. Le mécanisme RAGFlow a été vérifié
  jusqu'à la limite de ce qui ne coûte rien (enregistrement de fournisseur, forme exacte de
  l'API) ; l'appel réel de description d'image n'a pas pu être exécuté. Voir « Vérification ».
- **Groq envisagé comme contournement (2026-08-07, deuxième passe) et écarté** : `GROQ_API_KEY`
  est bien configurée et fonctionnelle côté RAGEngine (déjà utilisée ailleurs, génération
  d'évaluation Phase 8), mais **RAGFlow n'a aucun support vision pour Groq dans ce build** —
  confirmé à trois niveaux indépendants du code déployé (catalogue statique, classes `CvModel`,
  dispatch runtime), voir « Groq — non viable » ci-dessous. Rien n'a été enregistré côté RAGFlow ;
  le type `Literal` de `ragflow_vision_provider` n'a **pas** été étendu à `"groq"` — l'ajouter
  aurait exposé une option garantie de casser silencieusement (aucune description produite) plutôt
  que d'échouer proprement.

## Contexte

plan interne, Phase 13 Étape 7 (issue de l'audit du 2026-08-07) : RAGFlow a un type de
provider dédié `IMAGE2TEXT`, déjà branché automatiquement dans le pipeline `naive` que RAGEngine
utilise pour l'ingestion, mais vide sur le tenant RAGEngine (`img2txt_id=''`, vérifié en base
MySQL). L'hypothèse de départ, par analogie avec `reranker_model_id` (choix par KB), était qu'un
même pattern d'opt-in par base de connaissances serait possible. Cette ADR documente pourquoi ce
n'est pas le cas et acte le mécanisme adapté à ce que RAGFlow permet réellement (CONTRIBUTING.md §3 :
« on utilise RAGFlow à fond, tel qu'il est conçu »).

## Recherche empirique (contre l'instance réelle `ragflow-cpu` v0.26.4)

1. **Catalogue de fournisseurs** (`conf/llm_factories.json`, lu dans le conteneur déployé) :
   `Anthropic` et `OpenAI` y figurent nativement, avec des modèles tagués `IMAGE2TEXT` (ex.
   `claude-haiku-4-5-20251001`, `gpt-5-mini`). Implémentations dédiées dans `rag/llm/cv_model.py`
   (`AnthropicCV`, `GptV4`/`OpenAI_APICV`) — pas un passage générique par un connecteur
   OpenAI-compatible improvisé.
2. **Chemin de résolution** (`api/db/joint_services/tenant_model_service.py`,
   `get_tenant_default_model_by_type`) : lit `tenant.img2txt_id` — un seul champ par tenant,
   confirmé en relisant `Tenant` (`api/db/db_models.py`) et en traçant les appelants réels
   (`figure_parser.py`, `picture.py`, `naive.py`) : **aucun** ne passe un identifiant de modèle
   vision explicite par dataset/parser_config sur les chemins utilisés par RAGEngine.
3. **API d'enregistrement** (`api/apps/restful_apis/provider_api.py` /
   `services/provider_api_service.py`) : `PUT /providers` (déclare le fournisseur) puis
   `POST /providers/{factory}/instances` avec `model_info: [{"model_type": ["image2text"], ...}]`
   — `"image2text"` est la valeur exacte de `LLMType.IMAGE2TEXT.value` (`common/constants.py`),
   c'est elle qu'attend `get_tenant_default_model_by_type`. La création d'instance **probe
   réellement la clé API** via un appel chat live sur le fournisseur (`verify_api_key`) — un coût
   (minime) est engagé dès l'enregistrement, pas seulement à l'usage.
4. **Fixer le défaut tenant** : `PATCH /users/me/models` — exige les 4 champs
   (`asr_id`, `embd_id`, `llm_id`, `img2txt_id`) même si un seul change (`@validate_request`
   côté RAGFlow). Le tenant RAGEngine a aujourd'hui les 3 autres vides (`GET /users/me/models`
   vérifié en direct) : les réécrire à l'identique ne clobber rien, mais **tout bootstrap futur
   doit relire l'état courant avant d'écrire**, jamais écraser à l'aveugle.
5. **État initial confirmé** : `SELECT img2txt_id FROM tenant` (déjà établi avant cette session) et
   `GET /users/me/models` concordent — `img2txt_id=''`, aucun mécanisme vision actif aujourd'hui.

## Décision

1. **Mécanisme global, pas par KB.** `Settings.ragflow_vision_provider` (`"anthropic" | "openai" |
   None`, défaut `None`) et `Settings.ragflow_vision_model_id` pilotent une étape optionnelle de
   `scripts/bootstrap_ragflow.py` (`ensure_vision_provider`) : si non renseignées, rien n'est
   enregistré côté RAGFlow, aucun coût. Si renseignées, le fournisseur choisi est déclaré, un
   modèle `image2text` enregistré, et fixé comme défaut du tenant — **effet immédiat sur toutes
   les bases de connaissances du déploiement**, présentes et futures, pas une case par KB.
2. **Cohérence validée au démarrage**, pas seulement au bootstrap : `Settings` refuse un
   fournisseur sans modèle, ou sans la clé API correspondante déjà configurée — échec explicite
   plutôt qu'une KB qui découvre un défaut cassé à l'ingestion.
3. **Pas de colonne `KnowledgeBase.vision_*`.** Le seul levier par KB resterait cosmétique (une UI
   qui affiche « vision activée sur le déploiement » n'a pas besoin d'un état par ligne en base) ;
   si un vrai besoin de sélectivité par KB émerge plus tard, il faudra soit l'obtenir de RAGFlow
   (aucun signe qu'il existe dans ce build), soit le construire côté RAGEngine (pré-filtrage des
   documents avant indexation) — hors périmètre ici, non anticipé sans besoin réel.

## Question ouverte (partiellement tranchée 2026-08-08) — Anthropic vs OpenAI vs Gemini

Les trois sont viables techniquement (fait établi 1, étendu à Gemini le 2026-08-08). État réel de
cet environnement au 2026-08-07 : seule une clé `ANTHROPIC_API_KEY` était configurée
(`OPENAI_API_KEY` vide), avec un solde de crédit épuisé — **aucun des deux n'était utilisable tel
quel**. **Mise à jour 2026-08-08** : une `GEMINI_API_KEY` fonctionnelle a permis de compléter la
vérification bout-en-bout (section dédiée en tête de document) — Gemini (`gemini-3.6-flash`) est
donc aujourd'hui le seul fournisseur vision **actif** sur ce déploiement. Le choix définitif à long
terme (rester sur Gemini, ou basculer vers Anthropic/OpenAI une fois un compte approvisionné —
arbitrage qualité de description / coût / cohérence avec le reste du catalogue LLM de RAGEngine,
qui n'a par ailleurs aucun `LLMPort` Gemini) reste ouvert, pas tranché ici. L'implémentation
supporte les trois de façon symétrique (`Literal["anthropic", "openai", "gemini"]`) : changer de
fournisseur est une variable d'environnement plus un nouveau passage de
`scripts/bootstrap_ragflow.py`, jamais un changement de code.

## Groq — non viable, vérifié à trois niveaux (2026-08-07)

Anthropic restant à sec et `OPENAI_API_KEY` étant vide, l'utilisateur a demandé d'évaluer Groq
(`GROQ_API_KEY` déjà configurée et fonctionnelle, utilisée ailleurs pour la génération
d'évaluation) comme voie de sortie pour compléter la vérification de bout en bout. **RAGFlow ne
supporte pas Groq pour `IMAGE2TEXT` dans ce build** — Groq n'existe côté RAGFlow que comme
fournisseur **chat**, jamais vision. Trois preuves indépendantes, sur le code déployé du conteneur
`ragflow-cpu` :

1. **Catalogue statique** (`conf/llm_factories.json`) : l'entrée `Groq` a `"tags": "LLM"` (pas
   `VISION`/`IMAGE2TEXT` comme l'entrée `Anthropic`), et ses 8 modèles (`llama-3.3-70b-versatile`,
   `mixtral-8x7b-32768`, etc.) ont tous `"model_type": "chat"` — aucun tagué vision.
2. **Classes `CvModel`** (`rag/llm/cv_model.py`) : la liste exhaustive des `_FACTORY_NAME`
   enregistrés (28 fournisseurs — `OpenAI`, `Anthropic`, `Gemini`, `NVIDIA`, `Xinference`, `VLLM`,
   `Ollama`, `Bedrock`… jusqu'à `New API`) ne contient **pas** `Groq`. Contrairement à
   `chat_model.py`, ce module ne définit aucune classe `LiteLLMBase` avec un fallback générique
   par fournisseur — `Groq` n'y apparaît que dans `rag/llm/chat_model.py` (ligne 1553, dispatch
   `groq/` via LiteLLM, **chat uniquement**), jamais dans `cv_model.py`.
3. **Dispatch runtime** (`api/db/services/tenant_llm_service.py::model_instance`, la fonction que
   `LLMBundle`/`LLM4Tenant` appelle réellement à l'usage) : pour `model_type ==
   LLMType.IMAGE2TEXT.value`, le code fait `if model_config["llm_factory"] not in CvModel:
   logging.error(...); return None`, puis `LLM4Tenant.__init__` fait `assert self.mdl, "Can't find
   model for {tenant}/{type}/{name}"`. `Groq` n'étant pas une clé de `CvModel` (preuve 2), cet
   assert échouerait à l'exécution — les points d'appel réels (`figure_parser.py`, `picture.py`,
   `naive.py`) l'encapsulent en `try/except` best-effort, donc l'échec ne casserait pas
   l'ingestion, mais ne produirait **aucune** description d'image non plus : un enregistrement
   Groq réussirait côté base de données (`add_model_to_instance` n'a pas de garde-fou contre un
   `model_type` absent du catalogue) tout en étant silencieusement inopérant à l'usage — pire
   qu'une configuration absente, parce qu'elle donnerait l'illusion d'être active.

**Conclusion** : rien n'a été enregistré côté RAGFlow (aucun `PUT /providers`/`POST .../instances`
tenté pour Groq — la preuve à froid dans le code suffisait à conclure sans dépenser d'appel), et
`Settings.ragflow_vision_provider` reste `Literal["anthropic", "openai"]`, sans `"groq"`. La
vérification de bout en bout **reste bloquée**, dans l'attente d'un compte Anthropic approvisionné
ou d'une clé `OPENAI_API_KEY` fonctionnelle — pas d'un contournement possible via Groq.

## Vérification — ce qui a été confirmé et ce qui ne l'a pas été

**Confirmé contre l'instance réelle**, sans dépenser d'appel externe :
- `PUT /providers {"provider_name": "Anthropic"}` → `code=0`, déclaration réussie.
- `GET /providers?available=true` liste bien `Anthropic` (et `OpenAI`) comme fournisseurs
  disponibles avec des modèles compatibles.
- `GET /users/me/models` reflète l'état vide attendu (`img2txt_id=''`, les 3 autres champs aussi).
- `scripts/bootstrap_ragflow.py` avec `RAGFLOW_VISION_PROVIDER=anthropic` exécuté en direct :
  étapes 1 et 2 (compte de service, Xinference) inchangées et idempotentes, étape 3 déclare le
  fournisseur puis échoue **proprement** (`BootstrapError` propagée, code de sortie 1, aucun état
  partiel laissé — pas d'instance/modèle créés) au moment de `POST /providers/Anthropic/instances`,
  qui sonde réellement l'API Anthropic.
- La cause de cet échec a été isolée : appel direct à `https://api.anthropic.com/v1/messages`
  avec la même clé → `400 invalid_request_error`, « *Your credit balance is too low to access the
  Anthropic API* ». Le conteneur `ragflow-cpu` atteint bien `api.anthropic.com` (testé, `401` sur
  une clé bidon plutôt qu'une erreur réseau) : ce n'est pas un problème d'egress Docker, c'est un
  solde de compte épuisé.
- L'environnement a été remis dans son état d'origine après ces essais (`DELETE /providers/
  Anthropic`) : aucune trace résiduelle, `GET /providers` ne liste à nouveau que `Xinference`.

**Confirmé le 2026-08-08, avec Gemini** (cf. section de mise à jour en tête de document) :
- Un appel vision réel produisant une description de figure — RAGFlow appelle bien Gemini
  (log applicatif exact : « Use CV LLM to describe the picture » → « CV LLM respond: ... »).
- Le chunk final indexé contenant cette description (DoD de la mission) — vérifié via
  `GET /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks` côté RAGFlow directement
  (RAGEngine n'expose pas encore cette lecture en façade — écart noté, hors périmètre de cette
  ADR, à consigner si un besoin produit de consultation des chunks bruts émerge).

**Toujours non confirmé** : Anthropic et OpenAI spécifiquement (crédit/clé toujours absents à
cette date) — le mécanisme générique est néanmoins désormais prouvé de bout en bout via un
troisième fournisseur qui partage le même chemin de code (`get_tenant_default_model_by_type` →
`LLMBundle` → classe `*CV`), ce qui réduit fortement le risque résiduel sur les deux autres.

**Pour vérifier Anthropic/OpenAI spécifiquement** (une fois l'un des deux comptes approvisionné) :
```bash
# .env : RAGFLOW_VISION_PROVIDER=anthropic|openai + RAGFLOW_VISION_MODEL_ID, puis
./backend/.venv/bin/python scripts/bootstrap_ragflow.py
# puis ingérer une image sans texte extractible par OCR (une image avec du texte lisible peut
# faire sauter l'appel vision — RAGFlow juge alors l'OCR suffisant, cf. mise à jour du
# 2026-08-08) et lire le chunk produit.
```

## Conséquences

- `backend/app/core/config.py` : deux nouveaux champs (`ragflow_vision_provider`,
  `ragflow_vision_model_id`) + validation croisée (modèle requis si fournisseur renseigné, clé API
  correspondante requise) — testés (`backend/tests/test_config.py`).
- `scripts/bootstrap_ragflow.py` : étape `ensure_vision_provider`, sautée par défaut.
- `.env.example` : `RAGFLOW_VISION_PROVIDER=`, `RAGFLOW_VISION_MODEL_ID=`, vides, documentées
  comme globales au déploiement (pas par KB).
- **Aucun changement de schéma** (`KnowledgeBase`, Pydantic) — décision explicite, pas un oubli.
- Le budget vision engagé (dès l'enregistrement du fournisseur — probe live — puis à chaque
  image/figure détectée en indexation `naive`) doit être surveillé une fois activé ; aucun
  mécanisme de plafond côté RAGEngine n'existe pour cette brique (à envisager si l'usage réel le
  justifie, pas anticipé ici sans donnée).
