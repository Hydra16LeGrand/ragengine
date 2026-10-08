# ADR-028 — Run d'évaluation asynchrone : statut `EvaluationRun`, au-delà du plafond synchrone

**Date :** 2026-08-08 · **Statut :** accepté

## Contexte

Phase 12, Étape 4 (item restant). `POST .../evaluations` est resté strictement synchrone depuis
la Phase 8 : borné à `MAX_EVALUATION_SAMPLE_SIZE = 20` pour ne pas faire dépendre la latence de la
requête HTTP du nombre d'appels LLM (génération + jugement, plus un aller-retour retrieval par
document échantillonné). Ce plafond interdit tout échantillon représentatif en continu — c'était
la pièce manquante notée à la clôture de l'Étape 4 (routine programmée, commit `f3c2e8b`).

`EvaluationRun` ne portait alors **aucune colonne de statut** : un choix délibéré, documenté dans
`app/workers/tasks/evaluations.py`, jugé disproportionné pour la routine programmée seule — un run
programmé qui échoue avant toute persistance n'a besoin que du journal comme source de vérité, et
un run programmé qui réussit n'est jamais consulté par un humain avant d'être terminé. Cette
décision **ne tient plus** pour un run déclenché par un utilisateur à grand échantillon : sans
statut, un run en cours d'exécution asynchrone serait tout simplement absent de l'historique
jusqu'à sa fin — invisible, pas juste silencieux — alors que l'utilisateur qui vient de le lancer
s'attend précisément à pouvoir le suivre.

## Décisions

**1. Un enum dédié, minimal, pas la machine à états de `JobState`.** `EvaluationRunStatus` porte
trois valeurs : `pending` / `completed` / `failed`. Pas de `running` intermédiaire — la version la
plus simple à implémenter correctement (persister le run en `pending` dès sa création, puis tout
écrire d'un coup à la fin, exactement comme le fait déjà `create_run` pour le chemin synchrone)
n'a jamais d'écriture `running` observable : l'ajouter aurait été un état mort, jamais atteint par
le code, donc jamais une information réelle pour un client qui poll `GET .../evaluations/{id}`.

**2. Deux chemins sous le même `POST`, pas un nouvel endpoint.** `sample_size ≤
MAX_EVALUATION_SAMPLE_SIZE` (20) reste synchrone, comportement inchangé (`201`, run complet).
Au-delà, `EvaluationService.create_pending_run` valide tout ce qui ne coûte aucun appel LLM (base
activée, fournisseur résoluble, au moins un document éligible), persiste immédiatement un run
`pending` sans item, puis publie `run_async_evaluation` (Celery, file `default`) — `202`. Un
nouvel endpoint dédié aurait dupliqué toute la validation d'entrée pour un seul changement de
comportement (le mode d'exécution), déjà déductible de `sample_size` lui-même.

**3. Un plafond asynchrone séparé, réglage applicatif, pas contrainte de schéma.**
`Settings.evaluation_async_max_sample_size` (200 par défaut) borne le chemin asynchrone,
indépendamment du plafond synchrone (20) et du plafond de la routine programmée (8). Le `CHECK
sample_size BETWEEN 1 AND 20` de `evaluation_runs` devient `sample_size >= 1` (migration
`0007_evaluation_run_status`) : les deux plafonds réels restent des réglages Python, révisables
sans migration.

**4. `run_async_evaluation` est une tâche sœur de `run_scheduled_evaluation`, pas sa
généralisation.** Elle reçoit `knowledge_base_id`/`evaluation_run_id` explicites et relit
`sample_size`/`llm_provider_id` depuis le run déjà persisté — jamais résolus depuis
`evaluation_enabled` comme le fait la routine programmée. Le principal est reconstruit depuis
`EvaluationRun.requested_by_id` (l'utilisateur qui a réellement lancé le run), pas depuis le
propriétaire de la base.

**5. Le statut `failed` doit survivre au `ROLLBACK` de `run_in_session`.** Une exception non
rattrapée dans une tâche Celery déclenche un `ROLLBACK` complet de sa session
(`app.workers.session.session_scope`) : sans commit explicite du statut `failed` **avant** de
relever l'exception, ce statut serait annulé avec le reste de la transaction et le run resterait
bloqué en `pending` pour toujours — invisible comme échoué depuis `GET .../evaluations/{id}`.
`EvaluationService.run_pending_evaluation` commite donc ce statut à l'intérieur de son propre
`except`, avant de relever. Vérifié empiriquement (retrait temporaire du commit, régression
constatée sur `tests/workers/test_evaluation_tasks.py::
test_an_async_run_failure_survives_the_task_level_rollback`, puis restauré).

## Conséquences

- `app/domain/enums.py` : `EvaluationRunStatus`. `app/db/types.py` : type `ENUM` Postgres associé.
- Migration `0007_evaluation_run_status` : colonne `status` (défaut serveur `completed`, backfill
  implicite — toute ligne existante vient de l'ancien chemin, entièrement synchrone), `CHECK`
  `sample_size` déplafonné.
- `EvaluationRunSummary.status` exposé en lecture ; `EvaluationRunRead` (détail) hérite du champ.
- `app/workers/celery_app.py` : `RUN_ASYNC_EVALUATION_TASK`, routée sur `default` (réseau
  seulement, aucun appel GPU — même profil que le reste de la Phase 12 Étape 4, ADR-013).
- Pas de changement pour un appelant existant avec `sample_size ≤ 20` : même code de statut, même
  forme de réponse.
