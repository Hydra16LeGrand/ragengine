# ADR-049 — Reformulation LLM de la requête de retrieval, sur un deuxième tour ou plus

**Date :** 2026-09-10 · **Statut :** accepté

## Contexte

`RetrievalService.chat` n'utilisait que `payload.messages[-1].content` comme requête de recherche
(`query` de `RetrievalSearchRequest`) — jamais l'historique. Une première question trop vague qui
obtient « rien trouvé de pertinent », suivie d'un message qui la complète (« et pour ce cas
précis ? »), interroge donc le moteur sur ce complément seul : le retrieval échoue sur le tour
suivant exactement pour la même raison que sur le premier. Constaté en usage réel, décision
utilisateur du 2026-09-10.

Distinct du manque déjà tracé dans le plan interne (§ dettes non corrigées, q06) : celui-ci
concerne le traitement **translinguistique** d'une requête isolée (`cross_languages`, natif RAGFlow,
bloqué derrière `Tenant.llm_id`/Phase 13). Ce qui manquait ici est différent — combiner plusieurs
tours d'une même conversation — et ne dépend d'aucune fonctionnalité RAGFlow : un appel LLM propre à
RAGEngine, avant le `search()` déjà appelé par `chat()`, suffit.

## Décision

**Un appel `LLMPort.complete` court, inséré entre la résolution du fournisseur LLM et `search()`.**
`_build_retrieval_query` (`app/services/retrieval.py`) :

- **Sauté sur un premier message** (`len(messages) == 1`) : rien à condenser, l'appel serait
  purement coûteux pour reproduire ce que `messages[-1].content` donne déjà.
- **Sinon**, un seul appel non-streamé (`complete`, pas `stream` : la requête tient en quelques mots,
  pas besoin d'affichage au fil de l'eau), avec un prompt système qui instruit le modèle de renvoyer
  le dernier message tel quel s'il se suffit à lui-même, ou de le compléter avec juste ce qu'il faut
  de l'historique sinon — jamais répondre à la question, jamais inventer un fait ou un mot-clé absent
  des messages fournis, jamais changer la langue ni l'intention du dernier message.
- **Fenêtre d'historique bornée** (`_QUERY_BUILDER_HISTORY_MESSAGES = 6`, soit ~3 échanges) : au-delà,
  le contexte pertinent pour comprendre un message qui en complète un autre est épuisé, et
  condenser toute la conversation ferait grossir prompt et coût sans bénéfice.
- **`max_tokens` étroit** (`_QUERY_BUILDER_MAX_TOKENS = 200`) : un dérapage du modèle (il se met à
  répondre plutôt que reformuler) coûte peu et se remarque vite.

**Même fournisseur LLM que la génération, pas un modèle dédié plus rapide/économique.** Envisagé
(latence : un aller-retour LLM de plus, séquentiel, avant le vrai), écarté pour cette version :
`resolve_llm_provider` est déjà résolu et authentifié avant ce point précisément pour être réutilisé
sans nouvelle résolution ; introduire un second axe de configuration (quel modèle pour cette étape,
sur quel fournisseur, avec quelle clé) aurait ajouté une vraie surface de configuration pour un
gain non mesuré. Rouvrable si la latence mesurée en production le justifie.

**Tolérant à l'échec par construction, jamais bloquant.** Toute `LLMError` (panne, quota, timeout —
toutes les exceptions SDK des adaptateurs s'y traduisent déjà, `providers/llm/base.py`) et toute
réponse vide ou blanche retombent silencieusement sur `messages[-1].content`, journalisées en
`warning`. Le tour de chat ne doit jamais échouer à cause de cette étape : dégrader vers le
comportement d'avant ce correctif est toujours correct, jamais une erreur pour l'appelant.

**Aucun impact sur `payload.messages` envoyé au modèle de génération.** Seule la requête envoyée au
moteur de recherche change ; la conversation complète part toujours, inchangée, vers `llm.stream()`
pour la génération de la réponse finale (`test_chat_passes_the_full_conversation_history_after_the_
system_prompt`).

## Ce qui a été explicitement écarté

- **Un modèle dédié, moins cher/plus rapide, pour cette étape.** Cf. ci-dessus — pas de mécanisme de
  sélection de modèle par usage aujourd'hui, ajouté ici il aurait fallu l'inventer pour un seul appelant.
- **Réécriture systématique, même sur un premier message.** Le prompt pourrait aussi normaliser un
  message isolé (fautes, formulation) ; explicitement hors périmètre ici — le besoin exprimé est la
  combinaison multi-tour, pas la reformulation single-tour, et chaque appel LLM ajouté sur le chemin
  chaud a un coût réel.
- **Faire compter cet appel dans les quotas de la clé plateforme (ADR-047).** `enforce_*_quota`
  décompte des *requêtes* de chat (un `INCR` Redis par tour), pas un coût réel en tokens — déjà une
  approximation documentée (`platform_llm_assumed_cost_per_message_usd`, forfaitaire). Ce second
  appel interne l'accentue légèrement mais ne la fausse pas : aucun mécanisme n'impute aujourd'hui de
  coût réel après-coup, pour aucun appel LLM de ce chemin. Un vrai suivi de coût reste un chantier
  séparé, non engagé ici.

## Conséquences

- `backend/app/services/retrieval.py` : `_build_retrieval_query` + trois constantes
  (`_QUERY_BUILDER_HISTORY_MESSAGES`, `_QUERY_BUILDER_MAX_TOKENS`, `_QUERY_BUILDER_SYSTEM_PROMPT|Final`) ;
  `chat()` appelle cette fonction au lieu de lire `payload.messages[-1].content` directement.
- `backend/app/domain/schemas/retrieval.py` : docstring de `RetrievalChatRequest.messages` mise à jour
  (la requête de recherche n'est plus toujours le dernier message brut).
- Sept tests ajoutés/étendus (`backend/tests/services/test_retrieval_chat.py`) : requête inchangée sur
  un premier tour (et aucun appel `complete` déclenché), requête reformulée envoyée au moteur sur un
  tour suivant, repli sur panne LLM, repli sur réponse vide, bornage de la fenêtre d'historique.
- plan interne : le manque « aucun traitement de la requête » est désormais partiellement comblé
  (le volet multi-tour) ; le volet translinguistique (q06, `cross_languages`) reste ouvert, inchangé.
