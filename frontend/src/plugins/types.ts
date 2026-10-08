import type { ComponentType, ReactNode } from 'react';

import type { KnowledgeBaseRead } from '@/api/schemas';
import type { Locale } from '@/i18n';

/**
 * Contrat d'un greffon de la SPA (ADR-069) — miroir de `app.core.plugins` côté backend.
 *
 * Chaque champ est un **emplacement** que le cœur rend à un endroit précis ;
 * absent, le cœur rend son propre comportement, complet sans greffon. Le cœur
 * ne nomme jamais un greffon : il les découvre (`src/plugins/index.ts`).
 */

/** Entrée de la navigation principale, après celles du cœur. */
export interface PluginNavEntry {
  to: string;
  /** Libellé dans la langue courante — le dictionnaire est celui du greffon. */
  label: (locale: Locale) => string;
  /** Visibilité selon la session (rôle, statut) ; le cœur n'en sait rien de plus. */
  isVisible: (session: PluginSessionView) => boolean;
}

/** Ce qu'un greffon peut lire de la session pour décider d'un affichage. */
export interface PluginSessionView {
  isAuthenticated: boolean;
  isEphemeral: boolean;
  isAdmin: boolean;
}

/** Portée de clé API proposée à la création d'une clé (`settings/apiKeyScopes.ts`). */
export interface PluginApiKeyScope {
  value: string;
  label: (locale: Locale) => string;
}

export interface KnowledgeBaseSectionProps {
  base: KnowledgeBaseRead;
  isOwner: boolean;
}

export interface CreationGateProps {
  /** Le lien ou l'action de création, rendu tel quel si rien ne s'y oppose. */
  children: ReactNode;
  /** Rendu à la place quand le greffon refuse la création (`reason` déjà traduit). */
  renderBlocked: (reason: string) => ReactNode;
}

export interface FrontendPlugin {
  name: string;
  /** Nom de marque du déploiement ; `VITE_APP_NAME` l'emporte, le défaut du cœur est `RAGEngine`. */
  appName?: string;
  /** Le greffon sert des visiteurs sans compte : la coquille ne les renvoie plus à `/login`. */
  admitsAnonymous?: boolean;
  /** Routes hors coquille (`AppLayout`) : pages autonomes. */
  standaloneRoutes?: ReactNode;
  /** Routes dans la coquille, après celles du cœur. */
  shellRoutes?: ReactNode;
  navEntries?: readonly PluginNavEntry[];
  /** Bandeau rendu sous l'en-tête de la coquille. */
  ShellBanner?: ComponentType;
  /** Mentions ajoutées sous le nom du compte, dans l'en-tête. */
  IdentityBadges?: ComponentType;
  /** Bloc ajouté sous le formulaire de connexion. */
  AuthPageExtra?: ComponentType<{ nextPath: string }>;
  /** Écran d'un compte éphémère (`is_guest`) devant une page réservée aux comptes réels. */
  EphemeralAccountBlocked?: ComponentType;
  /** Section de la page d'une base, entre son corpus et sa configuration. */
  KnowledgeBaseSection?: ComponentType<KnowledgeBaseSectionProps>;
  /** Garde autour des actions « créer une base ». */
  KnowledgeBaseCreationGate?: ComponentType<CreationGateProps>;
  apiKeyScopes?: readonly PluginApiKeyScope[];
}
