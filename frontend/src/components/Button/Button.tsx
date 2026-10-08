import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { Spinner } from '@/components/Spinner';
import { cx } from '@/lib/cx';

import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Action en cours : le bouton devient inerte et affiche son activité. */
  loading?: boolean;
}

/**
 * Bouton de base. Aucune couleur en dur : tout vient des jetons sémantiques,
 * donc la variante s'adapte seule au thème clair/sombre.
 */
export function Button({
  variant = 'primary',
  size = 'md',
  type = 'button',
  loading = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps): ReactNode {
  return (
    <button
      type={type}
      className={cx(styles.button, styles[variant], styles[size], className)}
      disabled={disabled === true || loading}
      // `aria-busy` annonce l'attente sans retirer le libellé du bouton :
      // la largeur ne saute pas et le lecteur d'écran garde le contexte.
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}
