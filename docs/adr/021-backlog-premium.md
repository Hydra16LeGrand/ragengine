# ADR-021 — Backlog Premium : multi-tenancy, quotas, facturation

**Date :** 2026-07-31 · **Statut :** accepté (backlog, aucune implémentation engagée)

## Contexte
Phase 9 clôt le durcissement technique (sécurité, résilience, documentation). Le domaine, l'architecture ports/adapters et la surface REST sont stables. Le produit reste cependant mono-tenant : un compte est un espace isolé, sans notion d'organisation ni de facturation ; le seul mécanisme de quota existant est celui du mode invité (`GUEST_MAX_*`, `docs/adr/005-auth-rbac-et-mode-invite.md`), scopé à un espace éphémère, pas à un compte réel. Les trois axes ci-dessous conditionnent un lancement commercial et sont volontairement hors périmètre de ce dépôt tant qu'ils ne sont pas explicitement commandés.

## Décision
Backlog priorisé, dans cet ordre (chaque axe dépend du précédent) :

1. **Multi-tenancy** — le plus structurant. Nécessite une entité `Organization` reliant plusieurs `User`, et une refonte de la matrice de permissions pour distinguer un rôle au niveau tenant d'un rôle au niveau `KnowledgeBaseMember` (aujourd'hui la seule granularité). Implique une migration des données existantes (chaque `User`/`KnowledgeBase` actuel devient le tenant par défaut de son propriétaire).
2. **Quotas** — dépend de (1) pour être portés par l'organisation plutôt que par l'utilisateur individuel. Périmètre à couvrir au-delà de ce qui existe déjà pour les invités : nombre de bases, volume de stockage, tokens LLM consommés (génération + évaluations, Phase 7/8). Le système de scopes de clé API (`docs/adr/018-scopes-de-cle-api-et-securite.md`) et le rate limiting Redis déjà en place sont le socle technique naturel à étendre, pas à remplacer.
3. **Facturation** — dépend de (1) et (2) : mesurer l'usage par tenant avant de le facturer. Intégration d'un PSP (Stripe ou équivalent) hors périmètre actuel — aucun webhook, aucune donnée de paiement dans le dépôt aujourd'hui.

## Conséquences
- Aucun code engagé sur ces trois axes avant qu'ils ne soient explicitement priorisés par l'utilisateur.
- Revoir `docs/adr/006-licences.md` avant tout lancement commercial (dépendance déjà actée, notamment Elasticsearch et toute brique OCR ajoutée en cours de route).
- Le premier chantier concret, le jour où (1) démarre, est la migration de données `User`/`KnowledgeBase` → tenant par défaut : à traiter comme toute migration Alembic du dépôt (`docs/adr/010-migrations-au-demarrage-du-conteneur.md`).
