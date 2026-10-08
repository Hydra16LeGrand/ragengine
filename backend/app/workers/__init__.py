"""Exécution asynchrone : application Celery, tâches et routage des queues.

Deux queues : `gpu` (concurrency=1, sémaphore matériel pour precheck et OCR) et
`default` (parsing léger, I/O, orchestration, polling RAGFlow).
"""
