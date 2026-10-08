import { useId, type InputHTMLAttributes, type ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Checkbox.module.css';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: string;
  /** Précision affichée sous le libellé. */
  description?: string;
}

/**
 * Case à cocher.
 *
 * L'`<input type="checkbox">` natif reste dans le DOM (état, clavier et
 * annonce vocale gratuits) ; il est masqué visuellement et la coche est
 * dessinée à côté, pilotée par `:checked` sur le frère.
 */
export function Checkbox({ label, description, className, id, ...rest }: CheckboxProps): ReactNode {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const descriptionId = `${inputId}-description`;

  return (
    <div className={cx(styles.row, className)}>
      <input
        type="checkbox"
        id={inputId}
        className={styles.input}
        {...(description !== undefined ? { 'aria-describedby': descriptionId } : {})}
        {...rest}
      />
      <label className={styles.label} htmlFor={inputId}>
        <span className={styles.box} aria-hidden="true">
          <svg viewBox="0 0 16 16" focusable="false" strokeLinecap="round" strokeLinejoin="round">
            <path d="m3.75 8.5 2.75 2.75 5.75-6" />
          </svg>
        </span>
        <span className={styles.text}>
          <span className={styles.title}>{label}</span>
          {description !== undefined && (
            <span className={styles.description} id={descriptionId}>
              {description}
            </span>
          )}
        </span>
      </label>
    </div>
  );
}
