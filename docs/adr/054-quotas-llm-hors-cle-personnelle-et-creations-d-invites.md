# ADR-054 — Quotas LLM hors clé personnelle, reformulation comptée, créations d'invités plafonnées

**Date :** 2026-09-13 · **Statut :** accepté · **Complète :** ADR-047, ADR-049 · **Origine :** audit de sécurité 2026-09, constat 2

## Résumé

- Les quotas s'appliquent **dès que la clé qui paie n'est pas la clé personnelle de l'appelant** : clé plateforme ou clé serveur `.env`. Avant, sans clé plateforme (le défaut), aucun quota.
- **Addendum 2026-09-21** : le **modèle** est désormais forcé (`platform_llm_model_id`) dans ce même cas — y compris sur la clé serveur, qui laissait jusque-là l'appelant choisir n'importe quel modèle sous le même plafond de *messages*. Seule la clé personnelle garde le libre choix.
- **Addendum 2026-09-21** : les évaluations ont leur **propre** plafond de compte (`RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT`, 24/jour par défaut), indépendant de celui du chat — un run programmé ne s'arrête plus en route sur le plafond de chat, et ne le vide plus au passage.
- L'unité est **l'appel LLM**, imputé avant qu'il ait lieu. Un deuxième tour de chat en consomme deux : reformulation (ADR-049) et génération.
- **L'accès à la base est vérifié avant tout appel LLM.** Avant, la reformulation partait vers le fournisseur avant que `search()` refuse l'accès.
- **Le compteur d'identité passe avant le budget global.** Sinon une identité au plafond qui insiste épuise le budget de tous.
- **Compteurs en échec fermé** : si Redis est injoignable, la réponse est `503`, pas un laisser-passer.
- **Plafond global de créations d'espaces invités** : 20 par heure par défaut, `429` avec `Retry-After`.
- **Les évaluations suivent le même régime**, avec une imputation par appel. Un invité ne lance aucune évaluation, et la routine programmée ignore les bases des invités et des comptes suspendus.

## Contexte

`RetrievalService.chat` n'évaluait les quotas d'ADR-047 que si `RAGENGINE_PLATFORM_TRIAL_LLM_API_KEY` était posée. Sans elle, invités, anonymes et chatbots publiés utilisaient la clé serveur sans aucun plafond. La reformulation d'ADR-049 ajoutait un appel `complete()` hors quota, et partait avant le contrôle d'accès : un anonyme pouvait payer un appel par requête sur n'importe quel identifiant de base. Enfin, `POST /guest-sessions` n'est pas authentifié et les quotas `GUEST_MAX_*` sont comptés par espace : un cookie neuf repart de zéro.

## Décision

### Table de vérité : appelant et source de clé → quotas

*Mise à jour 2026-09-21 (addendum ci-dessous) : la colonne Modèle ne distingue plus plateforme/serveur — les deux forcent désormais `platform_llm_model_id` de façon identique — et la ligne Évaluation est scindée pour la même raison ; son compteur de compte est en outre désormais **dédié** (`évaluation`), distinct de celui du chat (`compte`).*

| Appelant | Clé personnelle pour ce fournisseur | Clé plateforme posée | Clé qui paie | Modèle | Compteurs (dans l'ordre) |
|---|---|---|---|---|---|
| Anonyme (Playground, base publique) | sans objet | oui / non | plateforme / serveur | forcé | IP → global |
| Invité (espace éphémère) | jamais consultée | oui / non | plateforme / serveur | forcé | IP → global |
| Compte connecté (session) | non | oui / non | plateforme / serveur | forcé | compte (chat) → global |
| Compte connecté (session) | **oui** | indifférent | personnelle | demandé | **aucun** |
| Clé API (compte propriétaire) | non | oui / non | plateforme / serveur | forcé | compte (chat) → global |
| Clé API (compte propriétaire) | **oui** | indifférent | personnelle | demandé | **aucun** |
| Visiteur d'un chatbot publié (anonyme, invité) | sans objet | oui / non | plateforme / serveur | forcé | chatbot → global |
| Visiteur d'un chatbot publié, connecté avec sa clé pour ce fournisseur | **oui** | indifférent | personnelle du visiteur | celui du propriétaire | **aucun** |
| Évaluation : manuelle, asynchrone ou programmée (demandeur ou propriétaire, compte réel) | non | oui / non | plateforme / serveur | forcé | compte (évaluation) → global |
| Évaluation : idem, avec clé personnelle du demandeur/propriétaire pour le fournisseur demandé | **oui** | indifférent | personnelle | demandé | **aucun** |
| Évaluation demandée par un invité, ou planifiée sur la base d'un invité | refusée | | | | aucun appel |

Un invité est compté par IP, pas par espace : un espace neuf ne rouvre pas de quota. Si l'IP manque, le compteur partagé `unknown` s'applique. Tests : `tests/services/test_retrieval_chat.py`, section « Quotas hors clé personnelle », et `tests/services/test_platform_llm_budget.py`, section « Addendum 2026-09-21 ».

### Comptage de la reformulation

Il y a une imputation pour la génération, puis une seconde si `_query_rewrite_needed` est vrai, c'est-à-dire à partir du deuxième message. C'est la même condition que celle qui déclenche `complete()`. Les deux imputations précèdent tout appel LLM. Si la seconde est refusée, la reformulation est sautée (repli sur le dernier message, comme sur une panne d'ADR-049) et la génération déjà imputée a lieu. Un premier tour ou un appelant BYOK n'impute aucune reformulation.

Écarté : **imputer deux unités d'un bloc** avec `INCRBY`. Il aurait fallu une nouvelle primitive sur le port `RateLimiter`, et un tour aurait été refusé alors qu'il restait de quoi générer.

### Ordre et atomicité

Dans `chat()`, l'ordre est : accès, source de clé, résolution du fournisseur, imputations, reformulation, `search()`, `stream()`. Chaque imputation passe par `RedisRateLimiter.hit` : un `INCR` atomique puis une comparaison, sans fenêtre « vérifier puis agir ». Sur 20 imputations concurrentes avec un plafond de 5, exactement 5 passent (test contre un vrai Redis).

Le compteur d'identité passe avant le global, à l'inverse d'ADR-047. Un appel refusé incrémente quand même son compteur, sans effet observable puisque le compteur est déjà au-delà du plafond. Un refus d'accès ou un fournisseur inconnu n'impute rien.

### Panne de Redis

Les compteurs de dépense échouent fermés : `ServiceUnavailableError` (`503`). La docstring de `platform_llm_budget` annonçait un échec ouvert, mais le code laissait remonter `RedisError`, soit une `500`. Le rate limiting anti-rafale (`RateLimiter`, ADR-036/040) reste en échec ouvert, inchangé. Conséquence : Redis en panne coupe le chat de tout appelant sans clé personnelle, ainsi que l'ouverture d'espaces invités.

### Évaluations

Un item tenté coûte jusqu'à trois appels : génération de la Q/R, réponse du chat, jugement. Une génération inexploitable coûte un appel, et le document suivant est tenté. Le nombre d'appels d'un run n'est donc pas connu d'avance : **chaque appel est imputé juste avant d'avoir lieu**. Génération et jugement passent par `_FundedLLM.complete`, la réponse par `RetrievalService.chat` (avec `via_evaluation=True`, addendum 2026-09-21). La source de clé vient de la même fonction que pour le chat (`resolve_llm_funding`) ; **le compteur, lui, est dédié** (`QuotaSubject.EVALUATION`, addendum ci-dessous) — les trois appels d'un item rejoignent le même compteur d'évaluation, jamais celui du chat. Un run consomme exactement autant d'unités qu'il fait d'appels.

Si le quota s'épuise en cours de run, la génération s'arrête et le run garde les items déjà produits : on ne jette pas des appels déjà payés. C'est la même issue qu'un run à court de documents exploitables. Si aucun item n'a été produit, l'erreur remonte : `429` en synchrone, run `failed` en asynchrone, échec journalisé pour la routine. Écarté : **imputer tout le run d'avance**. Son coût réel est inconnu, et un forfait sur-imputerait ou ne bornerait rien.

Le service revérifie lui-même le compte réel et l'accès `EDITOR` avant tout appel payé : deux des trois chemins s'exécutent sous Celery, sans routeur. **Aucune évaluation pour un invité.** C'est la solution la plus simple, et l'espace invité sert à essayer le Playground et une ingestion (ADR-005), pas un outil qualité à trois appels par question. La routine quotidienne (`dispatch_scheduled_evaluations`) ignore les bases dont le propriétaire est invité ou suspendu, et ses runs sont imputés au compte du propriétaire : le fan-out ne contourne aucun compteur.

### Créations d'espaces invités

`SessionService.open_guest_space` impute la création à un compteur **global** (`guest-spaces:created:global`, fenêtre fixe d'une heure). Le plafond vient de `GUEST_SPACE_CREATION_HOURLY_LIMIT` (défaut 20). L'imputation a lieu avant toute écriture, et la panne de Redis est traitée comme pour les compteurs de dépense. Ce plafond est indépendant du plafond par IP du middleware, qui dépend de l'IP réelle vue derrière le proxy. Le `429` porte `Retry-After` : `ragengine_error_handler` le pose désormais pour tout `TooManyRequestsError` levé avec `retry_after_seconds`.

## Conséquences

- **Le propriétaire est lui aussi sous quota** s'il utilise la clé serveur sans clé personnelle (20 appels par jour par défaut pour le chat, 24 pour les évaluations depuis l'addendum 2026-09-21). Pour un usage illimité, il renseigne sa clé personnelle ou relève les plafonds `RAGENGINE_PLATFORM_LLM_*_DAILY_LIMIT` concernés.
- ~~Un run programmé de 8 questions coûte jusqu'à 24 appels, au-delà du plafond de compte par défaut...~~ **Fermé par l'addendum 2026-09-21** : les évaluations ont désormais leur propre plafond de compte (24/jour par défaut, dimensionné exactement sur ce pire cas), indépendant de celui du chat.
- `EvaluationRun.llm_provider_id` enregistre le fournisseur effectif, c'est-à-dire le modèle forcé dès que la clé qui paie n'est pas la clé personnelle du demandeur (plateforme *ou*, depuis l'addendum 2026-09-21, serveur), et plus seulement le fournisseur demandé.
- ~~Risque résiduel, hors périmètre : sur la clé serveur, le modèle n'est pas forcé...~~ **Fermé par l'addendum 2026-09-21** : le modèle est désormais forcé sur la clé serveur exactement comme sur la clé plateforme.
- Les tests utilisent un limiteur en mémoire par défaut (`tests/in_memory_rate_limiter.py`). Aucun test n'incrémente plus les compteurs Redis de la stack de développement.
- **Quota anonyme normalisé par IP (2026-09-13, ADR-040/052)** : la clé `platform-llm-budget:anonymous:*` passe désormais par `normalize_client_ip` (préfixe `/64` en IPv6, IPv4 mappée ramenée à l'IPv4) — sinon ce plafond, comme le compteur IP anti-rafale, se contourne en changeant d'adresse dans son propre `/64`.

## Addendum — 2026-09-21 : modèle imposé hors clé personnelle, plafond dédié aux évaluations

Les deux points laissés à l'utilisateur par cette ADR (« Risque résiduel, hors périmètre » et le partage de compteur chat/évaluation, ci-dessus) ont été tranchés.

### 1. Le modèle est forcé dès que la clé qui paie n'est pas la clé personnelle

`resolve_llm_funding` (`app/services/retrieval.py`) forçait déjà `provider_id` à `platform_llm_model_id` quand la clé plateforme payait. Sur la clé serveur (le cas par défaut, sans `RAGENGINE_PLATFORM_TRIAL_LLM_API_KEY`), elle laissait passer le `provider_id` **demandé par l'appelant** tel quel — le budget global, qui compte des appels à un coût supposé calibré sur `platform_llm_model_id` (`platform_llm_assumed_cost_per_message_usd`), ne bornait donc pas la dépense réelle si l'appelant choisissait un modèle plus cher.

**Décision : même forçage sur la clé serveur.** `resolve_llm_funding` retourne désormais `LlmFunding(SERVER, settings.platform_llm_model_id, None)` — un seul changement, dans le seul point de décision partagé par le chat et les évaluations, qui ferme mécaniquement tous les chemins de la table de vérité mise à jour ci-dessus : anonyme, invité, compte sans clé perso, clé API, visiteur de chatbot publié (y compris le modèle choisi par le *propriétaire* à la publication, `chatbot_llm_provider_id` — c'était déjà vrai côté plateforme, c'est désormais vrai côté serveur), et évaluations. Seule la clé **personnelle** de l'appelant respecte encore son choix.

**Effet de bord assumé sur `evaluation_scheduled_llm_provider_id`.** Ce réglage (Groq par défaut) vise à ne jamais faire dépendre la routine programmée du même crédit Anthropic que le chat interactif (cf. sa docstring, `app/core/config.py`). Le propriétaire d'une base n'a en pratique presque jamais de clé personnelle pour ce fournisseur : la routine tourne donc hors clé personnelle, et son modèle **effectif** est maintenant lui aussi imposé à `platform_llm_model_id` — cette tension existait déjà dès qu'une clé plateforme était configurée (déjà documenté : « `EvaluationRun.llm_provider_id` enregistre le fournisseur effectif... »), elle s'applique simplement aussi au cas, plus courant, de la clé serveur. Un run programmé ne diversifie donc plus son fournisseur par défaut ; à rouvrir séparément si cette diversification reste souhaitée (par ex. une clé personnelle dédiée sur le compte propriétaire, ou un paramètre de modèle forcé propre à la routine).

**Effet de bord sur la validation d'entrée.** Avant cet addendum, `resolve_llm_provider` (appelé après la résolution de financement) était le seul point qui validait qu'un `llm_provider_id` correspond à un modèle du catalogue. Le forçage hors clé personnelle contourne désormais cette validation pour la valeur *demandée* (elle n'atteint plus jamais `resolve_llm_provider` dans ce cas). `resolve_llm_funding` porte donc lui-même la validation, avant toute décision de financement : un `provider_id` absent du catalogue lève `ValidationError` immédiatement, quel que soit l'appelant — invariant inchangé pour le reste (`chat()` docstring, § 3), déplacé plus tôt.

### 2. Les évaluations ont leur propre plafond de compte

`EvaluationService._fund` imputait au même `QuotaSubject.USER` que `RetrievalService.chat` — même compteur Redis, même identifiant de compte. Un run programmé par défaut (`evaluation_scheduled_sample_size=8`, jusqu'à 3 appels/item) consomme jusqu'à 24 appels : au-delà de `platform_llm_user_daily_limit` (20/jour par défaut), le run s'arrêtait en route, et videait au passage le plafond de chat du propriétaire pour le reste de la journée.

**Décision : compteur dédié.** Nouveau `QuotaSubject.EVALUATION` (`app/services/identity/platform_llm_budget.py`), clé Redis `platform-llm-budget:evaluation-user:{user_id}`, plafond `RAGENGINE_PLATFORM_LLM_EVALUATION_DAILY_LIMIT` (**défaut 24** = `evaluation_scheduled_sample_size` (8) × 3 appels/item — le pire cas exact d'un run programmé complet sous les valeurs par défaut). `EvaluationService._fund` impute désormais ce compteur pour la génération et le jugement (`_FundedLLM.complete`). La **réponse du chat** d'un item (`RetrievalService.chat`, rejouée par `_generate_answer`) devait suivre le même compteur : `chat()` gagne un paramètre `via_evaluation: bool = False`, posé par `EvaluationService._generate_answer`, qui redirige `_quota_scope` vers `QuotaSubject.EVALUATION` au lieu de `QuotaSubject.USER` — sans lui, seuls 2 des 3 appels d'un item auraient rejoint le nouveau compteur, et la réponse aurait continué à vider le plafond de chat comme avant. Le budget global (`enforce_global_budget`, appelé après le compteur d'identité dans `charge_llm_call`) reste inchangé et s'applique aux deux compteurs : c'est toujours la barrière ultime, jamais contournée.

Les invités restent exclus de toute évaluation (`_authorize_spending`, inchangé) : ce nouveau plafond ne rouvre rien de ce côté.

### Tests

- `tests/services/test_platform_llm_budget.py` : plafond d'évaluation isolé, indépendance vis-à-vis du plafond de chat pour le même compte, run programmé complet (24 appels) sous le nouveau défaut, échec fermé sur Redis injoignable — même politique que les quatre autres compteurs.
- `tests/services/test_retrieval_chat.py` : modèle forcé sur la clé serveur pour un compte connecté, un anonyme, un invité, une clé API et un chatbot publié (modèle du propriétaire écrasé) ; libre choix confirmé inchangé avec une clé personnelle.
- `tests/services/test_evaluations.py` : les trois appels d'un item rejoignent le compteur d'évaluation, jamais celui du chat.
- `tests/workers/test_evaluation_tasks.py` : un run programmé par défaut (8 items, 24 appels) se termine `completed` sous le nouveau plafond, sans entamer le plafond de chat du propriétaire ; plafond d'évaluation opposable à la routine, indépendamment du chat.

## Addendum — 2026-10-04 : aucun plafond ni modèle imposé par défaut dans le cœur (ADR-069)

Un relecteur externe a relevé qu'un auto-hébergeur de RAGEngine subissait, sans l'avoir demandé, les plafonds de la démo : 20 messages par jour, 24 évaluations, 10 $ de budget, `claude-haiku-4-5` imposé, message « essai gratuit ». Les chemins de dépense ne sont pas rouverts : seule la **valeur par défaut** change de propriétaire.

- **Cœur seul** : `RAGENGINE_PLATFORM_LLM_{MODEL_ID,USER_DAILY_LIMIT,EVALUATION_DAILY_LIMIT,DAILY_BUDGET_USD}` valent `None` par défaut. Un plafond absent n'imputera rien et ne sollicite pas Redis ; un modèle absent laisse le modèle demandé (`LlmFundingPolicy.shared_funding(settings, provider_id)`). Un opérateur active ce qu'il veut en posant la variable ; `VAR=` (vide) vaut « désactivé ». Les noms ne changent pas : aucun alias n'est nécessaire, les `.env` déployés sont lus tels quels. Avec un budget global, renseigner aussi le modèle imposé : le coût par appel est calibré dessus. Le message de plafond du cœur est neutre.
- **Offre hébergée** : le greffon `platform` porte les valeurs de la démo (`app/plugins/platform/llm_defaults.py`) et les branche sur le cœur par `Plugin.settings_defaults`. `Settings` les applique **uniquement aux réglages que l'environnement ne renseigne pas** : un `.env` de production qui pose une variable l'emporte, et un `.env` qui ne la pose pas retrouve exactement 20 / 24 / 10 $ / `claude-haiku-4-5` — `.env.prod.local` n'est pas modifié. Les messages de la démo (« essai gratuit », « clé LLM plateforme ») sont rétablis par le greffon (règle de compteur surchargée, `Plugin.llm_global_budget_detail`).
- **Prouvé** par `tests/plugins/platform/test_llm_spending_defaults.py` (défauts de la démo, priorité de l'environnement, messages) et `tests/test_core_without_plugins.py` (cœur sans plafond, sans modèle imposé, sans sollicitation de Redis). Les tests du greffon (table de vérité d'ADR-054, y compris le modèle forcé sur la clé serveur et les trois compteurs) passent inchangés.
- **Interface** : `GET /providers` expose `llm_forced_model_id` (`null` sans modèle imposé). Sans lui, le Playground et les évaluations annonçaient « choix ignoré » alors que le cœur seul respecte le modèle demandé ; le rappel n'apparaît plus quand la valeur est `null`.
