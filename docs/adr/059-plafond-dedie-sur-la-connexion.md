# ADR-059 — Plafond dédié sur la connexion

**Date :** 2026-09-16 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 8

## Contexte

`POST /api/v1/sessions` n'avait que le plafond IP générique du middleware anti-rafale
(`app.core.rate_limit`, 60 requêtes/minute par défaut) : un bourrage d'identifiants distribué sur
de nombreuses IP contre un même compte, ou concentré sur une IP contre de nombreux comptes, n'était
compté nulle part par cible.

## Décision

`SessionService._enforce_login_rate_limit` applique deux compteurs Redis dédiés, indépendants,
avant toute vérification de mot de passe : `ratelimit:login:ip:{IP normalisée /64}` et
`ratelimit:login:account:{adresse soumise, en minuscules}` — fenêtre commune de 15 minutes
(`LOGIN_RATE_LIMIT_WINDOW_SECONDS`), plafonds par défaut 20/IP et 10/compte
(`LOGIN_RATE_LIMIT_IP_ATTEMPTS`, `LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS`). La clé de compte est
l'adresse **soumise**, jamais le résultat d'une recherche en base : elle se comporte à l'identique
qu'un compte existe ou non, ce qui ne rouvre pas l'énumération que la connexion ferme déjà
(empreinte leurre + message unique, l'audit sécurité 2026-09 (interne) « Vérifié et sain »). Les deux
plafonds dépassés renvoient le même message (`TooManyRequestsError`, `429` + `Retry-After`, via le
port `RateLimiter` déjà utilisé partout ailleurs, ADR-052/054) : rien ne permet de distinguer lequel
des deux a été atteint.

**Échec ouvert.** Contrairement aux compteurs de dépense réelle (quotas LLM, créations d'espaces
invités — ADR-054, échec fermé), ce plafond protège un débit d'attaque, pas un budget : un Redis
injoignable ne doit pas rendre la connexion indisponible pour tout le monde le temps d'une panne. Le
choix suit le rate limiting anti-rafale déjà en place (ADR-036/040), pas les compteurs de dépense —
c'est la même famille de garde-fou. Résiduel accepté : une panne Redis ouvre une fenêtre sans
limitation dédiée (le plafond IP générique du middleware reste actif en amont, lui aussi en échec
ouvert).

**Réinitialisation après succès non implémentée.** La fenêtre de 15 minutes expire d'elle-même ;
ajouter une primitive de remise à zéro au port `RateLimiter` pour ce seul appelant n'est pas
justifié, et réinitialiser le compteur **IP** au premier succès ouvrirait un contournement (un
attaquant qui réussit une connexion légitime sur un compte, depuis une IP par ailleurs utilisée pour
bourrer d'autres comptes, effacerait le compteur qui les protège).

## Conséquences

- Deux nouvelles clés Redis par tentative de connexion, fenêtre fixe, même mécanisme `INCR`+`EXPIRE`
  que le reste de `app.core.rate_limit`.
- Un utilisateur légitime qui se trompe de mot de passe plus de 10 fois en 15 minutes est bloqué au
  même titre qu'un attaquant — compromis assumé, cohérent avec l'audit (« valeurs par défaut
  conservatrices »).
- Tests : `backend/tests/services/identity/test_sessions.py` (plafond IP, plafond compte, compte
  légitime non affecté, échec ouvert), `backend/tests/api/v1/test_identity_routes.py::test_login_beyond_the_ip_cap_answers_429_with_retry_after`.
