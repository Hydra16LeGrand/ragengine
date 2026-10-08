# ADR-030 — Modèle de reconnaissance OCR latin pour DeepDOC : les accents français au prix du chinois

**Date :** 2026-08-10 · **Statut :** accepté

## Contexte

ADR-023 route tout document sans couche texte vers `layout_recognize: "DeepDOC"`, donc vers l'OCR.
ADR-029 a maintenu `Plain Text` sur les documents à couche texte propre : ce routage contourne le
problème décrit ici, il ne le résout pas — un document **vraiment** scanné passe toujours par
DeepDOC. Or les accents en ressortaient corrompus, et l'hypothèse « qualité d'image » était fausse.

Cause racine, constatée dans le conteneur déployé : le dictionnaire de caractères livré avec
`infiniflow/ragflow:v0.26.4` (`/ragflow/rag/res/deepdoc/ocr.res`) compte 6623 entrées,
majoritairement chinoises, et **ne contient pas** `ç ï ô ê â û ù î œ` (seuls `é è à` y figurent). Le
décodeur est un CTC (`deepdoc/vision/postprocess.py::CTCLabelDecode`) : il ne peut physiquement pas
émettre un caractère absent de son vocabulaire. Ce n'est donc ni un réglage ni un problème de
netteté — c'est un plafond dur, et l'image ne livre qu'un seul couple modèle/dictionnaire, sans
variante sélectionnable.

Le remplacement est direct parce que **DeepDOC est du PaddleOCR PP-OCR exporté en ONNX**
(`deepdoc/vision/ocr.py::TextRecognizer` : `rec_image_shape = [3, 48, 320]`, `CTCLabelDecode`,
`character_dict_path = <model_dir>/ocr.res`, `use_space_char: True`) : mêmes forme d'entrée,
décodeur et format de dictionnaire qu'un modèle PaddleOCR latin.

Alternative écartée avant celle-ci : le VLM comme parseur de page (`layout_recognize` acceptant un
id de modèle `IMAGE2TEXT`) — un appel vision par page, coût prohibitif. La vision reste réservée
aux images à décrire (ADR-027).

## Vérification de compatibilité — le point où une erreur serait invisible

Un dictionnaire de 502 entrées remplace un dictionnaire de 6623. Si la dimension de sortie du modèle
ne correspondait pas exactement à la liste construite par `build_post_process`, le décodage CTC
produirait des caractères **systématiquement décalés** : un OCR faux mais plausible, qui passe un
test de fumée. Mesuré dans le conteneur, avant toute ingestion :

| | entrée | sortie (classes) | `len(build_post_process(...).character)` |
|---|---|---|---|
| officiel | `x` `[N, 3, 48, W dyn.]` | 6625 | 6625 (= 6623 lignes + espace + `blank`) |
| latin | `x` `[N, 3, 48, W dyn.]` | **504** | **504** (= 502 lignes + espace + `blank`) |

Alignement exact des deux côtés. Reconnaissance de contrôle sur huit lignes françaises accentuées
connues, en appelant directement `TextRecognizer` : **83,4 %** d'exactitude caractère et **0/8**
lignes exactes avec le modèle officiel, **100,0 %** et **8/8** avec le latin. Ce contrôle est rejoué
à la construction de l'image (garde-fou `RUN` de `docker/ragflow-cpu.Dockerfile`) : une image
désalignée ne peut pas sortir du build.

## Mesure du gain — scans à vérité terrain connue

`measure_ocr_latin_vs_official.py` (outil interne), résultats bruts dans
`ocr-latin-avant-20260810-212809` (mesure interne) et `.../ocr-latin-apres-20260810-213102/`.
Méthode : deux PDF francophones **à couche texte** du corpus versionné, rastérisés à 200 dpi
(`pdftoppm` + Pillow) pour leur retirer leur couche texte — couche résiduelle vérifiée nulle, donc
réellement routés vers l'OCR. La vérité terrain est exactement le texte du PDF d'origine, ce qui rend
le taux exact et non estimé. Aucun document personnel de l'utilisateur n'est employé ni versionné.

| document (rastérisé) | mots reconnus | couverture source | accents restitués | accents jamais rendus |
|---|---|---|---|---|
| `11-pdf-long-multipage.pdf` (8 p.) — avant | 88,8 % | 79,7 % | 37,2 % | `à` `è` |
| `11-pdf-long-multipage.pdf` (8 p.) — **après** | **100,0 %** | **100,0 %** | **100,0 %** | — |
| `02-pdf-multi-column-cv-fr.pdf` (1 p.) — avant | 83,3 % | 77,1 % | 31,2 % | `É` `à` `ç` |
| `02-pdf-multi-column-cv-fr.pdf` (1 p.) — **après** | **94,7 %** | **96,6 %** | **100,0 %** | — |

Soit +11,2 et +11,4 points de mots reconnus, +20,3 et +19,5 points de couverture source, et la
restitution des accents qui passe de ~1/3 à la totalité sur les deux documents.

## Décision

**Le modèle de reconnaissance de DeepDOC est remplacé par PP-OCRv5 latin (Apache 2.0), figé dans une
image dérivée `ragengine/ragflow-cpu:v0.26.4-ragengine.1`** (`docker/ragflow-cpu.Dockerfile`,
câblée dans `docker/docker-compose.ragflow.yml`). *Le tag portait initialement `-ocr-latin` ; il a
été renommé le 2026-08-13 quand une seconde divergence s'y est ajoutée (ADR-035) — un tag qui nomme
une seule des deux serait une fausse piste. `-ragengine.<n>` est désormais le numéro de révision de
l'**ensemble** des écarts posés sur `v0.26.4`, énumérés dans l'en-tête du Dockerfile.* Deux fichiers seulement sont remplacés : `rec.onnx` et
`ocr.res`. `det.onnx` n'est **pas** touché — la détection de zones est agnostique à la langue, la
remplacer ajouterait du risque sans bénéfice. Les fichiers sont figés à la construction, avec sommes
de contrôle épinglées : pas de téléchargement au démarrage du conteneur, et une republication amont
silencieuse fait échouer le build au lieu de changer l'OCR sans prévenir (même raisonnement que
`inference.Dockerfile`).

Aucun code de RAGFlow n'est modifié ni contourné (ADR-001) : on substitue un fichier de poids et son
dictionnaire, aux emplacements que le moteur compose lui-même.

**Conséquence assumée, décision utilisateur du 2026-08-09 — pas une dette à rembourser :** le modèle
latin ne reconnaît plus le chinois. Vérifié : sur des lignes chinoises nettes (`中文识别测试`,
`公司年度报告`), il rend une chaîne **vide**, là où la même image de contrôle française sort parfaite.
Le français et l'anglais sont la priorité du produit (CONTRIBUTING.md §1, corpus principalement
francophone) ; la perte du chinois **scanné** est explicitement acceptée. Portée réelle de cette
perte : elle ne concerne que l'OCR. Un document chinois **à couche texte** reste intégralement
indexé (routé `Plain Text` par ADR-023/029, l'OCR n'intervient pas), et l'embedding comme le
reranking restent multilingues (BGE-M3, ADR-003) — le chinois reste donc pleinement cherchable, sauf
s'il arrive scanné.

**Non tranché, signalé plutôt que passé sous silence** : l'un des motifs d'ADR-029 pour garder
`Plain Text` par défaut sur les documents à couche texte propre était que `DeepDOC` y abîmait les
accents. Ce motif-là tombe. Les autres (couverture, entrelacement de colonnes, position des
fragments) n'ont **pas** été remesurés avec le modèle latin, et ADR-029 reste donc en vigueur
telle quelle : on ne change pas un défaut de production sans la mesure qui le justifie. Rouvrir
l'arbitrage demanderait de rejouer `measure_deepdoc_vs_plaintext_real_docs.py` sur cette image.

*Suite (2026-08-11) — c'est fait : la mesure a été rejouée sur cette image, le motif est confirmé
tombé, et l'arbitrage est tranché par **ADR-031** (choix explicite au precheck, `DeepDOC` proposé
par défaut sur les PDF). Ce paragraphe est conservé tel quel : il datait d'avant la mesure.*

**Réversibilité** : revenir en arrière, c'est reposer `image: infiniflow/ragflow:v0.26.4` dans
`docker-compose.ragflow.yml` et recréer le conteneur — ce qui abandonne **aussi** la divergence
d'ADR-035, les deux vivant désormais dans la même image. Aucune donnée indexée n'est liée au choix du
modèle ; seuls les documents scannés ingérés entre-temps porteraient le texte de l'autre modèle et
demanderaient une réingestion.
