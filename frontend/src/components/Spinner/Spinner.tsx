import type { ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Spinner.module.css';

export interface SpinnerProps {
  className?: string;
}

/**
 * Indicateur d'activité **ponctuel**, réservé à une action en cours dans un
 * bouton (envoi de formulaire, suppression).
 *
 * Le chargement d'un écran ou d'une liste utilise `<Skeleton>`, jamais ceci :
 * un sablier plein écran n'annonce rien de ce qui arrive.
 */
export function Spinner({ className }: SpinnerProps): ReactNode {
  return (
    <svg
      className={cx(styles.spinner, className)}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      strokeLinecap="round"
    >
      <circle className={styles.track} cx="8" cy="8" r="6" />
      <path className={styles.head} d="M14 8a6 6 0 0 0-6-6" />
    </svg>
  );
}
