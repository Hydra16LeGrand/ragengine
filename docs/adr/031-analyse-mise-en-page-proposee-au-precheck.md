# ADR-031 — Analyse de mise en page proposée au precheck, plutôt qu'un défaut global basculé

**Date :** 2026-08-11 · **Statut :** accepté — révise l'arbitrage d'ADR-029

## Résumé

- Le motif écrit d'ADR-029 (`DeepDOC` abîme la qualité de texte) **est tombé** avec ADR-030,
  remesuré le 2026-08-11 contre l'image déployée : égalité stricte des mots reconnus sur 5
  documents sur 7.
- Reste un arbitrage **latence contre qualité de découpage**, que la mesure ne peut pas trancher :
  `DeepDOC` coûte ×3,8 de temps d'ingestion mais rend l'ordre des fragments exploitable
  (0 % → 100 %) et supprime les coupures mi-phrase (33-60 % → 0 %).
- Décision utilisateur : **exposer le choix au precheck avec `DeepDOC` proposé coché sur les PDF**,
  ni bascule du défaut global (qui supprimerait la porte de sortie sur un document volumineux), ni
  statu quo (dont le motif ne tient plus).
- **Le contrat d'API v1 ne bouge pas** : `heavy_ocr` reste `false` par défaut côté schéma. La
  bascule est une proposition d'interface, validée par un humain document par document.
- Réserves conservées : régression franche sur le document à tableau réglé, panel de 7 documents
  synthétiques insuffisant pour un routage plus fin.

## Contexte

ADR-029 gardait `Plain Text` par défaut sur les documents à couche texte propre, en acceptant
explicitement deux coûts (ordre de fragments inexploitable, phrases coupées en plein milieu) **en
échange** d'une meilleure qualité de texte. ADR-030 ayant supprimé le déficit d'accents de
`DeepDOC`, cette contrepartie a été remesurée avec le script d'ADR-029, contre l'image réellement
déployée, sur sept PDF à couche texte du corpus versionné — rapport complet et résultats bruts dans
`deepdoc-vs-plaintext-ocr-latin-20260811-201621` (mesure interne) :

- **Égalité stricte des mots reconnus sur 5 documents sur 7**, la métrique qui portait le grief.
  `Plain Text`, remesuré à neuf dans la même campagne, reproduit ses chiffres d'ADR-029 au chiffre
  près (une seule dérive, 0,4 pt) : le banc n'a pas dérivé, les écarts viennent bien du changement
  de modèle.
- Ce que `DeepDOC` rachète est inchangé et mesuré : fiabilité de position **0 % → 100 %**, coupures
  mi-phrase **33-60 % → 0 %** (chiffres ADR-029 sur documents réels, les seuls qui font foi sur ce
  point — le corpus synthétique y est aveugle).
- **Coût neuf, absent d'ADR-029 : le temps.** ×3,8 en moyenne (1,69 s/page contre 0,45 s/page),
  jusqu'à ×16,2 sur un document de 50 pages (63,5 s contre 3,9 s), sur l'image CPU déployée.

Le motif écrit d'ADR-029 est donc tombé, mais ce qui reste — un arbitrage latence/qualité — n'est
pas un résultat de mesure : c'est un choix produit, que la mesure a explicitement refusé de
trancher à la place de l'utilisateur.

## Décision

**Le mode d'extraction devient un choix explicite de l'écran d'aiguillage, avec l'analyse de mise
en page complète (`DeepDOC`) proposée cochée sur tout PDF.** Décision utilisateur du 2026-08-11, en
connaissance du coût de latence, préférée aux deux autres options : basculer le défaut global
supprimerait la porte de sortie d'un clic sur un document volumineux ; garder le statu quo
maintiendrait un défaut dont la raison ne tient plus.

Trois points de mise en œuvre, pas un de plus :

1. `suggestedDecision` (`frontend/src/api/precheck.ts`) propose `heavy_ocr` sur
   `format === "pdf"` au lieu de `is_likely_scanned`. **Restreint au PDF** parce que c'est le seul
   format où l'option décide de quelque chose : un tableur part en `chunk_method=table` sans passer
   par l'analyse de mise en page, et sur une image les deux options sont des no-op documentés
   (`app/ingestion/chunking_profile.py`) — l'interface ne propose pas ce qui ne ferait rien.
2. **Le libellé dit l'arbitrage au lieu de nommer la mécanique** : « Analyser la mise en page du
   document » remplace « Activer l'OCR lourd », et le texte d'aide porte les deux côtés (ordre des
   fragments et phrases entières d'un côté, ~3,8× de temps d'ingestion de l'autre, sans objet sur
   un scan). Une case dont le libellé tait son coût est cochée sans le savoir. Le badge distingue
   « Proposé par défaut » (produit) de « Suggéré par l'analyse » (constat du rapport).
3. **Le contrat d'API v1 est inchangé** : `PrecheckDecision.heavy_ocr` garde `default=False`. Un
   client REST qui omet le champ conserve le routage par `is_likely_scanned` (ADR-023) ; basculer
   ce défaut serait une mutation silencieuse de `/v1` (CONTRIBUTING.md §4). Pinné par
   `tests/domain/test_schemas.py::test_heavy_ocr_stays_opt_in_in_the_api_contract`.

Aucune ligne de logique de `chunking_profile.py` ne change. En particulier le `or
is_likely_scanned` de `profile_for_decision` reste : décocher la case ne peut pas condamner un scan
à `Plain Text`, qui n'y produirait rien.

## Conséquences

- Le chemin nominal d'un PDF versé par l'interface devient `DeepDOC` : ingestion plus lente
  (négligeable sur une page, ~1 minute au lieu de 4 s sur 50 pages), panneau de fragments enfin
  ordonné, plus de coupure mi-phrase. C'est un coût d'**ingestion** : ni la recherche ni le
  Playground ne ralentissent.
- L'acceptation en lot (« Accepter les suggestions pour tous ») lance donc `DeepDOC` sur tous les
  PDF du lot — voulu, le bouton accepte les propositions affichées et n'en invente pas de plus
  rapides ; son libellé le dit désormais.
- **Réserves de la mesure, non enterrées** : `03-pdf-ruled-table.pdf` reste une régression franche
  sous `DeepDOC` (couverture −12,7 pt, entrelacement −18,7 pt sur un tableau réglé), et un panel de
  7 documents synthétiques ne dit pas si c'est une famille de documents ou un cas isolé — pas de
  routage plus fin, faute de critère, même conclusion qu'ADR-029 sur 6 documents. Ce corpus est en
  outre aveugle aux défauts d'ordre et de coupure mi-phrase, qui n'apparaissent que sur documents
  réels : la mesure ne promet donc pas l'absence de régression sur un corpus réel.
- **Défaut d'interface trouvé en vérifiant le comportement réel** (pas par les tests, ni par le
  typage) : `PrecheckReview` initialisait ses cases avec `useState(suggestion.…)` à sa première
  monture, qui a lieu **avant** l'arrivée du rapport — la proposition n'apparaissait que dans le
  badge, la case restait décochée. Défaut préexistant, qui touchait aussi
  `optimize_for_tables`. Corrigé en ajoutant l'arrivée du rapport à la `key` du composant.
- **Réversible d'une ligne** : `heavyOcr: report.format === 'pdf'` redevient
  `report.isLikelyScanned`. Aucun document déjà indexé n'est affecté.
