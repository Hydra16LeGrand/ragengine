import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { providersQueryOptions } from '@/api/providers';

/**
 * Résout un `provider_id` technique en libellé humain, via le catalogue
 * `GET /v1/providers` (défaut 3, plan interne : le backend porte désormais
 * `label`, ce hook n'a plus qu'à l'indexer).
 *
 * Sert les écrans qui n'ont qu'un identifiant brut à afficher — un
 * `embedding_model_id`/`reranker_model_id` de base, un `llm_provider_id`
 * d'évaluation — pas l'entrée complète du catalogue. Un identifiant absent
 * du catalogue (fournisseur retiré depuis) retombe sur lui-même : rien n'est
 * inventé, comme l'ancien `providerLabels.ts` qu'il remplace.
 */
export function useProviderLabel(): (providerId: string) => string {
  const { data: catalog } = useQuery(providersQueryOptions());

  return useMemo(() => {
    const labels = new Map<string, string>();
    for (const provider of catalog?.embeddings ?? []) {
      labels.set(provider.provider_id, provider.label);
    }
    for (const provider of catalog?.rerankers ?? []) {
      labels.set(provider.provider_id, provider.label);
    }
    for (const provider of catalog?.llm ?? []) {
      labels.set(provider.provider_id, provider.label);
    }
    return (providerId: string): string => labels.get(providerId) ?? providerId;
  }, [catalog]);
}
