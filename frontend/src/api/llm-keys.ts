/**
 * Clés LLM personnelles du compte courant.
 *
 * Utilisées en priorité sur la clé serveur pour le fournisseur correspondant,
 * sur `POST /retrieval/chat` — jamais renvoyées en clair, y compris à la
 * création (`UserLlmKeyRead` ne porte que `provider`/horodatages). Réservé
 * aux comptes réels ; la garde réelle est `RequireAccount`
 * (`features/auth/RequireAccount.tsx`), pas ce module.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { LlmVendor, UserLlmKeyCreate, UserLlmKeyRead } from './schemas';

export const LLM_KEYS_PATH = '/v1/me/llm-keys';

export const llmKeyKeys = {
  all: ['llm-keys'] as const,
};

/** Liste des fournisseurs pour lesquels une clé personnelle est déclarée. */
export function llmKeysQueryOptions() {
  return queryOptions({
    queryKey: llmKeyKeys.all,
    queryFn: ({ signal }) => apiClient.get<UserLlmKeyRead[]>(LLM_KEYS_PATH, { signal }),
  });
}

/** Déclare ou remplace la clé d'un fournisseur — `PUT /v1/me/llm-keys/{provider}` (200). */
export function setLlmKeyMutationOptions() {
  return mutationOptions({
    mutationKey: ['llm-keys', 'set'] as const,
    mutationFn: ({ provider, apiKey }: { provider: LlmVendor; apiKey: string }) =>
      apiClient.put<UserLlmKeyRead>(`${LLM_KEYS_PATH}/${provider}`, {
        api_key: apiKey,
      } satisfies UserLlmKeyCreate),
  });
}

/** Retire la clé d'un fournisseur — `DELETE /v1/me/llm-keys/{provider}` (204). */
export function removeLlmKeyMutationOptions() {
  return mutationOptions({
    mutationKey: ['llm-keys', 'remove'] as const,
    mutationFn: (provider: LlmVendor) =>
      apiClient.delete<undefined>(`${LLM_KEYS_PATH}/${provider}`),
  });
}
