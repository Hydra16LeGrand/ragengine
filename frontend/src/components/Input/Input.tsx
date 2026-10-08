import type { InputHTMLAttributes, ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Input.module.css';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Rend la saisie en chasse fixe : slug, identifiant de modèle, UUID. */
  mono?: boolean;
  /** Affixe court affiché à gauche, dans le champ (ex. `/kb/`). */
  prefix?: string;
}

/** Champ de saisie sur une ligne. Le libellé est porté par `<Field>`. */
export function Input({ mono = false, prefix, className, ...rest }: InputProps): ReactNode {
  const input = <input className={cx(styles.input, mono && styles.mono, className)} {...rest} />;

  if (prefix === undefined) return input;

  return (
    <div className={styles.group}>
      <span className={styles.prefix} aria-hidden="true">
        {prefix}
      </span>
      {input}
    </div>
  );
}
