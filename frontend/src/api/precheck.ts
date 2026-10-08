/**
 * Lecture typée du rapport de precheck.
 *
 * ## Pourquoi ce fichier existe
 *
 * `DocumentRead.precheck_report` est typé dans l'OpenAPI (`PrecheckReportRead`,
 * ADR-070) : les champs à valeur par défaut y sont **optionnels**, parce qu'un
 * rapport écrit par une version plus ancienne du precheck ne les porte pas (le
 * backend ne les ajoute jamais à la réponse). `precheck_decision`, lui, reste
 * un dictionnaire JSONB libre.
 *
 * Plutôt que de lire le type généré tel quel, on normalise : un champ absent
 * doit produire un écran dégradé lisible, jamais un `undefined` qui traverse
 * l'UI jusqu'à un `NaN` affiché à l'utilisateur — même choix, et même raison,
 * que `problem.ts` pour les membres d'extension RFC 9457. Les familles de
 * format et les codes d'erreur ci-dessous sont, eux, vérifiés à la compilation
 * contre le schéma généré : un format retiré du backend casse le build.
 *
 * La forme validée reproduit `backend/app/domain/schemas/precheck.py`. Elle
 * est **tolérante par construction** : tout champ manquant retombe sur une
 * valeur neutre, et `supported: false` (ou `error` renseigné) signale un
 * rapport dégradé que l'écran d'aiguillage doit expliquer plutôt que masquer.
 */

import type { components } from './schema';

type WirePrecheckSchemas = components['schemas'];

/**
 * Familles de format reconnues par l'analyse préalable (`PrecheckFormat`).
 *
 * Sous-ensemble volontaire de l'énumération backend : seules y figurent les
 * familles dont **cet écran** fait quelque chose de différent. `image` en est
 * absent depuis l'origine (normalisé en `unsupported` par `parsePrecheckReport`,
 * et le résultat est le même partout : ni proposition, ni option avancée).
 * `presentation` y est, lui, parce qu'il change deux choses visibles — le badge
 * de format, et le fait que ce document ne reçoive aucun template (Phase 15).
 */
export type PrecheckFormat = 'pdf' | 'spreadsheet' | 'presentation' | 'unsupported';

/** Causes normalisées d'un rapport dégradé (`PrecheckErrorCode`). */
export type PrecheckErrorCode = 'unsupported_format' | 'corrupted_file' | 'extraction_failed';

export interface PrecheckFailure {
  code: PrecheckErrorCode | 'unknown';
  message: string;
}

/** Rapport de precheck normalisé, exploitable sans vérification supplémentaire. */
export interface PrecheckReport {
  supported: boolean;
  format: PrecheckFormat;
  sizeBytes: number;
  /** Pages du document. `null` pour un format non paginé (tableur). */
  pageCount: number | null;
  /** Pages réellement inspectées — l'analyse est bornée sur les gros documents. */
  analyzedPageCount: number;
  textCharacterCount: number;
  isLikelyScanned: boolean;
  /** Part des pages analysées jugées numérisées, `null` si la notion ne s'applique pas. */
  scannedPageRatio: number | null;
  tableCount: number;
  figureCount: number;
  /**
   * Pages analysées portant deux sections de texte côte à côte (ADR-042).
   *
   * Toujours 0 hors PDF. Signal d'aiguillage : l'analyse de mise en page
   * complète y rend le texte ligne par ligne en traversant les colonnes.
   */
  twoColumnPageCount: number;
  /** Codes ISO 639-1, par couverture décroissante (ADR-003). */
  detectedLanguages: string[];
  /** Renseigné dès que le rapport est dégradé. */
  error: PrecheckFailure | null;
}

/** Échoue à la compilation si une valeur d'ici n'existe plus dans le schéma du backend. */
type MustExistInBackend<Local extends Wire, Wire extends string> = Local;
export type PrecheckFormatsAreServed = MustExistInBackend<
  PrecheckFormat,
  WirePrecheckSchemas['PrecheckFormat']
>;
export type PrecheckErrorCodesAreServed = MustExistInBackend<
  PrecheckErrorCode,
  WirePrecheckSchemas['PrecheckErrorCode']
>;

const FORMATS: readonly PrecheckFormat[] = ['pdf', 'spreadsheet', 'presentation', 'unsupported'];
const ERROR_CODES: readonly PrecheckErrorCode[] = [
  'unsupported_format',
  'corrupted_file',
  'extraction_failed',
];

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function asCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
}

function asOptionalCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function asRatio(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if (value < 0 || value > 1) return null;
  return value;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

function asFailure(value: unknown): PrecheckFailure | null {
  const record = asRecord(value);
  if (record === null) return null;

  const { code, message } = record;
  const normalizedCode = ERROR_CODES.find((candidate) => candidate === code) ?? 'unknown';
  return {
    code: normalizedCode,
    message: typeof message === 'string' ? message : '',
  };
}

/**
 * Normalise `document.precheck_report`.
 *
 * Rend `null` — et non un rapport vide — quand le champ est absent : « pas
 * encore analysé » et « analysé, rien trouvé » sont deux écrans différents.
 */
export function parsePrecheckReport(raw: unknown): PrecheckReport | null {
  const record = asRecord(raw);
  if (record === null) return null;

  const format = FORMATS.find((candidate) => candidate === record.format) ?? 'unsupported';

  return {
    supported: asBoolean(record.supported, false),
    format,
    sizeBytes: asCount(record.size_bytes),
    pageCount: asOptionalCount(record.page_count),
    analyzedPageCount: asCount(record.analyzed_page_count),
    textCharacterCount: asCount(record.text_character_count),
    isLikelyScanned: asBoolean(record.is_likely_scanned, false),
    scannedPageRatio: asRatio(record.scanned_page_ratio),
    tableCount: asCount(record.table_count),
    figureCount: asCount(record.figure_count),
    twoColumnPageCount: asCount(record.two_column_page_count),
    detectedLanguages: asStringList(record.detected_languages),
    error: asFailure(record.error),
  };
}

/** Normalise `document.precheck_decision` (déjà appliquée, en relecture). */
export function parsePrecheckDecision(raw: unknown): {
  optimizeForTables: boolean;
  heavyOcr: boolean;
  useParentChild: boolean;
  chunkMethodOverride: AdvancedChunkMethod | null;
} | null {
  const record = asRecord(raw);
  if (record === null) return null;
  return {
    optimizeForTables: asBoolean(record.optimize_for_tables, false),
    heavyOcr: asBoolean(record.heavy_ocr, false),
    useParentChild: asBoolean(record.use_parent_child, false),
    chunkMethodOverride:
      ADVANCED_CHUNK_METHODS.find((candidate) => candidate === record.chunk_method_override) ??
      null,
  };
}

/* -------------------------------------------------------------------------- */
/* Sélection fine du template (Phase 15)                                       */
/* -------------------------------------------------------------------------- */

/** Templates du catalogue fermé backend (`AdvancedChunkMethod`), dans l'ordre d'affichage. */
const ADVANCED_CHUNK_METHODS = ['naive', 'laws', 'paper', 'manual', 'qa'] as const;

export type AdvancedChunkMethod = (typeof ADVANCED_CHUNK_METHODS)[number];

/**
 * Templates de découpage à proposer en « Paramètres avancés » pour CE document.
 *
 * Le catalogue backend dit ce qui est *permis* ; cette fonction dit ce qui est
 * *pertinent*, document par document, à partir des seuls signaux mesurés
 * (Phase 15, Étape 2). Trois règles, chacune adossée à un chiffre :
 *
 * - **`laws`/`paper`/`manual` : PDF uniquement, et jamais sur des colonnes.**
 *   Mesurés gagnants sur PDF structuré (sur le RGPD réel, `laws` rend 545
 *   fragments de 429 caractères médians contre 248 de 1773 pour `naive`, à
 *   couverture source égale) ; mais `laws` s'effondre à 19,2 % de fragments
 *   dégénérés dès que la mise en page passe à deux colonnes. Le precheck
 *   compte ces pages (`twoColumnPageCount`) : dès qu'il en voit une, ces trois
 *   templates disparaissent du sélecteur.
 * - **Jamais sur un scan** : toutes les mesures ont porté sur des PDF à couche
 *   texte. Restriction de prudence, assumée comme telle — pas un résultat.
 * - **`qa` : jamais sur un PDF.** Mesuré deux fois : sur un Markdown à
 *   sections il rend 4 fragments `Question: … Answer: …` exploitables ; sur un
 *   PDF de FAQ pourtant rédigé en « Q : / R : », le moteur échoue (« Unable to
 *   recognize Q&A structure. », 0 fragment).
 *
 * `naive` n'est jamais listé : c'est déjà le défaut du sélecteur (« routage
 * automatique »), et sur un tableur — le seul format où il diffère du routage
 * — l'utilisateur l'obtient en décochant « Optimiser pour les tableaux »,
 * contrôle qui existe déjà à l'écran.
 *
 * Une liste vide veut dire « pas de sélection fine à proposer ici » : l'écran
 * n'affiche alors aucun panneau, plutôt qu'un panneau vide.
 */
/**
 * Désactivé pour cette version (2026-09-06), toujours `[]` — incident réel :
 * `laws` sur un PDF réel a produit un fragment de 41 264 caractères jamais
 * mesuré aux limites du reranker en aval (`CUDA out of memory`, 6,43 Gio pour
 * un seul lot de reranking le contenant). Le backend rejette désormais aussi
 * `chunk_method_override` (422, `PrecheckDecision`) — décision explicite de
 * l'utilisateur, pas seulement un masquage côté écran. La logique de
 * pertinence par document ci-dessous reste intacte, pour une réactivation
 * après durcissement plutôt qu'une réécriture.
 */
export function advancedChunkMethodsFor(
  _report: PrecheckReport | null,
  _filename: string,
): readonly AdvancedChunkMethod[] {
  return [];
}

/**
 * Décision pré-cochée proposée à l'utilisateur.
 *
 * Le rapport est un **signal d'aide à la décision** : on propose ce qu'il
 * suggère (tableaux détectés ⇒ découpage tabulaire) plutôt que des cases vides
 * qui obligeraient à réinterpréter soi-même les chiffres. L'utilisateur reste
 * libre de contredire la proposition — c'est tout l'objet de cet écran.
 *
 * `heavyOcr` ne suit **plus** `isLikelyScanned` (ADR-031, décision utilisateur
 * du 2026-08-11) : l'analyse de mise en page complète est désormais proposée
 * sur **tout PDF**, scanné ou non. Le motif qui la réservait aux scans est
 * tombé avec ADR-030 — à qualité de texte désormais équivalente (égalité
 * stricte des mots reconnus sur 5 documents sur 7), elle rend l'ordre des
 * fragments exploitable (0 % → 100 %) et supprime les coupures mi-phrase
 * (33-60 % → 0 %), au prix d'une ingestion ×3,8 plus lente. C'est une
 * **proposition d'interface**, pas un changement de contrat : le défaut du
 * schéma d'API reste `heavy_ocr: false` (CONTRIBUTING.md §4).
 *
 * Restreinte au PDF parce que c'est le seul format où l'option décide de
 * quelque chose (`app/ingestion/chunking_profile.py`) :
 * - **tableur** — `optimize_for_tables` y choisit `chunk_method=table`, qui ne
 *   passe pas par l'analyse de mise en page, et `isLikelyScanned` y vaut
 *   toujours `false` ;
 * - **image** — le seul profil que le moteur accepte est le profil vide : les
 *   deux options y sont des no-op documentés, la proposer serait mentir.
 *   (Le backend classe l'image en `format: "image"`, absent de l'union ci-
 *   dessus donc normalisé en `unsupported` — dans les deux cas, pas `pdf`.)
 *
 * Décocher la case ne peut pas condamner un scan à `Plain Text` : le backend
 * impose `DeepDOC` dès qu'`is_likely_scanned` est vrai, quelle que soit la
 * décision reçue (`profile_for_decision`).
 */
export function suggestedDecision(report: PrecheckReport | null): {
  optimizeForTables: boolean;
  heavyOcr: boolean;
} {
  if (report?.supported !== true) {
    return { optimizeForTables: false, heavyOcr: false };
  }
  return {
    optimizeForTables: report.tableCount > 0,
    heavyOcr: report.format === 'pdf' && !readsBetterWithoutLayoutAnalysis(report),
  };
}

/**
 * Le document perd-il plus qu'il ne gagne à l'analyse de mise en page ? (ADR-042)
 *
 * Vrai quand le precheck a vu des **colonnes côte à côte** et **aucun tableau**.
 * Les deux conditions comptent :
 *
 * - les colonnes, parce que c'est là que l'analyse de mise en page se retourne
 *   contre le document — mesuré, elle y rend le texte ligne par ligne en
 *   traversant les colonnes (taux de basculement 0,168 contre 0,021 en mode
 *   direct sur `real-notice-2042.pdf`, pour une lecture correcte à 0,014) ;
 * - l'absence de tableau, parce que le mode direct ne sait pas en reconnaître.
 *   Un document qui porte les deux signaux oppose deux gains réels, et ce n'est
 *   pas à une heuristique de trancher : l'analyse de mise en page y reste
 *   proposée, et l'écran affiche les deux compteurs pour que l'utilisateur
 *   décide — c'est l'architecture d'ADR-031, pas une exception à celle-ci.
 *
 * Ne concerne jamais un scan : le backend impose l'analyse complète dès
 * `is_likely_scanned` (`profile_for_decision`), et le mode direct n'y produit
 * rien du tout — vérifié, 0 fragment et 0 caractère.
 */
export function readsBetterWithoutLayoutAnalysis(report: PrecheckReport | null): boolean {
  if (report?.supported !== true || report.format !== 'pdf') return false;
  return report.twoColumnPageCount > 0 && report.tableCount === 0;
}
