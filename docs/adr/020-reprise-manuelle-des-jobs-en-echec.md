# ADR-020 — Reprise manuelle des jobs en échec, cause structurée, DLQ par log

**Date :** 2026-07-30 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- **`IngestionJob.error` était déclaré mais jamais peuplé.** `_fail`
  (`app/workers/tasks/ingestion.py`) écrit désormais un `JobError` structuré (`code`,
  `retryable`, `retry_state`) à chaque transition vers `failed`, dans la même transaction que le
  reste (`IngestionJobService.transition`).
- **Le retry Celery natif (`autoretry_for`, `app/workers/celery_app.py`) est neutralisé par
  construction pour ce pipeline précis** : vérifié empiriquement (raisonnement ci-dessous, pas
  supposé) — la garde d'idempotence transforme toute tentative postérieure à un `failed` déjà
  écrit en un retour normal (`Outcome.SKIPPED`), donc en **succès** aux yeux de Celery. `POST
  /api/v1/jobs/{id}/retry` (`IngestionJobService.retry`) est donc le seul mécanisme de reprise
  réellement actionnable pour ce pipeline, pas une redite du retry Celery.
- **`failed` reste `is_terminal() == True`** (rien ne le relance automatiquement) **mais
  `ALLOWED_TRANSITIONS[failed]` n'est plus vide** : une reprise manuelle transite vers l'état
  *source* de l'étape qui a échoué (`RETRY_SOURCE_STATES` = `{queued, awaiting_review, chunking,
  embedding}`), jamais vers l'état qu'elle écrit elle-même.
- **La DLQ Celery est un log structuré, pas une file Redis dédiée** — décision motivée en detail
  au point 4, y compris pourquoi l'alternative « vraie file » a été écartée après l'avoir
  esquissée.

## Contexte

Phase 9 (résilience), deuxième puce : « gestion des indisponibilités RAGFlow/ES, dead-letter
queue Celery, procédure de reprise des jobs ». Décision déjà actée avec l'utilisateur pour le
dernier point : un vrai endpoint, pas une procédure documentée.

## Décisions

**1. `JobError` gagne un champ `retry_state: JobState | None`
(`app/domain/schemas/ingestion_job.py`).** Il vaut toujours `Step.source` de l'étape qui a
échoué — jamais `Step.target` — quel que soit l'endroit précis où l'exception est survenue dans
le corps de l'étape (`_job_error_for`). C'est l'état à restaurer pour que l'étape en échec
puisse être rejouée : la reprise ne « ressuscite » pas le job à l'endroit où il s'est arrêté, elle
le fait **retraverser l'entrée** de l'étape fautive, exactement comme le ferait une exécution
normale.

**2. Classification retryable : seule `EngineTransientError` vaut `true`.**
`EngineTransientError` (réseau injoignable, ou codes RAGFlow 100/105/106 — panne momentanée,
`app/providers/engine/base.py`) est la seule exception dont rejouer la cause a une chance
raisonnable de réussir sans intervention. `EngineValidationError` (argument rejeté), `EngineError`
générique (échec ambigu, ex. `code=102`), le blocage détecté par la veille anti-blocage
(`engine_stalled`), un échec explicitement rapporté par le moteur
(`engine_processing_failed`/`engine_processing_cancelled`), et toute exception de programmation
(`ValueError`, `NotFoundError`…) valent `false` par défaut. L'endpoint de reprise refuse (`409`)
une reprise automatique sur une cause `retryable=false`, mais accepte `force=true` : l'opérateur
assume alors avoir vérifié que la cause a été corrigée (document remplacé, configuration
ajustée, charge du moteur retombée…). Un blocage détecté par la veille (`engine_stalled`) est
volontairement classé `false` malgré son caractère a priori transitoire : le moteur avait déjà
reçu la requête et ne l'a pas menée à bien tout seul, rejouer le même document risque de
reproduire le même blocage — un `force=true` explicite reste ouvert si l'opérateur juge que la
charge momentanée du moteur en est la seule cause.

**3. Machine à états : `failed` gagne des arêtes sortantes vers `RETRY_SOURCE_STATES`
(`app/domain/state_machine.py`), sans cesser d'être « terminal » au sens `is_terminal()`.** Les
deux notions divergent intentionnellement : `is_terminal` reste consommé par la garde
d'idempotence du pipeline automatique (`_fail`, `_poll`) — rien, dans le pipeline lui-même, ne
fait jamais franchir cette arête ; seul `IngestionJobService.retry`, déclenché par un humain via
l'API, l'emprunte. `completed` et `cancelled` restent, eux, strictement sans successeur.
`app.workers.celery_app.RETRY_TASK_BY_SOURCE_STATE` fait le lien inverse (état source → tâche à
republier) ; un test croisé (`tests/domain/test_state_machine.py`,
`test_retry_source_states_are_exactly_the_pipeline_step_sources`) vérifie que
`RETRY_SOURCE_STATES` et les quatre `Step.source` de `app.workers.tasks.ingestion` ne peuvent pas
diverger en silence — c'est exactement la classe de bug (couture entre deux modules qui semblent
indépendants) que CONTRIBUTING.md §9 demande de garder en tête.

**4. Dead-letter queue : un log structuré et marqué (`event=ingestion.dead_letter`), pas une
file Celery/Redis dédiée — décision revue en cours d'implémentation, pas le plan initial.** Une
« vraie » file `dead` (tâche sentinelle routée dessus, `TASK_ROUTES`) a été esquissée puis
écartée pour deux raisons empiriques :
  - **Elle aurait cassé la suite de tests existante sans bénéfice proportionné.** `_dispatch_successor`/
    `_reschedule` ne sont jamais appelés sur le chemin d'échec (uniquement en succès), donc aucun
    test de l'échec n'a jamais eu besoin de mocker Celery. Ajouter un vrai `.delay()` sur ce même
    chemin d'échec aurait exigé de retrofitter la fixture `publications`
    (`tests/workers/conftest.py`) dans chaque test d'échec existant, pour un gain net faible :
    Redis, le broker de ce déploiement, n'a pas de dead-lettering natif (contrairement à
    RabbitMQ), donc une file `dead` non consommée par un worker dédié n'est jamais qu'un
    « log déguisé » — un `redis-cli llen dead` compte des messages qu'aucun outil de ce dépôt
    n'exploite encore.
  - **Le signal Celery natif pour ce besoin, `task_failure`, ne se déclenche presque jamais pour
    ce pipeline précis** (fait vérifié, pas supposé — cf. point suivant) : router dessus une
    visibilité opérationnelle aurait été, la plupart du temps, silencieusement inopérant.
  Le log porte `job_id`, `celery_task`, `error_code`, `retryable`, `reason` en `extra=` — de quoi
  câbler une règle d'alerte côté agrégateur de logs sans changement de code, le jour où l'un
  existe. `_dead_letter` (`app/workers/tasks/ingestion.py`) est le point d'émission unique,
  appelé après le commit de `_fail` (jamais depuis la coroutine elle-même), une fois par échec
  réel — la garde d'idempotence (`Outcome.SKIPPED` sur toute tentative postérieure à un `failed`
  déjà écrit) empêche toute répétition sur le même incident.

**5. Fait vérifié, pas supposé : `task_failure` ne se déclenche presque jamais sur ce
pipeline.** Avec `autoretry_for=(Exception,)`, une exception attrapée déclenche
`Task.retry()` (signal `task_retry`, état `RETRY`), jamais `task_failure` sur cette tentative.
À la tentative suivante, `_enter`/`_poll` retrouvent le job déjà `failed` (écrit par la
tentative précédente) : ni `step.target` ni `step.source` ne correspondent, la garde renvoie
`Outcome.SKIPPED`, la tâche **retourne normalement, sans exception** — un succès Celery
(`task_success`), pas un échec. `task_failure` ne se déclenche donc, pour ces quatre tâches, que
dans deux cas marginaux : une exception dans `dont_autoretry_for` (`ConflictError`,
`NotFoundError`, `ValueError` — échoue dès la première tentative, sans jamais retenter) ou le
cas catastrophique où `_fail` lui-même échoue à écrire en base (Redis/PostgreSQL indisponibles
au même moment) pendant `max_retries` tentatives consécutives — dans ce dernier cas, `_mark_failed`
journalise déjà (« Échec non journalisé en base ») à **chaque** tentative, avec le même marqueur
`event=ingestion.dead_letter` : aucun mécanisme Celery supplémentaire n'ajoute de couverture
utile à ce log déjà existant. Conséquence directe : **aucun hook sur `task_failure` n'a été
ajouté** — il aurait couvert un sous-ensemble strict de ce que `_dead_letter` couvre déjà, pour
un coût de code et de test non nul.

**6. `POST /api/v1/jobs/{job_id}/retry` (`app/api/v1/routes/jobs.py`, `IngestionJobService.retry`)**
refuse `409` si le job n'est pas `failed`, si `error` est absent (défensif — ne devrait plus
arriver), ou si `error.retryable` est faux sans `force=true`. Incrémente `attempts`, transite
vers `error.retry_state`, commite, **puis** republie
`RETRY_TASK_BY_SOURCE_STATE[error.retry_state]` (même règle d'ordre que ADR-015 : publier avant
le commit ferait lire à un worker concurrent un job encore `failed`) — vérifié par un test dédié
qui sonde une connexion indépendante au moment exact de la publication
(`tests/api/v1/test_pipeline_dispatch.py`, même méthode que les bugs A/B qui ont motivé
ADR-015).

## Conséquences

- Tout consommateur qui ne lit que `GET /api/v1/jobs/{id}` (API, frontend) voit désormais la
  cause structurée d'un échec, pas seulement le journal d'événements.
- Le point de couture le plus fragile — la correspondance entre `RETRY_SOURCE_STATES` (domaine),
  `RETRY_TASK_BY_SOURCE_STATE` (Celery) et les quatre `Step.source` (pipeline) — est verrouillé
  par un test croisé plutôt que par une convention non vérifiée.
- Une future étape de pipeline devra mettre à jour les trois en même temps ; leur absence de
  dépendance d'import direct (contrainte des cycles Python) rend cette synchronisation
  manuelle — le test croisé est la seule garde.
- La DLQ actuelle n'est exploitable que par un agrégateur de logs (ou `grep` sur les journaux du
  worker) : aucune commande `celery inspect`/Flower ne montrera de file `dead`. À revisiter si un
  besoin réel de drainage/rejeu automatisé apparaît — le point d'émission unique (`_dead_letter`)
  reste le seul endroit à faire évoluer ce jour-là.
