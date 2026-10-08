import type { ReactNode } from 'react';

import { useT } from '@/i18n';
import { cx } from '@/lib/cx';

import type { Toast, ToastTone } from './ToastProvider';
import styles from './Toast.module.css';

const ICONS: Record<ToastTone, ReactNode> = {
  success: <path d="M4.5 8.5 7 11l4.5-5" />,
  danger: (
    <>
      <path d="M8 4.5v4.25" />
      <path d="M8 11.5h.01" />
    </>
  ),
  info: (
    <>
      <path d="M8 7.25v4.25" />
      <path d="M8 4.5h.01" />
    </>
  ),
};

export interface ToastViewportProps {
  toasts: readonly Toast[];
  onDismiss: (id: number) => void;
}

/**
 * Pile des notifications.
 *
 * `role="status"` + `aria-live="polite"` : le contenu ajouté est annoncé sans
 * voler le focus de l'utilisateur en pleine saisie.
 */
export function ToastViewport({ toasts, onDismiss }: ToastViewportProps): ReactNode {
  const t = useT();

  return (
    <div className={styles.viewport} role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={cx(styles.toast, styles[toast.tone])}>
          <svg
            className={styles.icon}
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="8" cy="8" r="6.5" />
            {ICONS[toast.tone]}
          </svg>

          <div className={styles.text}>
            <p className={styles.title}>{toast.title}</p>
            {toast.description !== undefined && (
              <p className={styles.description}>{toast.description}</p>
            )}
          </div>

          <button
            type="button"
            className={styles.close}
            aria-label={t('toast.dismiss')}
            onClick={() => {
              onDismiss(toast.id);
            }}
          >
            <svg
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m4.5 4.5 7 7M11.5 4.5l-7 7" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
