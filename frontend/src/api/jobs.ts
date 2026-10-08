/**
 * Jobs d'ingestion — `GET /v1/jobs/{job_id}`, `DELETE /v1/jobs/{job_id}`.
 *
 * **La machine à états n'est jamais dérivée côté client.** `job.state` est la
 * seule vérité : l'UI l'affiche, elle ne la devine pas (pas de « si le job a
 * été créé il y a 3 s alors il doit être en train de chunker »).
 *
 * Le rythme d'interrogation, lui, est bien une décision cliente : cf.
 * `jobQueryOptions`. `CANCELLABLE_STATES` en est une autre, dans le même
 * esprit que `TERMINAL_STATES` ci-dessous : ce n'est pas une progression
 * inventée, c'est la copie d'une règle serveur figée (quels états acceptent
 * quelle action), qui sert uniquement à ne pas afficher une action vouée à un
 * `409` systématique.
 */
import { mutationOptions, queryOptions } from '@tanstack/react-query';

import { apiClient } from './client';
import type { IngestionJobRead, JobState } from './schemas';

export const JOBS_PATH = '/v1/jobs';

export const jobKeys = {
  all: ['jobs'] as const,
  detail: (jobId: string) => ['jobs', jobId] as const,
};

/** États où plus rien ne bougera : le backend n'y touchera plus. */
const TERMINAL_STATES: ReadonlySet<JobState> = new Set<JobState>([
  'completed',
  'failed',
  'cancelled',
]);

/**
 * États depuis lesquels l'annulation utilisateur est acceptée — miroir de
 * `CANCELLABLE_STATES` (`backend/app/domain/state_machine.py`).
 *
 * Ce n'est **pas** simplement « non terminal » : `prechecking`, `chunking`,
 * `embedding` et `indexing` sont eux aussi non terminaux mais ne sont pas
 * annulables — le backend y répond `409` à coup sûr, pas par exception. Un
 * bouton visible sur ces états échouerait systématiquement, pas seulement
 * dans une fenêtre de course.
 */
const CANCELLABLE_STATES: ReadonlySet<JobState> = new Set<JobState>(['queued', 'awaiting_review']);

/** États d'attente courte : le suivant arrive vite, on regarde souvent. */
const FAST_STATES: ReadonlySet<JobState> = new Set<JobState>(['queued', 'prechecking']);

const FAST_INTERVAL_MS = 1_200;
const SLOW_INTERVAL_MS = 2_500;

export function isTerminalJobState(state: JobState): boolean {
  return TERMINAL_STATES.has(state);
}

export function isCancellableJobState(state: JobState): boolean {
  return CANCELLABLE_STATES.has(state);
}

/**
 * Annulation — `DELETE /v1/jobs/{job_id}`.
 *
 * Répond `204` sans corps sur succès (le job passe à `cancelled`), `409` si
 * l'état courant n'est plus annulable (course entre le rendu du bouton et le
 * clic, ou état déjà terminal). L'appelant relit l'état via `jobQueryOptions`
 * plutôt que d'attendre un corps de réponse qui n'existe pas.
 */
export function cancelJobMutationOptions(jobId: string) {
  return mutationOptions({
    mutationKey: ['jobs', jobId, 'cancel'] as const,
    mutationFn: () => apiClient.delete<undefined>(`${JOBS_PATH}/${jobId}`),
  });
}

export interface JobQueryConfig {
  /**
   * Une décision de precheck vient d'être transmise pour ce job.
   *
   * `awaiting_review` est un point d'arrêt : le job attend un humain, le
   * réinterroger n'apprend rien et le polling s'y coupe. Mais après le `PUT
   * …/precheck/decision`, la reprise est asynchrone — l'état peut rester
   * `awaiting_review` quelques centaines de millisecondes. Sans ce drapeau, le
   * polling resterait éteint et l'écran se figerait juste après la validation.
   */
  resuming?: boolean;
  /** Interrompt le suivi (item retiré de la file, écran démonté). */
  enabled?: boolean;
}

/**
 * Suivi d'un job avec un rythme adapté à son état.
 *
 * Trois régimes, pour ne pas marteler la façade pendant qu'un GPU travaille :
 *  - états d'attente courte (`queued`, `prechecking`) : ~1,2 s ;
 *  - états de traitement (`chunking`, `embedding`, `indexing`) : ~2,5 s ;
 *  - `awaiting_review` et états terminaux : **plus aucune requête**.
 */
export function jobQueryOptions(jobId: string, config: JobQueryConfig = {}) {
  const { resuming = false, enabled = true } = config;

  return queryOptions({
    queryKey: jobKeys.detail(jobId),
    queryFn: ({ signal }) => apiClient.get<IngestionJobRead>(`${JOBS_PATH}/${jobId}`, { signal }),
    enabled,
    // L'état d'un job change sans que la SPA n'y soit pour rien : ce qui est en
    // cache est périmé dès qu'il est lu.
    staleTime: 0,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      if (state === undefined) return FAST_INTERVAL_MS;
      if (TERMINAL_STATES.has(state)) return false;
      if (state === 'awaiting_review') return resuming ? FAST_INTERVAL_MS : false;
      return FAST_STATES.has(state) ? FAST_INTERVAL_MS : SLOW_INTERVAL_MS;
    },
  });
}
