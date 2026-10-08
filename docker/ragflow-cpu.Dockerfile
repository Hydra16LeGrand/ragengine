# Image officielle RAGFlow + trois écarts : le modèle de reconnaissance OCR qui
# sait écrire le français (ADR-030), le prompt de description des figures
# (ADR-035) et le paquet `docling` figé à la construction (ADR-043).
#
# ─────────────────────────────────────────────────────────────────────────────
# CONVENTION DE TAG : `v<version amont>-ragengine.<n>`. Le tag ne décrit plus
# une divergence particulière — il y en a trois. `<n>` est le numéro de révision
# de **l'ensemble** des divergences posées sur cette version amont :
# l'incrémenter est un geste explicite, et une image dont le tag n'a pas bougé
# porte exactement les mêmes écarts. Toutes les divergences sont énumérées dans
# cet en-tête, et nulle part ailleurs.
#
#   .1 = OCR latin (ADR-030) + prompt de description des figures (ADR-035)
#   .2 = .1 + `docling` installé au build, en CPU strict (ADR-043)
#
# À CHAQUE MONTÉE DE VERSION DE RAGFLOW, les trois divergences sont à revalider —
# c'est le coût assumé, pas un oubli. Pour la seconde, la revalidation est un
# `diff` : `docker/ragflow-prompts/upstream-v0.26.4/` conserve la copie **exacte
# et non annotée** des deux prompts de l'amont tels qu'ils sont dans cette
# version, précisément pour que la comparaison soit mécanique :
#
#   docker run --rm --entrypoint cat infiniflow/ragflow:<nouvelle version> \
#     /ragflow/rag/prompts/vision_llm_figure_describe_prompt.md \
#     | diff - docker/ragflow-prompts/upstream-v0.26.4/vision_llm_figure_describe_prompt.md
#
# Un diff vide signifie que l'amont n'a pas touché son prompt : notre
# substitution reste un choix, pas l'écrasement d'une correction amont. Un diff
# non vide impose de lire ce que l'amont a changé avant de reconduire le nôtre.
#
# Pour la troisième, la revalidation est de relire `ensure_docling()` dans
# l'`entrypoint.sh` de la nouvelle image et de comparer le défaut de
# `DOCLING_VERSION` à notre `ARG DOCLING_VERSION` ci-dessous :
#
#   docker run --rm --entrypoint grep infiniflow/ragflow:<nouvelle version> \
#     -n "DOCLING_VERSION\|DOCLING_PIN" /ragflow/entrypoint.sh
#
# Une valeur identique confirme que nous installons au build exactement ce que
# l'amont installerait au démarrage. Une valeur différente impose de rejouer la
# mesure avant de suivre l'amont : c'est une version de moteur de mise en page,
# pas un numéro de patch anodin.
# ─────────────────────────────────────────────────────────────────────────────
#
# POURQUOI CETTE IMAGE DÉRIVÉE (constaté, pas supposé) : le dictionnaire de
# caractères livré dans `infiniflow/ragflow:v0.26.4`
# (`/ragflow/rag/res/deepdoc/ocr.res`) compte 6623 entrées, majoritairement
# chinoises, et **ne contient pas** `ç ï ô ê â û ù î œ` (seuls `é è à` y sont) —
# vérifié dans le conteneur déployé. Le décodeur est un CTC
# (`deepdoc/vision/postprocess.py::CTCLabelDecode`) : il ne peut physiquement
# pas émettre un caractère absent de son vocabulaire. Tout document sans couche
# texte — routé vers `layout_recognize: "DeepDOC"` par `is_likely_scanned`
# (ADR-023 §3) — ressort donc avec des accents corrompus, quelle que soit la
# qualité de l'image. Mesuré sur un scan francophone à vérité terrain connue :
# 37,2 % des occurrences de caractères accentués restituées, `à` et `è` jamais
# rendus (`ocr-latin-avant-*` (mesure interne)). Ce n'est pas un
# réglage : l'image ne livre qu'un seul couple modèle/dictionnaire, aucune
# variante n'est sélectionnable.
#
# POURQUOI CE REMPLACEMENT-LÀ : DeepDOC **est** du PaddleOCR PP-OCR exporté en
# ONNX (`deepdoc/vision/ocr.py::TextRecognizer` : `rec_image_shape = [3, 48,
# 320]`, `CTCLabelDecode`, `character_dict_path = <model_dir>/ocr.res`,
# `use_space_char: True`). Un modèle de reconnaissance PaddleOCR latin est donc
# un remplacement direct — mêmes forme d'entrée, décodeur et format de
# dictionnaire. Alignement CTC vérifié dans le conteneur avant tout usage, le
# seul point où une erreur produirait un décalage silencieux :
#   - officiel : entrée `x` [N, 3, 48, W dyn.], sortie 6625 classes
#                = 6623 lignes + espace (`use_space_char`) + `blank` ;
#   - latin    : entrée `x` [N, 3, 48, W dyn.], sortie  504 classes
#                =  502 lignes + espace + `blank`.
# `build_post_process` construit bien 6625 et 504 caractères respectivement :
# les deux couples sont alignés à l'unité près. Reconnaissance de contrôle sur
# huit lignes françaises accentuées connues : 83,4 % d'exactitude caractère et
# 0/8 lignes exactes avec le modèle officiel, 100,0 % et 8/8 avec le latin.
#
# `det.onnx` (détection de zones de texte) n'est **pas** remplacé : la détection
# est agnostique à la langue, la toucher ajouterait du risque sans bénéfice.
#
# COMPROMIS ASSUMÉ : le modèle latin ne reconnaît plus le chinois. Décision
# utilisateur du 2026-08-09 — le français et l'anglais sont la priorité du
# produit (CONTRIBUTING.md §1, corpus principalement francophone), la perte du chinois
# scanné est acceptée. Cf. ADR-030.
#
# Les deux fichiers sont figés dans une couche à la construction, jamais
# téléchargés au démarrage du conteneur : déterminisme et aucune dépendance
# réseau au pire moment — même raisonnement que `inference.Dockerfile`. Les
# sommes de contrôle épinglent la version exacte des poids : une republication
# amont silencieuse fait échouer la construction au lieu de changer le
# comportement de l'OCR sans prévenir.
FROM infiniflow/ragflow:v0.26.4

ARG OCR_LATIN_BASE=https://huggingface.co/monkt/paddleocr-onnx/resolve/main/languages/latin

# PP-OCRv5 latin, licence Apache 2.0 (usage commercial autorisé).
ADD --chmod=0644 --checksum=sha256:614ffc2d6d3902d360fad7f1b0dd455ee45e877069d14c4e51a99dc4ef144409 \
    ${OCR_LATIN_BASE}/rec.onnx /ragflow/rag/res/deepdoc/rec.onnx

# `dict.txt` en amont, `ocr.res` ici : c'est le nom que `TextRecognizer` compose
# lui-même (`os.path.join(model_dir, "ocr.res")`), non configurable.
ADD --chmod=0644 --checksum=sha256:3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff \
    ${OCR_LATIN_BASE}/dict.txt /ragflow/rag/res/deepdoc/ocr.res

# Garde-fou de construction : rejoue l'alignement CTC décrit plus haut sur les
# fichiers réellement présents dans la couche. Une image qui sortirait d'ici
# avec un décalage d'indice produirait un OCR faux mais plausible — le défaut
# exact que cette vérification rend impossible à livrer.
RUN /ragflow/.venv/bin/python -c "\
import sys; sys.path.insert(0, '/ragflow');\
import onnxruntime as ort;\
from deepdoc.vision.postprocess import build_post_process;\
d = '/ragflow/rag/res/deepdoc';\
s = ort.InferenceSession(d + '/rec.onnx', providers=['CPUExecutionProvider']);\
i, o = s.get_inputs()[0], s.get_outputs()[0];\
assert list(i.shape[1:3]) == [3, 48], i.shape;\
classes = o.shape[-1];\
pp = build_post_process({'name': 'CTCLabelDecode', 'character_dict_path': d + '/ocr.res', 'use_space_char': True});\
assert classes == len(pp.character), (classes, len(pp.character));\
missing = [c for c in 'çïôêâûùîœéèàÉÈÀÇ' if c not in pp.dict];\
assert not missing, missing;\
print('OCR latin OK : entree', i.shape, '- classes', classes, '= dictionnaire', len(pp.character))"

# ─── Divergence 2 — prompt de description des figures (ADR-035) ──────────────
#
# POURQUOI CETTE SUBSTITUTION (mesuré, pas supposé) : le prompt livré par
# l'amont demande explicitement de décrire l'apparence — « Describe visible
# regions and components in a stable order », « Describe spatial grouping,
# containment, and alignment of elements » — et ne dit **rien** de la langue
# attendue. Effet constaté sur un document français : des descriptions en
# anglais, orientées tracé (« stylized cursive strokes, loops »), où une
# mention manuscrite à portée juridique (« Lu et approuvé ») se retrouve noyée
# dans quatre lignes de description de trait. Ce n'est pas du bruit gratuit,
# c'est de l'information perdue : ces fragments sont indexés tels quels, et
# aucune requête utile ne tombe sur « traits anguleux, boucles courbes ».
#
# POURQUOI UNE IMAGE DÉRIVÉE ET PAS UN RÉGLAGE : `rag/prompts/template.py`
# `::load_prompt()` lit `<répertoire du paquet>/<nom>.md` et met le résultat en
# cache. Aucune variable d'environnement, aucune entrée de configuration,
# aucune surcharge par tenant — vérifié dans le conteneur déployé (ADR-034).
# Le seul levier voisin, `parser_config.image_context_size`, ne fait que
# basculer vers la variante « avec contexte » du **même** prompt : mesuré, sans
# effet utile.
#
# LES DEUX FICHIERS SONT REMPLACÉS, PAS UN. `figure_parser.py` choisit
# `vision_llm_figure_describe_prompt()` quand aucun contexte n'entoure la
# figure (notre chemin actuel, journal `prompt=default`) et sa variante
# `..._with_context()` sinon. N'en remplacer qu'un rendrait le comportement
# dépendant d'un réglage que personne ne relierait au prompt : poser un jour
# `image_context_size` ferait silencieusement revenir la description
# d'apparence. Les deux portent donc la même intention, les mêmes interdits et
# la même règle de langue ; seul le second exploite en plus le contexte reçu.
#
# PAS DE CONTRAT DE FORME EN AVAL, VÉRIFIÉ : `figure_parser.py` récupère la
# sortie du modèle et la concatène telle quelle
# (`descriptions[i] = txt + "\n".join(...)`), sans parsing, sans marqueur
# extrait, sans schéma attendu — le texte devient directement du contenu de
# fragment. Changer le prompt ne peut donc rompre aucune structure : il n'y en
# a pas. Seule contrainte de forme réelle : l'appel est encadré par
# `@timeout(30, 3)`, donc une sortie beaucoup plus longue serait un risque. Le
# nôtre va dans l'autre sens (mesuré plus court sur toutes les natures
# d'images testées).
#
# PORTÉE RÉELLE : `deepdoc/parser/mineru_parser.py` est un troisième appelant
# du même prompt. RAGEngine n'utilise pas MinerU (`USE_DOCLING: "false"`,
# routage DeepDOC/Plain Text d'ADR-023) : l'effet y est théorique, mais il
# existe et n'est pas passé sous silence.
#
# Le fichier est **écrit par nous** et versionné : aucun téléchargement, donc
# aucune somme de contrôle à épingler, contrairement aux poids OCR ci-dessus.
COPY ragflow-prompts/vision_llm_figure_describe_prompt.md \
     ragflow-prompts/vision_llm_figure_describe_prompt_with_context.md \
     /ragflow/rag/prompts/

# Garde-fou de construction : le prompt n'est pas lu par un `open()` maison mais
# par `load_prompt()`, qui compose lui-même son chemin et échouerait par
# `FileNotFoundError` sur un nom ou un répertoire faux — au moment de la
# première ingestion, pas ici. On rejoue donc le chargement réel, et on vérifie
# que c'est bien **notre** texte qui en sort : un fichier copié au mauvais
# endroit laisserait celui de l'amont en place et l'image sortirait du build en
# paraissant corrigée. On contrôle aussi que la variante « avec contexte »
# garde ses deux variables Jinja — les perdre ferait disparaître tout contexte
# en silence. La comparaison se fait sur le texte **aplati** (espaces normalisés) :
# un marqueur cherché tel quel échouerait au premier repli de ligne du fichier,
# ce qui ferait échouer le build pour une raison qui n'a rien à voir avec la
# substitution — constaté au premier build.
RUN /ragflow/.venv/bin/python -c "\
import sys; sys.path.insert(0, '/ragflow');\
from rag.prompts.generator import vision_llm_figure_describe_prompt as default_prompt, vision_llm_figure_describe_prompt_with_context as context_prompt;\
flat = lambda text: ' '.join(text.split());\
default = flat(default_prompt());\
context = flat(context_prompt(context_above='CTX_AVANT', context_below='CTX_APRES'));\
ours = 'reading an image extracted from a document that is being indexed';\
theirs = 'Describe spatial grouping';\
assert ours in default, default[:200];\
assert ours in context, context[:200];\
assert theirs not in default, 'prompt amont toujours en place (defaut)';\
assert theirs not in context, 'prompt amont toujours en place (avec contexte)';\
assert 'CTX_AVANT' in context and 'CTX_APRES' in context, 'variables Jinja perdues';\
print('Prompt figures OK :', len(default), 'et', len(context), 'caracteres charges par load_prompt()')"

# ─── Divergence 3 — `docling` figé au build, en CPU strict (ADR-043) ─────────
#
# CE N'EST PAS UNE DÉPENDANCE AJOUTÉE AU MOTEUR : `rag/app/naive.py` livre déjà
# `PARSERS["docling"] -> by_docling`, et l'`entrypoint.sh` de l'amont contient
# `ensure_docling()`, qui fait `uv pip install docling==2.71.0` **au démarrage
# du conteneur** quand `USE_DOCLING=true`. On installe donc au build exactement
# ce que l'amont installerait au démarrage, à la même version. La divergence
# porte sur le QUAND et le COMMENT, pas sur le QUOI.
#
# POURQUOI AU BUILD ET PAS AU DÉMARRAGE : le mécanisme de l'amont télécharge
# ~1,5 Go de roues Python depuis un miroir chinois (`pypi.tuna.tsinghua.edu.cn`)
# à chaque création de conteneur, au pire moment et sans somme de contrôle. Même
# raisonnement qu'ADR-030 pour les poids OCR : ce qui décide du comportement du
# moteur est figé dans une couche, pas récupéré sur le réseau au boot.
# `USE_DOCLING` reste donc à `"false"` dans `docker-compose.ragflow.yml` — c'est
# un drapeau d'*installation*, pas un drapeau de fonctionnalité : Docling est
# disponible parce qu'il est dans l'image, et `check_installation()` le voit.
#
# POURQUOI `--torch-backend cpu` EXPLICITEMENT : `docling` dépend de `torch` via
# `docling-ibm-models`. Une installation sans précaution résout `torch` sur la
# roue **CUDA** par défaut (constaté le 2026-08-23 lors de la campagne de
# mesure) : ~2,5 Go de bibliothèques CUDA inertes dans une image qui ne verra
# jamais de GPU. CONTRIBUTING.md §2 réserve la VRAM au service d'inférence dédié —
# `ragflow-cpu` tourne en `DEVICE: cpu`, sans `--gpus`, et doit le rester. Le
# garde-fou ci-dessous refuse une image où `torch.version.cuda` ne serait pas
# `None` ou où un paquet `nvidia-*` se serait glissé.
#
# LES MODÈLES SONT TÉLÉCHARGÉS ICI, PAS À LA PREMIÈRE INGESTION. Sans cela,
# `DoclingParser.parse_pdf()` déclenche un `snapshot_download` Hugging Face à la
# première conversion : plusieurs centaines de Mo dans le chemin critique d'un
# job, et une ingestion qui échoue si le réseau manque. `docling-tools models
# download` les fige (~565 Mo : mise en page « heron », TableFormer, RapidOCR),
# et `DOCLING_ARTIFACTS_PATH` est le seul mécanisme qui les fasse trouver :
# `PdfPipelineOptions().artifacts_path` vaut `None` dans `by_docling`, et sans
# cette variable Docling ignore le cache local et repart sur le Hub — vérifié,
# la conversion échoue en `LocalEntryNotFoundError` hors ligne.
#
# CE QUE CETTE INSTALLATION DÉPLACE DANS L'IMAGE, dit plutôt que découvert plus
# tard : `docling==2.71.0` plafonne `pillow<12` et `huggingface_hub<1`. Trois
# paquets déjà présents changent donc de version — `pillow` 12.2.0 -> 11.3.0,
# `huggingface_hub` 1.3.1 -> 0.36.2, `pydantic-settings` 2.12.0 -> 2.15.0 — et
# aucun n'est supprimé. RAGFlow n'utilise de `huggingface_hub` que
# `snapshot_download` et `hf_hub_download`, stables des deux côtés de la
# majeure. C'est exactement ce que subirait l'amont avec `USE_DOCLING=true`.
# **Ces trois changements atterrissent APRÈS les deux garde-fous ci-dessus** :
# ceux-ci ont donc validé un état de l'image qui n'est plus celui qui est livré.
# Le garde-fou de cette couche rejoue leurs deux invariants pour cette raison
# précise, et pas par redondance décorative.
ARG DOCLING_VERSION=2.71.0

# Chemin de cache lu par `docling.datamodel.settings` (préfixe `DOCLING_`).
# Posé en `ENV` et pas dans le compose : un réglage qui décide si le moteur
# télécharge ou non 565 Mo en pleine ingestion appartient à l'image.
ENV DOCLING_ARTIFACTS_PATH=/root/.cache/docling/models

RUN VIRTUAL_ENV=/ragflow/.venv uv pip install \
        --python /ragflow/.venv/bin/python \
        --torch-backend cpu \
        --no-cache-dir \
        "docling==${DOCLING_VERSION}" \
 && /ragflow/.venv/bin/docling-tools models download -q layout tableformer rapidocr

# Garde-fou de construction. Trois choses qu'aucun test en aval ne rattraperait :
#   1. le budget matériel — une roue CUDA passerait inaperçue jusqu'au jour où
#      quelqu'un exposerait un GPU au conteneur ;
#   2. l'autonomie réseau — la conversion est rejouée avec `HF_HUB_OFFLINE=1`
#      sur un PDF fabriqué ici, donc une image dont les modèles manquent ne sort
#      pas du build au lieu d'échouer à la première ingestion réelle. On vérifie
#      au passage que les sections portent bien leur balise de position `@@`,
#      la propriété même qui motive ADR-043 ;
#   3. la non-régression des deux divergences précédentes après les
#      changements de version décrits plus haut.
RUN /ragflow/.venv/bin/python -c "\
import os, sys; sys.path.insert(0, '/ragflow');\
import importlib.metadata as md;\
import torch;\
assert torch.version.cuda is None, ('roue torch CUDA installee', torch.version.cuda);\
assert not torch.cuda.is_available(), 'torch voit un GPU';\
nv = sorted((d.metadata['Name'] or '') for d in md.distributions() if (d.metadata['Name'] or '').startswith('nvidia-'));\
assert not nv, ('paquets CUDA installes', nv);\
os.environ.pop('DOCLING_SERVER_URL', None);\
os.environ['HF_HUB_OFFLINE'] = '1';\
from deepdoc.parser.docling_parser import DoclingParser;\
p = DoclingParser();\
assert p.check_installation(), 'le moteur ne voit pas Docling';\
from reportlab.pdfgen.canvas import Canvas;\
c = Canvas('/tmp/guard.pdf'); c.setFont('Helvetica', 14); c.drawString(72, 720, 'Chiffre d affaires 2026 : 1 234 euros'); c.showPage(); c.save();\
sections, tables = p.parse_pdf(filepath='/tmp/guard.pdf', binary=None, callback=None);\
os.remove('/tmp/guard.pdf');\
assert sections, 'conversion Docling hors ligne sans section';\
assert any('@@' in str(s[1]) for s in sections), ('positions perdues', sections[:2]);\
import onnxruntime as ort;\
from deepdoc.vision.postprocess import build_post_process;\
d = '/ragflow/rag/res/deepdoc';\
classes = ort.InferenceSession(d + '/rec.onnx', providers=['CPUExecutionProvider']).get_outputs()[0].shape[-1];\
pp = build_post_process({'name': 'CTCLabelDecode', 'character_dict_path': d + '/ocr.res', 'use_space_char': True});\
assert classes == len(pp.character), ('ADR-030 desaligne', classes, len(pp.character));\
from rag.prompts.generator import vision_llm_figure_describe_prompt as fp;\
assert 'reading an image extracted from a document that is being indexed' in ' '.join(fp().split()), 'ADR-035 perdu';\
import PIL, huggingface_hub;\
print('Docling OK :', torch.__version__, '- conversion hors ligne', len(sections), 'sections, positions emises');\
print('Non-regression OK : ADR-030', classes, 'classes - ADR-035 en place - pillow', PIL.__version__, '- huggingface_hub', huggingface_hub.__version__)"
