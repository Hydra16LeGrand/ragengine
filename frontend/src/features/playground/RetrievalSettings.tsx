import type { ReactNode } from 'react';

import type { DocumentRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Checkbox } from '@/components/Checkbox';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { useT } from '@/i18n';

import { MAX_TOP_K, MIN_TOP_K, type RetrievalSettingsValue } from './settings';
import styles from './Playground.module.css';

/** Formulaire des réglages de retrieval — cf. `settings.ts` pour le contrat. */
export interface RetrievalSettingsProps {
  value: RetrievalSettingsValue;
  onChange: (value: RetrievalSettingsValue) => void;
  /**
   * Documents interrogeables — déjà filtrés `indexed` côté serveur
   * (`indexedDocumentsSelectorQueryOptions`), jamais reconstitué en filtrant
   * une page tronquée : au-delà de 50 documents indexés, un filtre client sur
   * la première page en aurait rendu certains impossibles à sélectionner ici
   * (audit dette technique 2026-09, motif A).
   */
  indexedDocuments: DocumentRead[];
  /**
   * `true` quand la base compte plus de documents indexés que ce que le
   * parcours borné de `indexedDocumentsSelectorQueryOptions` a pu ramener —
   * l'éventail proposé n'est alors plus exhaustif, et ce doit rester visible
   * plutôt que silencieux.
   */
  indexedDocumentsTruncated?: boolean;
  /** La base déclare-t-elle un reranker ? Sinon la façade refuserait `rerank`. */
  rerankerAvailable: boolean;
  disabled?: boolean;
}

export function RetrievalSettings({
  value,
  onChange,
  indexedDocuments,
  indexedDocumentsTruncated = false,
  rerankerAvailable,
  disabled = false,
}: RetrievalSettingsProps): ReactNode {
  const t = useT();

  const toggleDocument = (documentId: string): void => {
    const selected = value.documentIds.includes(documentId)
      ? value.documentIds.filter((id) => id !== documentId)
      : [...value.documentIds, documentId];
    onChange({ ...value, documentIds: selected });
  };

  return (
    <div className={styles.settings}>
      <div className={styles.settingsHead}>
        <h2 className={styles.settingsTitle}>{t('playground.settings.title')}</h2>
        <p className={styles.settingsDescription}>{t('playground.settings.description')}</p>
      </div>

      <Field label={t('playground.settings.topK')} hint={t('playground.settings.topKHint')}>
        {(field) => (
          <Input
            {...field}
            type="number"
            min={MIN_TOP_K}
            max={MAX_TOP_K}
            step={1}
            value={value.topK}
            disabled={disabled}
            onChange={(event) => {
              const parsed = Number.parseInt(event.target.value, 10);
              if (Number.isNaN(parsed)) return;
              onChange({ ...value, topK: Math.min(MAX_TOP_K, Math.max(MIN_TOP_K, parsed)) });
            }}
          />
        )}
      </Field>

      <Checkbox
        label={t('playground.settings.rerank')}
        description={
          rerankerAvailable
            ? t('playground.settings.rerankHint')
            : t('playground.settings.rerankUnavailable')
        }
        checked={value.rerank && rerankerAvailable}
        disabled={disabled || !rerankerAvailable}
        onChange={(event) => {
          onChange({ ...value, rerank: event.target.checked });
        }}
      />

      <Field
        label={t('playground.settings.threshold')}
        hint={t('playground.settings.thresholdHint')}
      >
        {(field) => (
          <Input
            {...field}
            type="number"
            min={0}
            max={1}
            step={0.01}
            placeholder={t('playground.settings.thresholdPlaceholder')}
            value={value.similarityThreshold ?? ''}
            disabled={disabled}
            onChange={(event) => {
              const raw = event.target.value;
              if (raw === '') {
                onChange({ ...value, similarityThreshold: null });
                return;
              }
              const parsed = Number.parseFloat(raw);
              if (Number.isNaN(parsed)) return;
              onChange({
                ...value,
                similarityThreshold: Math.min(1, Math.max(0, parsed)),
              });
            }}
          />
        )}
      </Field>

      <div className={styles.filter}>
        <div className={styles.filterHead}>
          <span className={styles.filterLabel}>{t('playground.settings.documents')}</span>
          <Badge tone={value.documentIds.length > 0 ? 'accent' : 'neutral'}>
            {value.documentIds.length === 0
              ? t('playground.settings.documentsAll')
              : t('playground.settings.documentsSelected', { count: value.documentIds.length })}
          </Badge>
        </div>

        {indexedDocuments.length === 0 ? (
          <p className={styles.filterEmpty}>{t('playground.settings.documentsEmpty')}</p>
        ) : (
          <>
            <ul className={styles.filterList} role="list">
              {indexedDocuments.map((document) => (
                <li key={document.id}>
                  <Checkbox
                    label={document.filename}
                    checked={value.documentIds.includes(document.id)}
                    disabled={disabled}
                    onChange={() => {
                      toggleDocument(document.id);
                    }}
                  />
                </li>
              ))}
            </ul>
            <p className={styles.filterHint}>{t('playground.settings.documentsHint')}</p>
            {indexedDocumentsTruncated && (
              <p className={styles.filterHint} role="status">
                {t('playground.settings.documentsTruncated', { count: indexedDocuments.length })}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
