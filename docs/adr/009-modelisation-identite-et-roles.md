# ADR-009 — L'invité est un utilisateur éphémère ; les rôles sont des énumérations

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
La Phase 1 devait trancher trois points de modélisation que le plan désignait sans les arbitrer : comment représenter le visiteur non connecté d'ADR-005, comment matérialiser `Role`, et comment matérialiser `ProviderConfig`.

## Décisions

**1. Un invité est une ligne `users` éphémère** (`is_guest = true`, `email` et `password_hash` à `NULL`, `expires_at` non nul), et non une entité parallèle de type `GuestSession` propriétaire de ses propres ressources.

Motif : toute la chaîne aval — propriété d'une base de connaissances, documents, jobs, événements — s'articule sur `user_id`. Une entité invité parallèle imposerait une propriété polymorphe et un embranchement dans chaque requête et chaque contrôle de permission. Surtout, l'élévation d'ADR-005 (« sans perdre le contexte en cours ») devient une simple mise à jour de la ligne : on pose l'e-mail et le mot de passe, on efface `is_guest` et `expires_at`, et tout le travail déjà produit reste attaché au même identifiant. Aucune migration de données, aucune reprise de propriété.

Contrepartie assumée : la table `users` contient des lignes jetables. Elle est bornée par la purge des invités expirés et par les quotas.

**2. `Role` est une énumération PostgreSQL, pas une table.** Deux axes distincts : `UserRole` (`admin` | `member`) au niveau plateforme, et `MemberRole` (`owner` | `editor` | `viewer`) porté par la table d'association `knowledge_base_members`.

Motif : les rôles sont fixes en v1. Une table de rôles n'est rentable qu'avec des rôles personnalisables, ce qui relève du Premium multi-tenant. Une énumération évite une jointure sur chaque contrôle de permission et rend les règles vérifiables à la lecture du type. Écart assumé par rapport au libellé du plan, qui listait `Role` parmi les entités.

**3. `ProviderConfig` est un value object Pydantic sérialisé en JSONB** sur `knowledge_bases.provider_config`, et non une table. Les deux choix structurants — modèle d'embedding et reranker — sont dupliqués en colonnes dédiées pour rester filtrables en SQL.

Motif : la configuration provider n'a pas de cycle de vie propre ni d'identité ; elle n'existe que rattachée à une base de connaissances. Le JSONB absorbe l'ajout d'options en Phase 2 sans migration, pendant que les deux colonnes dénormalisées répondent aux questions réellement posées en SQL (« quelles KBs utilisent tel modèle ? », indispensable lors d'un changement de modèle).

## Conséquences
- Toute requête listant de vrais comptes doit filtrer `is_guest = false` — à encapsuler dans le repository, pas à répéter dans les services.
- Passer à des rôles personnalisables (Premium) impliquera une migration énumération → table ; le coût est accepté au regard du gain de simplicité en v1.
- La cohérence entre `provider_config` et les deux colonnes dénormalisées a un écrivain unique : le service de mise à jour d'une base de connaissances.
- L'introduction de rôles personnalisés ou d'une propriété d'organisation (multi-tenancy) fera l'objet d'une nouvelle ADR, pas d'une extension silencieuse de celle-ci.
