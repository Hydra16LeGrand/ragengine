import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react';

import { authKeys, logoutMutationOptions, meQueryOptions } from '@/api/auth';
import type { PrincipalRead, UserRead } from '@/api/schemas';

/**
 * État d'authentification de la SPA (ADR-005).
 *
 * Trois situations, et non deux — c'est ce qui permet « l'élévation
 * progressive » du produit :
 *  - `anonymous` : aucun principal. Lecture des bases `public` uniquement.
 *  - `ephemeral` : compte éphémère, sans identité durable (le cœur n'en crée jamais ;
 *                  un greffon peut en ouvrir).
 *  - `authenticated` : compte réel, seul habilité à posséder des bases.
 *
 * La source de vérité est `GET /v1/me` : le cookie de session est `HttpOnly`,
 * la SPA ne peut donc rien déduire toute seule et ne stocke aucun jeton.
 */

export type SessionStatus = 'loading' | 'anonymous' | 'ephemeral' | 'authenticated';

export interface SessionContextValue {
  status: SessionStatus;
  principal: PrincipalRead | null;
  user: UserRead | null;
  /** Identifiant du compte courant, `null` si anonyme. */
  userId: string | null;
  /** Compte réel connecté (exclut le compte éphémère). */
  isAuthenticated: boolean;
  isEphemeral: boolean;
  /** Peut agir en écriture : créer une base, en modifier une. */
  canWrite: boolean;
  /** Rôle plateforme `admin` (ADR-009/ADR-048) — jamais vrai pour un compte éphémère. */
  isAdmin: boolean;
  isLoading: boolean;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

function statusOf(principal: PrincipalRead | null | undefined, isLoading: boolean): SessionStatus {
  if (isLoading) return 'loading';
  if (principal?.user == null) return 'anonymous';
  return principal.is_guest ? 'ephemeral' : 'authenticated';
}

export function SessionProvider({ children }: { children: ReactNode }): ReactNode {
  const queryClient = useQueryClient();
  const { data: principal, isPending } = useQuery(meQueryOptions());
  const logoutMutation = useMutation(logoutMutationOptions());

  const logout = useCallback(async (): Promise<void> => {
    await logoutMutation.mutateAsync();
    // Le cookie est révoqué : tout ce qui a été lu sous cette identité est
    // potentiellement invisible désormais. On repart d'un cache vierge plutôt
    // que d'invalider ressource par ressource (et d'en oublier une).
    queryClient.setQueryData(authKeys.me, null);
    await queryClient.resetQueries();
  }, [logoutMutation, queryClient]);

  const value = useMemo<SessionContextValue>(() => {
    const status = statusOf(principal, isPending);
    const user = principal?.user ?? null;
    return {
      status,
      principal: principal ?? null,
      user,
      userId: user?.id ?? null,
      isAuthenticated: status === 'authenticated',
      isEphemeral: status === 'ephemeral',
      canWrite: status === 'authenticated' || status === 'ephemeral',
      isAdmin: status === 'authenticated' && principal?.role === 'admin',
      isLoading: status === 'loading',
      logout,
    };
  }, [principal, isPending, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** Principal courant. Doit être appelé sous un `<SessionProvider>`. */
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error('useSession() doit être utilisé à l’intérieur d’un <SessionProvider>.');
  }
  return context;
}
