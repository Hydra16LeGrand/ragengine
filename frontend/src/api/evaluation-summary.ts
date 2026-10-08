/**
 * Vue globale des évaluations, toutes bases visibles confondues (Phase 12, Étape 5).
 *
 * Un seul endpoint racine, en lecture seule : pas de mutation, pas de clé
 * paramétrée par base — le périmètre RBAC est déjà résolu côté serveur
 * (`app.services.evaluation_summary`, même portée que
 * `GET /api/v1/knowledge-bases`).
 */
import { queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { EvaluationsSummaryRead } from './schemas';

export const EVALUATIONS_SUMMARY_PATH = '/v1/evaluations/summary';

export const evaluationsSummaryKeys = {
  all: ['evaluations-summary'] as const,
};

export function evaluationsSummaryQueryOptions() {
  return queryOptions({
    queryKey: evaluationsSummaryKeys.all,
    queryFn: ({ signal }) =>
      apiClient.get<EvaluationsSummaryRead>(EVALUATIONS_SUMMARY_PATH, { signal }),
  });
}
