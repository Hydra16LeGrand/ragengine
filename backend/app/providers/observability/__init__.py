"""Port `Observability` et ses adaptateurs (Langfuse via OpenTelemetry).

Brique optionnelle : en l'absence de configuration, la factory renvoie une
implémentation no-op. L'observabilité ne doit jamais faire échouer une requête
(CONTRIBUTING.md §3.4).
"""
