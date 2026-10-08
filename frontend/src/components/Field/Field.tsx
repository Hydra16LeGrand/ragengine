import { useId, type ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Field.module.css';

export interface FieldRenderProps {
  /** À poser sur le contrôle (`id`), déjà relié au `<label>`. */
  id: string;
  /** À poser sur le contrôle : relie l'aide et l'erreur au lecteur d'écran. */
  'aria-describedby': string | undefined;
  'aria-invalid': boolean | undefined;
}

export interface FieldProps {
  label: string;
  /** Texte d'aide affiché sous le libellé, avant le contrôle. */
  hint?: string;
  /** Message d'erreur ; sa présence marque le contrôle comme invalide. */
  error?: string;
  required?: boolean;
  /** Libellé du marqueur « facultatif » (i18n : jamais de chaîne en dur ici). */
  optionalLabel?: string;
  className?: string;
  children: (props: FieldRenderProps) => ReactNode;
}

/**
 * Enveloppe de champ de formulaire : libellé, aide, erreur.
 *
 * Le contrôle est fourni par une fonction enfant afin que `Field` puisse lui
 * transmettre les identifiants ARIA qu'il génère — la liaison
 * label/aide/erreur est ainsi correcte par construction, jamais oubliée.
 */
export function Field({
  label,
  hint,
  error,
  required = false,
  optionalLabel,
  className,
  children,
}: FieldProps): ReactNode {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  const describedBy = cx(hint !== undefined && hintId, error !== undefined && errorId) || undefined;

  return (
    <div className={cx(styles.field, className)}>
      <label className={styles.label} htmlFor={id}>
        <span>{label}</span>
        {!required && optionalLabel !== undefined && (
          <span className={styles.optional}>{optionalLabel}</span>
        )}
      </label>

      {/* Le contrôle suit immédiatement le libellé, et l'aide vient dessous.
          Conséquence voulue : dans une grille à deux colonnes, les champs
          s'alignent sur leur contrôle quelle que soit la longueur de l'aide —
          une aide sur deux lignes ne décale plus la colonne voisine. */}
      {children({
        id,
        'aria-describedby': describedBy,
        'aria-invalid': error !== undefined ? true : undefined,
      })}

      {hint !== undefined && (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      )}

      {error !== undefined && (
        <p className={styles.error} id={errorId} role="alert">
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            className={styles.errorIcon}
          >
            <circle cx="8" cy="8" r="6.25" />
            <path d="M8 5v3.75M8 11h.01" strokeLinecap="round" />
          </svg>
          {error}
        </p>
      )}
    </div>
  );
}
