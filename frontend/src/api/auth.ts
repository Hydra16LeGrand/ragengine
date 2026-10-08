/**
 * Comptes, sessions et principal courant (ADR-005).
 *
 * Le backend pose un cookie de session `HttpOnly` sur `POST /v1/sessions` :
 * le jeton n'est donc jamais lisible en JavaScript, et `client.ts` envoie
 * `credentials: 'same-origin'`. Il n'y a rien à stocker côté SPA — la seule
 * source de vérité de l'état d'authentification est `GET /v1/me`.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient, isApiError } from './client';
import type { PrincipalRead, SessionCreate, UserCreate, UserRead } from './schemas';

export const ME_PATH = '/v1/me';
export const USERS_PATH = '/v1/users';
export const SESSIONS_PATH = '/v1/sessions';
export const CURRENT_SESSION_PATH = '/v1/sessions/current';

export const authKeys = {
  me: ['auth', 'me'] as const,
};

/**
 * Principal courant, ou `null` si personne n'est authentifié.
 *
 * `GET /v1/me` répond `401` à un visiteur anonyme : ce n'est pas une panne mais
 * une réponse attendue, on la convertit donc en `null`. Sans cela, l'écran de
 * connexion lui-même afficherait un état d'erreur alors que « pas connecté »
 * est un état nominal du produit.
 */
export function meQueryOptions() {
  return queryOptions({
    queryKey: authKeys.me,
    queryFn: async ({ signal }): Promise<PrincipalRead | null> => {
      try {
        return await apiClient.get<PrincipalRead>(ME_PATH, { signal });
      } catch (error) {
        if (isApiError(error) && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

/** Inscription — `POST /v1/users` (201, ou 409 si l'e-mail est déjà pris). */
export function registerMutationOptions() {
  return mutationOptions({
    mutationKey: ['auth', 'register'] as const,
    mutationFn: (payload: UserCreate) => apiClient.post<UserRead>(USERS_PATH, payload),
  });
}

/** Connexion — `POST /v1/sessions` (201 + cookie de session, ou 401). */
export function loginMutationOptions() {
  return mutationOptions({
    mutationKey: ['auth', 'login'] as const,
    mutationFn: (payload: SessionCreate) => apiClient.post<PrincipalRead>(SESSIONS_PATH, payload),
  });
}

/** Déconnexion — `DELETE /v1/sessions/current` (204, idempotent). */
export function logoutMutationOptions() {
  return mutationOptions({
    mutationKey: ['auth', 'logout'] as const,
    mutationFn: () => apiClient.delete<undefined>(CURRENT_SESSION_PATH),
  });
}
