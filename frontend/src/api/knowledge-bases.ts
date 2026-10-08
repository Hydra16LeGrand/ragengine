/**
 * Bases de connaissances — CRUD et appartenances.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type {
  KnowledgeBaseCreate,
  KnowledgeBaseMemberRead,
  KnowledgeBaseRead,
  KnowledgeBaseUpdate,
  MemberRole,
  PageKnowledgeBaseRead,
} from './schemas';

export const KNOWLEDGE_BASES_PATH = '/v1/knowledge-bases';

export const knowledgeBaseKeys = {
  all: ['knowledge-bases'] as const,
  list: (limit: number) => ['knowledge-bases', 'list', limit] as const,
  detail: (kbId: string) => ['knowledge-bases', kbId] as const,
  members: (kbId: string) => ['knowledge-bases', kbId, 'members'] as const,
  member: (kbId: string, userId: string) => ['knowledge-bases', kbId, 'members', userId] as const,
};

const DEFAULT_PAGE_SIZE = 50;

/**
 * Liste des bases visibles par le principal courant.
 *
 * Accessible sans authentification : un visiteur anonyme y voit les bases
 * `public` (le backend filtre, la SPA n'a rien à décider).
 */
export function knowledgeBasesQueryOptions(limit: number = DEFAULT_PAGE_SIZE) {
  return queryOptions({
    queryKey: knowledgeBaseKeys.list(limit),
    queryFn: ({ signal }) =>
      apiClient.get<PageKnowledgeBaseRead>(KNOWLEDGE_BASES_PATH, { query: { limit }, signal }),
  });
}

export function knowledgeBaseQueryOptions(kbId: string) {
  return queryOptions({
    queryKey: knowledgeBaseKeys.detail(kbId),
    queryFn: ({ signal }) =>
      apiClient.get<KnowledgeBaseRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}`, { signal }),
  });
}

export function createKnowledgeBaseMutationOptions() {
  return mutationOptions({
    mutationKey: ['knowledge-bases', 'create'] as const,
    mutationFn: (payload: KnowledgeBaseCreate) =>
      apiClient.post<KnowledgeBaseRead>(KNOWLEDGE_BASES_PATH, payload),
  });
}

export function updateKnowledgeBaseMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['knowledge-bases', kbId, 'update'] as const,
    mutationFn: (payload: KnowledgeBaseUpdate) =>
      apiClient.patch<KnowledgeBaseRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}`, payload),
  });
}

export function deleteKnowledgeBaseMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['knowledge-bases', kbId, 'delete'] as const,
    mutationFn: () => apiClient.delete<undefined>(`${KNOWLEDGE_BASES_PATH}/${kbId}`),
  });
}

/* -------------------------------------------------------------------------- */
/* Membres                                                                     */
/* -------------------------------------------------------------------------- */

/** Annuaire complet des membres — `GET …/members` (réservé à l'`owner`). */
export function knowledgeBaseMembersQueryOptions(kbId: string) {
  return queryOptions({
    queryKey: knowledgeBaseKeys.members(kbId),
    queryFn: ({ signal }) =>
      apiClient.get<KnowledgeBaseMemberRead[]>(`${KNOWLEDGE_BASES_PATH}/${kbId}/members`, {
        signal,
      }),
  });
}

/** Rôle d'un utilisateur sur une base, ou `null` s'il n'en est pas membre. */
export function knowledgeBaseMemberQueryOptions(kbId: string, userId: string, enabled: boolean) {
  return queryOptions({
    queryKey: knowledgeBaseKeys.member(kbId, userId),
    queryFn: ({ signal }) =>
      apiClient.get<KnowledgeBaseMemberRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}/members/${userId}`, {
        signal,
      }),
    enabled,
    retry: false,
  });
}

export interface SetMemberVariables {
  userId: string;
  role: MemberRole;
}

/** Ajout ou changement de rôle — `PUT …/members/{user_id}` (201 ou 200). */
export function setKnowledgeBaseMemberMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['knowledge-bases', kbId, 'members', 'set'] as const,
    mutationFn: ({ userId, role }: SetMemberVariables) =>
      apiClient.put<KnowledgeBaseMemberRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}/members/${userId}`, {
        role,
      }),
  });
}

/** Retrait — `DELETE …/members/{user_id}` (204 ; 409 si l'on vise le owner). */
export function removeKnowledgeBaseMemberMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['knowledge-bases', kbId, 'members', 'remove'] as const,
    mutationFn: (userId: string) =>
      apiClient.delete<undefined>(`${KNOWLEDGE_BASES_PATH}/${kbId}/members/${userId}`),
  });
}
