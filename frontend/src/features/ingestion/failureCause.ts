/**
 * Lecture humaine des causes d'échec et d'avertissement d'un job d'ingestion.
 *
 * `JobError.message` est, dans le cas le plus fréquent
 * (`code = engine_processing_failed`), le **dump brut du moteur** recopié tel
 * quel par `app/workers/tasks/ingestion.py` :
 *
 *     12:10:44 Page(1~4): [ERROR]429 RESOURCE_EXHAUSTED. {'error': {'code': 429, …
 *
 * Illisible pour l'utilisateur, mais indispensable au diagnostic. Ce module
 * n'en supprime donc rien : il ajoute **au-dessus** un constat et un conseil,
 * quand la cause fait partie de celles réellement observées. Le message brut
 * reste rendu par `JobOutcome`, replié.
 *
 * Les règles portent sur le **contenu du message**, pas sur le code : le
 * moteur range trois causes radicalement différentes (quota d'API épuisé,
 * format non lu, image illisible) sous le même `engine_processing_failed` ou
 * `engine_error`. Une règle par code ne saurait pas les distinguer.
 *
 * Aucune règle ne correspond ⇒ `null`, et l'interface retombe sur le message
 * brut seul. C'est le comportement d'avant ce module : ne rien reconnaître
 * n'est jamais une régression.
 */
import type { JobError, JobWarning } from '@/api/schemas';
import type { TranslationKey } from '@/i18n';

/** Constat + conseil, tous deux traduits par l'appelant. */
export interface OutcomeCause {
  /** Ce qui s'est passé, en une ligne — remplace le titre générique. */
  title: TranslationKey;
  /** Ce que l'utilisateur peut faire. Peut interpoler `{format}`. */
  advice: TranslationKey;
}

interface ErrorRule {
  /** Éprouvée sur le message brut du moteur, pas sur le code. */
  pattern: RegExp;
  cause: OutcomeCause;
}

/**
 * Ordonnées par fréquence observée pendant la campagne de tests manuels du
 * 2026-08 (l'analyse interne des tests manuels §2), la première qui
 * correspond l'emporte.
 */
const ERROR_RULES: readonly ErrorRule[] = [
  {
    // Cause n°1 : 4 échecs distincts du corpus, tous le même quota gratuit
    // épuisé par l'ingestion en lot. `RESOURCE_EXHAUSTED` est le libellé gRPC
    // du fournisseur ; le `429` nu couvre les fournisseurs qui ne renvoient que
    // le statut HTTP.
    pattern: /RESOURCE_EXHAUSTED|\b429\b/i,
    cause: {
      title: 'ingest.cause.quotaExhausted.title',
      advice: 'ingest.cause.quotaExhausted.advice',
    },
  },
  {
    // « Not supported yet! » (pptx) et « file type not supported yet(...) ».
    pattern: /not supported yet/i,
    cause: {
      title: 'ingest.cause.unsupportedFormat.title',
      advice: 'ingest.cause.unsupportedFormat.advice',
    },
  },
  {
    // Remontée par PIL côté moteur sur une image tronquée ou mal encodée.
    pattern: /cannot identify image file/i,
    cause: {
      title: 'ingest.cause.unreadableImage.title',
      advice: 'ingest.cause.unreadableImage.advice',
    },
  },
];

/** La cause reconnue derrière un échec, ou `null` si le message n'en dit rien. */
export function diagnoseJobError(error: JobError): OutcomeCause | null {
  for (const rule of ERROR_RULES) {
    if (rule.pattern.test(error.message)) return rule.cause;
  }
  return null;
}

/**
 * Conseil attaché à un avertissement qualité (ADR-024).
 *
 * Le titre et le constat chiffré existent déjà
 * (`ingest.qualityWarning.code.*`, `JobWarning.message`) : seul manquait le
 * « et maintenant ? ». Un code inconnu — un garde-fou ajouté côté backend sans
 * libellé ici — rend `null` plutôt qu'un conseil générique trompeur.
 */
export function adviceForJobWarning(warning: JobWarning): TranslationKey | null {
  const advice: Record<string, TranslationKey> = {
    zero_chunks: 'ingest.qualityWarning.advice.zero_chunks',
    degenerate_content: 'ingest.qualityWarning.advice.degenerate_content',
    low_source_coverage: 'ingest.qualityWarning.advice.low_source_coverage',
  };
  return advice[warning.code] ?? null;
}

/**
 * Extension en majuscules (`« rapport.PPTX » → « PPTX »`), pour nommer le
 * format dans le conseil plutôt que de laisser l'utilisateur le deviner.
 *
 * Rendue vide plutôt qu'inventée quand le nom n'en porte pas : le libellé qui
 * l'interpole prévoit ce cas.
 */
export function fileExtensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  if (dot <= 0 || dot === filename.length - 1) return '';
  return filename.slice(dot + 1).toUpperCase();
}
