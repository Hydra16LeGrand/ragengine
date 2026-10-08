# ADR-055 — Balayage de maintenance planifié (jobs orphelins, decisions expirées, invités échus)

**Date :** 2026-09-13 · **Statut :** accepté · **Origine :** l'audit de dette 2026-09 (interne)
Motif C · l'audit sécurité 2026-09 (interne) constat 6

## Contexte

`beat_schedule` (`workers/celery_app.py`) ne portait qu'une entrée (répartition des évaluations).
Trois défauts distincts partageaient la même cause — l'absence de tout balayage périodique : des
jobs `embedding`/`indexing` orphelins quand la chaîne de sondage meurt (spécimen réel,
`5bf73ccc-1e1e-43bd-84da-bba7aa606643`, immobile depuis six semaines, ni `cancel` ni `retry` ne le
rattrapent) ; des jobs `awaiting_review` qui ne timeoutent jamais malgré deux docstrings qui
l'affirment ; et `purge_expired_guests`, jamais appelée et de toute façon impubliable telle quelle
(un `DELETE` groupé qui contourne `KnowledgeBaseService.delete`/`DocumentService.delete`, donc fuit
dataset moteur et fichiers).

## Décision

Une tâche unique `run_maintenance_sweep` (`workers/tasks/maintenance.py`), sous
`pg_try_advisory_xact_lock` non bloquant, sélectionne trois catégories bornées
(`maintenance_sweep_batch_limit` par catégorie) et fan-out une tâche par item sur `default` —
jamais `gpu` :

1. **`sweep_stalled_ingestion_job`** fait échouer un job `embedding`/`indexing` immobile depuis plus
   de `ragflow_stuck_job_timeout_seconds + ingestion_job_orphan_grace_seconds` (45 min par défaut).
   La décision est `fail_stalled_job`, extraite de `_poll` plutôt que redupliquée : point de règle
   unique de la veille anti-blocage, appelé avec un `idle` calculé différemment (ancienneté connue
   en base, sans appel moteur) mais partageant la même écriture. Relecture sous `with_for_update()`
   pour renoncer si une chaîne de sondage a entre-temps fait progresser le job.
2. **`resume_expired_precheck_decision`** republie `run_chunking` pour un `awaiting_review` sans
   décision depuis plus de `awaiting_review_decision_timeout_hours` (7 jours par défaut, lecture
   conservatrice faute de SLA produit documentée). N'écrit aucune transition elle-même : `_enter`
   l'écrit à l'exécution, exactement comme une décision humaine explicite.
3. **`purge_expired_guest`** remplace `purge_expired_guests` (supprimé) par trois primitives de
   lecture/suppression fine dans `services/identity/guests.py`. Itère sur les bases d'un invité
   échu via `KnowledgeBaseService.delete`/`DocumentService.delete` (moteur puis stockage), et ne
   supprime la ligne `User` qu'une fois toutes ses bases parties. Diffère (sans rien supprimer)
   si une base porte un `IngestionJob` non terminal, ou sur `EngineTransientError` — la trace de ce
   qui reste est simplement l'état de la base, revu identiquement au passage suivant.

## Conséquences

- `domain/state_machine.py` et `workers/tasks/ingestion.py` documentaient un comportement qui
  existe désormais réellement (Motif B clos).
- Nouveaux réglages (`core/config.py`) : `maintenance_sweep_interval_minutes` (15),
  `maintenance_sweep_batch_limit` (50), `ingestion_job_orphan_grace_seconds` (1800),
  `awaiting_review_decision_timeout_hours` (168).
- **Risque résiduel accepté** : `_enter`/`_poll` lisent encore le job par un `db.get` nu, sans
  `with_for_update()` de leur côté (dette isolée déjà signalée par l'audit, hors périmètre ici). Le
  seuil élargi (45 min, trois fois le seuil de blocage brut) rend la fenêtre de recouvrement avec
  une chaîne de sondage encore active négligeable en pratique, jamais nulle en théorie.
