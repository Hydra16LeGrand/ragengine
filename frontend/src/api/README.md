# `src/api/` — couche d'accès à la façade RAGEngine

Tout appel réseau passe par ici. Aucun `fetch` dans un composant, aucune URL de backend
ailleurs que dans ce dossier.

## Contenu

| Fichier                                                                                         | Rôle                                                                                       |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `client.ts`                                                                                     | Wrapper `fetch` typé : construction d'URL, en-têtes, timeout, annulation, erreurs RFC 9457 |
| `sse.ts`                                                                                        | Lecteur `text/event-stream` sur `fetch` + `ReadableStream` (cf. « Flux SSE » ci-dessous)   |
| `query-client.ts`                                                                               | Configuration TanStack Query (politique de retry, fraîcheur, cache)                        |
| `schema.ts`                                                                                     | **Généré** par `npm run generate:api` — jamais édité à la main                             |
| `schemas.ts`                                                                                    | Ré-export nommé des types générés (seule indirection autorisée)                            |
| `problem.ts`                                                                                    | Lecture des membres d'extension RFC 9457 (`errors` d'une 422)                              |
| `precheck.ts`                                                                                   | Validation à l'exécution du `precheck_report` (JSONB non décrit par l'OpenAPI)             |
| `health.ts`                                                                                     | Sonde `GET /api/v1/health`                                                                 |
| `auth.ts` · `knowledge-bases.ts` · `documents.ts` · `jobs.ts` · `retrieval.ts` · `providers.ts` | Une ressource par fichier, en `queryOptions` / `mutationOptions`                           |

## Flux SSE — pourquoi un lecteur écrit à la main

`POST /v1/retrieval/chat` répond en `text/event-stream`. L'API native `EventSource` **ne peut
pas** l'appeler : elle n'émet que des `GET`, sans corps. `sse.ts` lit donc le `ReadableStream`
de la réponse `fetch` et implémente la grammaire SSE (champs `event`/`data`/`id`, lignes `data`
jointes par `\n`, ligne vide = fin d'évènement, `:` = commentaire).

Aucune dépendance npm n'est ajoutée pour cela — le format tient en quelques lignes, et une
librairie tierce apporterait une politique d'erreur concurrente de celle de `client.ts`. Le
lecteur réutilise au contraire ses helpers (`buildUrl`, `readErrorProblem`,
`describeFetchFailure`), exportés pour lui : une seule vérité pour les erreurs.

Conséquence de contrat à connaître : une erreur **avant le premier octet** (accès,
`llm_provider_id` inconnu, 422) est une réponse HTTP classique et fait **rejeter** l'appel ; une
erreur **pendant** le flux arrive en évènement `error` et n'est jamais suivie de `done`. Les deux
ne se présentent pas de la même façon à l'écran, et `retrieval.ts` les sépare explicitement.

## Données non décrites par l'OpenAPI

`DocumentRead.precheck_report` et `precheck_decision` sont typés `{ [key: string]: unknown }` :
le backend les stocke en JSONB sans publier leur forme. On ne caste donc pas à l'aveugle — ils
sont validés à l'exécution dans `precheck.ts`, comme `problem.ts` valide les extensions RFC 9457.

## Gestion des erreurs — RFC 9457

Le backend renvoie ses erreurs en `application/problem+json` (CONTRIBUTING.md §4). `client.ts` les
convertit systématiquement en `ApiError`, porteuse d'un `ProblemDetail` (`type`, `title`,
`status`, `detail`, `instance` + membres d'extension).

Les pannes locales (backend éteint, timeout, requête annulée) sont normalisées dans **la même
forme**, avec `status: 0` et un `type` en `urn:ragengine:client:*`. L'UI n'a donc qu'un seul
type d'erreur à traiter :

```ts
try {
  await apiClient.post('/v1/knowledge-bases', payload);
} catch (error) {
  if (isApiError(error) && error.isNetworkError) {
    /* backend injoignable */
  }
}
```

## Origine de l'API

Le client cible des chemins relatifs préfixés par `import.meta.env.VITE_API_BASE_URL` (défaut
`/api`). En développement, le dev-server Vite proxifie `/api` vers `http://localhost:8000` ; en
production, le reverse-proxy sert la SPA et la façade sur la même origine. Conséquence : jamais
de CORS, jamais d'URL absolue dans le code.

## Types de ressources — À NE PAS ÉCRIRE À LA MAIN

Les types des ressources métier (`KnowledgeBase`, `Document`, `IngestionJob`, `RetrievalResult`…)
seront **générés depuis le schéma OpenAPI du backend en Phase 6**, une fois les contrats `/api/v1`
figés (Phase 1). Le schéma généré sera la source de vérité ; toute divergence sera une erreur de
compilation, pas un bug d'exécution.

En attendant : **aucun type métier n'est écrit à la main ici.** La seule exception assumée est
`HealthStatus` dans `health.ts` — un contrat d'infrastructure, provisoire, remplacé par le type
généré le moment venu.

## Convention pour les futures ressources

Un fichier par ressource, exposant des `queryOptions` / `mutationOptions` réutilisables plutôt que
des hooks maison, afin que la clé de cache reste unique et colocalisée avec la requête :

```ts
export const knowledgeBaseKeys = {
  all: ['knowledge-bases'] as const,
  detail: (id: string) => ['knowledge-bases', id] as const,
};
```
