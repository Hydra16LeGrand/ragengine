export type ClassValue = string | false | null | undefined;

/**
 * Concatène des classes CSS en ignorant les valeurs vides.
 *
 * Indispensable avec `noUncheckedIndexedAccess` : l'accès à une classe de CSS
 * Module (`styles[variant]`) est typé `string | undefined`, et un template
 * littéral produirait la chaîne « undefined » dans le DOM.
 *
 * @example cx(styles.button, isActive && styles.active, className)
 */
export function cx(...values: readonly ClassValue[]): string {
  let result = '';
  for (const value of values) {
    if (typeof value !== 'string' || value.length === 0) continue;
    result = result.length === 0 ? value : `${result} ${value}`;
  }
  return result;
}
