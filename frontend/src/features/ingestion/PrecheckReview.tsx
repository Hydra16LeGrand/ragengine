import { useMemo, useState, type ReactNode } from 'react';

import { problemMessageOf } from '@/api/problem';
import {
  advancedChunkMethodsFor,
  readsBetterWithoutLayoutAnalysis,
  suggestedDecision,
  type AdvancedChunkMethod,
  type PrecheckFailure,
  type PrecheckFormat,
  type PrecheckReport,
} from '@/api/precheck';
import type { PrecheckDecision } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Checkbox } from '@/components/Checkbox';
import { Field } from '@/components/Field';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { useI18n, useT, type TranslationKey } from '@/i18n';
import { cx } from '@/lib/cx';
import { formatInteger, formatPercent } from '@/lib/format';

import styles from './Ingestion.module.css';

/** Un libellé et une justification chiffrée par template exposé — jamais de nom brut à l'écran. */
const CHUNK_METHOD_LABELS: Record<AdvancedChunkMethod, TranslationKey> = {
  naive: 'precheck.advanced.method.naive',
  laws: 'precheck.advanced.method.laws',
  paper: 'precheck.advanced.method.paper',
  manual: 'precheck.advanced.method.manual',
  qa: 'precheck.advanced.method.qa',
};

const FORMAT_LABELS: Record<PrecheckFormat, TranslationKey> = {
  pdf: 'precheck.format.pdf',
  spreadsheet: 'precheck.format.spreadsheet',
  presentation: 'precheck.format.presentation',
  unsupported: 'precheck.format.unsupported',
};

/**
 * En deçà de ce nombre de caractères, la couche texte d'un document reconnu
 * comme scanné est tenue pour vide : moins d'une ligne pour tout le document,
 * donc un numéro de page ou un artefact d'export, jamais du contenu lisible.
 * Au-delà, le chiffre est une information réelle (scan partiel) et reste affiché.
 */
const NEGLIGIBLE_TEXT_CHARACTERS = 40;

const FAILURE_LABELS: Record<PrecheckFailure['code'], TranslationKey> = {
  unsupported_format: 'precheck.error.unsupported_format',
  corrupted_file: 'precheck.error.corrupted_file',
  extraction_failed: 'precheck.error.extraction_failed',
  unknown: 'precheck.error.unknown',
};

export interface PrecheckReviewProps {
  report: PrecheckReport | null;
  /**
   * Nom du fichier tel que téléversé.
   *
   * Sert au seul sélecteur avancé, et pour une raison précise : le precheck
   * ne décrit localement que PDF, tableurs, images et diaporamas — un `.md`
   * en ressort `format: "unsupported"`, indistinguable d'un `.docx`. Le nom
   * est la seule chose qui les sépare, et il est déjà à l'écran
   * (`advancedChunkMethodsFor`).
   */
  filename: string;
  /** Le rapport n'est pas encore lisible : on montre la forme à venir. */
  loading: boolean;
  submitting: boolean;
  error: unknown;
  onSubmit: (decision: PrecheckDecision) => void;
  /**
   * Renoncer à ce document reste possible depuis cet écran, à côté de l'action
   * de validation plutôt qu'isolé en haut de la carte — les deux actions qui
   * comptent au même endroit. `undefined` : aucun bouton n'est rendu (état non
   * annulable, décidé par l'appelant).
   */
  onRequestCancel?: () => void;
}

/**
 * Écran d'aiguillage : ce que l'analyse a vu, puis ce que l'utilisateur décide.
 *
 * Le rapport est restitué en **constats**, pas en champs bruts : « Document
 * scanné détecté » plutôt que `is_likely_scanned: true`, « 3 tableaux repérés »
 * plutôt que `table_count: 3`. Chaque constat porte le chiffre qui le fonde,
 * pour qu'un utilisateur puisse contredire l'heuristique en connaissance de
 * cause — c'est tout l'objet de cet écran (ADR-012).
 */
export function PrecheckReview({
  report,
  filename,
  loading,
  submitting,
  error,
  onSubmit,
  onRequestCancel,
}: PrecheckReviewProps): ReactNode {
  const t = useT();
  const suggestion = useMemo(() => suggestedDecision(report), [report]);
  const advancedMethods = useMemo(
    () => advancedChunkMethodsFor(report, filename),
    [report, filename],
  );

  // Les deux premières cases sont pré-cochées, puis appartiennent à
  // l'utilisateur : la `key` posée par `JobTracker` garantit qu'un nouveau
  // document — et une première arrivée du rapport, cf. le commentaire de ce
  // `key` — repartent de la proposition, sans hériter du choix précédent.
  // C'est cette `key` qui rend `useState` légitime ici plutôt qu'un état
  // dérivé : sans elle, la proposition ne serait lue qu'à la première monture,
  // avant même que le rapport n'existe.
  // `optimize_for_tables` suit l'analyse (tableaux détectés) ; `heavy_ocr`
  // est proposé sur **tout PDF** depuis ADR-031 — ce n'est donc plus un
  // constat de l'analyse mais un défaut produit, d'où le libellé de badge
  // distinct (`byDefault` vs `suggested`), et d'où l'arbitrage latence/qualité
  // dit dans le texte d'aide plutôt que laissé à deviner.
  //
  // `use_parent_child` (Phase 13) démarre toujours décochée : rien dans le
  // rapport de precheck ne permet de deviner si l'utilisateur la veut —
  // toujours un choix explicite, jamais une suggestion (`PrecheckDecision`,
  // docstring).
  const [optimizeForTables, setOptimizeForTables] = useState(suggestion.optimizeForTables);
  const [heavyOcr, setHeavyOcr] = useState(suggestion.heavyOcr);
  const [useParentChild, setUseParentChild] = useState(false);

  // Sélection fine (Phase 15) : chaîne vide = « routage automatique », c'est-à-
  // dire l'absence de consigne, qui est le défaut du contrat d'API
  // (`chunk_method_override: null`). Jamais pré-remplie : aucune mesure ne
  // permet de deviner qu'un utilisateur veut forcer un template plutôt que de
  // laisser le routage décider — même discipline que `use_parent_child`.
  const [chunkMethodOverride, setChunkMethodOverride] = useState<AdvancedChunkMethod | ''>('');

  // ADR-042 — deux situations distinctes, deux textes distincts : le document
  // que l'analyse de mise en page dessert (colonnes, aucun tableau) et celui où
  // les deux gains s'opposent (colonnes **et** tableaux), qu'aucune heuristique
  // ne peut trancher à la place de l'utilisateur.
  const columnsWarning =
    report?.format !== 'pdf' || report.twoColumnPageCount === 0
      ? null
      : readsBetterWithoutLayoutAnalysis(report)
        ? t('precheck.decision.ocrColumnsWarning')
        : t('precheck.decision.ocrColumnsWithTables');

  if (loading) return <ReportSkeleton />;

  return (
    <div className={styles.review}>
      <ReportSummary report={report} />

      <div className={styles.decision}>
        <div className={styles.decisionHead}>
          <p className={styles.decisionTitle}>{t('precheck.decision.title')}</p>
          <p className={styles.decisionDescription}>{t('precheck.decision.description')}</p>
        </div>

        <div className={styles.options}>
          <div className={styles.option}>
            <Checkbox
              label={t('precheck.decision.tables')}
              description={t('precheck.decision.tablesHint')}
              checked={optimizeForTables}
              disabled={submitting}
              onChange={(event) => {
                setOptimizeForTables(event.target.checked);
              }}
            />
            {suggestion.optimizeForTables && (
              <Badge tone="accent" variant="outline" className={styles.suggestion}>
                {t('precheck.decision.suggested')}
              </Badge>
            )}
          </div>

          <div className={styles.option}>
            <Checkbox
              label={t('precheck.decision.ocr')}
              description={t('precheck.decision.ocrHint')}
              checked={heavyOcr}
              disabled={submitting}
              onChange={(event) => {
                setHeavyOcr(event.target.checked);
              }}
            />
            {suggestion.heavyOcr && (
              <Badge tone="accent" variant="outline" className={styles.suggestion}>
                {t('precheck.decision.byDefault')}
              </Badge>
            )}
            {/* ADR-042 — dire *pourquoi* la proposition d'ADR-031 ne s'applique
                pas ici, ou pourquoi elle s'applique quand même malgré les
                colonnes. Sans ce texte, l'utilisateur verrait une case cochée
                sur un document et pas sur l'autre sans pouvoir deviner ce qui
                les distingue. */}
            {columnsWarning !== null && (
              <p className={styles.decisionDescription}>{columnsWarning}</p>
            )}
          </div>

          <div className={styles.option}>
            <Checkbox
              label={t('precheck.decision.parentChild')}
              description={t('precheck.decision.parentChildHint')}
              checked={useParentChild}
              disabled={submitting}
              onChange={(event) => {
                setUseParentChild(event.target.checked);
              }}
            />
          </div>
        </div>

        {/* Paramètres avancés — replié par défaut, même patron que
            `KnowledgeBaseForm.tsx`. Il n'apparaît que s'il a quelque chose de
            mesuré à proposer *pour ce document* : un panneau qui ne
            contiendrait qu'un « routage automatique » serait une porte vers
            une pièce vide. */}
        {advancedMethods.length > 0 && (
          <details className={styles.advanced}>
            <summary className={styles.advancedSummary}>
              <span className={styles.advancedSummaryText}>
                <span className={styles.advancedTitle}>{t('precheck.advanced.title')}</span>
                <span className={styles.advancedHint}>{t('precheck.advanced.description')}</span>
              </span>
              <svg
                className={styles.advancedChevron}
                viewBox="0 0 16 16"
                aria-hidden="true"
                focusable="false"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m4 6.5 4 4 4-4" />
              </svg>
            </summary>

            <div className={styles.advancedBody}>
              <Field
                label={t('precheck.advanced.chunkMethod')}
                hint={t('precheck.advanced.chunkMethodHint')}
              >
                {(field) => (
                  <Select
                    {...field}
                    name="chunk_method_override"
                    value={chunkMethodOverride}
                    disabled={submitting}
                    onChange={(event) => {
                      setChunkMethodOverride(event.target.value as AdvancedChunkMethod | '');
                    }}
                  >
                    <option value="">{t('precheck.advanced.method.automatic')}</option>
                    {advancedMethods.map((method) => (
                      <option key={method} value={method}>
                        {t(CHUNK_METHOD_LABELS[method])}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
          </details>
        )}

        {/* Silence total sur ce point ne se distingue pas d'un oubli : dire
            pourquoi le panneau n'apparaît pas évite qu'un utilisateur avec
            plusieurs documents en file se demande pourquoi l'un l'a et pas
            l'autre (report supporté : les cas dégradés ont déjà leur propre
            message plus haut). */}
        {advancedMethods.length === 0 && report !== null && !isDegradedReport(report) && (
          <p className={styles.advancedUnavailable}>{t('precheck.advanced.unavailable')}</p>
        )}

        {error != null && (
          <p className={styles.error} role="alert">
            {t('precheck.decision.errorTitle')} — {problemMessageOf(error, t('error.unexpected'))}
          </p>
        )}

        <div className={styles.decisionActions}>
          {onRequestCancel !== undefined && (
            <Button
              variant="ghost"
              className={styles.cancelAction}
              disabled={submitting}
              onClick={onRequestCancel}
            >
              {t('job.cancel.action')}
            </Button>
          )}
          <Button
            loading={submitting}
            onClick={() => {
              onSubmit({
                optimize_for_tables: optimizeForTables,
                heavy_ocr: heavyOcr,
                use_parent_child: useParentChild,
                // `null`, pas la chaîne vide : le contrat d'API dit « aucune
                // consigne », pas « une consigne vide » (le catalogue fermé
                // refuserait cette dernière).
                chunk_method_override: chunkMethodOverride === '' ? null : chunkMethodOverride,
              });
            }}
          >
            {t('precheck.decision.submit')}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Restitution du rapport                                                      */
/* -------------------------------------------------------------------------- */

/** Un rapport dégradé porte déjà son propre message : ni indices, ni sélection fine à proposer. */
function isDegradedReport(report: PrecheckReport): boolean {
  return !report.supported || report.error !== null;
}

function ReportSummary({ report }: { report: PrecheckReport | null }): ReactNode {
  const t = useT();
  const { locale } = useI18n();

  if (report === null) {
    return (
      <div className={styles.degraded}>
        <p className={styles.degradedTitle}>{t('precheck.degradedTitle')}</p>
        <p className={styles.degradedText}>{t('precheck.degradedDescription')}</p>
      </div>
    );
  }

  const degraded = isDegradedReport(report);

  // L'analyse préalable est plafonnée (`MAX_ANALYZED_PAGES`, backend) : sur un
  // document long, « 3 tableaux repérés » veut dire « 3 sur les 24 premières
  // pages », pas « 3 dans le document ». Vrai, mais trompeur tant que la portée
  // n'est pas dite — la métrique Pages la disait déjà, les comptages de
  // tableaux et de figures non. Rien n'est ajouté quand l'échantillon couvre
  // tout le document : ce serait du bruit.
  const sampledPageCount =
    report.pageCount !== null &&
    report.analyzedPageCount > 0 &&
    report.analyzedPageCount < report.pageCount
      ? report.analyzedPageCount
      : null;

  // Sur un scan, la couche texte est légitimement vide : « 0 caractères lus »
  // juste sous « Document scanné détecté » est à la fois redondant et alarmant
  // alors que c'est le cas nominal. On dit donc ce qui va se passer — la
  // reconnaissance optique — plutôt qu'un chiffre nul lu comme un échec.
  const emptyScannedText =
    report.isLikelyScanned && report.textCharacterCount < NEGLIGIBLE_TEXT_CHARACTERS;

  return (
    <div className={styles.report}>
      <div className={styles.reportHead}>
        <div>
          <p className={styles.reportTitle}>{t('precheck.reportTitle')}</p>
          <p className={styles.reportDescription}>{t('precheck.reportDescription')}</p>
        </div>
        <Badge tone={degraded ? 'warning' : 'neutral'}>{t(FORMAT_LABELS[report.format])}</Badge>
      </div>

      {degraded && (
        <div className={styles.degraded}>
          <p className={styles.degradedTitle}>{t('precheck.degradedTitle')}</p>
          <p className={styles.degradedText}>
            {report.error === null
              ? t('precheck.degradedDescription')
              : report.error.message.length > 0
                ? report.error.message
                : t(FAILURE_LABELS[report.error.code])}
          </p>
        </div>
      )}

      {!degraded && (
        <details className={styles.reportDetails} open>
          <summary className={styles.reportDetailsSummary}>
            <span>{t('precheck.report.detailsToggle')}</span>
            <svg
              className={styles.reportDetailsChevron}
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m4 6.5 4 4 4-4" />
            </svg>
          </summary>
          <ul className={styles.metrics} role="list">
            <Metric
              icon="scan"
              tone={report.isLikelyScanned ? 'warning' : 'neutral'}
              title={
                report.isLikelyScanned
                  ? t('precheck.metric.scanned')
                  : t('precheck.metric.scannedNone')
              }
              detail={
                report.isLikelyScanned
                  ? report.scannedPageRatio === null
                    ? undefined
                    : t('precheck.metric.scannedRatio', {
                        percent: formatPercent(report.scannedPageRatio, locale),
                      })
                  : t('precheck.metric.scannedNoneDetail')
              }
            />

            <Metric
              icon="table"
              tone={report.tableCount > 0 ? 'accent' : 'neutral'}
              title={
                report.tableCount > 0
                  ? t('precheck.metric.tablesValue', { count: report.tableCount })
                  : t('precheck.metric.tablesNone')
              }
              detail={
                sampledPageCount === null
                  ? t('precheck.metric.tables')
                  : t('precheck.metric.tablesSampled', { count: sampledPageCount })
              }
            />

            <Metric
              icon="table"
              tone={report.twoColumnPageCount > 0 ? 'accent' : 'neutral'}
              title={
                report.twoColumnPageCount > 0
                  ? t('precheck.metric.columnsValue', { count: report.twoColumnPageCount })
                  : t('precheck.metric.columnsNone')
              }
              detail={t('precheck.metric.columns')}
            />

            <Metric
              icon="figure"
              tone="neutral"
              title={
                report.figureCount > 0
                  ? t('precheck.metric.figuresDetected')
                  : t('precheck.metric.figuresNone')
              }
              detail={
                sampledPageCount === null
                  ? t('precheck.metric.figures')
                  : t('precheck.metric.figuresSampled', { count: sampledPageCount })
              }
            />

            <Metric
              icon="pages"
              tone="neutral"
              title={
                report.pageCount === null
                  ? t('precheck.metric.pagesUnknown')
                  : t('precheck.metric.pagesValue', { count: report.pageCount })
              }
              detail={
                report.analyzedPageCount > 0
                  ? t('precheck.metric.pagesAnalyzed', { count: report.analyzedPageCount })
                  : t('precheck.metric.pages')
              }
            />

            <Metric
              icon="text"
              tone="neutral"
              title={
                emptyScannedText
                  ? t('precheck.metric.textScanned')
                  : t('precheck.metric.textValue', {
                      count: formatInteger(report.textCharacterCount, locale),
                    })
              }
              detail={
                emptyScannedText
                  ? t('precheck.metric.textScannedDetail')
                  : t('precheck.metric.text')
              }
            />

            <li className={styles.metric}>
              <MetricIcon name="language" tone="neutral" />
              <div className={styles.metricText}>
                {report.detectedLanguages.length === 0 ? (
                  <span className={styles.metricTitle}>{t('precheck.metric.languagesEmpty')}</span>
                ) : (
                  <span className={styles.languages}>
                    {report.detectedLanguages.map((language) => (
                      <Badge key={language} tone="accent">
                        {language.toUpperCase()}
                      </Badge>
                    ))}
                  </span>
                )}
                <span className={styles.metricDetail}>{t('precheck.metric.languages')}</span>
              </div>
            </li>
          </ul>
        </details>
      )}
    </div>
  );
}

type MetricIconName = 'scan' | 'table' | 'figure' | 'pages' | 'text' | 'language';
type MetricTone = 'neutral' | 'accent' | 'warning';

function Metric({
  icon,
  tone,
  title,
  detail,
}: {
  icon: MetricIconName;
  tone: MetricTone;
  title: string;
  detail?: string;
}): ReactNode {
  return (
    <li className={styles.metric}>
      <MetricIcon name={icon} tone={tone} />
      <div className={styles.metricText}>
        <span className={styles.metricTitle}>{title}</span>
        {detail !== undefined && <span className={styles.metricDetail}>{detail}</span>}
      </div>
    </li>
  );
}

/** Pictogrammes dessinés ici : aucune bibliothèque d'icônes, aucun CDN. */
function MetricIcon({ name, tone }: { name: MetricIconName; tone: MetricTone }): ReactNode {
  return (
    <span className={cx(styles.metricIcon, styles[tone])} aria-hidden="true">
      <svg viewBox="0 0 20 20" focusable="false" strokeLinecap="round" strokeLinejoin="round">
        {name === 'scan' && (
          <>
            <path d="M3 7V4.5A1.5 1.5 0 0 1 4.5 3H7M13 3h2.5A1.5 1.5 0 0 1 17 4.5V7M17 13v2.5a1.5 1.5 0 0 1-1.5 1.5H13M7 17H4.5A1.5 1.5 0 0 1 3 15.5V13" />
            <path d="M3.5 10h13" />
          </>
        )}
        {name === 'table' && (
          <>
            <rect x="3" y="4.5" width="14" height="11" rx="1.5" />
            <path d="M3 8.5h14M8 8.5v7M12.5 8.5v7" />
          </>
        )}
        {name === 'figure' && (
          <>
            <rect x="3" y="4.5" width="14" height="11" rx="1.5" />
            <path d="m3.5 13 3.75-3.5 3 2.75L13.5 8l3 3.5" />
            <circle cx="7.25" cy="7.75" r="1" />
          </>
        )}
        {name === 'pages' && (
          <>
            <path d="M6.5 3h5L15 6.5V17H6.5z" />
            <path d="M11.5 3v3.5H15M5 5.5v11.25A1.25 1.25 0 0 0 6.25 18H13" />
          </>
        )}
        {name === 'text' && <path d="M5 5h10M5 9h10M5 13h6" />}
        {name === 'language' && (
          <>
            <circle cx="10" cy="10" r="7" />
            <path d="M3.2 10h13.6M10 3a11 11 0 0 1 0 14 11 11 0 0 1 0-14" />
          </>
        )}
      </svg>
    </span>
  );
}

function ReportSkeleton(): ReactNode {
  const t = useT();
  return (
    <div className={styles.review} aria-busy="true">
      <div className={styles.report}>
        <div className={styles.reportHead}>
          <div>
            <p className={styles.reportTitle}>{t('precheck.analyzingTitle')}</p>
            <p className={styles.reportDescription}>{t('precheck.analyzingDescription')}</p>
          </div>
        </div>
        <ul className={styles.metrics} role="list">
          {Array.from({ length: 6 }, (_, index) => (
            <li key={index} className={styles.metric}>
              <Skeleton shape="circle" width="1.75rem" height="1.75rem" />
              <div className={styles.metricText}>
                <Skeleton width="9rem" height="0.7rem" />
                <Skeleton width="5rem" height="0.6rem" />
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
