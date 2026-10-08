# ADR-022 — Multi-tenancy : `Organization` et `OrganizationMember`

**Date :** 2026-08-01 · **Statut :** accepté, implémentation en cours

## Contexte

Premier axe du backlog Premium (ADR-021), le plus structurant : les axes 2 (quotas) et 3 (facturation) en dépendent, car ils doivent être portés par un tenant et non par un `User` individuel. Le produit est aujourd'hui mono-tenant : `KnowledgeBase.owner_id` référence directement un `User`, et la seule granularité de rôle est `KnowledgeBaseMember` (par base).

## Décision

- Nouvelle entité `Organization` (id, name, slug **unique globalement**, timestamps) : le tenant, futur porteur des quotas et de la facturation.
- Nouvelle table d'association `OrganizationMember` (`organization_id`, `user_id` — PK composite, pas de colonne `id`, même pattern que `KnowledgeBaseMember`), portant un rôle **au niveau tenant** : `OrganizationRole.OWNER` / `OrganizationRole.MEMBER`. Deux niveaux seulement — un `admin` intermédiaire s'ajoutera par migration future si un besoin réel apparaît, pas par anticipation.
- Relation `User` ↔ `Organization` **N:M** dès maintenant : `OrganizationMember` est par nature une table d'association N:M, la restreindre à une seule organisation par utilisateur n'économiserait rien au niveau du modèle de données. Chaque utilisateur reçoit une organisation personnelle par défaut à sa création (compte réel **et** invité), mais pourra être invité dans d'autres organisations sans nouvelle migration de structure.
- `KnowledgeBase` gagne une colonne `organization_id` (FK NOT NULL après backfill). `owner_id` **ne change pas** : l'organisation est une dimension orthogonale (le tenant/la frontière de quota), pas un remplacement du propriétaire ni de `KnowledgeBaseMember`, qui reste la granularité fine par base.
- Trois rôles distincts coexistent désormais, à ne pas confondre : `UserRole` (plateforme, admin RAGEngine), `OrganizationRole` (tenant, owner/member), `MemberRole` (par base, owner/editor/viewer). `PermissionService` (KB) n'est pas modifié ; un service de résolution d'accès équivalent est ajouté pour `Organization`.
- Suppression d'une organisation refusée (`409`) tant qu'elle possède des bases de connaissances — pas de cascade physique multi-KB depuis la suppression d'un tenant, à la différence de la cascade `User` → `KnowledgeBase` déjà en place (ADR différent, granularité différente).
- Endpoints REST complets dans cette même itération (`/api/v1/organizations`, gestion des membres) : le socle sur lequel s'appuiera directement l'axe 2 (quotas), pas seulement le modèle de données.

## Conséquences

- Migration Alembic unique : crée `organizations` + `organization_members` + l'enum `organization_role`, ajoute `knowledge_bases.organization_id` nullable, **backfill** (une organisation personnelle + une ligne `OrganizationMember` owner par `User` existant, rattachement de chaque `KnowledgeBase` à l'organisation de son propriétaire), puis colonne NOT NULL.
- `UserService.register` et `SessionService.open_guest_space` doivent chacun créer l'organisation personnelle par défaut au moment de la création de la ligne `User` — sinon un compte fraîchement créé ne pourrait créer aucune base.
- `KnowledgeBaseService.create` doit résoudre l'organisation cible (payload explicite si le principal appartient à plusieurs organisations, défaut sinon) avant d'écrire la base.
- `docs/domain-model.md` à mettre à jour (§1 identité et accès, §4 matrice, §5 surface REST) : ce document fait foi, tout écart s'y documente.
