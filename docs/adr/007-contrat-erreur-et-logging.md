# ADR-007 — Contrat d'erreur RFC 9457 et logging JSON via la stdlib

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
La Phase 0 devait fixer deux conventions transverses avant que la surface d'API ne grossisse : la forme des erreurs et le format des logs. Ces choix sont coûteux à changer une fois des dizaines d'endpoints et de tâches Celery écrits.

## Décision

**Erreurs — RFC 9457 (Problem Details), sans exception.** Une hiérarchie `RAGEngineError` (`NotFoundError`, `ConflictError`, `ValidationError`, `UnauthorizedError`, `ForbiddenError`) est convertie en `application/problem+json` par des handlers enregistrés via `register_exception_handlers(app)` dans `app/core/errors.py`. Un handler catch-all sur `Exception` garantit qu'aucune erreur ne sort de l'enveloppe. Le champ `type` utilise des **URI relatives** (`/problems/not-found`, …) pour ne coder en dur aucun nom de domaine.

**Logging — `python-json-logger` sur le `logging` de la stdlib**, plutôt que structlog. La sémantique stdlib est conservée : les loggers d'uvicorn, Celery et Alembic remontent dans le même handler JSON par simple propagation, sans adaptateur. `json_ensure_ascii=False` pour que le corpus francophone reste lisible dans les logs ; le champ `color_message` injecté par uvicorn est filtré.

## Conséquences
- Tout nouveau service lève une sous-classe de `RAGEngineError` ; les routers ne construisent jamais de réponse d'erreur à la main.
- Ajouter une catégorie d'erreur = ajouter une sous-classe + son URI `type`, jamais un handler ad hoc.
- Les logs sont directement exploitables par le collecteur OpenTelemetry de la Phase 8 sans reformatage.
- Le client frontend parse un type `ProblemDetail` unique (`src/api/client.ts`) pour toutes les erreurs de l'API.
