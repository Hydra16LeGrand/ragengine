import type { ReactNode } from 'react';

import { OrganizationsPanel } from './OrganizationsPanel';
import styles from './Organizations.module.css';

/** Écran `/settings/organizations` : liste et création des organisations du compte. */
export function OrganizationsPage(): ReactNode {
  return (
    <div className={styles.page}>
      <OrganizationsPanel />
    </div>
  );
}
