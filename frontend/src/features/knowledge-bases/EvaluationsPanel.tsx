import { useMutation, useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { isApiError } from '@/api/client';
import {
  createEvaluationMutationOptions,
  knowledgeBaseEvaluationQueryOptions,
  knowledgeBaseEvaluationsQueryOptions,
} from '@/api/evaluations';
import { problemMessageOf } from '@/api/problem';
import { providersQueryOptions } from '@/api/providers';
import type { KnowledgeBaseRead, LlmVendor, LLMProviderStatus } from '@/api/schemas';
import { Badge, type BadgeTone } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { Spinner } from '@/components/Spinner';
import { useToast } from '@/components/Toast';
import { useI18n, useT, type Translate } from '@/i18n';
import { cx } from '@/lib/cx';
import { formatDateTime, formatPercent } from '@/lib/format';

import { useProviderLabel } from './useProviderLabel';
import styles from './EvaluationsPanel.module.css';

/** Bornes du contrat backend (`EvaluationCreate.sample_size`). */
const MIN_SAMPLE_SIZE = 1;
const DEFAULT_SAMPLE_SIZE = 5;

/**
 * Plafond du chemin **synchrone** (`MAX_EVALUATION_SAMPLE_SIZE`,
 * `app/domain/schemas/evaluation.py`) : au-delà, `POST .../evaluations`
 * bascule en asynchrone (`202`, run `pending`, ADR-028) — c'est ce seuil, pas
 * une limite de saisie, qui détermine la mise en scène (bannière d'attente vs
 * bannière d'envoi, cf. `startRun`).
 */
const SYNC_SAMPLE_SIZE_THRESHOLD = 20;

/**
 * Plafond de saisie (`ABSOLUTE_MAX_EVALUATION_SAMPLE_SIZE`, même module) :
 * garde-fou Pydantic statique, pas la borne métier réelle du chemin
 * asynchrone (`Settings.evaluation_async_max_sample_size`, dynamique par
 * déploiement, jamais exposée au client). Un `sample_size` accepté ici peut
 * donc tout de même être refusé par le serveur (`422`,
 * `kb.evaluations.hint422`) si le plafond déployé est plus bas.
 */
const INPUT_MAX_SAMPLE_SIZE = 1000;

/** Palier au-delà duquel un score est jugé bon, puis acceptable. */
const GOOD_SCORE = 0.75;
const FAIR_SCORE = 0.5;

/**
 * Teinte d'un ratio `0..1`, partagée par la barre de score et le badge d'un
 * item : une même note doit avoir la même couleur partout dans le panneau.
 * Sous-ensemble volontaire de `BadgeTone` — jamais `neutral` ni `accent`, qui
 * ne diraient rien de la qualité mesurée.
 */
function scoreTone(ratio: number): Extract<BadgeTone, 'success' | 'warning' | 'danger'> {
  if (ratio >= GOOD_SCORE) return 'success';
  if (ratio >= FAIR_SCORE) return 'warning';
  return 'danger';
}

/**
 * Conseil propre au statut d'un refus.
 *
 * `problemMessageOf` restitue déjà le `detail` du backend, qui est exact mais
 * technique. Ces phrases disent quoi *faire* — ce que le contrat REST ne dit
 * pas et que l'utilisateur ne peut pas déduire d'un code HTTP.
 */
function runErrorHint(error: unknown, t: Translate): string | undefined {
  if (!isApiError(error)) return undefined;
  switch (error.status) {
    case 403:
      return t('kb.evaluations.hint403');
    case 409:
      return t('kb.evaluations.hint409');
    case 422:
      return t('kb.evaluations.hint422');
    default:
      return undefined;
  }
}

export interface EvaluationsPanelProps {
  base: KnowledgeBaseRead;
  /**
   * Le principal peut-il engager une dépense sur cette base ? Le backend exige
   * le rôle `editor` pour lancer un run (budget LLM réel) : la garde côté
   * client n'est qu'une politesse, le refus reste au serveur.
   */
  canWrite: boolean;
  /**
   * Fournisseurs pour lesquels le compte courant a déclaré sa propre clé LLM
   * (`GET /me/llm-keys`, comptes réels uniquement — un compte éphémère n'en a jamais,
   * et de toute façon `EvaluationService._authorize_spending` lui refuse tout
   * run). Détermine si le modèle choisi dans la modale de lancement
   * s'applique réellement : la dépense d'un run suit la même table de vérité
   * que le chat (`resolve_llm_funding`, réutilisée telle quelle par
   * `EvaluationService._fund`) — c'est la clé du **demandeur** (le compte qui
   * clique « Lancer le run »), jamais celle du propriétaire de la base, qui
   * décide. Dès qu'elle n'est pas personnelle, le backend impose son propre
   * modèle (`platform_llm_model_id`, addendum 2026-09-21 à ADR-054) — le
   * sélecteur reste utilisable dans tous les cas, mais son choix est
   * silencieusement ignoré hors de cet ensemble. Ensemble vide et non
   * `undefined` : un appel en cours de chargement ne doit jamais laisser
   * croire, même temporairement, qu'un choix s'applique alors que ce n'est
   * pas encore su.
   */
  personalKeyVendors: ReadonlySet<LlmVendor>;
  /**
   * Ouvre l'édition de la base — activer l'évaluation est une option de
   * `KnowledgeBaseForm`, pas un geste que ce panneau sait faire lui-même.
   * `undefined` pour qui n'a de toute façon pas la main sur la configuration :
   * l'état vide « désactivée » reste alors sans bouton, la phrase seule.
   */
  onRequestEdit?: () => void;
}

/**
 * Rappelle, sous le sélecteur de modèle de la modale de lancement, si le
 * choix affiché s'applique réellement — cf. le commentaire de
 * `EvaluationsPanelProps.personalKeyVendors`. Le second segment (même modèle
 * pour la génération et le jugement) reste vrai dans les deux cas : seule la
 * première phrase varie avec l'honnêteté du choix. `undefined` (catalogue pas
 * encore chargé, ou vide) retombe sur la chaîne vide plutôt que d'affirmer
 * quoi que ce soit sur un fournisseur qu'on ne connaît pas encore.
 */
function modelHintFor(
  provider: LLMProviderStatus | undefined,
  personalKeyVendors: ReadonlySet<LlmVendor>,
  forcedModelId: string | null | undefined,
  t: Translate,
): string {
  if (provider === undefined) return '';
  // Aucun modèle imposé par le serveur : le choix s'applique toujours, rien à rappeler.
  if (forcedModelId === null) return '';
  const vendor = t(`settings.llmKeys.vendor.${provider.vendor}`);
  return personalKeyVendors.has(provider.vendor)
    ? t('kb.evaluations.modelAppliesHint', { vendor })
    : t('kb.evaluations.modelOverriddenHint', { vendor });
}

/**
 * Évaluations d'une base : historique des runs et lancement d'un nouveau run.
 *
 * Le lancement est **synchrone côté façade** et facturé : c'est ce qui dicte la
 * mise en scène de ce panneau — une confirmation explicite dans une modale,
 * puis un état d'attente qui annonce la durée au lieu de faire tourner un
 * sablier muet.
 */
export function EvaluationsPanel({
  base,
  canWrite,
  personalKeyVendors,
  onRequestEdit,
}: EvaluationsPanelProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const { notify } = useToast();
  const providerLabel = useProviderLabel();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [sampleSize, setSampleSize] = useState(DEFAULT_SAMPLE_SIZE);
  const [chosenProviderId, setChosenProviderId] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const enabled = base.evaluation_enabled;

  const history = useQuery({ ...knowledgeBaseEvaluationsQueryOptions(base.id), enabled });
  // Chargé dès que l'historique l'est, pas seulement pour `canWrite` : un
  // lecteur seul doit lui aussi voir un libellé lisible sur chaque run passé,
  // pas seulement celui qui peut ouvrir la modale de lancement.
  const providers = useQuery({ ...providersQueryOptions(), enabled });
  const run = useMutation(createEvaluationMutationOptions(base.id));

  const availableProviders = (providers.data?.llm ?? []).filter((provider) => provider.available);
  // Sélection dérivée plutôt qu'un effet de synchronisation : le premier
  // fournisseur servi fait office de défaut jusqu'à ce qu'on en choisisse un.
  const providerId =
    chosenProviderId !== '' ? chosenProviderId : (availableProviders[0]?.provider_id ?? '');
  const selectedProvider = availableProviders.find(
    (provider) => provider.provider_id === providerId,
  );

  const runs = history.data ?? [];
  const runHint = run.isError ? runErrorHint(run.error, t) : undefined;
  // Connu avant même la réponse : c'est `sample_size`, pas le serveur, qui
  // décide du chemin (`app/domain/schemas/evaluation.py`). Sert à la fois à
  // la bannière d'attente (ci-dessous) et au toast de fin — jamais déduit du
  // `item_count` renvoyé, qui vaut `0` sur un run tout juste accepté comme sur
  // un run synchrone qui n'a produit aucun item.
  const isAsyncSubmission = sampleSize > SYNC_SAMPLE_SIZE_THRESHOLD;

  const startRun = async (): Promise<void> => {
    if (providerId === '') return;
    // La modale se retire avant l'attente : le run dure, et garder une modale
    // bloquante pendant une minute empêcherait de consulter le reste de la
    // page pour rien. L'attente est racontée dans le panneau.
    setDialogOpen(false);
    try {
      const created = await run.mutateAsync({
        sample_size: sampleSize,
        llm_provider_id: providerId,
      });
      setExpandedId(created.id);
      // `created.status === 'pending'` sur le chemin asynchrone (`202`) : le
      // run vient d'être accepté, pas terminé — un toast de succès qui
      // annoncerait des questions notées serait faux. Son historique
      // (`kb.evaluations.status.pending`) et le détail déplié racontent la
      // suite au fil du polling, sans que ce toast n'ait besoin d'y revenir.
      notify(
        created.status === 'pending'
          ? {
              tone: 'success',
              title: t('kb.evaluations.acceptedTitle'),
              description: t('kb.evaluations.acceptedDescription', { count: sampleSize }),
            }
          : {
              tone: 'success',
              title: t('kb.evaluations.successTitle'),
              description: t('kb.evaluations.successDescription', { count: created.item_count }),
            },
      );
    } catch {
      // `run.error` porte déjà le refus : la bannière du panneau le raconte,
      // avec le conseil correspondant à son statut.
    }
  };

  return (
    <Card>
      <CardHeader
        eyebrow={t('kb.detail.section.quality')}
        title={t('kb.evaluations.title')}
        description={t('kb.evaluations.description')}
        actions={
          enabled && canWrite ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={run.isPending}
              onClick={() => {
                run.reset();
                setDialogOpen(true);
              }}
            >
              {t('kb.evaluations.launch')}
            </Button>
          ) : undefined
        }
      />

      {!enabled ? (
        <EmptyState
          compact
          illustration="lock"
          title={t('kb.evaluations.disabledTitle')}
          description={t('kb.evaluations.disabledDescription')}
          // Activer l'évaluation est une option de la base, pas de ce panneau
          // (`KnowledgeBaseForm`) — sans prise sur la configuration (lecteur,
          // ou `onRequestEdit` non câblé), la phrase reste seule.
          action={
            canWrite && onRequestEdit !== undefined ? (
              <Button variant="secondary" size="sm" onClick={onRequestEdit}>
                {t('kb.detail.edit')}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          {run.isPending && (
            <div className={styles.running} role="status">
              <Spinner className={styles.runningSpinner} />
              <div className={styles.runningText}>
                <p className={styles.runningTitle}>
                  {t(
                    isAsyncSubmission
                      ? 'kb.evaluations.submittingTitle'
                      : 'kb.evaluations.runningTitle',
                  )}
                </p>
                <p className={styles.runningHint}>
                  {t(
                    isAsyncSubmission
                      ? 'kb.evaluations.submittingHint'
                      : 'kb.evaluations.runningHint',
                    { count: sampleSize },
                  )}
                </p>
              </div>
            </div>
          )}

          {run.isError && (
            <div className={styles.failure} role="alert">
              <p className={styles.failureTitle}>{t('kb.evaluations.runErrorTitle')}</p>
              <p className={styles.failureDetail}>
                {problemMessageOf(run.error, t('error.unexpected'))}
              </p>
              {runHint !== undefined && <p className={styles.failureHint}>{runHint}</p>}
            </div>
          )}

          {history.isError ? (
            <p className={styles.error} role="alert">
              {problemMessageOf(history.error, t('error.unexpected'))}
            </p>
          ) : history.isPending ? (
            <ul className={styles.list} aria-busy="true">
              {Array.from({ length: 2 }, (_, index) => (
                <li key={index} className={styles.item}>
                  <div className={styles.summarySkeleton}>
                    <Skeleton width="12rem" />
                    <Skeleton width="8rem" height="0.6rem" />
                  </div>
                  <Skeleton width="9rem" height="1.6rem" />
                </li>
              ))}
            </ul>
          ) : runs.length === 0 ? (
            <EmptyState
              compact
              illustration="search"
              title={t('kb.evaluations.emptyTitle')}
              description={
                canWrite
                  ? t('kb.evaluations.emptyDescription')
                  : t('kb.evaluations.emptyDescriptionReadOnly')
              }
              action={
                canWrite ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      run.reset();
                      setDialogOpen(true);
                    }}
                  >
                    {t('kb.evaluations.launch')}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <ul className={styles.list}>
              {runs.map((summary) => {
                const open = expandedId === summary.id;
                return (
                  <li key={summary.id} className={cx(styles.item, open && styles.itemOpen)}>
                    <button
                      type="button"
                      className={styles.summary}
                      aria-expanded={open}
                      onClick={() => {
                        setExpandedId(open ? null : summary.id);
                      }}
                    >
                      <span className={styles.summaryMain}>
                        <span className={styles.dateRow}>
                          <span className={styles.date}>
                            {formatDateTime(summary.created_at, locale)}
                          </span>
                          {/* `completed` est l'issue attendue de tout run, y
                              compris ceux lancés avant l'existence du chemin
                              asynchrone (ADR-028) : silencieuse. `pending` et
                              `failed` sont les deux seuls écarts qui méritent
                              l'œil, sans lire les barres de score. */}
                          {summary.status !== 'completed' && (
                            <Badge
                              tone={summary.status === 'pending' ? 'accent' : 'danger'}
                              variant="outline"
                            >
                              {t(
                                summary.status === 'pending'
                                  ? 'kb.evaluations.status.pending'
                                  : 'kb.evaluations.status.failed',
                              )}
                            </Badge>
                          )}
                        </span>
                        <span className={styles.meta}>
                          <span>{providerLabel(summary.llm_provider_id)}</span>
                          <span>{t('kb.evaluations.items', { count: summary.item_count })}</span>
                        </span>
                      </span>

                      <span className={styles.meters}>
                        <ScoreMeter
                          label={t('kb.evaluations.judgeScore')}
                          ratio={summary.average_judge_score}
                          locale={locale}
                          emptyLabel={t('kb.evaluations.scoreUnavailable')}
                        />
                        <ScoreMeter
                          label={t('kb.evaluations.recall')}
                          ratio={summary.retrieval_recall_rate}
                          locale={locale}
                          emptyLabel={t('kb.evaluations.scoreUnavailable')}
                        />
                      </span>

                      <svg
                        className={styles.chevron}
                        viewBox="0 0 16 16"
                        aria-hidden="true"
                        focusable="false"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M4 6.5 8 10.5l4-4" />
                      </svg>
                    </button>

                    {open && <RunItems knowledgeBaseId={base.id} evaluationId={summary.id} />}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false);
        }}
        title={t('kb.evaluations.dialogTitle')}
        description={t('kb.evaluations.dialogDescription')}
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setDialogOpen(false);
              }}
            >
              {t('form.cancel')}
            </Button>
            <Button
              disabled={providerId === ''}
              onClick={() => {
                void startRun();
              }}
            >
              {t('kb.evaluations.submit')}
            </Button>
          </>
        }
      >
        {providers.isPending ? (
          <div className={styles.dialogForm} aria-busy="true">
            <Skeleton height="3rem" />
            <Skeleton height="3rem" />
          </div>
        ) : availableProviders.length === 0 ? (
          <p className={styles.dialogNotice}>{t('kb.evaluations.noProvider')}</p>
        ) : (
          <div className={styles.dialogForm}>
            <Field
              label={t('kb.evaluations.sampleSize')}
              hint={t(
                isAsyncSubmission
                  ? 'kb.evaluations.sampleSizeHintAsync'
                  : 'kb.evaluations.sampleSizeHint',
              )}
              required
            >
              {(field) => (
                <Input
                  {...field}
                  type="number"
                  min={MIN_SAMPLE_SIZE}
                  max={INPUT_MAX_SAMPLE_SIZE}
                  step={1}
                  value={sampleSize}
                  onChange={(event) => {
                    const parsed = Number.parseInt(event.target.value, 10);
                    if (Number.isNaN(parsed)) return;
                    setSampleSize(
                      Math.min(INPUT_MAX_SAMPLE_SIZE, Math.max(MIN_SAMPLE_SIZE, parsed)),
                    );
                  }}
                />
              )}
            </Field>

            <Field
              label={t('kb.evaluations.model')}
              hint={modelHintFor(
                selectedProvider,
                personalKeyVendors,
                providers.data?.llm_forced_model_id,
                t,
              )}
              required
            >
              {(field) => (
                <Select
                  {...field}
                  value={providerId}
                  onChange={(event) => {
                    setChosenProviderId(event.target.value);
                  }}
                >
                  {availableProviders.map((provider) => (
                    <option key={provider.provider_id} value={provider.provider_id}>
                      {provider.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            <p className={styles.dialogWarning}>{t('kb.evaluations.costWarning')}</p>
          </div>
        )}
      </Dialog>
    </Card>
  );
}

/**
 * Score `0..1` en barre + pourcentage.
 *
 * `null` n'est pas zéro : un run sans item n'a pas obtenu une mauvaise note, il
 * n'a rien mesuré. La distinction est explicite à l'écran, jamais une barre
 * vide qui se lirait comme un échec.
 */
function ScoreMeter({
  label,
  ratio,
  locale,
  emptyLabel,
}: {
  label: string;
  ratio: number | null;
  locale: string;
  emptyLabel: string;
}): ReactNode {
  if (ratio === null) {
    return (
      <span className={styles.meter}>
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
    <span className={styles.meter}>
      <span className={styles.meterHead}>
        <span className={styles.meterLabel}>{label}</span>
        <span className={cx(styles.meterValue, styles[tone])}>
          {formatPercent(clamped, locale)}
        </span>
      </span>
      {/* Redondance graphique du pourcentage déjà lisible juste au-dessus :
          masquée aux lecteurs d'écran plutôt que répétée. */}
      <span className={styles.meterTrack} aria-hidden="true">
        <span
          className={cx(styles.meterFill, styles[tone])}
          style={{ inlineSize: `${String(clamped * 100)}%` }}
        />
      </span>
    </span>
  );
}

/** Items d'un run — chargés au dépliage, jamais avant. */
function RunItems({
  knowledgeBaseId,
  evaluationId,
}: {
  knowledgeBaseId: string;
  evaluationId: string;
}): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const { data, isPending, isError, error } = useQuery(
    knowledgeBaseEvaluationQueryOptions(knowledgeBaseId, evaluationId),
  );

  if (isError) {
    return (
      <p className={styles.error} role="alert">
        {problemMessageOf(error, t('error.unexpected'))}
      </p>
    );
  }

  if (isPending) {
    return (
      <div className={styles.details} aria-busy="true">
        <Skeleton height="6rem" />
        <Skeleton height="6rem" />
      </div>
    );
  }

  // Run asynchrone (ADR-028) encore en cours : ses items n'existent pas
  // encore, `run_pending_evaluation` les écrit tous en un seul lot à la
  // résolution — jamais progressivement. `knowledgeBaseEvaluationQueryOptions`
  // réinterroge déjà en tâche de fond ; ce bloc ne fait que le raconter, avec
  // la même grammaire visuelle que la bannière de lancement (`styles.running`).
  if (data.status === 'pending') {
    return (
      <div className={styles.running} role="status">
        <Spinner className={styles.runningSpinner} />
        <div className={styles.runningText}>
          <p className={styles.runningTitle}>{t('kb.evaluations.itemsPendingTitle')}</p>
          <p className={styles.runningHint}>{t('kb.evaluations.itemsPendingHint')}</p>
        </div>
      </div>
    );
  }

  if (data.status === 'failed') {
    return (
      <p className={styles.detailsEmpty} role="alert">
        {t('kb.evaluations.itemsFailed')}
      </p>
    );
  }

  const items = data.items ?? [];

  if (items.length === 0) {
    return <p className={styles.detailsEmpty}>{t('kb.evaluations.itemsEmpty')}</p>;
  }

  return (
    <ol className={styles.details}>
      {items.map((item, index) => (
        <li key={item.id} className={styles.detail}>
          <div className={styles.detailHead}>
            <span className={styles.detailIndex}>
              {t('kb.evaluations.item.index', { index: index + 1 })}
            </span>
            <span className={styles.detailBadges}>
              <Badge
                tone={item.retrieval_rank == null ? 'danger' : 'success'}
                variant="outline"
                title={t('kb.evaluations.item.rankTitle')}
              >
                {item.retrieval_rank == null
                  ? t('kb.evaluations.item.rankMissing')
                  : t('kb.evaluations.item.rank', { rank: item.retrieval_rank })}
              </Badge>
              <Badge tone={scoreTone(item.judge_score)}>
                {t('kb.evaluations.item.judge', {
                  score: formatPercent(item.judge_score, locale),
                })}
              </Badge>
            </span>
          </div>

          <p className={styles.question}>{item.question}</p>

          <dl className={styles.answers}>
            <div className={styles.answer}>
              <dt>{t('kb.evaluations.item.reference')}</dt>
              <dd>{item.reference_answer}</dd>
            </div>
            <div className={styles.answer}>
              <dt>{t('kb.evaluations.item.generated')}</dt>
              <dd>{item.generated_answer}</dd>
            </div>
            <div className={cx(styles.answer, styles.rationale)}>
              <dt>{t('kb.evaluations.item.rationale')}</dt>
              <dd>{item.judge_rationale}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ol>
  );
}
