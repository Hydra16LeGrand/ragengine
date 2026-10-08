import { QueryClient } from '@tanstack/react-query';

import { isApiError } from './client';

/**
 * Configuration TanStack Query (état serveur).
 *
 * Choix de Phase 0 :
 *  - on ne rejoue jamais une erreur 4xx (contrat violé : réessayer ne changera
 *    rien) ; on rejoue au maximum deux fois une 5xx ou une panne réseau ;
 *  - pas de refetch au focus par défaut : les écrans qui doivent rester frais
 *    (suivi de job d'ingestion) déclareront leur propre `refetchInterval` ;
 *  - `staleTime` court mais non nul pour éviter les rafales de requêtes au
 *    montage des écrans.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (isApiError(error) && error.isClientError) return false;
          return failureCount < 2;
        },
        retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 8000),
      },
      mutations: {
        retry: false,
      },
    },
  });
}

/** Instance unique de l'application (montée dans `main.tsx`). */
export const queryClient = createQueryClient();
