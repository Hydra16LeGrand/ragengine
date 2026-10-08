import type { ReactNode } from 'react';

import { ApiKeysPanel } from './ApiKeysPanel';
import styles from './ApiKeys.module.css';

/**
 * Écran `/settings/api-keys` : gestion des clés API du compte courant.
 *
 * Le titre de page et les onglets vivent dans `SettingsLayout` (parent de
 * route) — cet écran ne porte plus que son contenu, pour éviter un double
 * en-tête depuis que `/settings` couvre plus qu'un seul écran.
 */
export function ApiKeysPage(): ReactNode {
  return (
    <div className={styles.page}>
      <ApiKeysPanel />
    </div>
  );
}
