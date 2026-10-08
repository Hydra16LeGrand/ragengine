/**
 * Traduction de la machine à états d'un job en étapes affichables.
 *
 * ## Règle du fichier
 *
 * `job.state` entre, une liste d'étapes sort. **Rien d'autre n'entre** : ni
 * horodatage, ni `progress_percent`, ni mémoire d'un état précédent. Une étape
 * n'est « franchie » que parce que le backend a annoncé un état postérieur,
 * jamais parce que le client a estimé qu'il était temps.
 *
 * ## Pourquoi `progress_percent` est ignoré
 *
 * Le champ existe et vaut `0` pendant tout le traitement : le moteur
 * d'ingestion ne publie pas d'avancement fiable (ADR-012). L'afficher tel quel
 * montrerait une barre figée à 0 % pendant plusieurs minutes ; l'interpoler
 * inventerait une durée. On ne montre donc que des faits : l'étape en cours et
 * celles qui sont derrière.
 *
 * ## Pourquoi l'échec ne désigne pas d'étape
 *
 * `failed` remplace l'état de travail : le job ne dit plus *où* il en était. On
 * pourrait retenir le dernier état observé côté client, mais les relevés sont
 * espacés de 1 à 3 secondes et le pointage serait faux dès qu'une étape passe
 * entre deux relevés. L'écran affiche donc l'échec pour ce qu'il est — un arrêt
 * avec un message — plutôt qu'une étape désignée à tort.
 */
import type { ProgressStep, StepStatus } from '@/components/StepProgress';
import type { TranslationKey } from '@/i18n';
import type { JobState } from '@/api/schemas';

/** Une étape d'écran et les états serveur qu'elle recouvre. */
interface StepDefinition {
  id: string;
  labelKey: TranslationKey;
  /** États pendant lesquels cette étape est celle en cours. */
  states: readonly JobState[];
}

/**
 * Le parcours complet, précheck et validation humaine compris.
 *
 * `queued` et `prechecking` sont fondus en une seule étape « Analyse » : la
 * distinction (attendre un worker / lire le document) n'a aucune conséquence
 * pour l'utilisateur, qui ne peut agir sur ni l'un ni l'autre.
 */
const STEPS: readonly StepDefinition[] = [
  { id: 'precheck', labelKey: 'job.step.precheck', states: ['queued', 'prechecking'] },
  { id: 'review', labelKey: 'job.step.review', states: ['awaiting_review'] },
  { id: 'chunking', labelKey: 'job.step.chunking', states: ['chunking'] },
  { id: 'embedding', labelKey: 'job.step.embedding', states: ['embedding'] },
  { id: 'indexing', labelKey: 'job.step.indexing', states: ['indexing'] },
];

/** Libellé i18n de l'état courant, affiché sous l'étape en cours. */
export const JOB_STATE_LABELS: Record<JobState, TranslationKey> = {
  queued: 'job.state.queued',
  prechecking: 'job.state.prechecking',
  awaiting_review: 'job.state.awaiting_review',
  chunking: 'job.state.chunking',
  embedding: 'job.state.embedding',
  indexing: 'job.state.indexing',
  completed: 'job.state.completed',
  failed: 'job.state.failed',
  cancelled: 'job.state.cancelled',
};

export interface BuildStepsInput {
  state: JobState;
  translate: (key: TranslationKey) => string;
}

/**
 * Construit les étapes à afficher pour l'état courant.
 *
 * `completed` marque tout comme franchi ; `failed` et `cancelled` laissent
 * toutes les étapes en attente (cf. docstring de module) — l'appelant affiche
 * alors un bloc d'échec, pas cette progression.
 */
export function buildJobSteps({ state, translate }: BuildStepsInput): ProgressStep[] {
  const currentIndex = STEPS.findIndex((step) => step.states.includes(state));

  return STEPS.map((step, index) => {
    let status: StepStatus = 'upcoming';
    if (state === 'completed') {
      status = 'done';
    } else if (currentIndex !== -1) {
      if (index < currentIndex) status = 'done';
      else if (index === currentIndex) status = 'current';
    }

    const description = status === 'current' ? translate(JOB_STATE_LABELS[state]) : undefined;

    return {
      id: step.id,
      label: translate(step.labelKey),
      status,
      ...(description === undefined ? {} : { description }),
    };
  });
}
