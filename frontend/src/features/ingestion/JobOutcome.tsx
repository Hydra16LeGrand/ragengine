/**
 * Restitution de l'issue d'un job : la cause d'un échec, ou l'avertissement
 * qualité d'une indexation réussie mais suspecte.
 *
 * Ces deux blocs vivaient dans `JobTracker` — donc **uniquement** pendant la
 * session de versement en cours. Dès que l'écran était quitté ou rafraîchi, la
 * cause enregistrée en base devenait inatteignable dans l'interface, et
 * plusieurs échecs de la campagne de tests manuels ont été remontés comme
 * « sans cause visible » alors que la cause existait
 * (l'analyse interne des tests manuels §3). Ils sont extraits ici pour
 * que la liste des documents (`DocumentsPanel`) les rende à l'identique, sans
 * dupliquer ni la mise en forme ni la lecture des codes.
 */
import type { ReactNode } from 'react';

import type { JobError, JobWarning } from '@/api/schemas';
import { useI18n, useT, type TranslationKey } from '@/i18n';
import { formatInteger, formatPercent } from '@/lib/format';

import { adviceForJobWarning, diagnoseJobError, fileExtensionOf } from './failureCause';
import styles from './Ingestion.module.css';

const QUALITY_WARNING_CODE_LABELS: Record<string, TranslationKey> = {
  zero_chunks: 'ingest.qualityWarning.code.zero_chunks',
  degenerate_content: 'ingest.qualityWarning.code.degenerate_content',
  low_source_coverage: 'ingest.qualityWarning.code.low_source_coverage',
};

const QUALITY_WARNING_METRIC_LABELS: Record<string, TranslationKey> = {
  chunk_count: 'ingest.qualityWarning.metric.chunk_count',
  degenerate_chunk_pct: 'ingest.qualityWarning.metric.degenerate_chunk_pct',
  significant_char_total: 'ingest.qualityWarning.metric.significant_char_total',
  source_coverage: 'ingest.qualityWarning.metric.source_coverage',
};

/** Métriques du `JobWarning` restituées en pourcentage plutôt qu'en nombre brut. */
const QUALITY_WARNING_PERCENT_METRICS = new Set(['degenerate_chunk_pct', 'source_coverage']);

export interface JobFailureCauseProps {
  error: JobError;
  /** Nom du fichier versé — sert à nommer le format dans le conseil. */
  filename: string;
}

/**
 * Corps d'un échec : le constat actionnable quand la cause est reconnue, le
 * message brut du moteur dans tous les cas.
 *
 * L'ordre n'est pas cosmétique. Le message brut reste **présent et complet**,
 * simplement replié : c'est lui qui permet le diagnostic (numéro de page,
 * charge utile du fournisseur), et le supprimer au profit d'une jolie phrase
 * échangerait un défaut d'ergonomie contre une perte d'information. Quand
 * aucune règle ne reconnaît la cause, il reprend sa place d'origine — déplié,
 * seul — plutôt que d'être caché derrière un repli que rien n'annonce.
 */
export function JobFailureCause({ error, filename }: JobFailureCauseProps): ReactNode {
  const t = useT();
  const cause = diagnoseJobError(error);
  const extension = fileExtensionOf(filename);
  const format = extension === '' ? t('ingest.cause.unsupportedFormat.formatFallback') : extension;

  return (
    <>
      {cause === null ? (
        <p className={styles.failureText}>{error.message}</p>
      ) : (
        <>
          <p className={styles.causeTitle}>{t(cause.title)}</p>
          <p className={styles.failureText}>{t(cause.advice, { format })}</p>
          <details className={styles.rawCause}>
            <summary className={styles.rawCauseSummary}>{t('ingest.cause.rawToggle')}</summary>
            <p className={styles.rawCauseMessage}>{error.message}</p>
          </details>
        </>
      )}

      <p className={styles.failureMeta}>
        <code className={styles.failureCode}>{error.code}</code>
        {error.retryable && <span>{t('ingest.error.retryable')}</span>}
      </p>
    </>
  );
}

export interface JobFailurePanelProps {
  /** `null` quand le job est terminal sans cause enregistrée (annulation). */
  error: JobError | null | undefined;
  filename: string;
  /** Déjà traduit par l'appelant : « échec » et « annulé » ne se disent pas pareil. */
  title: string;
  /** Complément d'en-tête (badge de tentatives, par exemple). */
  aside?: ReactNode;
}

/** Bloc complet — en-tête et cause — partagé par le suivi et la liste des documents. */
export function JobFailurePanel({
  error,
  filename,
  title,
  aside,
}: JobFailurePanelProps): ReactNode {
  return (
    <div className={styles.failure} role="alert">
      <div className={styles.failureHead}>
        <p className={styles.failureTitle}>{title}</p>
        {aside}
      </div>
      {error != null && <JobFailureCause error={error} filename={filename} />}
    </div>
  );
}

/**
 * Garde-fou qualité (ADR-024) : le job a réussi techniquement, mais le contenu
 * produit est mesurablement suspect. Restitué dans le même esprit que
 * `PrecheckReview` (ADR-012) — un constat lisible, porté par le chiffre qui le
 * fonde — plutôt que le code et les métriques brutes du `JobWarning`.
 */
export function QualityWarning({ warning }: { warning: JobWarning }): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const codeLabel = QUALITY_WARNING_CODE_LABELS[warning.code];
  const advice = adviceForJobWarning(warning);
  const metrics = Object.entries(warning.metrics);

  return (
    <div className={styles.qualityWarning} role="status">
      <p className={styles.qualityWarningTitle}>
        {codeLabel !== undefined ? t(codeLabel) : t('ingest.qualityWarningTitle')}
      </p>
      <p className={styles.qualityWarningIntro}>{t('ingest.qualityWarningIntro')}</p>
      <p className={styles.qualityWarningMessage}>{warning.message}</p>
      {advice !== null && <p className={styles.qualityWarningAdvice}>{t(advice)}</p>}
      {metrics.length > 0 && (
        <ul className={styles.qualityWarningMetrics} role="list">
          {metrics.map(([key, value]) => {
            const label = QUALITY_WARNING_METRIC_LABELS[key];
            const formatted =
              value === null
                ? '—'
                : QUALITY_WARNING_PERCENT_METRICS.has(key)
                  ? formatPercent(value, locale)
                  : formatInteger(value, locale);
            return (
              <li key={key} className={styles.qualityWarningMetric}>
                <span>{label !== undefined ? t(label) : key}</span>
                <span className={styles.qualityWarningMetricValue}>{formatted}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
