# ADR-052 — Durcissement du bord : IP cliente réelle, en-têtes de sécurité, bornes de corps

**Date :** 2026-09-13 · **Statut :** accepté · **Portée :** `backend/Dockerfile`, `app/core/trusted_proxies.py`, `app/core/security_headers.py`, `docker/docker-compose.traefik.yml`, `docker/traefik/dynamic.yml`

## Résumé

- **IP cliente** : Traefik garde les en-têtes de proxy venant de son sous-réseau Docker. Uvicorn approuve ce sous-réseau **moins la passerelle**, et retient la première adresse non approuvée en lisant `X-Forwarded-For` de droite à gauche : c'est le visiteur, ajouté par l'edge Cloudflare. Le sous-réseau est lu au démarrage, jamais figé : le réseau `ragengine` n'a pas à être recréé.
- **Invariant** : `cloudflared` rejoint le réseau Docker et vise `http://traefik:80`. Un port publié n'est jamais une entrée publique.
- **En-têtes** : un middleware Traefik sur les routeurs `api` et `frontend` pose les mêmes valeurs que l'API. Il les écrase, donc une seule occurrence par en-tête. HSTS dure un an, sans `includeSubDomains` ni `preload`. La CSP se limite à `frame-ancestors 'none'`.
- **Corps** : 1 Mo pour l'API, 100 Mio + 1 Mio pour l'upload. `buffering` met aussi la **réponse** en tampon. Le chat SSE a donc un routeur sans buffering, borné par un `Content-Length` d'au plus 6 chiffres.

## Contexte

Audit de sécurité (l'audit sécurité 2026-09 (interne), constats 1, 3 et 5). Uvicorn gardait `forwarded_allow_ips=127.0.0.1` : l'IP du conteneur Traefik servait donc de clé au plafond IP 60/min pour tout Internet (DoS à coût nul). Elle servait aussi de clé au quota anonyme et au plafond chatbot, et remplissait `sessions.ip_address`. Le HTML servi par nginx ne portait aucun en-tête de sécurité. Aucune taille de corps n'était bornée.

Comportements vérifiés, jamais supposés :

- **uvicorn 0.52.4** (image) : `proxy_headers` vaut `True` par défaut. Uvicorn honore `X-Forwarded-For` et `X-Forwarded-Proto` seulement si le pair direct est approuvé. Il parcourt alors l'XFF de droite à gauche et retient la première adresse non approuvée (CIDR acceptés). Si toutes sont approuvées, il retient la plus à gauche.
- **Traefik 3.5.6** : pour un pair listé dans `forwardedHeaders.trustedIPs`, Traefik conserve l'XFF, `X-Forwarded-Proto` et même un `X-Real-IP` forgé, puis **ajoute l'IP du pair** à l'XFF. Pour un pair non approuvé, il remplace ces trois en-têtes. `CF-Connecting-IP` traverse toujours tel quel.
- **cloudflared** (source) : ne touche ni l'XFF ni `X-Forwarded-Proto`. Il recopie le `Content-Length` de l'edge.

Deux hypothèses de l'audit sont infirmées :

1. `--forwarded-allow-ips=<IP Traefik>` ne suffit pas. Traefik ajoute l'IP de cloudflared à l'XFF, et tout visiteur deviendrait cloudflared.
2. Valider `CF-Connecting-IP` contre les plages Cloudflare ne s'applique pas à un tunnel, où le pair est un conteneur local.

## Décision

**Chaîne de confiance** :

1. L'edge Cloudflare ajoute le visiteur à l'XFF : `<forgé…>, <visiteur>`.
2. `cloudflared` transmet sans modification à `traefik:80`.
3. Traefik (trustedIPs = son sous-réseau, lu par `ip route` au démarrage) ajoute l'IP de cloudflared : `<forgé…>, <visiteur>, <cloudflared>`.
4. Uvicorn (`python -m app.core.trusted_proxies` : sous-réseau moins passerelle, en échec ⇒ pas de démarrage) saute cloudflared et retient le visiteur. Le préfixe forgé n'est jamais atteint.

**Exclusion de la passerelle** : tout ce qui entre par un port publié (`127.0.0.1:8000`, `:8090`) arrive sous l'IP de la passerelle (NAT, ADR-040). Comme elle n'est pas approuvée, l'hôte ne peut rien forger. Il garde ses 600/min (ADR-040). Internet ne peut jamais retomber sur cette IP : son dernier saut non approuvé est une IP publique.

**Si cloudflared visait le port publié de l'API** (ou celui de Traefik), tout Internet arriverait sous la seule IP de la passerelle :

- un seul compartiment, celui des 600/min de la passerelle, partagé par tous les visiteurs : DoS et plafond affaibli à la fois ;
- quotas et `sessions.ip_address` redeviendraient globaux ;
- via le port de l'API, les en-têtes de sécurité et les bornes de corps du proxy seraient en plus contournés.

C'est un invariant de sécurité (le plan de déploiement interne, Phase C).

**En-têtes** :

- **HSTS forcé côté Traefik** : le tunnel arrive en clair, et un navigateur ignore HSTS reçu en clair (RFC 6797 §8.1). Côté API, HSTS dépend de `request.url.scheme`, jamais de l'en-tête brut.
- **Sans `includeSubDomains`** : le cookie de session est limité à l'hôte, et un sous-domaine futur non HTTPS (DNS seul) serait bloqué un an, sans retour possible. `preload` est exclu, car irréversible.
- **CSP limitée à `frame-ancestors`** : aucune politique `script-src` n'a été éprouvée sur tous les écrans (le script de thème de `index.html` est inline). `object-src 'none'` risquerait la visionneuse PDF de `/documents/{id}/content`.

**Corps** :

- **API, 1 Mo** : le plus gros JSON légitime est l'historique de chat, plafonné à 40 messages et 64 000 caractères (ADR-053). Cela fait au pire environ 400 Ko de JSON, donc le `422` applicatif précède toujours le `413` du proxy. Au-delà de 256 Kio, le tampon passe sur disque.
- **Upload, `105906176` octets** : le plafond applicatif plus 1 Mio d'enveloppe multipart. Le tampon passe sur disque au-delà de 1 Mio.
- **Chat en flux** : il n'a pas de buffering. Une requête sans `Content-Length` ou trop grosse retombe sur le routeur `api`. Elle y reçoit `413`, ou une réponse servie d'un bloc.

## Vérification (stack réelle, simulation de cloudflared)

Un conteneur jetable sur le réseau `ragengine` visait `traefik:80`. Observables : journal d'accès uvicorn, clés Redis `ratelimit:ip:*`, `sessions.ip_address`.

- **Forge** : `XFF: 6.6.6.6, 172.18.0.1, 203.0.113.7` avec `X-Real-IP` et `CF-Connecting-IP` forgés donne `203.0.113.7`. Une forge depuis l'hôte (`:8090` ou `:8000`) donne `172.18.0.1`.
- **Deux visiteurs** donnent deux clés distinctes. Aucune clé ne porte l'IP de Traefik.
- **Rafale** : 62 requêtes d'un visiteur à préfixe « passerelle » forgé reçoivent `429` dès la 61ᵉ. L'hôte continue ensuite en `200`.
- **Session invitée** : `sessions.ip_address` vaut `203.0.113.77`.
- **SSE réel** (chatbot publié) : 40 fragments reçus entre 0,03 s et 1,84 s, contre un bloc unique à la fin avec `buffering` sur banc d'essai.
- **Corps** :
  - `413` au proxy, sans atteindre l'API : 1 Mo + 1 en JSON (sur l'API comme sur le chat), 1,2 Mo en `chunked` sur le chat, upload de 102 Mio ;
  - upload de 100 Mio transmis à l'API ;
  - upload réel de 2,2 Mio : `202`, SHA-256 identique.
- **En-têtes** : une seule occurrence de chaque sur `/`, une route SPA et `/api`.

## Conséquences

- **Résiduel accepté** : un conteneur du réseau `ragengine` (workers, `ragflow-cpu`, `langfuse-web`) qui contacte `api:8000` directement peut choisir son IP. Seul le plafond est en jeu.
- **À ne jamais lire côté application** : `X-Real-IP` et `CF-Connecting-IP`, forgeables. Seul `request.client.host` fait foi.
- **Côté Cloudflare** : ne jamais activer la transformation « Remove visitor IP headers ». Laisser le HSTS de l'edge désactivé, ou lui donner les mêmes valeurs.
- **Changement de l'image** : relever `RAGENGINE_UPLOAD_MAX_BYTES` impose de relever `body-limit-upload`. `FORWARDED_ALLOW_IPS` n'est jamais lu.
- **Tests** : `tests/test_trusted_proxies.py` éprouve la valeur calculée contre le vrai middleware d'uvicorn. `tests/test_security_headers.py` compare `dynamic.yml` au module Python.

## Addendum (2026-09-16) — transport `cloudflared` : QUIC/UDP instable, bascule en HTTP/2

**Symptôme signalé** : deux versements échoués sur `rag.example.com` (942,9 ko → Cloudflare 520 « invalid
or incomplete response » ; 8,9 Mo → Cloudflare 530 / 1033 « tunnel injoignable »).

**Écarté par la preuve, pas par raisonnement** : les deux fichiers (et un test volontairement
au-delà d'`upload_max_bytes`) sont passés sans erreur en direct sur Traefik prod en boucle locale
(`127.0.0.1:8090`, en-tête `Host: rag.example.com`) **et** à travers le tunnel réel
(`https://rag.example.com`), avec un vrai espace invité et un vrai `multipart/form-data`. Le routage
(`api-upload` matche bien l'URL émise par le frontend), les bornes de corps et le chaînage IP
(reste du présent ADR) ne sont donc pas en cause — aucune régression ici.

**Cause réelle** : `docker logs <projet>-cloudflared` montrait les 4 connexions à l'edge Cloudflare
tomber ensemble toutes les ~15 minutes (`accept stream listener error…timeout: no recent network
activity`, `Failed to dial a quic connection`), rouvertes en quelques secondes. cloudflared utilise
QUIC (UDP) par défaut ; une requête — de n'importe quelle taille — qui démarre ou traverse cette
fenêtre de quelques secondes échoue côté Cloudflare (520 si la connexion meurt en cours de réponse,
530/1033 si aucune connexion n'est enregistrée au moment de la requête). C'est un défaut du chemin
UDP sortant de cette machine (routeur/FAI probable — hors périmètre du dépôt), pas un défaut de
l'application : les deux fichiers rejoués plus tard, hors de ces fenêtres, sont passés du premier
coup.

**Correctif retenu** : `command: tunnel --no-autoupdate --protocol http2 run`
(`docker/docker-compose.traefik.yml`) force le transport TCP/HTTP2 vers l'edge Cloudflare au lieu du
QUIC/UDP par défaut — insensible aux NAT/pare-feu qui coupent les flux UDP inactifs. Le flag a
disparu de `cloudflared tunnel run --help` (protocole documenté comme obsolète) mais reste
fonctionnel sur l'image épinglée `2026.9.1` : vérifié en ouvrant une seconde connexion réelle au
tunnel de production (`docker exec … cloudflared tunnel --protocol http2 run`, 5 s, tuée ensuite),
qui s'est bien enregistrée à l'edge en `protocol=http2`. **Résiduel accepté** : si une image future
retire le flag pour de bon, cloudflared refusera de démarrer (`flag provided but not defined`) —
le repli est alors de stabiliser le chemin UDP côté réseau (routeur/FAI), pas un correctif applicatif.
Ne change rien à la chaîne de confiance IP ni au SSE en flux (§ buffering) : seul le tronçon
cloudflared↔edge change de transport, pas la sémantique HTTP proxifiée vers Traefik.

**Piège à connaître** : `cloudflared tunnel run --help` lancé avec `TUNNEL_TOKEN` déjà dans
l'environnement du conteneur affiche ce jeton en clair (comportement par défaut d'`urfave/cli`,
qui imprime la valeur résolue d'un flag, y compris venue d'une variable d'environnement). Ne
jamais lancer d'aide ni de diagnostic `cloudflared` dans un conteneur qui porte le jeton.
