import { useMutation } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { retrievalSearchMutationOptions } from '@/api/retrieval';
import type { RetrievalSearchRequest } from '@/api/schemas';
import { MAX_QUERY_LENGTH, requestErrorMessage } from '@/api/text-limits';
import { Button } from '@/components/Button';
import { EmptyState } from '@/components/EmptyState';
import { Field } from '@/components/Field';
import { Skeleton } from '@/components/Skeleton';
import { Textarea } from '@/components/Textarea';
import { useI18n } from '@/i18n';
import { formatInteger } from '@/lib/format';

import { PassageCard } from './PassageCard';
import type { RetrievalSettingsValue } from './settings';
import styles from './Playground.module.css';

export interface SearchPanelProps {
  knowledgeBaseId: string;
  settings: RetrievalSettingsValue;
}

/** Mode Recherche : la sortie brute du moteur, sans reformulation. */
export function SearchPanel({ knowledgeBaseId, settings }: SearchPanelProps): ReactNode {
  const { t, locale } = useI18n();
  const [query, setQuery] = useState('');
  const search = useMutation(retrievalSearchMutationOptions());

  const submit = (): void => {
    const trimmed = query.trim();
    if (trimmed.length === 0) return;

    const request: RetrievalSearchRequest = {
      knowledge_base_id: knowledgeBaseId,
      query: trimmed,
      top_k: settings.topK,
      rerank: settings.rerank,
      similarity_threshold: settings.similarityThreshold,
      // Le Playground n'expose pas encore de bouton d'accusé de réception
      // (ADR-026) : combiner reranking et seuil non nul y répond donc 422,
      // affiché tel quel via `requestErrorMessage` plutôt que masqué.
      acknowledge_threshold_risk: false,
      document_ids: settings.documentIds.length > 0 ? settings.documentIds : null,
    };
    search.mutate(request);
  };

  const results = search.data?.results ?? [];

  return (
    <div className={styles.panel}>
      <form
        className={styles.queryForm}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field
          label={t('playground.search.queryLabel')}
          hint={
            query.length >= MAX_QUERY_LENGTH
              ? t('error.inputLimitReached', { max: formatInteger(MAX_QUERY_LENGTH, locale) })
              : undefined
          }
        >
          {(field) => (
            <Textarea
              {...field}
              rows={3}
              maxLength={MAX_QUERY_LENGTH}
              value={query}
              placeholder={t('playground.search.queryPlaceholder')}
              disabled={search.isPending}
              onChange={(event) => {
                setQuery(event.target.value);
              }}
            />
          )}
        </Field>
        <div className={styles.queryActions}>
          <Button type="submit" loading={search.isPending} disabled={query.trim().length === 0}>
            {t('playground.search.submit')}
          </Button>
        </div>
      </form>

      {search.isError && (
        <div className={styles.errorBlock} role="alert">
          <p className={styles.errorTitle}>{t('playground.search.errorTitle')}</p>
          <p className={styles.errorText}>{requestErrorMessage(search.error, t, locale)}</p>
        </div>
      )}

      {search.isPending && <ResultsSkeleton />}

      {search.isSuccess && results.length === 0 && (
        <EmptyState
          illustration="search"
          title={t('playground.search.noResultsTitle')}
          description={t('playground.search.noResultsDescription')}
        />
      )}

      {results.length > 0 && !search.isPending && (
        <section className={styles.results} aria-label={t('playground.search.resultsTitle')}>
          <div className={styles.resultsHead}>
            <h2 className={styles.resultsTitle}>{t('playground.search.resultsTitle')}</h2>
            <span className={styles.resultsCount}>
              {t('playground.search.resultsCount', { count: results.length })}
            </span>
          </div>
          <p className={styles.scoreLegend}>{t('playground.score.legend')}</p>

          <div className={styles.passages}>
            {results.map((result, index) => (
              <PassageCard key={result.chunk_id} rank={index + 1} result={result} />
            ))}
          </div>
        </section>
      )}

      {search.isIdle && (
        <EmptyState
          illustration="search"
          title={t('playground.search.emptyTitle')}
          description={t('playground.search.emptyDescription')}
        />
      )}
    </div>
  );
}

function ResultsSkeleton(): ReactNode {
  return (
    <div className={styles.passages} aria-busy="true">
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className={styles.passage}>
          <div className={styles.passageHead}>
            <Skeleton shape="circle" width="1.5rem" height="1.5rem" />
            <Skeleton width="12rem" height="0.7rem" />
          </div>
          <Skeleton height="0.7rem" />
          <Skeleton height="0.7rem" />
          <Skeleton width="70%" height="0.7rem" />
        </div>
      ))}
    </div>
  );
}
