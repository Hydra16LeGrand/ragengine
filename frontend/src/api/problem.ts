/**
 * Lecture des membres d'extension RFC 9457 renvoyés par la façade.
 *
 * Sur une `422`, le backend joint un tableau `errors` dérivé de Pydantic :
 *
 * ```json
 * { "type": "/problems/validation-error", "status": 422,
 *   "errors": [{ "type": "value_error", "loc": ["body", "email"], "msg": "…" }] }
 * ```
 *
 * Ces membres sont typés `unknown` dans `ProblemDetail` (le contrat RFC 9457
 * les autorise sans les décrire) : on les valide donc ici au lieu de caster à
 * l'aveugle, pour qu'un formulaire puisse afficher le message **sur le champ
 * fautif** plutôt qu'une bannière générique.
 */
import { isApiError } from './client';

export interface FieldViolation {
  /** Nom du champ visé, dernier segment de `loc` (ex. `email`). */
  field: string;
  message: string;
}

function isStringOrNumberArray(value: unknown): value is (string | number)[] {
  return (
    Array.isArray(value) &&
    value.every((item) => typeof item === 'string' || typeof item === 'number')
  );
}

/** Extrait les violations champ par champ, ou un tableau vide s'il n'y en a pas. */
export function fieldViolationsOf(error: unknown): FieldViolation[] {
  if (!isApiError(error)) return [];
  const raw = error.problem.errors;
  if (!Array.isArray(raw)) return [];

  const violations: FieldViolation[] = [];
  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) continue;
    const record = entry as Record<string, unknown>;
    const { loc, msg } = record;
    if (!isStringOrNumberArray(loc) || typeof msg !== 'string') continue;

    // `loc` vaut typiquement ['body', 'email'] : le champ est le dernier segment.
    const last = loc.at(-1);
    if (last === undefined) continue;
    violations.push({ field: String(last), message: msg });
  }
  return violations;
}

/** Index `champ → message`, pratique pour alimenter des `<Field error>`. */
export function fieldErrorsOf(error: unknown): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const { field, message } of fieldViolationsOf(error)) {
    errors[field] ??= message;
  }
  return errors;
}

/**
 * Message lisible à afficher en bannière.
 *
 * On privilégie `detail` (spécifique à l'occurrence) puis `title` (stable), ce
 * qui restitue le texte réel du backend au lieu d'un « une erreur est
 * survenue » qui n'aide personne à corriger sa saisie.
 */
export function problemMessageOf(error: unknown, fallback: string): string {
  if (!isApiError(error)) return fallback;
  const { detail, title } = error.problem;
  if (typeof detail === 'string' && detail.length > 0) return detail;
  if (title.length > 0) return title;
  return fallback;
}
