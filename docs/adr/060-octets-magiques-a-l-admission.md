# ADR-060 — Validation du contenu réel d'un téléversement par ses octets magiques

**Date :** 2026-09-17 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 9

## Contexte

`resolve_content_type` (`app.services.documents`) s'appuie sur `mimetypes.guess_type(filename)`,
puis à défaut sur le type déclaré par le client — jamais sur le contenu. `ensure_upload_within_limits`
compare ce résultat à une liste blanche mais ne regarde, elle non plus, aucun octet. Un fichier
quelconque renommé en `.pdf` la traversait donc sans être détecté ; `looks_like_pdf`/`looks_like_xlsx`
(`app.ingestion.precheck`) n'étaient consultées qu'au precheck, pour le rapport dégradé, jamais à
l'admission.

## Décision

`app.services.file_signatures.ensure_content_matches_signature(content, content_type)`, appelée dans
`DocumentService.register` juste après `ensure_upload_within_limits` (même emplacement, avant
l'empreinte SHA-256 et toute écriture). Un type de média sans correspondance à ses octets déclenche
`UnsupportedMediaTypeError` (`415`), avec le `content_type` en extension du Problem Details.

Trois familles, une par forme de conteneur réellement présente dans `upload_allowed_content_types` —
vérifiée dans le code, pas réinventée :

- **Signature fixe** (PDF, images PNG/JPEG/TIFF/GIF/BMP/WEBP) : comparaison directe des premiers
  octets. `looks_like_pdf` (`app.ingestion.precheck.pdf`) est réutilisée telle quelle, pas dupliquée.
- **Conteneur ZIP** (DOCX/XLSX/XLSM/PPTX) : le magic ZIP seul (`PK\x03\x04`) ne distingue pas les
  quatre formats OOXML, qui le partagent tous — un `.pptx` renommé en `.docx` le passerait. Le
  répertoire central de l'archive (`ZipFile.namelist()`, **aucune entrée décompressée** — pas le
  chemin de la bombe de décompression, ADR-061) est inspecté pour `[Content_Types].xml` (commun aux
  quatre) et le dossier caractéristique (`word/`, `xl/`, `ppt/`). Le `.xlsm` officiel
  (`application/vnd.ms-excel.sheet.macroEnabled.12`) reste un conteneur OOXML malgré son type de
  média hérité de la famille `vnd.ms-excel` — les deux casses observées (`macroEnabled`/
  `macroenabled`, selon la table `mimetypes` de la plateforme) pointent le même vérificateur.
- **Conteneur OLE2/CFBF** (DOC/XLS/PPT, pré-2007) : les trois partagent le même en-tête de 8 octets.
  **Écarté** : les distinguer précisément (bibliothèque type `olefile`, absente de la bibliothèque
  standard). Non justifié — la menace visée est un fichier quelconque déguisé en document Office, pas
  la confusion entre deux formats Office legacy eux-mêmes acceptés l'un comme l'autre à l'admission.

**Formats sans octets magiques** (CSV, TXT, MD, HTML, JSON, `message/rfc822`) : acceptés
explicitement (entrée de table à `None`, jamais absents), sans contrôle de contenu — un texte brut
n'a pas de signature binaire, et il est indexé tel quel, jamais exécuté.

**Aucune nouvelle dépendance** : `zipfile` (stdlib) pour l'inspection ZIP, comparaisons d'octets pour
le reste. `openpyxl`/`Pillow`, déjà présentes (precheck, tests), ne sont pas ajoutées au runtime pour
ce correctif.

## Conséquences

- Un fichier renommé (n'importe quel format vers un autre) est refusé avant tout traitement.
- Résiduel accepté, documenté dans le code : DOC/XLS/PPT legacy ne se distinguent pas entre eux par
  octets magiques (partagent le même conteneur CFBF).
- Défaut trouvé en chemin, hors périmètre de ce correctif : `mimetypes.guess_type(".xlsm")` renvoie
  `application/vnd.ms-excel.sheet.macroEnabled.12` sur cette machine (table système, absent des
  types intégrés à `mimetypes`), casse différente de l'entrée `upload_allowed_content_types`
  (minuscule) — un `.xlsm` peut donc être refusé par la liste blanche **avant** d'atteindre ce
  contrôle, selon la table `mimetypes` du conteneur de déploiement. Pas corrigé ici : ni l'un des
  quatre constats du mandat, ni une régression introduite par ce correctif (le comportement était
  déjà celui de la liste blanche existante) — signalé pour arbitrage.
- Tests : `backend/tests/services/test_file_signatures.py` (tous les types acceptés, fichier renommé,
  vide, tronqué, confusion inter-formats OOXML, classeur `openpyxl` réel) ;
  `backend/tests/services/test_documents.py` et `backend/tests/api/v1/test_document_lifecycle_routes.py`
  passent inchangés (fixtures déjà préfixées `%PDF-1.4`).
