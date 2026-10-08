/**
 * Organisations — cycle de vie et appartenances (ADR-022).
 *
 * `POST /organizations` est réservé à un compte réel : l'organisation
 * personnelle d'un compte est provisionnée automatiquement côté serveur,
 * jamais via cette route. La garde réelle est `RequireAccount`
 * (`features/auth/RequireAccount.tsx`), pas ce module.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type {
  OrganizationCreate,
  OrganizationMemberRead,
  OrganizationMemberUpdate,
  OrganizationRead,
  OrganizationUpdate,
  PageOrganizationRead,
} from './schemas';

export const ORGANIZATIONS_PATH = '/v1/organizations';

export const organizationKeys = {
  all: ['organizations'] as const,
  list: (limit: number) => ['organizations', 'list', limit] as const,
  detail: (organizationId: string) => ['organizations', organizationId] as const,
  members: (organizationId: string) => ['organizations', organizationId, 'members'] as const,
};

const DEFAULT_PAGE_SIZE = 50;

/** Liste paginée par curseur, de la plus récente à la plus ancienne — organisations dont le principal est membre. */
export function organizationsQueryOptions(limit: number = DEFAULT_PAGE_SIZE) {
  return queryOptions({
    queryKey: organizationKeys.list(limit),
    queryFn: ({ signal }) =>
      apiClient.get<PageOrganizationRead>(ORGANIZATIONS_PATH, { query: { limit }, signal }),
  });
}

export function organizationQueryOptions(organizationId: string) {
  return queryOptions({
    queryKey: organizationKeys.detail(organizationId),
    queryFn: ({ signal }) =>
      apiClient.get<OrganizationRead>(`${ORGANIZATIONS_PATH}/${organizationId}`, { signal }),
  });
}

export function createOrganizationMutationOptions() {
  return mutationOptions({
    mutationKey: ['organizations', 'create'] as const,
    mutationFn: (payload: OrganizationCreate) =>
      apiClient.post<OrganizationRead>(ORGANIZATIONS_PATH, payload),
  });
}

export function updateOrganizationMutationOptions(organizationId: string) {
  return mutationOptions({
    mutationKey: ['organizations', organizationId, 'update'] as const,
    mutationFn: (payload: OrganizationUpdate) =>
      apiClient.patch<OrganizationRead>(`${ORGANIZATIONS_PATH}/${organizationId}`, payload),
  });
}

/** Refusée (409) tant que l'organisation possède encore des bases de connaissances. */
export function deleteOrganizationMutationOptions(organizationId: string) {
  return mutationOptions({
    mutationKey: ['organizations', organizationId, 'delete'] as const,
    mutationFn: () => apiClient.delete<undefined>(`${ORGANIZATIONS_PATH}/${organizationId}`),
  });
}

/* -------------------------------------------------------------------------- */
/* Membres                                                                     */
/* -------------------------------------------------------------------------- */

/** Annuaire complet — `GET …/members`, ouvert à tout membre (pas réservé à l'`owner`). */
export function organizationMembersQueryOptions(organizationId: string) {
  return queryOptions({
    queryKey: organizationKeys.members(organizationId),
    queryFn: ({ signal }) =>
      apiClient.get<OrganizationMemberRead[]>(`${ORGANIZATIONS_PATH}/${organizationId}/members`, {
        signal,
      }),
  });
}

export interface SetOrganizationMemberVariables {
  userId: string;
  role: OrganizationMemberUpdate['role'];
}

/** Ajout ou changement de rôle — `PUT …/members/{user_id}` (201 ou 200), réservé à l'`owner`. */
export function setOrganizationMemberMutationOptions(organizationId: string) {
  return mutationOptions({
    mutationKey: ['organizations', organizationId, 'members', 'set'] as const,
    mutationFn: ({ userId, role }: SetOrganizationMemberVariables) =>
      apiClient.put<OrganizationMemberRead>(
        `${ORGANIZATIONS_PATH}/${organizationId}/members/${userId}`,
        { role },
      ),
  });
}

/** Retrait — `DELETE …/members/{user_id}` (204 ; 409 sur le dernier `owner`), réservé à l'`owner`. */
export function removeOrganizationMemberMutationOptions(organizationId: string) {
  return mutationOptions({
    mutationKey: ['organizations', organizationId, 'members', 'remove'] as const,
    mutationFn: (userId: string) =>
      apiClient.delete<undefined>(`${ORGANIZATIONS_PATH}/${organizationId}/members/${userId}`),
  });
}
