import type { ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useSession } from './SessionProvider';

/**
 * Garde de route : réserve un écran aux principaux habilités à écrire.
 *
 * C'est la face « élévation » d'ADR-005 : on ne cache pas l'existence de la
 * fonctionnalité, on demande l'identité au moment où elle devient nécessaire,
 * et on renvoie l'utilisateur exactement là où il allait (`?next=`) une fois
 * connecté.
 */
export function RequireAuth(): ReactNode {
  const { canWrite, isLoading } = useSession();
  const location = useLocation();

  // Tant que `GET /me` n'a pas répondu, rediriger ferait clignoter l'écran
  // de connexion devant un utilisateur pourtant déjà authentifié.
  if (isLoading) return null;

  if (!canWrite) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return <Outlet />;
}
