import { useEffect, useId, useRef, type ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Dialog.module.css';

export interface DialogProps {
  open: boolean;
  /** Fermeture demandée : Échap, clic sur le fond, bouton de fermeture. */
  onClose: () => void;
  title: string;
  description?: string;
  /** Libellé accessible du bouton de fermeture (i18n). */
  closeLabel: string;
  /** Pied de la modale : actions, alignées à droite. */
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/**
 * Modale bâtie sur l'élément `<dialog>` natif.
 *
 * `showModal()` fournit gratuitement — et correctement — ce qu'une modale en
 * `div` doit réimplémenter : piège de focus, fermeture par Échap, inertie du
 * reste de la page pour les lecteurs d'écran, et rendu dans la « top layer »
 * (donc jamais rogné par un `overflow` parent). Aucune librairie requise.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  closeLabel,
  footer,
  children,
  className,
}: DialogProps): ReactNode {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;

    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;

    // `cancel` couvre la touche Échap : on laisse React piloter l'ouverture
    // plutôt que de laisser le DOM fermer l'élément dans notre dos.
    const onCancel = (event: Event): void => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
    };
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      className={cx(styles.dialog, className)}
      aria-labelledby={headingId}
      // Un clic dont la cible est le `<dialog>` lui-même tombe sur le fond :
      // le contenu réel est dans `.panel`, qui intercepte ses propres clics.
      onClick={(event) => {
        if (event.target === dialogRef.current) onClose();
      }}
    >
      <div className={styles.panel}>
        <div className={styles.header}>
          <div className={styles.headerText}>
            <h2 className={styles.title} id={headingId}>
              {title}
            </h2>
            {description !== undefined && <p className={styles.description}>{description}</p>}
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label={closeLabel}>
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

        {children !== undefined && <div className={styles.body}>{children}</div>}
        {footer !== undefined && <div className={styles.footer}>{footer}</div>}
      </div>
    </dialog>
  );
}
