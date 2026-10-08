import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import {
  documentKeys,
  documentQueryOptions,
  precheckDecisionMutationOptions,
} from '@/api/documents';
import {
  cancelJobMutationOptions,
  isCancellableJobState,
  isTerminalJobState,
  jobKeys,
  jobQueryOptions,
} from '@/api/jobs';
import { parsePrecheckReport, suggestedDecision } from '@/api/precheck';
import { problemMessageOf } from '@/api/problem';
import type { IngestionJobRead, JobState, PrecheckDecision } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Dialog } from '@/components/Dialog';
import { Skeleton } from '@/components/Skeleton';
import { StepProgress } from '@/components/StepProgress';
import { useToast } from '@/components/Toast';
import { useT } from '@/i18n';

import { buildJobSteps } from './jobSteps';
import { JobFailurePanel, QualityWarning } from './JobOutcome';
import { PrecheckReview } from './PrecheckReview';
import styles from './Ingestion.module.css';

/**
 * Décision soumise sans clic sur l'écran d'aiguillage, pour une action groupée.
 *
 * - `suggested` — ce que le rapport propose (`suggestedDecision`) ;
 * - `none` — aucune option, c'est-à-dire le refus explicite des propositions.
 *
 * Dans les deux cas le document **est indexé** : ces deux modes décident du
 * *comment*, jamais du *si*. Renoncer à indexer reste l'annulation du job, une
 * action distincte, définitive, et confirmée document par document.
 */
export type AutoDecisionMode = 'suggested' | 'none';

export interface JobTrackerProps {
  documentId: string;
  jobId: string;
  filename: string;
  /** Remonté à chaque changement d'état serveur (jamais déduit localement). */
  onStateChange?: (state: JobState) => void;
  /** Appelé une seule fois, à l'entrée dans un état terminal. */
  onSettled?: (job: IngestionJobRead) => void;
  /**
   * Actions groupées « … pour tous » : soumet automatiquement la décision
   * correspondante dès que ce document atteint `awaiting_review`, sans attendre
   * un clic sur `PrecheckReview`. Une fois transmise, `resuming` (dérivé de
   * `decision.isSuccess`) rallume le polling normalement — l'auto-soumission
   * emprunte exactement le même chemin qu'une validation manuelle, elle ne le
   * contourne pas. `null` : ce document reste à la main de l'utilisateur.
   */
  autoDecision?: AutoDecisionMode | null;
  /** Issue de la soumission déclenchée par `autoDecision` — jamais pour une validation manuelle. */
  onAutoDecisionSettled?: (outcome: 'success' | 'error') => void;
}

/**
 * Suivi d'un document versé, de l'analyse préalable à l'indexation.
 *
 * Toute la logique d'affichage part de `job.state` — un seul `switch`, aucune
 * mémoire locale de « là où on en était ». Le composant ne fait donc jamais
 * avancer une étape de son propre chef : si la façade ne l'a pas dit, l'écran
 * ne le montre pas.
 */
export function JobTracker({
  documentId,
  jobId,
  filename,
  onStateChange,
  onSettled,
  autoDecision = null,
  onAutoDecisionSettled,
}: JobTrackerProps): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [confirmingCancel, setConfirmingCancel] = useState(false);

  // Garde-fou contre un double déclenchement de l'auto-soumission : l'effet
  // qui la déclenche peut se relancer (polling, changement de référence de
  // callback) tant que `decision` n'est pas encore `isPending`/`isSuccess`.
  const autoDecisionTriggered = useRef(false);

  const decision = useMutation({
    ...precheckDecisionMutationOptions(documentId),
    onSuccess: async (document) => {
      // Le document vient de changer d'état côté serveur : on remet en cache la
      // version renvoyée plutôt que de la redemander.
      queryClient.setQueryData(documentKeys.detail(documentId), document);
      await queryClient.invalidateQueries({ queryKey: documentKeys.all });
      if (autoDecisionTriggered.current) onAutoDecisionSettled?.('success');
    },
    onError: () => {
      if (autoDecisionTriggered.current) onAutoDecisionSettled?.('error');
    },
  });

  const cancel = useMutation({
    ...cancelJobMutationOptions(jobId),
    onSuccess: async () => {
      setConfirmingCancel(false);
      // Pas de corps sur un `204` : on relit l'état plutôt que d'en deviner un.
      await queryClient.invalidateQueries({ queryKey: jobKeys.detail(jobId) });
    },
    onError: (cancelError) => {
      // Défense en profondeur : l'état a pu changer entre le rendu du bouton
      // (état annulable) et le clic (409, transition devenue illégale).
      setConfirmingCancel(false);
      notify({
        tone: 'danger',
        title: t('job.cancel.errorTitle'),
        description: problemMessageOf(cancelError, t('error.unexpected')),
      });
    },
  });

  // `resuming` n'est vrai qu'entre la validation et la reprise effective du job.
  // Il rallume le polling que `awaiting_review` avait éteint — sans lui, l'écran
  // resterait figé sur l'écran d'aiguillage déjà validé.
  const job = useQuery(jobQueryOptions(jobId, { resuming: decision.isSuccess }));
  const state = job.data?.state;

  const document = useQuery(documentQueryOptions(documentId, state === 'awaiting_review'));

  const report = useMemo(
    () => parsePrecheckReport(document.data?.precheck_report),
    [document.data?.precheck_report],
  );

  // Notifications de cycle de vie : émises depuis un effet, jamais pendant le
  // rendu, et déduplicées par état pour ne pas rejouer un toast à chaque
  // relevé de polling.
  const notifiedState = useRef<JobState | null>(null);
  useEffect(() => {
    if (state === undefined || notifiedState.current === state) return;
    notifiedState.current = state;
    onStateChange?.(state);
    if (isTerminalJobState(state) && job.data !== undefined) onSettled?.(job.data);
  }, [state, job.data, onStateChange, onSettled]);

  // Actions groupées : soumettent la décision dès que ce document atteint
  // `awaiting_review`, sans intervention de l'utilisateur sur cette ligne
  // précise. Passent par `decision.mutate` — le chemin normal — pour que
  // `resuming` (dérivé de `decision.isSuccess`) rallume le polling comme pour
  // une validation manuelle.
  //
  // `suggested` est le second consommateur de `suggestedDecision`, à garder
  // aligné sur `PrecheckReview` : depuis ADR-031, la proposition inclut
  // `heavy_ocr` sur tout PDF, donc l'acceptation en lot lance l'analyse de mise
  // en page complète (~×3,8 de temps d'ingestion). C'est voulu — le bouton
  // accepte les propositions, il n'en invente pas de plus rapides dans le dos
  // de l'utilisateur.
  //
  // `none` est exactement l'inverse — aucune option — et c'est la seule chose
  // qu'un refus puisse vouloir dire ici : le document est déjà versé, déjà
  // analysé, et l'API n'a qu'une décision à recevoir. Le rapport n'y est donc
  // pas attendu : rien n'en est déduit.
  useEffect(() => {
    if (autoDecision === null) return;
    if (state !== 'awaiting_review') return;
    if (autoDecision === 'suggested' && report === null) return;
    if (autoDecisionTriggered.current) return;
    if (decision.isPending || decision.isSuccess) return;

    autoDecisionTriggered.current = true;
    const suggestion =
      autoDecision === 'suggested'
        ? suggestedDecision(report)
        : { optimizeForTables: false, heavyOcr: false };
    decision.mutate({
      optimize_for_tables: suggestion.optimizeForTables,
      heavy_ocr: suggestion.heavyOcr,
      // Phase 13 : jamais suggérée par le precheck (`PrecheckDecision`,
      // docstring) — une action groupée ne peut donc jamais l'activer, seul un
      // choix explicite sur l'écran d'aiguillage le peut.
      use_parent_child: false,
      // Phase 15, même raison : une sélection fine est par définition un choix
      // délibéré, jamais quelque chose qu'un bouton « pour tous » impose.
      chunk_method_override: null,
    });
  }, [autoDecision, state, report, decision]);

  if (job.isPending) return <TrackerSkeleton />;

  if (job.isError) {
    return (
      <div className={styles.failure} role="alert">
        <p className={styles.failureTitle}>{t('ingest.trackingErrorTitle')}</p>
        <p className={styles.failureText}>{problemMessageOf(job.error, t('error.unexpected'))}</p>
      </div>
    );
  }

  const current = job.data;

  if (current.state === 'failed' || current.state === 'cancelled') {
    return <JobFailure job={current} filename={filename} />;
  }

  const steps = buildJobSteps({ state: current.state, translate: t });
  const cancellable = isCancellableJobState(current.state);

  return (
    <div className={styles.tracker}>
      <StepProgress label={t('job.progress.label')} steps={steps} />

      {/* En attente de validation, l'annulation vit à côté du bouton de
          validation dans `PrecheckReview` — même rangée, action opposée —
          plutôt qu'isolée ici, loin de tout ce qu'on est venu faire sur cet
          écran. Pour tout autre état annulable (aucun bouton d'action
          concurrent à cet endroit), elle reste ici. */}
      {cancellable && current.state !== 'awaiting_review' && (
        <div className={styles.cancelRow}>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setConfirmingCancel(true);
            }}
          >
            {t('job.cancel.action')}
          </Button>
        </div>
      )}

      {current.state === 'awaiting_review' && (
        // La `key` porte **deux** informations, et la seconde n'est pas
        // cosmétique : le document (repartir de zéro d'un document à l'autre)
        // **et** l'arrivée du rapport. `PrecheckReview` initialise ses cases
        // avec `useState(suggestion.…)`, or sa première monture a lieu avant
        // que `document` ne soit chargé (la requête n'est activée qu'à
        // `awaiting_review`) : sans ce second segment, l'état initial est figé
        // sur le rapport `null`, donc sur des cases décochées, et la
        // proposition n'apparaît plus que dans le badge — constaté en pilotant
        // l'interface réelle, invisible au typage comme au lint. Le marqueur
        // est **binaire**, pas l'identité du rapport : il bascule une seule
        // fois, un rafraîchissement ultérieur ne peut pas écraser un choix en
        // cours de l'utilisateur.
        <PrecheckReview
          key={`${documentId}:${report === null ? 'no-report' : 'report'}`}
          report={report}
          filename={filename}
          loading={document.isPending}
          submitting={decision.isPending || decision.isSuccess}
          error={decision.error}
          onSubmit={(payload: PrecheckDecision) => {
            decision.mutate(payload);
          }}
          onRequestCancel={
            cancellable
              ? () => {
                  setConfirmingCancel(true);
                }
              : undefined
          }
        />
      )}

      {current.state === 'completed' && (
        <>
          <p className={styles.completed}>{t('ingest.completedDescription', { name: filename })}</p>
          {current.warning != null && <QualityWarning warning={current.warning} />}
        </>
      )}

      <Dialog
        open={confirmingCancel}
        onClose={() => {
          setConfirmingCancel(false);
        }}
        title={t('job.cancel.title')}
        description={t('job.cancel.description', { name: filename })}
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirmingCancel(false);
              }}
              disabled={cancel.isPending}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={cancel.isPending}
              onClick={() => {
                cancel.mutate();
              }}
            >
              {t('job.cancel.confirm')}
            </Button>
          </>
        }
      />
    </div>
  );
}

/**
 * Échec : la façade ne dit plus à quelle étape le job s'est arrêté (cf.
 * `jobSteps.ts`). On affiche donc le fait — l'arrêt, sa cause, sa
 * réparabilité — plutôt qu'une progression qui désignerait une étape au hasard.
 *
 * La cause elle-même est rendue par `JobOutcome`, partagé avec la liste des
 * documents : depuis la campagne du 2026-08, un échec doit rester consultable
 * après la fin de la session de versement, donc au même format aux deux
 * endroits.
 */
function JobFailure({ job, filename }: { job: IngestionJobRead; filename: string }): ReactNode {
  const t = useT();
  const cancelled = job.state === 'cancelled';

  return (
    <JobFailurePanel
      error={job.error}
      filename={filename}
      title={cancelled ? t('ingest.state.cancelled') : t('ingest.failedTitle')}
      aside={
        job.attempts > 1 ? (
          <Badge tone="neutral">{t('ingest.attempts', { count: job.attempts })}</Badge>
        ) : undefined
      }
    />
  );
}

function TrackerSkeleton(): ReactNode {
  return (
    <div className={styles.tracker} aria-busy="true">
      <div className={styles.trackerSkeleton}>
        <Skeleton height="1.375rem" />
        <Skeleton width="60%" height="0.7rem" />
      </div>
    </div>
  );
}
