import type { HTMLAttributes, ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Card.module.css';

export type CardTone = 'default' | 'sunken' | 'accent' | 'danger';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: CardTone;
  /** Retire le rembourrage interne : au parent de le poser (listes, tableaux). */
  flush?: boolean;
}

/** Surface élémentaire : un bloc délimité, jamais un conteneur de mise en page. */
export function Card({
  tone = 'default',
  flush = false,
  className,
  ...rest
}: CardProps): ReactNode {
  return (
    <div className={cx(styles.card, styles[tone], flush && styles.flush, className)} {...rest} />
  );
}

export interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {
  /** Micro-titre en capitales au-dessus du titre — signature typographique. */
  eyebrow?: string;
  title: string;
  description?: string;
  /** Actions alignées à droite du titre. */
  actions?: ReactNode;
}

export function CardHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
  ...rest
}: CardHeaderProps): ReactNode {
  return (
    <div className={cx(styles.header, className)} {...rest}>
      <div className={styles.headerText}>
        {eyebrow !== undefined && <p className={styles.eyebrow}>{eyebrow}</p>}
        <h2 className={styles.title}>{title}</h2>
        {description !== undefined && <p className={styles.description}>{description}</p>}
      </div>
      {actions !== undefined && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>): ReactNode {
  return <div className={cx(styles.body, className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>): ReactNode {
  return <div className={cx(styles.footer, className)} {...rest} />;
}
