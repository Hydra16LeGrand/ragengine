import type { ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { pluginSlots } from '@/plugins';

import { useSession } from './SessionProvider';

/**
 * Garde de route : réserve un écran aux **comptes réels**.
 *
 * Distincte de `RequireAuth`, qui gate sur `canWrite` — vrai aussi pour un
 * compte éphémère (`is_guest`). Certaines ressources (les clés API notamment,
 * `docs/domain-model.md` §1) sont interdites à un tel compte.
 *
 * Un compte éphémère n'est **jamais** redirigé vers `/login` : `AuthPage` renvoie
 * tout principal dont `canWrite` est vrai hors de l'écran de connexion, ce qui
 * bouclerait indéfiniment ici. Le cœur n'en crée jamais ; le greffon qui en
 * ouvre fournit l'écran à afficher (`EphemeralAccountBlocked`, ADR-069).
 */
export function RequireAccount(): ReactNode {
  const { isAuthenticated, isEphemeral, isLoading } = useSession();
  const location = useLocation();

  // Tant que `GET /me` n'a pas répondu, rediriger ferait clignoter l'écran
  // de connexion devant un compte pourtant déjà authentifié.
  if (isLoading) return null;

  if (isEphemeral) {
    return pluginSlots('EphemeralAccountBlocked').map((Blocked, index) => <Blocked key={index} />);
  }

  if (!isAuthenticated) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return <Outlet />;
}
