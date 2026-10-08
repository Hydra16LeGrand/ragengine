# ADR-036 — Rate limiting : un troisième compteur pour le cookie de session

**Date :** 2026-08-15 · **Statut :** accepté · **Portée :** `app/core/rate_limit.py`

## Contexte

`RateLimitMiddleware` appliquait deux compteurs : IP (toujours, `rate_limit_anonymous_requests`,
défaut 60/min) et clé API (`rate_limit_requests`, défaut 120/min) quand l'en-tête
`Authorization: Bearer` est présent. L'interface web s'authentifie par **cookie de session**
(`app/api/v1/dependencies.py`), jamais par cet en-tête. Un utilisateur connecté au navigateur
restait donc plafonné à la limite anonyme — un chargement de plusieurs documents (un suivi de job
par document, en polling) la dépassait vite et déclenchait des `429`.

## Décision

Ajouter un troisième compteur, `ratelimit:session:{hash_token(cookie)}`, avec la même limite
généreuse que la clé API (`rate_limit_requests`). Priorité identique à
`PrincipalResolver.resolve` : en-tête `Authorization` présent ⇒ compteur de clé, sinon cookie de
session présent ⇒ compteur de session, sinon aucun des deux (IP seule).

**Aucune validation en base dans le middleware.** Le jeton de session est haché et utilisé comme
clé de compartiment tel quel, sans vérifier son existence/expiration via
`PrincipalResolver.resolve_session` — exactement le même choix, déjà en place et documenté, que
pour la clé API (`ratelimit:key:{hash}` ne vérifie pas non plus la clé en base). Deux raisons : (1)
CONTRIBUTING.md interdit une requête DB par appel dans ce chemin, déjà sur le chemin chaud de chaque
requête HTTP ; (2) dupliquer la logique d'expiration de `resolve_session` dans le middleware serait
une deuxième source de vérité pour la même règle. Conséquence assumée : un cookie présent mais
invalide/expiré en base obtient tout de même son propre compartiment généreux plutôt que de
retomber strictement sur la limite anonyme — mais le compteur IP, lui, s'applique **toujours** en
premier et n'est jamais affaibli ; c'est lui qui reste le filet de sécurité contre un appelant qui
changerait de cookie à chaque requête pour échapper à son propre compartiment (même rationnel que
la note déjà en commentaire sur `rate_limit_anonymous_requests`, `config.py:151`).

## Conséquences

- Un utilisateur connecté au navigateur bénéficie désormais de la limite généreuse sans poser
  d'en-tête `Authorization`.
- Le compteur IP anonyme est inchangé, y compris pour un visiteur sans session.
- `_session_token()` relit le cookie brut par un simple accès dict, sans importer
  `app.api.v1.dependencies` (le sens des couches `core/` → `api/` resterait à sens unique sinon,
  CONTRIBUTING.md §3) : il n'y a rien à valider ici, donc rien à dupliquer.
- Tests dans `backend/tests/test_rate_limit.py` : cookie seul ⇒ compteur généreux ; ni cookie ni
  clé ⇒ compteur anonyme inchangé ; cookie et clé API sont deux compartiments indépendants ; en-tête
  présent ⇒ priorité sur le cookie, qui n'est même pas consommé.
