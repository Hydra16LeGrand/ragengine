import type { ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './EmptyState.module.css';
import { Illustration, type IllustrationName } from './illustrations';

export interface EmptyStateProps {
  illustration: IllustrationName;
  title: string;
  description?: string;
  /** Appel à l'action principal (bouton, lien). */
  action?: ReactNode;
  /** Variante resserrée pour un état vide à l'intérieur d'une carte. */
  compact?: boolean;
  className?: string;
  /**
   * Contenu additionnel entre la description et l'action — réservé aux états
   * vides qui doivent expliquer un parcours (ex. les trois étapes de la
   * première utilisation), pas une simple précision qui appartient déjà à
   * `description`.
   */
  children?: ReactNode;
}

/**
 * État vide dessiné.
 *
 * Un écran sans données doit expliquer ce qui manque **et** proposer la suite,
 * pas afficher « aucun résultat » (CONTRIBUTING.md §7 : états vides dessinés).
 */
export function EmptyState({
  illustration,
  title,
  description,
  action,
  compact = false,
  className,
  children,
}: EmptyStateProps): ReactNode {
  return (
    <div className={cx(styles.root, compact && styles.compact, className)}>
      <Illustration name={illustration} />
      <div className={styles.text}>
        <p className={styles.title}>{title}</p>
        {description !== undefined && <p className={styles.description}>{description}</p>}
      </div>
      {children !== undefined && children}
      {action !== undefined && <div className={styles.action}>{action}</div>}
    </div>
  );
}

export type { IllustrationName };
