import type { ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './StatusDot.module.css';

export type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'pending';

export interface StatusDotProps {
  tone?: StatusTone;
  className?: string;
}

/**
 * Pastille d'état. Purement décorative : le sens doit toujours être porté par
 * un texte adjacent (l'élément est masqué aux lecteurs d'écran).
 */
export function StatusDot({ tone = 'neutral', className }: StatusDotProps): ReactNode {
  return <span aria-hidden="true" className={cx(styles.dot, styles[tone], className)} />;
}
