# ADR-037 — Résidu OCR après la description de figure : bug moteur constaté, non corrigé côté RAGEngine

**Date :** 2026-08-15 · **Statut :** constat établi, non corrigé (ADR-001) · **Portée :** fragments
issus de `VisionFigureParser` (ADR-035)

## Contexte

Retour d'usage sur `Avenant suite.pdf` (base TEST3, lecture seule) : sur une zone de signature/
cachet à une page, 3 fragments existent. Le fragment 2 contient une description de vision propre
du bloc signature, suivie **sans séparateur** d'une queue de charabia OCR (« …centered with the
text "Page 1/1".apfne\nPRO 1450 Abidiak.con\n… »). Le fragment 3 est entièrement du charabia et ne
sert à rien.

## Investigation — code moteur *et* API réelle, pas l'un ou l'autre

**Code lu dans le conteneur `ragflow-cpu` déployé** :

1. `deepdoc/parser/pdf_parser.py::_extract_table_figure` — chaque figure détectée par la mise en
   page est un groupe de boîtes OCR (`figures[layoutno]`). Ligne clé :
   `txt = "\n".join([b["text"] for b in bxs]); ... res.append((img, [txt]))` — la description
   initiale d'une figure est **toujours** le texte OCR brut trouvé dans/près de sa zone, jamais
   vide (une figure sans texte OCR à proximité est même silencieusement écartée).
2. `deepdoc/parser/figure_parser.py::VisionFigureParser.__call__` :
   `self.descriptions[figure_num] = txt + "\n".join(self.descriptions[figure_num])` — `txt` est la
   description du modèle de vision ; `self.descriptions[figure_num]` est encore, à ce point, la
   liste `[ocr_brut]` construite en (1). Le résultat est une **concaténation directe, sans
   séparateur**, description de vision + OCR brut résiduel. Aucune vérification que la description
   de vision couvre déjà le contenu, aucune déduplication.

**Vérifié contre l'API réelle** (`GET /datasets/{id}/documents/{doc}/chunks`, TEST3, lecture
seule) :
- Fragment 2 : `positions = [[1, 280, 1689, 1392, 1898]]` — la description de vision se termine
  bien par « …the text "Page 1/1". » directement suivi de « apfne\nPRO 1450 Abidiak.con\n… »,
  confirmant la concaténation lue dans le code au caractère près.
- Fragment 3 : `positions = [[1, 1127, 1637, 1548, 1841]]` — cette boîte est **entièrement
  contenue** dans celle du fragment 2 (1127-1637 ⊂ 280-1689 en x, 1548-1841 ⊂ 1392-1898 en y) :
  la mise en page a détecté une **seconde zone de figure**, imbriquée dans la première, avec son
  propre appel au modèle de vision — dont la sortie sur ce recadrage plus petit et moins net est de
  mauvaise qualité, puis subit le même bug de concaténation.

Les deux symptômes rapportés sont donc confirmés, avec cause exacte identifiée dans
`deepdoc/parser/figure_parser.py` (bug moteur, pas une hypothèse).

## Décision : pas de correctif côté RAGEngine

**ADR-001 interdit de modifier `ragflow-cpu`.** Les deux leviers RAGEngine envisagés ont été
écartés :

1. **Filtrer le résidu OCR en fin de fragment** (normalisation, `normalize_chunk_whitespace`,
   `providers/engine/ragflow/client.py`) : le seul motif observable sur cet exemple — un point suivi
   directement d'une minuscule sans espace (`"Page 1/1".apfne`) — est un artefact de coïncidence
   (la description de vision se termine par une phrase ponctuée), pas une propriété garantie de la
   concaténation. Si la description de vision ne se termine pas par une ponctuation, ou si le texte
   OCR résiduel commence par une majuscule, rien ne distingue plus le résidu du contenu légitime.
   Une regex sur ce seul indice serait une heuristique devinée sur un point de données, exactement
   ce que la mission demande d'éviter.
2. **Écarter/fusionner un fragment dont la position est contenue dans celle d'un autre** (même
   fichier de normalisation) : signal géométrique réel et généralisable, mais validé sur un **seul**
   document. Une figure légitimement imbriquée dans une autre (schéma avec vignette, diagramme
   contenant un encart) produirait le même chevauchement — le mécanisme ne peut pas distinguer
   « doublon de détection » de « sous-figure voulue ». Écarter automatiquement risquerait de faire
   disparaître silencieusement un contenu réel ailleurs dans le corpus, un coût plus grave qu'un
   fragment bruyant conservé. Pas assez de données pour ce risque.

**Aucun correctif appliqué.** Le résidu OCR en fin de fragment 2 et le fragment 3 intégralement
bruité restent tels quels — limite connue du moteur (version RAGFlow déployée), pas de RAGEngine.

## Conséquences

- Un document dont une zone de figure/signature est détectée deux fois (boîtes imbriquées) peut
  produire un fragment supplémentaire sans valeur de recherche, et le fragment principal peut porter
  une queue de texte OCR bruyant après sa description de vision. Sans effet sur la qualité de
  recherche au-delà du bruit lui-même (aucun cas observé où cela masque ou remplace un contenu
  correct).
- **Piste laissée ouverte, non engagée ici** : si ce signal de conteneurs de positions se confirme
  sur plusieurs documents/corpus à l'avenir (pas seulement celui-ci), la fusion ou l'écart de
  fragments strictement contenus redeviendrait un candidat raisonnable — mais seulement avec un
  échantillon assez large pour distinguer doublon de détection et sous-figure légitime.
- Non re-testé sur RAGFlow au-delà de la version actuellement déployée (`v0.26.4`) : une mise à jour
  moteur pourrait corriger ce point sans action côté RAGEngine.
