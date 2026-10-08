# ADR-065 — Compteur d'octets applicatif sur le corps des requêtes

**Date :** 2026-09-18 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 13
(liste de contrôle, point 5), défense en profondeur

## Résumé

Seules les bornes de Traefik (`docker/traefik/dynamic.yml`, ADR-052) arrêtaient un corps de requête
trop gros — suffisant tant que l'API n'est joignable que par le proxy, mais une barrière unique.
`RequestBodyLimitMiddleware` (`backend/app/core/body_limit.py`) en est la seconde, indépendante :
un middleware ASGI qui tamponne le corps jusqu'à une borne configurable, l'admet tel quel s'il
tient dedans, répond `413` (Problem Details) sinon — jamais une exception laissée remonter vers
FastAPI. La route d'ingestion suit `upload_max_bytes` (+ enveloppe multipart) ; toutes les autres,
`request_body_max_bytes` (1 Mo par défaut, même valeur que `body-limit-api` côté Traefik). Le flux
SSE du chat n'est jamais concerné : ce module ne touche que `receive` (la requête), jamais `send`
(la réponse).

## Contexte

`DocumentService.ensure_upload_within_limits` borne le *fichier* d'un upload, après que Starlette a
déjà entièrement reçu et parsé le `multipart/form-data` ; aucune route de l'API ne borne le corps
de la requête *avant* cette étape, ni sur les autres routes (JSON du chat, de la recherche…).
`app.core.rate_limit.RateLimitMiddleware` prouve déjà qu'un middleware ASGI ajouté via
`add_middleware` peut construire sa propre réponse Problem Details sans jamais lever d'exception
(hors de la portée d'`ExceptionMiddleware`) : même famille de solution ici, pour un problème
différent (compter des octets de *requête*, pas décider d'un débit).

## Décision

**Tamponner jusqu'à la borne, puis rejeter ou rejouer — jamais lever d'exception vers l'application
interne.** Une première variante, plus simple, levait une exception maison depuis `receive()` en
misant sur sa remontée jusqu'au middleware. **Vérifié empiriquement, pas supposé** : elle ne remonte
jamais proprement. Starlette enveloppe lui-même la lecture du corps d'une requête `application/json`
(`Request.json()`, dépendance Pydantic de FastAPI) et d'un `multipart/form-data` (`UploadFile`) dans
son propre `try/except`, et recase **toute** exception survenue pendant cette lecture — y compris une
exception sans rapport avec un JSON malformé — en `400 "There was an error parsing the body"`. Le
`413` voulu n'était jamais atteint. Reproduit avec un script jetable avant d'écrire quoi que ce soit
dans le module : sur un corps JSON et sur un `UploadFile`, l'exception levée depuis `receive()`
ressort en `400`, jamais en `413`.

Le tampon-puis-rejeu élimine le problème à la racine : `self.app(...)` n'est jamais appelé tant que
la décision (admettre/refuser) n'est pas prise, donc aucune exception ne traverse jamais cette
frontière ni n'est recatégorisée en chemin par un `try/except` interne à Starlette. Une fois admis,
le corps tamponné est rejoué à l'identique (`_ReplayReceive`) : l'application interne ne voit aucune
différence avec le flux d'origine.

**Rejet rapide sur `Content-Length` déclaré**, avant tout octet lu, quand il dépasse déjà la borne —
mais jamais utilisé pour *admettre* : le compteur réel reste seul juge, `chunked` (sans
`Content-Length` connu à l'avance) y compris, exactement le cas qu'un contrôle sur le seul en-tête
manquerait.

**Deux plafonds, pas un seul.** La route d'ingestion (`POST /knowledge-bases/{id}/documents/ingest`,
même expression régulière que le routeur `api-upload` de Traefik, dupliquée volontairement — ce
module ne doit pas dépendre de `app/api/`) reçoit `upload_max_bytes + UPLOAD_MULTIPART_ENVELOPE_BYTES`
(1 Mio, même marge que `body-limit-upload` côté Traefik) : relever `RAGENGINE_UPLOAD_MAX_BYTES`
impose de relever cette borne applicative comme celle du proxy, elle suit automatiquement puisqu'elle
en dérive. Toute autre route reçoit `request_body_max_bytes` (1 Mo, même valeur et même
justification que `body-limit-api` — le plus gros corps JSON légitime est l'historique d'une
conversation, ADR-053, ~400 Ko au pire).

**Mémoire bornée, pas de streaming zéro-copie.** Le corps tamponné tient en mémoire jusqu'à la
limite appliquée avant d'être rejoué — jamais plus qu'un `RAGENGINE_UPLOAD_MAX_BYTES` sur la route
d'ingestion, 1 Mo ailleurs. Même philosophie que `read_upload_content`
(`app.services.documents`), qui borne déjà sa propre lecture de la même façon : ce n'est pas une
régression de conception, c'est la continuation d'un choix déjà assumé dans ce code.

**Ajouté au plus interne de la pile de middlewares** (`app.add_middleware`, juste après
`RateLimitMiddleware`), pour la même raison qu'elle : un `413` construit ici traverse encore `CORS`
et `SecurityHeadersMiddleware`, donc reste lisible par un navigateur et porte les mêmes en-têtes
durcis qu'une réponse ordinaire.

## Conséquences

- `RAGENGINE_REQUEST_BODY_MAX_BYTES` (nouveau, défaut 1 000 000) documente le plafond bas générique ;
  absent de `.env.example` pour l'instant (cf. rapport de session).
- Aucune route existante n'est affectée en usage normal : les corps JSON réels (ADR-053) et les
  uploads réels (sous `RAGENGINE_UPLOAD_MAX_BYTES`) tiennent largement sous ces plafonds. Vérifié sur
  toute la suite de routes existante (`backend/tests/api/v1/`, 217 tests, y compris upload et chat en
  flux), aucune régression.
- Le flux SSE du chat n'est jamais concerné : `RequestBodyLimitMiddleware` ne touche que `receive`,
  jamais `send` — vérifié par un test dédié (`test_a_streaming_response_is_never_touched_by_the_body_limit`).
- Tests : `backend/tests/test_body_limit.py` — corps admis inchangé, `413` Problem Details avec
  `Content-Length` déclaré ou en flux `chunked` sans lui, rejeu fidèle d'un corps tamponné, plafond
  spécifique de la route d'ingestion (admis sous ce plafond, refusé au-delà), requête sans corps,
  réponse en flux non affectée.
