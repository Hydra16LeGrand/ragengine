# ADR-017 — Génération d'évaluation : source des passages, scoring, exécution synchrone

**Date :** 2026-07-29 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- **`IngestionEnginePort.list_document_chunks` plutôt qu'un `search()` scopé au document.**
  Vérifié empiriquement contre l'instance RAGFlow réellement déployée : `GET /datasets/{id}/
  documents/{doc}/chunks` existe (absent de l'audit Phase 4), même enveloppe `code`/`data` que
  le reste de l'API. Pas de requête à deviner pour simuler un listing.
- **Un item par document échantillonné**, pas une liste ouverte de questions : le modèle de
  données (`EvaluationItem.source_document_id` singulier) le fige, et ça évite d'avoir à
  arbitrer combien de questions tirer d'un même document.
- **Exécution synchrone, bornée par `sample_size ≤ 20`** — pas de job Celery : coût borné et
  connu à l'avance, contrairement à l'ingestion (durée imprévisible selon le document).
- **RBAC : `MemberRole.EDITOR` pour lancer un run**, `VIEWER` pour lire l'historique/détail —
  lancer consomme un budget LLM réel, lire ne coûte rien de plus qu'une requête ponctuelle.
- **`EvaluationRun.requested_by_id` est nullable, sans pattern `*_by` à reproduire** : ni
  `IngestionJob` ni `Document` ne tracent l'auteur d'une action dans ce schéma. Écart assumé par
  rapport au brief d'origine, qui supposait ce pattern déjà présent ailleurs.
- **Un document dont la génération échoue est écarté**, jamais une raison de faire échouer tout
  le run — `sample_size` reste la cible du tirage, `item_count` (calculé à la lecture) le
  résultat réel.

## Contexte

Phase 8 : générer un jeu de questions/réponses depuis une base de connaissances pour en évaluer
le retrieval et la génération, sans réimplémenter de logique métier hors de `LLMPort` et
`IngestionEnginePort` (ADR-001, RAGFlow exploité « à fond »). Le brief de session laissait deux
questions ouvertes à trancher empiriquement, plus un choix de RBAC et un choix de modélisation
non couverts par les décisions déjà actées.

## Décisions

**1. Source des passages : `list_document_chunks`, vérifié contre l'instance réelle.**
`GET /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks` a été appelé contre le
déploiement Docker local (dataset et document réels, déjà indexés) : réponse `{"code": 0,
"data": {"chunks": [...], "doc": {...}, "total": n}}`, chaque fragment portant `id` et `content`
exploitables tels quels. Document inconnu ⇒ `code=102`, même ambiguïté « introuvable/interdit »
que le reste de l'adaptateur (ADR-012). L'endpoint est donc ajouté au port
(`EngineChunk`, `IngestionEnginePort.list_document_chunks`) et implémenté dans
`RagflowEngineAdapter` — voir `docs/ingestion-engine-architecture.md` §8 pour le détail vérifié.
Le repli prévu par le brief (`RetrievalService.search()` scopé par `document_refs`) n'a donc pas
été nécessaire : il aurait exigé de fabriquer une requête de recherche arbitraire (quel texte
interroger pour « obtenir un passage quelconque du document » ?) là où un vrai listing existe.

**2. Un passage, une question, un item — par document échantillonné.** Le tirage sélectionne
`min(sample_size, documents éligibles)` documents (`DocumentStatus.INDEXED` et référence moteur
non nulle), lit la première page de fragments de chacun (jusqu'à 20, pas de pagination complète
— largement suffisant pour choisir un passage représentatif), en tire un au hasard, et demande à
`LLMPort.complete()` une question + réponse de référence en JSON strict. `sample_size` est donc
la **cible** au moment du tirage ; un document dont la génération échoue (fragment vide, JSON non
parsable, champs manquants) est journalisé et écarté au profit du document éligible suivant
(tirage mélangé, pas seulement les `sample_size` premiers) — jamais une raison de faire échouer
tout le run pour une question ratée. `item_count` (champ calculé, jamais stocké) peut donc être
inférieur à `EvaluationRun.sample_size` si trop peu de documents produisent une Q/R exploitable.

**3. Scoring en deux appels indépendants, sans optimisation prématurée.** `retrieval_rank` vient
d'un appel `RetrievalService.search()` dédié (rang 1-based de `source_document_id` dans les
résultats, `null` s'il est absent du top-k) ; `generated_answer` vient d'un appel
`RetrievalService.chat()` séparé (flux consommé jusqu'au bout, pas de streaming exposé à
l'appelant de l'évaluation) ; `judge_score`/`judge_rationale` viennent d'un second
`LLMPort.complete()`, même `llm_provider_id`, prompt de juge demandant un JSON `{"score":
0.0-1.0, "rationale": "..."}`. Les deux appels retrieval sont délibérément **non fusionnés**
alors que `chat()` calcule déjà une recherche identique en interne (`ChatStream.results`) : les
réutiliser couplerait l'évaluation à un détail d'implémentation de `RetrievalService.chat` qui
pourrait changer sans préavis. Le coût (un aller-retour moteur de plus par question, sur un
run borné à 20) est jugé négligeable face au découplage gagné.

**4. Exécution synchrone, pas de job Celery.** Contrairement à l'ingestion (durée imprévisible,
dépend de la taille et du type de document), un run d'évaluation est borné à l'avance par
`sample_size ≤ MAX_EVALUATION_SAMPLE_SIZE = 20`, donc à un nombre connu d'appels LLM/retrieval.
`POST .../evaluations` bloque jusqu'à la fin du run et répond `201` avec le run complet — pas de
polling à câbler pour une action déjà bornée.

**5. RBAC : `EDITOR` pour écrire, `VIEWER` pour lire.** Le brief laissait le choix ouvert entre
`VIEWER` (même minimum que `/retrieval/search`) et un rôle plus strict. Lancer un run consomme un
budget LLM réel et répété (jusqu'à deux appels de génération/jugement par document échantillonné,
plus un aller-retour retrieval), ce qui le rapproche davantage d'une action de gestion de contenu
(`ingest`, `EDITOR` déjà) que d'une requête de lecture ponctuelle (`search`/`chat`, `VIEWER`). Les
deux endpoints de lecture (`GET .../evaluations` et `GET .../evaluations/{id}`) restent `VIEWER`,
symétriques du reste de l'API.

**6. `evaluation_enabled` (déjà présent sur `KnowledgeBase` depuis le schéma initial, migration
`0001_initial_schema`) gate le lancement.**
`POST .../evaluations` répond `422` (`ValidationError`) si le drapeau est à `false` sur la base
ciblée — même statut que le refus de reranking sans reranker configuré (`RetrievalService`), pas
un `403`/`409` : c'est une configuration de requête invalide au regard de l'état de la base, pas
un problème d'autorisation ni de conflit d'état d'une ressource.

**7. `requested_by_id` nullable, sans pattern `*_by` préexistant à reproduire.** Le brief
supposait qu'`IngestionJob` ou `Document` traçaient déjà l'auteur d'une action, à reproduire à
l'identique — vérification faite, aucun des deux ne porte de colonne de ce type (l'auteur d'une
ingestion n'est nulle part enregistré dans ce schéma). `EvaluationRun.requested_by_id` suit donc
la convention la plus proche déjà en place pour une référence optionnelle vers `users`
(nullable, `ON DELETE SET NULL`, comme `Document.current_job_id`) : un run garde un historique
lisible même si le compte qui l'a lancé est supprimé par la suite.

## Conséquences
- `IngestionEnginePort` grandit d'une méthode (`list_document_chunks`) et d'un type
  (`EngineChunk`) ; tout futur moteur alternatif (hors périmètre v1, ADR-001) devrait l'implémenter
  ou l'adaptateur composite qui le remplacerait à ce moment-là devra combler l'écart.
- Le coût réel d'un run est directement proportionnel à `sample_size` : deux appels LLM et deux
  allers-retours retrieval par item produit, jamais mis en cache ni réutilisé d'un run à l'autre.
- Un run peut légitimement contenir moins d'items que `sample_size` sans que ce soit un échec —
  l'interface (Phase 8, agent frontend suivant) doit afficher `item_count`/`sample_size` côte à
  côte plutôt que de supposer qu'ils coïncident toujours.
- Le catalogue de RBAC de l'API gagne sa première dissociation lecture/écriture motivée par un
  coût d'exécution (budget LLM) plutôt que par une distinction structurelle (lecture vs mutation
  de contenu) : à rappeler si une future action de ce type (import en masse, ré-embedding) se
  pose la même question.
