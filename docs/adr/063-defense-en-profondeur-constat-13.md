# ADR-063 — Défense en profondeur sur trois points à barrière unique

**Date :** 2026-09-17 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 13

## Contexte

Le constat 13 relevait trois points **non exploitables aujourd'hui** (chaque chemin actuel se
protège correctement), mais qui ne tenaient que par une seule barrière — un futur appelant qui
l'oublierait rouvrirait la faille sans qu'aucune seconde garde ne la retienne.

## Décision

**1. `PermissionService.authorize` revérifie `chatbot_enabled`.** La branche `via_published_chatbot`
(`backend/app/services/identity/permissions.py`) faisait confiance à l'appelant unique de ce chemin
(`RetrievalService`, résolu via `get_knowledge_base_by_chatbot_slug`, qui vérifie déjà
`chatbot_enabled` en amont) sans le revérifier elle-même. Elle le fait désormais :
`via_published_chatbot and minimum is MemberRole.VIEWER and knowledge_base.chatbot_enabled`. Un
futur appelant qui oublierait de repasser par la résolution par slug ne peut plus ouvrir une base
dont la publication est désactivée.

**2. `/admin/*` et `/me/llm-keys*` portent leurs scopes.** Le catalogue `ApiKeyScope`
(`backend/app/domain/scopes.py`) gagne `ADMIN_READ`/`ADMIN_WRITE` et `LLM_KEYS_READ`/`LLM_KEYS_WRITE`,
appliqués par `require_scope` sur chaque route de `admin.py` (9 routes) et `llm_keys.py` (3 routes),
**en plus de** `RequireAdmin`/`require_account` — les deux mécanismes sont indépendants,
`require_role` vérifie le rôle du compte, `require_scope` vérifie le droit de la clé API utilisée.
Avant ce correctif, une clé d'un compte admin scopée à un périmètre métier étroit (ex.
`documents:read`) ouvrait tout `/admin/*` sur la seule foi du rôle plateforme du compte — la
promesse d'ADR-018 ne tenait pas sur ces deux surfaces, seules oubliées lors de l'introduction des
scopes.

Reclassement documenté dans `app.domain.scopes` : le carve-out `/me*` d'ADR-018 (« un porteur de clé
API gère toujours son propre compte ») reste valable pour `GET/PATCH /me`, `/me/credentials` et
`/sessions*`, mais ne s'appliquait à `/me/llm-keys*` que par généralisation de préfixe — une clé LLM
tierce déclarée là est un secret de facturation externe, pas un réglage de profil, et relève du même
principe que les autres ressources scopées.

**Conséquence pour les clés existantes** : une clé API émise avant cette version avec un scope
explicite (pas `"*"`) et utilisée jusqu'ici sur `/admin/*` ou `/me/llm-keys*` perd cet accès au
déploiement — elle doit être réémise avec le scope adéquat, ou `"*"`. Les clés `scopes=["*"]`
(défaut historique, `DEFAULT_SCOPES`) ne sont pas affectées.

**3. `PATCH /documents/{id}` reconverge vers `resolve_filename`.** `DocumentService.update`
(`backend/app/services/documents.py`) appliquait `filename` tel quel : un `../../etc/passwd` passé
en `PATCH` — jamais soumis à `resolve_filename` (le point qui réduit un nom au nom de base à
l'admission, `register`) — survivait en base puis partait vers RAGFlow
(`app.workers.tasks.ingestion`, `filename=document.filename`). `update` applique désormais
`resolve_filename` au nouveau `filename` avant de l'écrire — même garantie sur les deux chemins
d'écriture du champ. Relu contre ADR-060 (octets magiques à l'admission, introduite la veille) :
`PATCH` ne change pas le contenu du fichier, seulement son nom affiché, donc aucune revérification
de signature n'est nécessaire ici — seule la normalisation du nom manquait.

**Écarté du périmètre** : le `max_length` de `theme` existe déjà (`MAX_THEME_LENGTH`,
`DocumentUpdate.theme`) — le constat le signalait absent, il ne l'est plus (corrigé entre l'audit et
cette session, non retracé).

## Conséquences

- `backend/app/services/identity/permissions.py::PermissionService.authorize` : une ligne de garde
  en plus, aucun changement de signature ni de comportement pour tout appelant qui passait déjà par
  un chatbot réellement publié.
- Routes concernées par les nouveaux scopes : `GET /admin/platform-llm-usage*`, `GET /admin/users`,
  `PUT /admin/users/{id}/active`, `GET /admin/chatbots`, `GET /admin/sessions`,
  `GET/PUT/DELETE /me/llm-keys*`.
- `frontend/src/features/settings/apiKeyScopes.ts` (et ses traductions FR/EN) suit le catalogue
  backend avec les quatre nouvelles entrées — dupliqué volontairement, rejet `422` côté backend en
  cas de divergence (voir docstring du fichier).
- Tests : un test de la plateforme (non publié) (nouveau cas `chatbot_enabled=False`
  neutralise `via_published_chatbot`, cas existants adaptés à la revérification),
  `backend/tests/services/test_retrieval_chat.py` (fixtures adaptées : `chatbot_enabled=True` là où
  le test simule un chatbot publié), un test de la plateforme (non publié) et le nouveau
  `backend/tests/api/v1/test_llm_keys_routes.py` (clé insuffisamment scopée refusée, scope exact
  admis, pas de hiérarchie implicite entre `READ`/`WRITE`, joker `"*"` toujours total),
  `backend/tests/services/test_documents.py::test_updating_a_document_normalizes_a_path_carrying_filename`.
