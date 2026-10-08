# ADR-026 — Retrieval : rerank activé par défaut si disponible, garde-fou sur la combinaison rerank+seuil

**Date :** 2026-08-07 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- **Décision 1** : `RetrievalFilters.rerank` reste `bool` mais son comportement par défaut change —
  si l'appelant n'envoie **pas** explicitement `rerank`, le service active le reranking
  **automatiquement si un reranker est configuré sur la KB** (`reranker_model_id is not None`).
  Un `rerank=false` explicite reste toujours respecté (désactivation volontaire possible). Une KB
  sans reranker configuré se comporte exactement comme aujourd'hui (`rerank` sans effet).
- **Décision 2** : combiner `rerank=true` (effectif, donc après résolution du défaut) et
  `similarity_threshold` non nul est **refusé (422)** sauf si l'appelant pose explicitement
  `acknowledge_threshold_risk=true` (nouveau champ). Empêche la combinaison la plus coûteuse
  mesurée (chute de recall@5 de 0,889 à 0,593, Phase 12 Étape 2) de passer inaperçue, sans
  interdire un usage volontaire et informé.
- **Pourquoi maintenant, pas avant** : les deux risques étaient déjà documentés (ADR-012 §4) mais
  jamais chiffrés à l'échelle réelle — la matrice de l'Étape 2 de la Phase 12 fournit enfin la
  preuve qui justifie un changement de comportement par défaut, pas une intuition.

## Contexte

Phase 12, Étape 3 (arrêt obligatoire). La matrice de l'Étape 2 (432 appels réels contre RAGFlow)
établit deux faits chiffrés : (1) activer le reranking améliore mesurablement recall@1/recall@5/MRR
sur le corpus testé ; (2) le combiner à un seuil de similarité non nul dégrade fortement les
résultats, l'ADR-012 §4 avait déjà identifié ce risque en théorie (changement d'échelle du score
combiné) sans jamais le quantifier. Deux décisions utilisateur distinctes en découlent.

## Décisions

**1. Défaut intelligent pour `rerank`**, même logique produit que la Phase 11 Étape 5
(`layout_recognize` suivant `is_likely_scanned`) : un meilleur comportement par défaut invisible
plutôt qu'une case à cocher à découvrir. `RetrievalService` résout le défaut effectif ainsi :
- `rerank` explicitement fourni (`true` ou `false`) dans la requête → respecté tel quel.
- `rerank` absent de la requête → `true` si `knowledge_base.reranker_model_id is not None`,
  sinon `false` (comportement identique à aujourd'hui pour une KB sans reranker).

Le schéma `RetrievalFilters.rerank` passe donc de `bool` à `bool | None` (`default=None`), le
service porte la résolution du défaut — pas le schéma, qui ne connaît pas la KB.

**2. Garde-fou sur la combinaison à risque.** Après résolution du défaut ci-dessus, si le
`rerank` effectif est `true` **et** `similarity_threshold` est non nul (fourni et différent de
`0.0`), la requête est refusée avec `422 Unprocessable Entity` (RFC 9457) **sauf** si
`acknowledge_threshold_risk: bool = False` (nouveau champ de `RetrievalFilters`) vaut `true`. Le
message d'erreur cite le chiffre établi (chute mesurée de recall@5) plutôt qu'un avertissement
générique — même principe que `PrecheckReview` (ADR-012, constats chiffrés plutôt que champs
bruts).

**3. Champ fermé, comme `PrecheckDecision`.** `acknowledge_threshold_risk` rejoint le catalogue de
`RetrievalFilters` : toute nouvelle option doit avoir son effet vérifié contre l'instance réelle
avant d'être ajoutée — ici c'est déjà fait (Étape 2).

## Conséquences

- `backend/app/domain/schemas/retrieval.py` : `rerank: bool | None = None`,
  `acknowledge_threshold_risk: bool = False` sur `RetrievalFilters`.
- `backend/app/services/retrieval.py` : résolution du défaut `rerank` avant l'appel moteur ; garde
  422 avant tout appel RAGFlow (ne pas dépenser un appel moteur pour une requête qu'on va refuser).
- **Changement de comportement pour les intégrateurs API existants** qui n'envoient jamais
  `rerank` explicitement sur une KB avec reranker configuré : ils passent de `rerank=false` à
  `rerank=true` de facto. Effet attendu : latence légèrement accrue (appel reranker), qualité de
  résultat mesurément meilleure. À mentionner dans le changelog API.
- Le Playground (`frontend/src/features/playground/settings.ts`) fixait déjà `rerank: true` côté
  client par défaut — son comportement observable ne change pas, seul le défaut **serveur** pour
  les appels API directs change.
- `docs/api-guide.md` et le docstring de `RetrievalFilters` (description du champ `rerank`,
  `similarity_threshold`) à mettre à jour en cohérence — porté par l'implémentation, pas par cette
  ADR seule.

## Addendum (2026-08-08) — le seuil non précisé n'était pas neutre

Trouvé en campagne de vérification E2E, contre l'instance RAGFlow réellement déployée : la Décision
2 protège la combinaison rerank effectif + seuil **explicite** non nul, mais `similarity_threshold
= None` (seuil non précisé, censé être le choix « sûr » d'après le message d'erreur de la garde)
laissait RAGFlow appliquer **son propre défaut serveur**, calibré pour l'échelle du score vectoriel
seul — exactement le changement d'échelle que la Décision 2 cherche à éviter, mais invisible :
aucune erreur, une liste de résultats simplement vidée. Une requête connue pour faire remonter le
bon document sans reranking en rendait zéro avec reranking effectif et seuil non précisé.

`RetrievalService.search` résout désormais `similarity_threshold` en `0.0` (seuil explicite qui ne
filtre rien) dès que le reranking est **effectif** et que l'appelant n'a fourni aucun seuil — avant
l'appel moteur, jamais de substitution côté adaptateur (`RagflowEngineAdapter.search` continue de
transmettre `None` tel quel quand il le reçoit ainsi, pour tout appelant qui court-circuiterait le
service). Sans reranking effectif, `None` reste `None` : aucun risque de changement d'échelle,
aucune raison de s'en mêler.
