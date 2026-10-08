/**
 * Clés API du compte courant (Phase 9).
 *
 * Ressource réservée aux comptes réels (§4 CONTRIBUTING.md, `docs/domain-model.md`
 * §1) : la façade répond `403` à un compte éphémère sur `POST`/`DELETE`. La SPA ne fait
 * que refléter cette règle — la garde réelle est `RequireAccount`
 * (`features/auth/RequireAccount.tsx`), pas ce module.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { ApiKeyCreate, ApiKeyCreated, PageApiKeyRead } from './schemas';

export const API_KEYS_PATH = '/v1/api-keys';

export const apiKeyKeys = {
  all: ['api-keys'] as const,
  list: (limit: number) => ['api-keys', 'list', limit] as const,
};

const DEFAULT_PAGE_SIZE = 50;

/**
 * Liste paginée par curseur, de la plus récente à la plus ancienne. Les clés
 * révoquées y figurent (elles restent auditables) ; le jeton n'y apparaît
 * jamais, seul `prefix` permet de reconnaître une clé.
 */
export function apiKeysQueryOptions(limit: number = DEFAULT_PAGE_SIZE) {
  return queryOptions({
    queryKey: apiKeyKeys.list(limit),
    queryFn: ({ signal }) =>
      apiClient.get<PageApiKeyRead>(API_KEYS_PATH, { query: { limit }, signal }),
  });
}

/**
 * Création — `POST /v1/api-keys` (201).
 *
 * La réponse (`ApiKeyCreated`) est le seul instant où le jeton en clair
 * (`token`) est communiqué : l'appelant doit l'afficher une fois puis le
 * laisser sortir de son état, jamais le repersister ni le remettre en cache
 * TanStack Query (seul `ApiKeyRead` — sans jeton — y est invalidé/relu).
 */
export function createApiKeyMutationOptions() {
  return mutationOptions({
    mutationKey: ['api-keys', 'create'] as const,
    mutationFn: (payload: ApiKeyCreate) => apiClient.post<ApiKeyCreated>(API_KEYS_PATH, payload),
  });
}

/** Révocation — `DELETE /v1/api-keys/{id}` (204, idempotente, non destructive côté backend). */
export function revokeApiKeyMutationOptions() {
  return mutationOptions({
    mutationKey: ['api-keys', 'revoke'] as const,
    mutationFn: (apiKeyId: string) => apiClient.delete<undefined>(`${API_KEYS_PATH}/${apiKeyId}`),
  });
}
