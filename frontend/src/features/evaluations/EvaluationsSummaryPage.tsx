import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { evaluationsSummaryQueryOptions } from '@/api/evaluation-summary';
import { problemMessageOf } from '@/api/problem';
import type { EvaluationsOverallSummary, KnowledgeBaseEvaluationSummary } from '@/api/schemas';
import type { BadgeTone } from '@/components/Badge';
import { Card, CardHeader } from '@/components/Card';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';
import { Skeleton } from '@/components/Skeleton';
import { useI18n, useT, type Translate } from '@/i18n';
import { cx } from '@/lib/cx';
import { formatInteger, formatPercent } from '@/lib/format';

import styles from './EvaluationsSummaryPage.module.css';

/** Mêmes paliers que `EvaluationsPanel` (`kb.evaluations`) : une note se lit
 * de la même façon partout dans le produit. */
const GOOD_SCORE = 0.75;
const FAIR_SCORE = 0.5;

function scoreTone(ratio: number): Extract<BadgeTone, 'success' | 'warning' | 'danger'> {
  if (ratio >= GOOD_SCORE) return 'success';
  if (ratio >= FAIR_SCORE) return 'warning';
  return 'danger';
}

/** `recall_at_k` arrive en clés JSON (chaînes) : tri numérique explicite, l'ordre d'un
 * objet JS n'est garanti que pour ce cas précis (entiers positifs), pas de quoi s'y fier. */
function sortedRecallEntries(recallAtK: Record<string, number | null>): [number, number | null][] {
  return Object.entries(recallAtK)
    .map(([k, value]): [number, number | null] => [Number.parseInt(k, 10), value])
    .sort(([a], [b]) => a - b);
}

/**
 * Vue globale des évaluations (Phase 12, Étape 5) : « notre retrieval est-il
 * bon dans l'ensemble », pas seulement base par base (`EvaluationsPanel`,
 * `features/knowledge-bases`, reste strictement par base).
 *
 * Page racine, pas nichée sous une base : le périmètre RBAC est résolu côté
 * serveur (`GET /api/v1/evaluations/summary`, même portée que la liste des
 * bases) — un visiteur anonyme y voit un agrégat vide plutôt qu'un refus, un
 * membre n'y voit que ce à quoi il a accès.
 */
export function EvaluationsSummaryPage(): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const { data, isPending, isError, error } = useQuery(evaluationsSummaryQueryOptions());

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('evaluationsSummary.eyebrow')}
        title={t('evaluationsSummary.title')}
        description={t('evaluationsSummary.description')}
      />

      {isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(error, t('error.unexpected'))}
        </p>
      ) : isPending ? (
        <SummarySkeleton />
      ) : data.overall.item_count === 0 ? (
        <EmptyState
          illustration="search"
          title={t('evaluationsSummary.emptyTitle')}
          description={t('evaluationsSummary.emptyDescription')}
        />
      ) : (
        <>
          <OverallCard overall={data.overall} t={t} locale={locale} />
          <KnowledgeBaseTable entries={data.knowledge_bases} t={t} locale={locale} />
        </>
      )}
    </div>
  );
}

function OverallCard({
  overall,
  t,
  locale,
}: {
  overall: EvaluationsOverallSummary;
  t: Translate;
  locale: string;
}): ReactNode {
  return (
    <Card>
      <CardHeader
        title={t('evaluationsSummary.overallTitle')}
        description={t('evaluationsSummary.overallDescription')}
      />
      <div className={styles.stats}>
        <StatTile
          label={t('evaluationsSummary.statBases')}
          value={formatInteger(overall.knowledge_base_count, locale)}
        />
        <StatTile
          label={t('evaluationsSummary.statRuns')}
          value={formatInteger(overall.run_count, locale)}
        />
        <StatTile
          label={t('evaluationsSummary.statItems')}
          value={formatInteger(overall.item_count, locale)}
        />
      </div>
      <div className={styles.meters}>
        {sortedRecallEntries(overall.recall_at_k).map(([k, ratio]) => (
          <ScoreMeter
            key={k}
            label={t('evaluationsSummary.recallAtK', { k })}
            ratio={ratio}
            locale={locale}
            emptyLabel={t('evaluationsSummary.scoreUnavailable')}
          />
        ))}
        <ScoreMeter
          label={t('evaluationsSummary.mrr')}
          ratio={overall.mrr}
          locale={locale}
          emptyLabel={t('evaluationsSummary.scoreUnavailable')}
        />
        <ScoreMeter
          label={t('evaluationsSummary.judgeScore')}
          ratio={overall.average_judge_score}
          locale={locale}
          emptyLabel={t('evaluationsSummary.scoreUnavailable')}
        />
      </div>
    </Card>
  );
}

function KnowledgeBaseTable({
  entries,
  t,
  locale,
}: {
  entries: KnowledgeBaseEvaluationSummary[];
  t: Translate;
  locale: string;
}): ReactNode {
  return (
    <Card flush>
      <CardHeader
        className={styles.tableHeader}
        title={t('evaluationsSummary.tableTitle')}
        description={t('evaluationsSummary.tableDescription')}
      />
      <ul className={styles.list}>
        {entries.map((entry) => (
          <li key={entry.knowledge_base_id} className={styles.item}>
            <Link
              to={`/knowledge-bases/${entry.knowledge_base_id}`}
              className={styles.itemNameLink}
            >
              <span className={styles.itemName}>{entry.knowledge_base_name}</span>
              <span className={styles.itemMeta}>
                {t('evaluationsSummary.rowRuns', { count: entry.run_count })}
                {' · '}
                {t('evaluationsSummary.rowItems', { count: entry.item_count })}
              </span>
            </Link>

            <span className={styles.itemMeters}>
              {sortedRecallEntries(entry.recall_at_k).map(([k, ratio]) => (
                <ScoreMeter
                  key={k}
                  label={t('evaluationsSummary.recallAtK', { k })}
                  ratio={ratio}
                  locale={locale}
                  emptyLabel={t('evaluationsSummary.scoreUnavailable')}
                  compact
                />
              ))}
              <ScoreMeter
                label={t('evaluationsSummary.judgeScore')}
                ratio={entry.average_judge_score}
                locale={locale}
                emptyLabel={t('evaluationsSummary.scoreUnavailable')}
                compact
              />
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function StatTile({ label, value }: { label: string; value: string }): ReactNode {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}

/**
 * Score `0..1` en barre + pourcentage — même composant que
 * `EvaluationsPanel.ScoreMeter` (`features/knowledge-bases`), dupliqué à
 * dessein : portée différente (agrégat transverse contre un run unique),
 * jamais couplée à un écran par base pour une seule barre de progression.
 */
function ScoreMeter({
  label,
  ratio,
  locale,
  emptyLabel,
  compact = false,
}: {
  label: string;
  ratio: number | null;
  locale: string;
  emptyLabel: string;
  compact?: boolean;
}): ReactNode {
  if (ratio === null) {
    return (
      <span className={cx(styles.meter, compact && styles.meterCompact)}>
        <span className={styles.meterHead}>
          <span className={styles.meterLabel}>{label}</span>
          <span className={styles.meterEmpty}>{emptyLabel}</span>
        </span>
      </span>
    );
  }

  const tone = scoreTone(ratio);
  const clamped = Math.min(1, Math.max(0, ratio));

  return (
    <span className={cx(styles.meter, compact && styles.meterCompact)}>
      <span className={styles.meterHead}>
        <span className={styles.meterLabel}>{label}</span>
        <span className={cx(styles.meterValue, styles[tone])}>
          {formatPercent(clamped, locale)}
        </span>
      </span>
      <span className={styles.meterTrack} aria-hidden="true">
        <span
          className={cx(styles.meterFill, styles[tone])}
          style={{ inlineSize: `${String(clamped * 100)}%` }}
        />
      </span>
    </span>
  );
}

function SummarySkeleton(): ReactNode {
  return (
    <div className={styles.page} aria-busy="true">
      <Card>
        <div className={styles.stats}>
          {Array.from({ length: 3 }, (_, index) => (
            <div key={index} className={styles.stat}>
              <Skeleton width="4rem" height="2rem" />
              <Skeleton width="6rem" />
            </div>
          ))}
        </div>
        <div className={styles.meters}>
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} width="9rem" height="2.5rem" />
          ))}
        </div>
      </Card>
      <Card flush>
        <ul className={styles.list}>
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className={styles.item}>
              <Skeleton width="10rem" />
              <Skeleton width="16rem" height="1.6rem" />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
