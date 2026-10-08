# ADR-018 — Scopes de clé API, validation d'upload, CORS/en-têtes et rate limiting

**Date :** 2026-07-30 · **Statut :** accepté

## Résumé

Quatre décisions de durcissement (Phase 9, première puce), regroupées ici parce qu'elles forment une seule « passe sécurité » de session plutôt que quatre sujets indépendants :

1. **Scopes** : granularité `ressource:action` (`knowledge-bases:read`, `documents:write`…), catalogue fermé dans `app/domain/scopes.py`, appliqué par `require_scope()` (`app/api/v1/dependencies.py`) monté par route. `"*"` reste un joker total — rétrocompatibilité avec les clés déjà émises. Sans effet sur un principal authentifié par cookie de session : les scopes n'existent que pour les clés API.
2. **Upload** : plafond `RAGENGINE_UPLOAD_MAX_BYTES` (défaut 100 Mio) et liste blanche `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES`, appliqués à **tout** principal — vérifiés avant le hachage SHA-256 et avant toute écriture, en plus (pas à la place) du quota cumulé des invités.
3. **CORS/en-têtes** : `RAGENGINE_CORS_ORIGINS=*` est désormais **refusé au démarrage** (`allow_credentials=True` en dur + joker d'origine = faille connue) ; `allow_methods`/`allow_headers` passent d'un joker à une liste explicite ; `SecurityHeadersMiddleware` pose `X-Content-Type-Options`, `X-Frame-Options`/CSP, `Referrer-Policy`, et `Strict-Transport-Security` conditionné à HTTPS.
4. **Rate limiting** : compteur Redis (fenêtre fixe, script Lua atomique) sur `REDIS_URL` (base `0`, inutilisée jusqu'ici), pas de nouvelle dépendance. Deux compteurs cumulatifs : par IP toujours, par clé API en plus si l'en-tête `Authorization: Bearer` est bien formé. Échec ouvert si Redis est injoignable. `/health` en est exclu.

Le reste du document détaille le contexte et les alternatives écartées pour chacun ; à lire si l'un des quatre points ci-dessus soulève une question non résolue par ce résumé.

## Contexte

plan interne Phase 9 demandait une passe sécurité en quatre volets sans trancher leur mise en œuvre : le stockage des scopes existait déjà (`ApiKey.scopes`) mais rien ne l'appliquait ; aucune limite de taille/type n'existait sur l'upload pour un compte réel (seul le quota cumulé des invités) ; le CORS n'avait jamais été audité pour la combinaison `allow_credentials=True` + origines larges ; aucun rate limiting n'existait, alors que Redis est déjà dans la stack (Celery).

## Décision

### 1. Scopes de clé API

Granularité `ressource:action` plutôt que (a) un scope unique par endpoint — trop fin, une clé d'intégration légitime finirait avec des dizaines de scopes à cocher — ou (b) un scope global unique — trop grossier, aucune clé ne pourrait être cantonnée en lecture seule. Catalogue fermé (`ApiKeyScope`, `app/domain/scopes.py`) plutôt qu'une chaîne libre : `ApiKeyCreate.scopes` est validé contre ce catalogue à la création (`422` immédiat sur une faute de frappe, plutôt qu'un `403` incompris des semaines plus tard). Pas de hiérarchie implicite entre scopes (`*:write` ne couvre pas `*:read` automatiquement) : plus simple à raisonner, au prix d'une déclaration un peu plus verbeuse.

`/retrieval/search` et `/retrieval/chat` ont chacun leur scope (`retrieval:search`/`retrieval:chat`) plutôt qu'un seul `retrieval:*` : le second déclenche un appel LLM externe facturé, le premier non — une clé d'intégration en lecture seule ne doit pas pouvoir déclencher de génération.

Hors catalogue, donc jamais scopées : `/health` (sonde de liveness, aucune dépendance externe par construction), `/providers` (catalogue public, déjà sans authentification), et les routes de gestion du principal lui-même (`/users`, `/sessions*`, `/me*`) — un porteur de clé API gère son propre compte quel que soit le périmètre métier accordé à cette clé, comme il peut toujours la révoquer elle-même.

`require_scope(scope)` (`app/api/v1/dependencies.py`) est monté via `dependencies=[Depends(require_scope(...))]` sur chaque route, en plus — pas à la place — des dépendances d'authentification/permission existantes (`require_kb_permission`, `AuthenticatedPrincipal`…). Elle n'a d'effet que si `principal.api_key is not None` : un principal anonyme ou session la traverse sans y être soumis.

### 2. Validation stricte des uploads

`RAGENGINE_UPLOAD_MAX_BYTES` (défaut 100 Mio) et `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES` (défaut : PDF, bureautique OOXML/legacy, texte/Markdown/HTML/JSON/CSV, e-mail, images courantes pour l'OCR) sont vérifiés dans `DocumentService.register` via `ensure_upload_within_limits`, **avant** le calcul de l'empreinte SHA-256 et avant toute écriture — un rejet ne doit payer ni le hachage du fichier entier ni la moindre I/O disque. Le type de média retenu pour la comparaison est celui déjà calculé par `resolve_content_type` (extension prioritaire sur la déclaration client, logique préexistante) — pas une valeur brute du client. `413`/`415` RFC 9457 (`PayloadTooLargeError`/`UnsupportedMediaTypeError`, `app/core/errors.py`), même contrat que le reste de l'API (ADR-007).

Plafond appliqué à **tout** principal, invité comme compte réel : `GUEST_MAX_UPLOAD_BYTES` reste un quota *cumulé* propre aux invités (nombre de fichiers + volume total), ce plafond-ci protège le stockage et la mémoire du process contre un unique fichier surdimensionné, quel que soit l'appelant — les deux contrôles s'appliquent en complément, pas en alternative.

La liste blanche va au-delà des formats analysés en profondeur par le precheck local (PDF, XLSX/XLSM, CSV) : RAGFlow, le moteur d'ingestion réel (ADR-001), sait traiter DOCX/PPTX/texte/HTML/images, et le precheck les traite déjà en mode dégradé sans échouer (`supported=False`, ingestion non bloquée). Restreindre la liste blanche au seul périmètre du precheck aurait cassé des uploads légitimes que le pipeline sait pourtant traiter.

### 3. CORS et en-têtes de sécurité

`RAGENGINE_CORS_ORIGINS=*` est désormais **refusé au démarrage** (`Settings._forbid_wildcard_origin_with_credentials`, `ValueError`) : `allow_credentials=True` est posé en dur dans `main.py` (nécessaire au cookie de session, ADR-005) et n'est pas configurable — combiné à un joker d'origine, Starlette reflète l'`Origin` de la requête au lieu de renvoyer `*`, ce qui revient à autoriser n'importe quel site tiers à rejouer le cookie de session d'un visiteur. Fermer la faille à la source de la configuration plutôt qu'au niveau du middleware : une seule ligne de défense, pas une par appelant de `CORSMiddleware`.

`allow_methods`/`allow_headers` passent de `["*"]` à des listes explicites (`GET/POST/PUT/PATCH/DELETE`, `Authorization/Content-Type/Accept`) — l'API n'expose ni verbe ni en-tête au-delà de cet ensemble, un joker n'apportait rien qu'une surface plus large à auditer.

`SecurityHeadersMiddleware` (`app/core/security_headers.py`) pose `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'`, `Referrer-Policy: no-referrer`, et `Strict-Transport-Security` — ce dernier uniquement si la requête est vue en HTTPS (schéma direct ou `X-Forwarded-Proto` derrière un reverse proxy) : l'annoncer en développement (servi en clair) forcerait le navigateur à exiger HTTPS pour un hôte qui ne le sert pas, cassant tout accès ultérieur depuis ce navigateur.

### 4. Rate limiting par clé API / IP

**Pas de nouvelle dépendance.** `redis>=5.2` (`pyproject.toml`) est déjà présent pour Celery et expose un client asynchrone (`redis.asyncio`) suffisant pour un compteur `INCR`/`EXPIRE` — ajouter `slowapi` ou `fastapi-limiter` pour ça aurait été la dépendance lourde évitable que CONTRIBUTING.md §9 demande de ne pas ajouter quand l'existant suffit. `REDIS_URL` pointe sur la base Redis `0`, non consommée par aucun autre composant (Celery vit sur les bases `1`/`2`) — aucune collision de clés.

Compteur à **fenêtre fixe** (un script Lua `INCR`+`EXPIRE` conditionnel, atomique, une seule commande Redis par requête) plutôt qu'une fenêtre glissante (`ZADD`/`ZREMRANGEBYSCORE`, plus précise mais plus coûteuse) : l'imprécision d'une fenêtre fixe — jusqu'au double du débit nominal à la frontière de deux fenêtres — est sans conséquence pour un garde-fou anti-abus.

**Deux compteurs cumulatifs**, pas un seul : toute requête est comptée par IP (`RAGENGINE_RATE_LIMIT_ANONYMOUS_REQUESTS`, défaut 60/min) ; si l'en-tête `Authorization: Bearer <jeton>` est bien formé, elle est *en plus* comptée par empreinte du jeton (`RAGENGINE_RATE_LIMIT_REQUESTS`, défaut 120/min, plus généreux). Limiter uniquement par empreinte de jeton aurait laissé un contournement trivial : un appelant qui ferait varier son `Authorization` à chaque requête (jamais vérifié en base par ce middleware — aucune requête SQL n'y est faite, par conception, pour rester bon marché) obtiendrait un compartiment neuf à chaque fois. La couche IP reste incontournable, quitte à être plus généreuse que nécessaire pour un client légitime derrière un NAT partagé.

Construit comme un **middleware ASGI** (`RateLimitMiddleware`, ajouté via `app.add_middleware`), pas comme une dépendance FastAPI : une dépendance nécessiterait de résoudre le `Principal` complet (requête SQL) avant même de savoir si la requête doit être comptée, ce qui défait l'objectif (protéger la base d'un flot abusif). Conséquence directe : le middleware ne peut **pas** lever de `RAGEngineError` pour signaler un `429` (il s'exécute hors de la portée d'`ExceptionMiddleware`, ajoutée par FastAPI plus « à l'intérieur » que les middlewares utilisateur) — `TooManyRequestsError.to_problem()` est réutilisé pour construire la réponse RFC 9457 directement, sans jamais lever l'exception.

`/health` est explicitement exclu (suffixe de chemin) : sonde de liveness Docker, documentée comme ne sollicitant aucune dépendance externe — la coupler au rate limiting romprait cette garantie et ferait échouer le healthcheck sur un simple ralentissement Redis.

**Échec ouvert** si Redis est injoignable (avertissement journalisé, requête laissée passer) : la disponibilité de l'API prime sur l'exactitude d'un débit. `RAGENGINE_RATE_LIMIT_ENABLED` permet de désactiver entièrement le middleware (tests, environnements sans Redis dédié).

## Conséquences

- Toute nouvelle route doit déclarer sa portée (`dependencies=[Depends(require_scope(ApiKeyScope.…))]`) et, si elle n'en a pas besoin, le documenter explicitement (comme pour `/health`/`/providers`/`/users`/`/sessions*`/`/me*` ici) — un oubli laisse la route accessible à toute clé, jamais l'inverse (`"*"` par défaut).
- `.env.example` documente `RAGENGINE_UPLOAD_MAX_BYTES`, `RAGENGINE_UPLOAD_ALLOWED_CONTENT_TYPES`, `RAGENGINE_RATE_LIMIT_*`. Toute valeur par défaut de la liste blanche d'upload doit rester alignée sur ce que RAGFlow sait effectivement ingérer, pas seulement sur ce que le precheck analyse en détail.
- Un déploiement existant avec `RAGENGINE_CORS_ORIGINS=*` ne démarre plus : c'est une rupture volontaire (l'alternative silencieuse aurait laissé la faille active).
- Le rate limiting ajoute une lecture puis (au premier coup de fenêtre) une écriture Redis par requête ; négligeable face à la latence réseau déjà présente vers PostgreSQL sur le chemin critique de toute route.
- Les clés API émises avant cette phase gardent `scopes=["*"]` : aucune n'est cassée par l'activation de `require_scope`.
