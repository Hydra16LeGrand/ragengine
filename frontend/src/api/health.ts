import { queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { HealthResponse } from './schemas';

/**
 * Sonde de disponibilité de la façade FastAPI.
 *
 * Le type `HealthStatus` écrit à la main en Phase 0 a été remplacé par
 * `HealthResponse`, généré depuis l'OpenAPI (`npm run generate:api`) : plus
 * aucun type de réponse n'est désormais rédigé à la main dans ce dossier.
 */
export type { HealthResponse };

export const HEALTH_PATH = '/v1/health';

export const healthQueryKey = ['health'] as const;

export function healthQueryOptions() {
  return queryOptions({
    queryKey: healthQueryKey,
    queryFn: ({ signal }) =>
      apiClient.get<HealthResponse>(HEALTH_PATH, { signal, timeoutMs: 5000 }),
    // Sonde d'infrastructure : on n'insiste pas et on n'inonde pas la console
    // de tentatives quand le backend est absent.
    retry: false,
    staleTime: 15_000,
  });
}
