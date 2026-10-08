# ADR-025 — Mesure de qualité du retrieval : extension du modèle Postgres existant, pas Langfuse

**Date :** 2026-08-07 · **Statut :** accepté

## Contexte

Phase 12, Étape 1 (arrêt obligatoire). L'Étape 0 vient de produire une première baseline chiffrée
(recall@k, MRR) en étendant l'outil d'évaluation existant (Phase 8, ADR-017,
`backend/app/services/evaluations.py` + nouveau `retrieval_metrics.py`). Deux options réelles pour
la suite de la Phase 12 (cartographie des leviers, routine d'évaluation, dashboard transverse) :
étendre encore ce modèle Postgres, ou migrer vers les briques natives de Langfuse (déjà intégré à
RAGEngine pour le traçage OpenTelemetry, cf. CONTRIBUTING.md §3), qui exposent réellement des Datasets,
Dataset Runs, Scores et un mécanisme d'évaluateurs automatiques — vérifié par requêtes
authentifiées sur l'instance self-hostée réelle, pas seulement documenté (plan interne, fait
établi 4).

## Décision

**Étendre le modèle Postgres existant.** Rejeté : la migration vers Langfuse.

Raisons empiriques, pas de principe :
- Le modèle Postgres est **déjà réel, stable, et vient de produire une baseline exploitable** —
  27 items sur 3 bases, recall@k/MRR calculés, rejouable (plan interne, Étape 0). Aucun besoin
  de migration pour continuer.
- Il porte **déjà une UI** (`EvaluationsPanel.tsx`), câblée et utilisée en production (Phase 8).
- L'API d'évaluateurs LLM-juge automatiques de Langfuse est **marquée `/unstable` par Langfuse
  lui-même** — l'adosser au mécanisme de qualité central du produit reviendrait à faire dépendre
  une garantie produit d'une surface que son propre éditeur ne garantit pas encore.
- Langfuse reste, par construction, une brique **optionnelle et no-op par défaut**
  (`ObservabilityPort`, CONTRIBUTING.md §3 : « leur absence de configuration ne doit jamais faire échouer
  une requête »). Faire de ses Datasets/Scores la source de vérité de la qualité retrieval
  contredirait ce principe — un déploiement sans Langfuse configuré perdrait une capacité produit
  centrale, pas seulement de l'observabilité.

## Conséquences

- Les étapes suivantes de la Phase 12 (cartographie des leviers, routine d'évaluation
  programmée, dashboard transverse) étendent `evaluation_runs`/`evaluation_items` et
  `retrieval_metrics.py` — pas de nouvelle dépendance à l'API Langfuse.
- L'export vers Langfuse pour bénéficier de son interface de visualisation transverse (option (c)
  de l'ADR proposée, non retenue pour l'instant) reste ouvert plus tard, en **complément** —
  Postgres resterait alors la source de vérité, Langfuse un miroir de confort. Pas engagé
  aujourd'hui, à réévaluer si le dashboard transverse maison (Étape 5) s'avère insuffisant.
- Le déplafonnement de `MAX_EVALUATION_SAMPLE_SIZE` et le passage en job asynchrone (Étape 4)
  restent dans le même modèle Postgres/Celery déjà en place pour le reste du pipeline
  d'ingestion — cohérence architecturale, pas de nouveau paradigme d'exécution à introduire.
