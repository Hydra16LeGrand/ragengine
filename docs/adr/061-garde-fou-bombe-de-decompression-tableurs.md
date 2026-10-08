# ADR-061 — Garde-fou bombe de décompression `.xlsx`/`.xlsm`

**Date :** 2026-09-17 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 10

## Contexte

`app.ingestion.precheck.spreadsheet.analyze_xlsx` appelle `openpyxl.load_workbook` directement sur
le contenu reçu. Un `.xlsx`/`.xlsm` est une archive ZIP : `read_only=True` lit le classeur en flux
mais ne protège pas `sharedStrings.xml`, décompressé et parsé intégralement en mémoire avant que ce
module ne puisse borner quoi que ce soit. Un classeur de quelques dizaines de Ko peut décompresser en
plusieurs Go et tuer le worker Celery (`queue=default`, pas de plafond mémoire par tâche).

## Décision

`_guard_against_zip_bomb(content, settings)`, appelée en tête d'`analyze_xlsx`, avant tout appel à
`openpyxl.load_workbook`. Elle lit uniquement le **répertoire central** de l'archive
(`ZipFile.infolist()`) — aucune entrée n'est décompressée pour ça, seules les tailles déclarées par
le format ZIP lui-même sont additionnées et comparées :

- **Ratio par entrée** (`file_size` / `compress_size`) au-delà de `spreadsheet_max_decompression_ratio`
  (défaut **100:1**) — ignoré sous 1 Mio décompressé (`_MIN_RATIO_CHECK_BYTES`) : une petite entrée
  interne du conteneur (`_rels/.rels`, `[Content_Types].xml`) peut afficher un ratio élevé sans le
  moindre risque.
- **Taille décompressée totale** au-delà de `spreadsheet_max_decompressed_bytes` (défaut **1 Gio**).

Les deux seuils sont réglables (`RAGENGINE_SPREADSHEET_MAX_DECOMPRESSION_RATIO`,
`RAGENGINE_SPREADSHEET_MAX_DECOMPRESSED_BYTES`). Le dépassement lève `ExtractionError`, avec un
message clair — déjà couvert par le filet de sécurité existant d'`analyzer.analyze_document_sync`
(`except ExtractionError` / `except Exception`) qui produit un rapport de precheck **dégradé**, jamais
une exception brute ni une trace exposée à l'appelant : c'est ce filet, pas ce correctif, qui garantit
l'échec propre du job (`supported=True` — le format reste reconnu —, `error.code=EXTRACTION_FAILED`,
`error.message` = le texte du garde-fou).

### Seuils, mesurés empiriquement

| Seuil | Valeur | Mesure à l'appui |
|---|---|---|
| Ratio de décompression | 100:1 | Classeur `openpyxl` le plus compressible produit (20 000 lignes identiques, 21 colonnes) : **~30:1**. Flux DEFLATE pathologique (`zlib`, niveau 9, 200 Mio de zéros) : **~1029:1**, proche du maximum théorique d'un flux DEFLATE unique (~1032:1). 100 laisse 3× de marge sur l'usage légitime mesuré, loin sous le plafond réel d'une bombe. |
| Taille décompressée totale | 1 Gio | Même classeur pathologique `openpyxl` (930 Kio compressés) : **~27 Mio** décompressés. 1 Gio laisse une marge large (≈ 37×) sur l'usage légitime tout en arrêtant loin en amont d'une bombe visant plusieurs Gio — le cas décrit par l'audit. |

### Autres conteneurs ZIP du même pipeline

`.docx`/`.pptx` sont reconnus par le precheck pour leur seul aiguillage
(`app.ingestion.precheck.analyzer`, `_unanalyzed`) : ni l'un ni l'autre n'a d'extracteur, donc aucun
n'est jamais ouvert par ce module ni par un autre — RAGFlow les traite seul, dans son propre
conteneur, hors périmètre de RAGEngine (CONTRIBUTING.md §3). Rien à garder de plus dans ce dépôt.

## Conséquences

- Un classeur pathologique est refusé en quelques millisecondes (mesuré : suite de tests complète en
  0,25 s), sans qu'`openpyxl.load_workbook` ne soit jamais invoqué — vérifié par un test qui fait
  échouer `load_workbook` s'il est appelé.
- Un zip invalide (pas la même chose qu'une bombe) n'est pas intercepté ici : `openpyxl` lève sa
  propre erreur, déjà traduite en `ExtractionError` par le `except Exception` existant — pas de
  double diagnostic.
- Tests : `backend/tests/ingestion/precheck/test_spreadsheet.py` (classeur normal accepté, ratio
  absurde refusé sous les seuils par défaut, taille totale refusée indépendamment du ratio,
  `openpyxl` jamais invoqué, message de precheck propre bout en bout, classeur `openpyxl` réel le
  plus compressible resté sous les seuils par défaut).
