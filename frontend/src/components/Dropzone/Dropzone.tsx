import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react';

import { cx } from '@/lib/cx';

import styles from './Dropzone.module.css';

export interface DropzoneProps {
  /** Appel à l'action principal, en gras dans la zone. */
  label: string;
  /** Précision sous le libellé : formats acceptés, taille maximale. */
  hint?: string;
  /** Libellé du déclencheur clavier/souris (« parcourir vos fichiers »). */
  browseLabel: string;
  /** Filtre `accept` du champ natif (`.pdf,.docx`…). Indicatif, jamais une garantie. */
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** Reçoit les fichiers déposés **ou** choisis dans la boîte de dialogue. */
  onFiles: (files: File[]) => void;
  className?: string;
}

/**
 * Zone de dépôt de fichiers.
 *
 * Deux chemins d'entrée, un seul rappel : le glisser-déposer **et** le champ
 * `<input type="file">` natif. Ce dernier n'est pas décoratif — c'est lui qui
 * apporte gratuitement l'ouverture au clavier, l'annonce vocale et le
 * sélecteur système sur mobile. Il reste donc dans le DOM, focalisable, et la
 * zone se met en évidence via `:focus-within` : aucun comportement natif n'est
 * réimplémenté.
 *
 * Le survol de dépôt est compté (`dragenter`/`dragleave` se déclenchent aussi
 * en passant d'un enfant à l'autre) : sans compteur, la zone clignoterait
 * pendant que l'utilisateur déplace son fichier au-dessus d'elle.
 */
export function Dropzone({
  label,
  hint,
  browseLabel,
  accept,
  multiple = false,
  disabled = false,
  onFiles,
  className,
}: DropzoneProps): ReactNode {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  const reset = (): void => {
    depth.current = 0;
    setDragging(false);
  };

  const emit = (fileList: FileList | null): void => {
    if (fileList === null || fileList.length === 0) return;
    const files = Array.from(fileList);
    onFiles(multiple ? files : files.slice(0, 1));
  };

  const onDragEnter = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled) return;
    event.preventDefault();
    depth.current += 1;
    setDragging(true);
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled) return;
    // Sans `preventDefault` sur `dragover`, le navigateur refuse le dépôt et
    // ouvre le fichier dans un nouvel onglet à la place.
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  };

  const onDragLeave = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled) return;
    event.preventDefault();
    depth.current -= 1;
    if (depth.current <= 0) reset();
  };

  const onDrop = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled) return;
    event.preventDefault();
    reset();
    emit(event.dataTransfer.files);
  };

  return (
    <div
      className={cx(
        styles.zone,
        dragging && styles.dragging,
        disabled && styles.disabled,
        className,
      )}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      data-dragging={dragging || undefined}
    >
      <svg
        className={styles.glyph}
        viewBox="0 0 48 40"
        aria-hidden="true"
        focusable="false"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Une feuille qui se pose dans un bac : l'objet du geste, dessiné —
            pas une icône de nuage empruntée à un service tiers. */}
        <path d="M6 27v6.5A3.5 3.5 0 0 0 9.5 37h29a3.5 3.5 0 0 0 3.5-3.5V27" />
        <path className={styles.glyphSheet} d="M18 8.5h9.5L34 15v13.5H18z" />
        <path className={styles.glyphSheet} d="M27.5 8.5V15H34" />
        <path className={styles.glyphArrow} d="M24 18v9m0 0-3.25-3.25M24 27l3.25-3.25" />
      </svg>

      <div className={styles.text}>
        <p className={styles.label}>{label}</p>
        {hint !== undefined && (
          <p className={styles.hint} id={hintId}>
            {hint}
          </p>
        )}
      </div>

      <label className={styles.browse} htmlFor={inputId}>
        {browseLabel}
      </label>

      <input
        id={inputId}
        className={styles.input}
        type="file"
        multiple={multiple}
        disabled={disabled}
        {...(accept !== undefined ? { accept } : {})}
        {...(hint !== undefined ? { 'aria-describedby': hintId } : {})}
        onChange={(event) => {
          emit(event.target.files);
          // Remise à zéro : sans elle, redéposer le même fichier après une
          // erreur n'émettrait aucun `change` (valeur identique).
          event.target.value = '';
        }}
      />
    </div>
  );
}
