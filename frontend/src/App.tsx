import { Fragment, type ReactNode } from 'react';
import { Navigate, Outlet, Route, Routes } from 'react-router-dom';

import { AuthPage, RequireAccount, RequireAuth } from '@/features/auth';
import { DevelopersGuidePage } from '@/features/developers';
import { EvaluationsSummaryPage } from '@/features/evaluations';
import {
  KnowledgeBaseCreatePage,
  KnowledgeBaseDetailPage,
  KnowledgeBaseListPage,
} from '@/features/knowledge-bases';
import { PlaygroundPage } from '@/features/playground';
import {
  ApiKeysPage,
  LlmKeysPage,
  OrganizationDetailPage,
  OrganizationsPage,
  SettingsLayout,
} from '@/features/settings';
import { admitsAnonymous, getPlugins } from '@/plugins';
import { AppLayout } from '@/routes/AppLayout';
import { NotFoundPage } from '@/routes/not-found/NotFoundPage';

/**
 * Garde des écrans de la coquille : le cœur exige une connexion pour tout
 * (ADR-069). Un greffon qui sert des visiteurs sans compte (`admitsAnonymous`)
 * laisse passer l'anonyme — chaque écran gère alors lui-même ce qu'il lui montre,
 * et les écrans d'écriture gardent leur propre `RequireAuth`.
 */
function VisitorGate(): ReactNode {
  return admitsAnonymous() ? <Outlet /> : <RequireAuth />;
}

/**
 * Arbre de routes de la SPA.
 *
 * Les providers (thème, i18n, TanStack Query, session, notifications) sont
 * montés dans `main.tsx` : ce composant ne décrit que la navigation.
 *
 * `/` redirige vers le tableau de bord plutôt que de servir une page d'accueil
 * distincte : RAGEngine est un outil, pas un site vitrine.
 *
 * Les greffons (ADR-069) ajoutent leurs routes à deux endroits seulement : hors
 * coquille (`standaloneRoutes`, pages autonomes) et dans la coquille, après
 * celles du cœur (`shellRoutes`).
 */
export function App(): ReactNode {
  const plugins = getPlugins();
  return (
    <Routes>
      {plugins.map((plugin) => (
        <Fragment key={plugin.name}>{plugin.standaloneRoutes}</Fragment>
      ))}

      <Route element={<AppLayout />}>
        <Route path="login" element={<AuthPage />} />

        <Route element={<VisitorGate />}>
          <Route index element={<Navigate to="/knowledge-bases" replace />} />

          <Route path="knowledge-bases">
            <Route index element={<KnowledgeBaseListPage />} />
            {/* Créer suppose un principal habilité : la garde élève avant d'afficher. */}
            <Route element={<RequireAuth />}>
              <Route path="new" element={<KnowledgeBaseCreatePage />} />
            </Route>
            {/* `PlaygroundPage` rend le 404 « base invisible » pour une base
              `private` non membre — pas une invitation à se connecter, qui
              confirmerait son existence. */}
            <Route path=":kbId/playground" element={<PlaygroundPage />} />
            <Route path=":kbId" element={<KnowledgeBaseDetailPage />} />
          </Route>

          {/* Agrégat transverse (Phase 12, Étape 5) : même ouverture que la liste des
            bases — le périmètre RBAC réel est résolu côté serveur. */}
          <Route path="evaluations" element={<EvaluationsSummaryPage />} />

          {/* Guide développeur : même ouverture que la liste des bases. */}
          <Route path="developers" element={<DevelopersGuidePage />} />

          {/* Réservé aux comptes réels (compte éphémère exclu) : la garde vit ici, pas
            dans chaque écran de `features/settings`. Si une future
            sous-page (profil, langue) doit rester ouverte à un compte éphémère, elle
            sortira de ce sous-arbre plutôt que de contourner la garde. */}
          <Route path="settings" element={<RequireAccount />}>
            <Route element={<SettingsLayout />}>
              <Route index element={<Navigate to="/settings/api-keys" replace />} />
              <Route path="api-keys" element={<ApiKeysPage />} />
              <Route path="organizations" element={<OrganizationsPage />} />
              <Route path="organizations/:organizationId" element={<OrganizationDetailPage />} />
              <Route path="llm-keys" element={<LlmKeysPage />} />
            </Route>
          </Route>

          {plugins.map((plugin) => (
            <Fragment key={plugin.name}>{plugin.shellRoutes}</Fragment>
          ))}

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
