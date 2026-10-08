/**
 * Catalogue des briques disponibles — `GET /v1/providers`.
 *
 * Endpoint public (aucune authentification requise) et quasi statique : il
 * décrit ce que le déploiement sait faire, pas l'état d'un traitement. On le
 * garde donc frais longtemps plutôt que de le réinterroger à chaque montage
 * du formulaire de création.
 */
import { queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { ProviderCatalog } from './schemas';

export const PROVIDERS_PATH = '/v1/providers';

export const providerKeys = {
  catalog: ['providers'] as const,
};

export function providersQueryOptions() {
  return queryOptions({
    queryKey: providerKeys.catalog,
    queryFn: ({ signal }) => apiClient.get<ProviderCatalog>(PROVIDERS_PATH, { signal }),
    staleTime: 10 * 60_000,
  });
}
