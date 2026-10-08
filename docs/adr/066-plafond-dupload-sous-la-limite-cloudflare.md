# ADR-066 — Plafond d'upload abaissé sous la limite du plan Cloudflare gratuit

**Date :** 2026-09-18 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, liste de
contrôle point 6

## Contexte

Le plan Cloudflare Free coupe un corps de requête à environ 100 000 000 octets décimaux (~95,4 Mio).
L'ancien défaut, `RAGENGINE_UPLOAD_MAX_BYTES=100 Mio` (104 857 600 octets), dépassait déjà ce seuil
**avant même** d'ajouter l'enveloppe multipart (bornes de forme, en-têtes de partie, `name`,
`theme`) — un envoi légitime proche du maximum recevait donc la page d'erreur brute de Cloudflare au
lieu du `413` Problem Details applicatif, sur les deux barrières qui existent aujourd'hui : le
plafond applicatif (`DocumentService.ensure_upload_within_limits`) et, depuis ADR-065,
`RequestBodyLimitMiddleware`.

## Décision

`RAGENGINE_UPLOAD_MAX_BYTES` passe de 100 à 90 Mio (94 371 840 octets). Avec l'enveloppe multipart
(`UPLOAD_MULTIPART_ENVELOPE_BYTES`, 1 Mio, `app.core.body_limit`), le plafond applicatif réel atteint
95 420 416 octets — une marge d'environ 4,4 Mio sous le seuil Cloudflare, suffisante pour absorber la
variation de taille d'une enveloppe multipart réelle sans s'en approcher au point de retomber dans la
même situation pour un fichier proche de la nouvelle limite.

**Trois points gardés cohérents, un seul nombre de référence.** `RAGENGINE_UPLOAD_MAX_BYTES`
(`backend/app/core/config.py`) reste la seule source ; `docker/traefik/dynamic.yml`
(`body-limit-upload.maxRequestBodyBytes`, ADR-052) et le plafond de
`RequestBodyLimitMiddleware` sur la route d'ingestion (ADR-065, qui *dérive* de ce même réglage)
suivent. Un test compare directement la valeur de `docker/traefik/dynamic.yml` à
`Settings().upload_max_bytes + UPLOAD_MULTIPART_ENVELOPE_BYTES`
(`backend/tests/test_body_limit.py::test_traefik_s_upload_body_limit_matches_the_application_default`),
même méthode que `test_security_headers.py` pour les en-têtes : toute dérive future entre les deux
fichiers fait échouer la suite plutôt que de se découvrir en production.

**Message affiché côté frontend.** Aucun message n'affiche aujourd'hui une taille maximale en dur
(vérifié : ni `Dropzone`, ni les traductions FR/EN n'en mentionnent une) — les erreurs `413`/`415`
remontent déjà par l'enveloppe Problem Details générique (`frontend/src/api/problem.ts`), dont le
`detail` reflète la valeur configurée sans code à maintenir en double. Rien à changer ici ; à
surveiller si un futur écran affiche la limite en dur.

## Conséquences

- `.env.example` documente `RAGENGINE_UPLOAD_MAX_BYTES` — pas de ligne dédiée à la valeur
  par défaut à l'heure de cette session (hérite du défaut du code) ; si une session future y ajoute
  une valeur explicite, la garder alignée sur 90 Mio.
- `.env.prod`/`.env.prod.local` ne surchargent pas `RAGENGINE_UPLOAD_MAX_BYTES` aujourd'hui (vérifié,
  aucune des deux) : le nouveau défaut s'applique donc automatiquement au prochain déploiement, sans
  ligne à changer dans `.env.prod.local` — signalé au cas où un opérateur préfère l'y épingler
  explicitement, pour ne plus dépendre d'un défaut de code (cf. rapport de session).
- Tests : `backend/tests/test_config.py::test_upload_and_rate_limit_defaults_are_conservative`
  (nouveau défaut) ; `backend/tests/test_body_limit.py` (marge sous le seuil Cloudflare, cohérence
  avec `docker/traefik/dynamic.yml`).
