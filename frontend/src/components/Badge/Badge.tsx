import type { HTMLAttributes, ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Badge.module.css';

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
export type BadgeVariant = 'soft' | 'outline';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  variant?: BadgeVariant;
  /** Glyphe optionnel affiché avant le texte (cadenas, globe…). */
  icon?: ReactNode;
}

/**
 * Étiquette compacte : visibilité d'une base, rôle d'un membre, état d'un
 * document. Le sens est porté par le texte — la couleur ne fait que le
 * renforcer, jamais le remplacer (lisible en niveaux de gris).
 */
export function Badge({
  tone = 'neutral',
  variant = 'soft',
  icon,
  className,
  children,
  ...rest
}: BadgeProps): ReactNode {
  return (
    <span className={cx(styles.badge, styles[tone], styles[variant], className)} {...rest}>
      {icon !== undefined && <span className={styles.icon}>{icon}</span>}
      {children}
    </span>
  );
}
