import type { ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './PageHeader.module.css';

export interface PageHeaderProps {
  /** Micro-titre en capitales — repère de section, signature du système. */
  eyebrow?: string;
  title: string;
  description?: string;
  /** Actions principales, alignées à droite du titre. */
  actions?: ReactNode;
  /** Fil d'Ariane ou lien de retour, au-dessus du sur-titre. */
  breadcrumb?: ReactNode;
  /** Contenu additionnel sous la description (badges, métadonnées). */
  meta?: ReactNode;
  className?: string;
}

/** En-tête d'écran : la même mesure typographique sur toutes les pages. */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  breadcrumb,
  meta,
  className,
}: PageHeaderProps): ReactNode {
  return (
    <header className={cx(styles.header, className)}>
      {breadcrumb !== undefined && <div className={styles.breadcrumb}>{breadcrumb}</div>}

      <div className={styles.row}>
        <div className={styles.text}>
          {eyebrow !== undefined && <p className={styles.eyebrow}>{eyebrow}</p>}
          <h1 className={styles.title}>{title}</h1>
          {description !== undefined && <p className={styles.description}>{description}</p>}
        </div>
        {actions !== undefined && <div className={styles.actions}>{actions}</div>}
      </div>

      {meta !== undefined && <div className={styles.meta}>{meta}</div>}
    </header>
  );
}
