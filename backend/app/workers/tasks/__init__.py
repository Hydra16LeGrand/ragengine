"""Tâches Celery de RAGEngine, un module par domaine fonctionnel.

Les modules déclarés ici sont importés au démarrage du worker via
`TASK_MODULES` (`app.workers.celery_app`) : une tâche absente de cette liste
n'existe pas pour le worker, quelle que soit sa décoration.
"""
