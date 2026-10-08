import type { CSSProperties, ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Skeleton.module.css';

export type SkeletonShape = 'line' | 'block' | 'circle';

export interface SkeletonProps {
  shape?: SkeletonShape;
  /** Largeur CSS (`100%`, `12ch`…). Par défaut : toute la largeur. */
  width?: string;
  /** Hauteur CSS. Par défaut : dérivée de la forme. */
  height?: string;
  className?: string;
}

/**
 * Bloc de chargement animé.
 *
 * Il reproduit la forme du contenu attendu plutôt que d'afficher un sablier :
 * la page ne bouge pas au moment où les données arrivent, et l'utilisateur
 * comprend ce qui se prépare (CONTRIBUTING.md §7 : jamais de spinner générique).
 *
 * `aria-hidden` : la zone en attente est annoncée une seule fois par son
 * conteneur (`aria-busy`), pas par chaque barre.
 */
export function Skeleton({ shape = 'line', width, height, className }: SkeletonProps): ReactNode {
  const style: CSSProperties = {
    ...(width !== undefined ? { inlineSize: width } : {}),
    ...(height !== undefined ? { blockSize: height } : {}),
  };

  return (
    <span
      aria-hidden="true"
      className={cx(styles.skeleton, styles[shape], className)}
      style={style}
    />
  );
}
