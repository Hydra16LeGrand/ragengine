import { useId, type ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './SegmentedControl.module.css';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** Glyphe optionnel affiché avant le libellé. */
  icon?: ReactNode;
}

export interface SegmentedControlProps<T extends string> {
  /** Nom accessible du groupe (« Mode du playground »). */
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Bascule entre quelques modes exclusifs.
 *
 * Construit sur des `<input type="radio">` natifs, pour la même raison que
 * `Select` s'appuie sur le `<select>` natif : la navigation par flèches, le
 * groupement annoncé et la sémantique « un seul choix » sont gratuits et
 * corrects, là où une rangée de boutons obligerait à tout réimplémenter — et à
 * mentir sur la nature du contrôle.
 *
 * La pastille active est un élément séparé, translaté par transformation : le
 * glissement d'un segment à l'autre reste fluide sans animer une couleur de
 * fond sur chaque libellé.
 */
export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
  className,
}: SegmentedControlProps<T>): ReactNode {
  const name = useId();
  const activeIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(styles.group, disabled && styles.disabled, className)}
      style={
        {
          '--segment-count': options.length,
          '--segment-index': activeIndex,
        } as Record<string, string | number>
      }
    >
      <span className={styles.thumb} aria-hidden="true" />

      {options.map((option) => {
        const id = `${name}-${option.value}`;
        return (
          <span key={option.value} className={styles.segment}>
            <input
              type="radio"
              id={id}
              name={name}
              className={styles.input}
              value={option.value}
              checked={option.value === value}
              disabled={disabled}
              onChange={() => {
                onChange(option.value);
              }}
            />
            <label className={styles.label} htmlFor={id}>
              {option.icon !== undefined && <span className={styles.icon}>{option.icon}</span>}
              {option.label}
            </label>
          </span>
        );
      })}
    </div>
  );
}
