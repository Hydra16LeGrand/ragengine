# ADR-053 — Bornes de taille des textes envoyés au GPU et au LLM

**Date :** 2026-09-13 · **Statut :** accepté · **Origine :** audit de sécurité 2026-09, constat 3

## Contexte

`RetrievalSearchRequest.query` et `ChatTurn.content` n'avaient que `min_length=1`, `messages` aucune
borne, et ces champs sont atteignables **sans compte** (base `public`, chatbot publié). La requête
est embeddée (TEI tronque à 8 192 tokens, `--auto-truncate` vrai par défaut en 1.9) puis **appariée à
chaque candidat** du reranking ; l'historique part en entier au LLM, souvent sur la clé plateforme.
Précédent : un fragment de 41 264 caractères (~8 000 tokens) a suffi à un `CUDA out of memory`
(ADR-044, addendum). Le frontend envoyait tout l'historique, sans fenêtre : borner le backend seul
aurait fait finir toute longue conversation en `422`.

## Décision

| Borne | Valeur | Justification |
|---|---|---|
| `query`, tour `user` | 4 000 car. | ~800 tokens (≈ 5 car./token en français), 10× sous la troncature TEI ; payée une fois par candidat reranké, elle reste au niveau d'un fragment ordinaire. Une page A4 ≈ 3 000 car. |
| tour `assistant` | 32 000 car. | Réponse générée par la façade, ≤ 4 096 tokens (~16 400 car.) : près du double. |
| `messages` | 40 tours | 20 échanges. |
| somme de `messages` | 64 000 car. | ~16 000 tokens par tour au LLM : coût borné, sous le contexte des modèles du catalogue. |
| filtres | 1 000 `document_ids`, 100 valeurs, 35/255/200 car. | BCP 47, RFC 6838, thème. |
| identifiants de fournisseur, thème, nom de fichier | 200 / 200 / 255 car. | Catalogue fermé, libellé, `NAME_MAX`. |

- Le tour `user` est borné comme une requête parce qu'il **en devient une** (premier tour, repli de la
  reformulation) : borné plus large, il lèverait une erreur de validation **dans** le service — `500`.
- Les invariants de l'historique (bornes, dernier tour `user`) vivent dans un type partagé,
  `ChatMessages`, porté aussi par `PublicChatbotRequest`.
- **Le frontend fenêtre l'historique** avec les mêmes valeurs (`frontend/src/api/text-limits.ts`,
  `boundChatHistory`) : tours les plus récents, réponse trop longue tronquée, jamais de tour
  `assistant` en tête. Saisie limitée par `maxLength`, refus de taille traduit (FR/EN).

## Conséquences

- Une longue conversation ne finit jamais en `422` ; le modèle perd de vue ses débuts (la
  reformulation de requête ne lisait déjà que les 6 derniers messages).
- Les constantes existent en deux exemplaires (Python, TypeScript) : les changer ensemble.
- Défaut corrigé au passage : une conversation publique finissant par un tour `assistant` répondait
  `500` au lieu de `422`.
- Hors périmètre : la lecture du corps JSON lui-même n'est pas bornée par la façade (`request.body()`) —
  c'est le plafond de corps du reverse proxy qui la protège.
