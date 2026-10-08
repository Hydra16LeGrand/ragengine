import type { ReactNode } from 'react';

import { LlmKeysPanel } from './LlmKeysPanel';
import styles from './LlmKeys.module.css';

/**
 * Écran `/settings/llm-keys` : clés LLM personnelles du compte courant.
 *
 * Le titre de page et les onglets vivent dans `SettingsLayout` (parent de
 * route) — cet écran ne porte que son contenu, même découpage que
 * `ApiKeysPage`/`OrganizationsPage`.
 */
export function LlmKeysPage(): ReactNode {
  return (
    <div className={styles.page}>
      <LlmKeysPanel />
    </div>
  );
}
