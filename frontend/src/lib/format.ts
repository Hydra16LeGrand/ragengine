/**
 * Formatage présentationnel (dates, tailles, slugs).
 *
 * Aucune dépendance : `Intl` est natif et déjà localisé. La locale est passée
 * explicitement plutôt que lue d'un contexte, pour que ces fonctions restent
 * pures et testables.
 */

/** Date courte lisible : « 28 juil. 2026 ». */
export function formatDate(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(date);
}

/** Date + heure, pour les vues de détail. */
export function formatDateTime(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

const BYTE_UNITS = ['B', 'kB', 'MB', 'GB', 'TB'] as const;

/** Taille de fichier lisible, base 1000 (celle qu'affichent les OS). */
export function formatBytes(bytes: number, locale: string): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';

  let value = bytes;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < BYTE_UNITS.length - 1) {
    value /= 1000;
    unitIndex += 1;
  }

  const formatted = new Intl.NumberFormat(locale, {
    maximumFractionDigits: unitIndex === 0 ? 0 : 1,
  }).format(value);
  return `${formatted} ${BYTE_UNITS[unitIndex] ?? 'B'}`;
}

/** Entier localisé (séparateurs de milliers) : compte de caractères, de pages… */
export function formatInteger(value: number, locale: string): string {
  if (!Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value);
}

/** Ratio `0..1` rendu en pourcentage entier : « 87 % ». */
export function formatPercent(ratio: number, locale: string): string {
  if (!Number.isFinite(ratio)) return '—';
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 0,
  }).format(ratio);
}

/**
 * Score de similarité.
 *
 * Trois décimales, jamais arrondi à deux : les scores de passages voisins se
 * jouent au millième, et un affichage plus grossier ferait croire à une égalité
 * là où le moteur a bel et bien tranché.
 */
export function formatScore(value: number, locale: string): string {
  if (!Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  }).format(value);
}

/**
 * Dérive un slug conforme au motif exigé par le backend
 * (`^[a-z0-9]+(?:-[a-z0-9]+)*$`) : minuscules, accents dépliés, tirets simples.
 */
export function slugify(value: string): string {
  return (
    value
      .normalize('NFD')
      // Retire les marques diacritiques dépliées par NFD (« é » → « e » + accent).
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64)
      .replace(/-+$/g, '')
  );
}
