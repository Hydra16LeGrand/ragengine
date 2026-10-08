/**
 * Évaluations d'une base de connaissances (Phase 8).
 *
 * Trois endpoints, deux natures très différentes :
 *  - `GET .../evaluations` et `GET .../evaluations/{id}` : lecture ordinaire,
 *    accessible au rôle `viewer` ;
 *  - `POST .../evaluations` : **synchrone et coûteux**. La façade génère les
 *    questions, y répond et les fait juger — plusieurs appels LLM facturés par
 *    document échantillonné — avant de répondre `201`. D'où deux écarts
 *    assumés par rapport aux autres mutations du client :
 *      1. un délai d'attente très large (cf. `RUN_TIMEOUT_MS`) ;
 *      2. aucun rejeu (garanti par la configuration globale des mutations) :
 *         relancer automatiquement une requête abandonnée doublerait la dépense
 *         sans que personne ne l'ait demandé.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import { KNOWLEDGE_BASES_PATH } from './knowledge-bases';
import { queryClient } from './query-client';
import type { EvaluationCreate, EvaluationRunRead, EvaluationRunSummary } from './schemas';

export const evaluationKeys = {
  all: ['evaluations'] as const,
  listByKnowledgeBase: (kbId: string) => ['evaluations', 'knowledge-base', kbId] as const,
  detail: (kbId: string, evaluationId: string) =>
    ['evaluations', 'knowledge-base', kbId, evaluationId] as const,
};

/**
 * Dix minutes. Le pire cas réaliste (`sample_size` à 20, trois appels LLM par
 * item, modèle distant chargé) dépasse largement le délai de 15 s calibré pour
 * du JSON ordinaire. Abandonner côté client n'annulerait ni le travail déjà
 * engagé ni les tokens déjà consommés : mieux vaut attendre la réponse que
 * jeter un run payé.
 */
const RUN_TIMEOUT_MS = 600_000;

/**
 * Rythme de suivi d'un run asynchrone (ADR-028) `pending`.
 *
 * Un run asynchrone dure au minimum plusieurs dizaines de secondes (jusqu'à
 * 200 documents, plusieurs appels LLM chacun) : pas la peine d'un rythme
 * aussi serré que `jobQueryOptions` (`app/api/jobs.ts`, qui suit un GPU local).
 */
const EVALUATION_POLL_INTERVAL_MS = 4_000;

/**
 * Historique résumé, du plus récent au plus ancien — sans les items.
 *
 * Tant qu'un run `pending` (chemin asynchrone, ADR-028) figure dans la liste,
 * on la réinterroge : c'est le seul moyen de voir sa résolution
 * (`completed`/`failed`) sans recharger la page. Aucun run `pending` visible
 * ⇒ plus aucune requête, même logique que `jobQueryOptions`
 * (`app/api/jobs.ts`).
 */
export function knowledgeBaseEvaluationsQueryOptions(kbId: string) {
  return queryOptions({
    queryKey: evaluationKeys.listByKnowledgeBase(kbId),
    queryFn: ({ signal }) =>
      apiClient.get<EvaluationRunSummary[]>(`${KNOWLEDGE_BASES_PATH}/${kbId}/evaluations`, {
        signal,
      }),
    refetchInterval: (query) => {
      const runs = query.state.data;
      if (runs === undefined) return false;
      return runs.some((run) => run.status === 'pending') ? EVALUATION_POLL_INTERVAL_MS : false;
    },
  });
}

/**
 * Détail d'un run, items compris.
 *
 * Un run **résolu** (`completed`/`failed`) est immuable : ses items, ses
 * notes et ses agrégats ne changeront plus, on le garde donc frais
 * indéfiniment — replier puis déplier une ligne de l'historique ne doit pas
 * rejouer la requête. Un run `pending` (chemin asynchrone, ADR-028) est
 * l'exception : il n'a pas encore d'items, et l'exécution Celery le fera
 * transiter vers `completed`/`failed` sans que la SPA n'y soit pour rien — on
 * le réinterroge donc jusqu'à sa résolution, exactement comme
 * `jobQueryOptions` (`app/api/jobs.ts`) suit un job d'ingestion.
 */
export function knowledgeBaseEvaluationQueryOptions(kbId: string, evaluationId: string) {
  return queryOptions({
    queryKey: evaluationKeys.detail(kbId, evaluationId),
    queryFn: ({ signal }) =>
      apiClient.get<EvaluationRunRead>(
        `${KNOWLEDGE_BASES_PATH}/${kbId}/evaluations/${evaluationId}`,
        { signal },
      ),
    staleTime: (query) => (query.state.data?.status === 'pending' ? 0 : Infinity),
    refetchInterval: (query) =>
      query.state.data?.status === 'pending' ? EVALUATION_POLL_INTERVAL_MS : false,
  });
}

/**
 * `POST .../evaluations` — lance un run et rend le résultat complet.
 *
 * `onSuccess` est porté par les options plutôt que laissé à l'appelant : la
 * cohérence du cache après un run est une propriété de l'endpoint, pas une
 * politesse d'écran. Le run rendu est *déjà* le détail canonique (il contient
 * ses items) : on l'installe donc directement, ce qui évite un aller-retour
 * inutile au premier dépliage. L'historique, lui, est invalidé.
 *
 * ⚠️ Un appelant qui redéfinirait `onSuccess` en surchargeant ces options
 * écraserait cette mise à jour : utiliser `mutateAsync` et enchaîner après
 * l'attente, comme le fait `EvaluationsPanel`.
 */
export function createEvaluationMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['evaluations', 'create', kbId] as const,
    mutationFn: (payload: EvaluationCreate) =>
      apiClient.post<EvaluationRunRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}/evaluations`, payload, {
        timeoutMs: RUN_TIMEOUT_MS,
      }),
    onSuccess: async (created: EvaluationRunRead) => {
      queryClient.setQueryData(evaluationKeys.detail(kbId, created.id), created);
      await queryClient.invalidateQueries({
        queryKey: evaluationKeys.listByKnowledgeBase(kbId),
      });
    },
  });
}
