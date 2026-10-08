# ADR-024 — Garde-fou qualité : avertissement structuré sur le job + indicateur dénormalisé sur le document

**Date :** 2026-08-07 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- **Mécanisme (décision utilisateur)** : les deux — un `JobWarning` structuré sur `IngestionJob`
  (même forme que `JobError`, ADR-020) porte le détail, **et** un champ dénormalisé léger sur
  `Document` (`has_quality_warning: bool`) porte la visibilité liste, écrit dans la même
  transaction que la transition vers `completed`. `DocumentStatus` n'est **pas** étendu — il reste
  une pure projection 1:1 de `JobState` (`DOCUMENT_STATUS_BY_JOB_STATE`), l'avertissement est une
  dimension orthogonale à l'état du pipeline (le job a réussi techniquement ; c'est la qualité du
  contenu qui est en cause).
- **Deux points d'écriture existants à instrumenter, pas un seul** (`app/workers/tasks/ingestion.py`) :
  `_poll`, ligne ~835 (`entering and finished` — cas rapide, un document qui termine en un seul
  sondage) **et** ligne ~842 via `_complete()` (cas normal — transition d'entrée déjà écrite, on ne
  sonde plus que la conclusion). Les deux mènent à `JobState.COMPLETED` pour l'étape d'indexation ;
  en oublier un reproduit exactement la classe de bug déjà rencontrée sur ce projet (dispatch
  manquant à un point de couture qui semblait indépendant, cf. rappels d'exécution
  plan interne).
- **Seuils de déclenchement (policy produit)** : seulement les signaux jugés fiables à l'issue des
  étapes 0-2, pas tous les signaux mesurés. Détail au point 3.
- **Logique de calcul déjà écrite, à réutiliser, pas à redériver** :
  `measure_chunking_quality.py` (outil interne) (`significant_char_count`, `source_coverage`,
  `DEGENERATE_CHUNK_THRESHOLD_CHARS`) — porter cette logique dans `app/`, ne pas la dupliquer à la
  main.

## Contexte

plan interne, Étape 4. Le routage retenu (ADR-023) n'atteint pas 100 % des cas : deux exemples
concrets motivent ce garde-fou — `chunk_method=email` sur un PDF « réussit » (`run=done`) avec un
contenu corrompu (couverture du texte source à 2,2 % contre 88-100 % sur les cas sains, matrice de
l'étape 2) ; `28-pdf-corrupted.pdf` finit `completed` avec 0 chunk, sans aucun signal (étape 0). La
promesse produit (« extraction maximisée + qualité mesurée et signalée ») exige que ces deux cas ne
puissent plus passer inaperçus.

## Décisions

**1. Mécanisme : `JobWarning` + `Document.has_quality_warning`, pas de nouveau `DocumentStatus`.**
Justifié par le principe déjà en place : `DocumentStatus` est un état de pipeline à écrivain
unique (le service de transition), et un `JobState.COMPLETED` ne doit pas pouvoir projeter vers
deux statuts document différents selon un critère qui lui est étranger — ça briserait l'invariant
testé (`DOCUMENT_STATUS_BY_JOB_STATE`). Le champ dénormalisé sur `Document` suit exactement le
même raisonnement produit que `DocumentStatus` lui-même (éviter une jointure pour peupler une
liste), sans toucher à la machine à états.

**2. `JobWarning` (nouveau schéma, `app/domain/schemas/ingestion_job.py`, à côté de `JobError`) :**
```python
class JobWarning(BaseModel):
    code: str            # ex. "zero_chunks", "degenerate_content", "low_source_coverage"
    message: str          # lisible, avec le chiffre qui le fonde (cf. PrecheckReview, ADR-012)
    metrics: dict[str, float | int | None]  # valeurs brutes ayant motivé le déclenchement
```
Champ `warning: JobWarning | None` sur `IngestionJob`/`IngestionJobRead`, écrit **uniquement** à la
transition vers `JobState.COMPLETED` de l'étape d'indexation (jamais à une transition
intermédiaire) — ne bloque jamais, ne change aucune transition existante, ne rend rien
`retryable`.

**3. Seuils de déclenchement — seulement les signaux fiables, documentés comme tels :**
   - **0 chunk après complétion → toujours**, quel que soit le format. Signal univoque, aucun faux
     positif possible.
   - **% de chunks dégénérés (`DEGENERATE_CHUNK_THRESHOLD_CHARS = 20`, déjà défini dans le
     harnais) au-dessus d'un seuil**, à l'exclusion des documents dont le total de caractères
     significatifs extraits est lui-même très court — sans cette exclusion, un document
     légitimement court (`25-pdf-single-word.pdf`, 100 % « dégénéré » par construction, pas un
     défaut) déclencherait un faux positif systématique. Seuil et logique d'exclusion exacts
     laissés à l'implémentation, informés par la baseline (les résultats de mesure internes
     run-20260807-104858/`), mais **doivent** traiter ce cas — sinon reproduire ce faux positif
     connu et documenté est un bug d'implémentation, pas une surprise.
   - **Couverture du texte source (PDF uniquement, référence `pdfplumber`) sous un seuil bas** —
     déclenche sur une perte sévère (`email` sur PDF, 2,2 %), pas sur le bruit de mesure déjà
     documenté (le cas légitime le plus bas de la baseline, en-têtes/pieds répétés, est à 44,4 %).
     Choisir un seuil nettement en dessous de ce plancher connu (ex. de l'ordre de 25-30 %, à
     affiner par l'implémentation) plutôt qu'un seuil proche qui ferait du bruit sur des documents
     sains.
   - **Hors périmètre v1, volontairement** — à documenter dans le code, pas à oublier en silence :
     - **entrelacement de colonnes** : l'étape 0 a déjà établi un plancher de bruit non nul propre
       à l'heuristique (34,7 % sur un témoin sans complexité) — pas assez calibré pour trancher un
       seuil sans sur-déclencher.
     - **taux de mots reconnus seul** : faux positif certain sur tout document CJK (0 token
       alphabétique reconnu par construction, `word_recognition_rate` renvoie déjà `None` dans ce
       cas — le garde-fou doit ignorer une métrique à `None`, pas la traiter comme un échec).
     - **couverture du texte source pour un format non-PDF** : pas de référence indépendante
       équivalente à `pdfplumber` pour du docx/xlsx/csv/image — aucun seuil fiable à poser
       aujourd'hui.

**4. Points d'instrumentation exacts** (`app/workers/tasks/ingestion.py`) : le calcul et l'écriture
du `JobWarning`/`Document.has_quality_warning` doivent avoir lieu **après** que les chunks finaux
soient disponibles côté moteur mais **dans la même transaction** que l'écriture de
`JobState.COMPLETED`, aux deux points d'écriture existants (`_poll` ligne ~835 et `_complete()`
ligne ~365, tous deux atteignables pour l'étape d'indexation) — factoriser dans une fonction unique
appelée par les deux plutôt que dupliquer la logique deux fois, précisément pour éviter la classe
de bug « couture oubliée entre deux points d'appel qui semblaient indépendants ».

## Conséquences

- Nouvelle migration Alembic (`Document.has_quality_warning`, colonne `IngestionJob.warning` en
  JSON — même pattern que `IngestionJob.error`).
- `IngestionJobRead`/`DocumentRead` (schémas Pydantic) exposent les nouveaux champs ; régénération
  du client API frontend (`npm run generate:api`) nécessaire avant tout travail frontend sur le
  badge.
- Le frontend doit afficher l'avertissement quelque part (liste de documents pour la visibilité,
  détail du document/job pour le message précis) — portée exacte (quel composant, quel libellé)
  laissée à l'implémentation, pas figée par cette ADR.
- Les signaux exclus du v1 (entrelacement, couverture non-PDF) restent calculés et exposés dans le
  harnais de mesure pour analyse manuelle ; ils ne bloquent ni n'avertissent automatiquement tant
  qu'ils n'ont pas été mieux calibrés — à revisiter si de nouveaux cas dégradés non couverts
  apparaissent en usage réel.
