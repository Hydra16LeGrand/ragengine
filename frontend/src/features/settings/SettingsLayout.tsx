import type { ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router-dom';

import { PageHeader } from '@/components/PageHeader';
import { useT } from '@/i18n';
import { cx } from '@/lib/cx';

import styles from './SettingsLayout.module.css';

/**
 * Coquille de `/settings/*` : en-tête commun, onglets, contenu routé.
 *
 * Monté **sous** `RequireAccount` (`App.tsx`) — jamais atteint par un compte éphémère
 * ou un anonyme, ce composant ne revérifie donc rien lui-même, même logique
 * que `MembersPanel`/`ApiKeysPanel` vis-à-vis de leur garde.
 */
export function SettingsLayout(): ReactNode {
  const t = useT();

  return (
    <div className={styles.shell}>
      <PageHeader eyebrow={t('settings.eyebrow')} title={t('settings.pageTitle')} />

      <nav className={styles.tabs} aria-label={t('settings.tabsLabel')}>
        <NavLink
          to="/settings/api-keys"
          className={({ isActive }) => cx(styles.tab, isActive && styles.tabActive)}
        >
          {t('settings.tabs.apiKeys')}
        </NavLink>
        <NavLink
          to="/settings/organizations"
          className={({ isActive }) => cx(styles.tab, isActive && styles.tabActive)}
        >
          {t('settings.tabs.organizations')}
        </NavLink>
        <NavLink
          to="/settings/llm-keys"
          className={({ isActive }) => cx(styles.tab, isActive && styles.tabActive)}
        >
          {t('settings.tabs.llmKeys')}
        </NavLink>
      </nav>

      <Outlet />
    </div>
  );
}
