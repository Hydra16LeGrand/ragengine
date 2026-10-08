import type { ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './StepProgress.module.css';

export type StepStatus = 'done' | 'current' | 'upcoming' | 'failed';

export interface ProgressStep {
  id: string;
  label: string;
  /** Précision affichée sous le libellé (état en cours, cause d'un échec…). */
  description?: string;
  status: StepStatus;
}

export interface StepProgressProps {
  /** Nom accessible de la progression (« Progression de l'ingestion »). */
  label: string;
  steps: readonly ProgressStep[];
  className?: string;
}

/**
 * Progression **par étapes franchies**, jamais par pourcentage.
 *
 * Le composant n'accepte délibérément aucun ratio : ce qui l'alimente ici est
 * une machine à états serveur qui ne produit pas d'avancement continu fiable
 * (ADR-012). Une barre qui se remplit régulièrement serait une invention de
 * l'interface — elle donnerait une estimation de durée que personne ne peut
 * tenir. Franchir une étape est en revanche un fait vérifiable, et c'est le
 * seul que l'on montre.
 *
 * L'état est porté par le texte (`aria-current`, libellé d'étape) autant que
 * par la couleur : la progression reste lisible en niveaux de gris.
 */
export function StepProgress({ label, steps, className }: StepProgressProps): ReactNode {
  return (
    <ol className={cx(styles.list, className)} aria-label={label}>
      {steps.map((step, index) => {
        const isLast = index === steps.length - 1;
        return (
          <li
            key={step.id}
            className={cx(styles.step, styles[step.status])}
            aria-current={step.status === 'current' ? 'step' : undefined}
          >
            <div className={styles.track}>
              <span className={styles.marker} aria-hidden="true">
                {step.status === 'done' && (
                  <svg
                    viewBox="0 0 16 16"
                    focusable="false"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="m4 8.25 2.75 2.75L12 5.5" />
                  </svg>
                )}
                {step.status === 'failed' && (
                  <svg viewBox="0 0 16 16" focusable="false" strokeLinecap="round">
                    <path d="m5.5 5.5 5 5m0-5-5 5" />
                  </svg>
                )}
                {step.status === 'current' && <span className={styles.pulse} />}
              </span>
              {!isLast && <span className={styles.line} aria-hidden="true" />}
            </div>

            <div className={styles.body}>
              <span className={styles.label}>{step.label}</span>
              {step.description !== undefined && (
                <span className={styles.description}>{step.description}</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
