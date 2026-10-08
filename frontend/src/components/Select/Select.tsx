import type { ReactNode, SelectHTMLAttributes } from 'react';

import { cx } from '@/lib/cx';

import styles from './Select.module.css';

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

/**
 * Liste déroulante bâtie sur le `<select>` natif.
 *
 * Choix assumé : le natif apporte gratuitement la navigation clavier, la
 * recherche par frappe, l'annonce vocale et le rendu adapté sur mobile. Un
 * menu réimplémenté en div coûterait des centaines de lignes pour un résultat
 * moins accessible. Seul le chevron est redessiné (aucune image externe).
 */
export function Select({ className, children, ...rest }: SelectProps): ReactNode {
  return (
    <div className={styles.wrapper}>
      <select className={cx(styles.select, className)} {...rest}>
        {children}
      </select>
      <svg
        className={styles.chevron}
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="m4 6.5 4 4 4-4" />
      </svg>
    </div>
  );
}
