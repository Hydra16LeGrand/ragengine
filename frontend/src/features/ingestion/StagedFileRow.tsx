import type { ReactNode } from 'react';

import type { DocumentRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { useI18n, useT } from '@/i18n';
import { formatBytes } from '@/lib/format';

import styles from './Ingestion.module.css';

/**
 * Fichier déposé, pas encore confirmé : aucun appel réseau tant qu'il est ici.
 *
 * Partagé entre `UploadPanel` (mise en attente avant versement sur une base
 * existante) et `KnowledgeBaseForm` en mode création (mise en attente avant
 * même que la base existe) — même geste local, deux moments différents de sa
 * confirmation.
 */
export interface StagedFile {
  id: string;
  file: File;
}

export interface StagedFileRowProps {
  item: StagedFile;
  /** Document existant que ce fichier remplacerait, `null` sinon (sans objet avant la création d'une base). */
  replaces: DocumentRead | null;
  onRemove: () => void;
}

/** Une ligne de la liste de mise en attente — glyphe, nom, taille, retrait. */
export function StagedFileRow({ item, replaces, onRemove }: StagedFileRowProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();

  return (
    <li className={styles.item}>
      <div className={styles.itemHead}>
        <span className={styles.itemIcon} aria-hidden="true">
          <svg viewBox="0 0 20 20" focusable="false" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 2.5h5L15 6.5v11H6z" />
            <path d="M11 2.5v4h4" />
          </svg>
        </span>

        <div className={styles.itemIdentity}>
          <span className={styles.itemName} title={item.file.name}>
            {item.file.name}
          </span>
          <span className={styles.itemSize}>{formatBytes(item.file.size, locale)}</span>
        </div>

        {replaces !== null && (
          <Badge tone="warning">{t('ingest.staging.replaces', { name: replaces.filename })}</Badge>
        )}

        <button
          type="button"
          className={styles.itemRemove}
          title={t('ingest.item.remove')}
          onClick={onRemove}
        >
          <span className={styles.visuallyHidden}>{t('ingest.item.remove')}</span>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" strokeLinecap="round">
            <path d="m5 5 6 6m0-6-6 6" />
          </svg>
        </button>
      </div>
    </li>
  );
}
