/**
 * Bornes de taille des textes envoyés à la recherche et au chat (ADR-053).
 *
 * **Miroir exact** des constantes de `backend/app/domain/schemas/retrieval.py`
 * — le schéma OpenAPI généré porte bien les `maxLength`, mais sous forme de
 * types, pas de valeurs exploitables à l'exécution. Une valeur changée d'un
 * côté sans l'autre fait finir une conversation légitime en `422` : les deux se
 * modifient ensemble.
 *
 * Les longueurs se comptent ici en unités UTF-16 (`String.length`, comme
 * l'attribut `maxLength` d'un champ), là où Python compte des points de code :
 * un caractère hors plan de base pèse 2 ici et 1 là-bas. L'écart ne joue que
 * dans un sens — tout texte borné ici passe la borne du backend.
 */
import type { Translate } from '@/i18n';
import { formatInteger } from '@/lib/format';

import { isApiError } from './client';
import { problemMessageOf } from './problem';
import type { ChatTurn } from './schemas';

/** Requête de recherche, et tout tour `user` du chat. */
export const MAX_QUERY_LENGTH = 4_000;
/** Tour `assistant` renvoyé dans l'historique. */
export const MAX_ASSISTANT_TURN_LENGTH = 32_000;
/** Nombre de tours de l'historique envoyé. */
export const MAX_CHAT_MESSAGES = 40;
/** Somme des contenus de l'historique envoyé. */
export const MAX_CHAT_TRANSCRIPT_LENGTH = 64_000;

function clip(content: string, limit: number): string {
  if (content.length <= limit) return content;
  // Ne jamais couper une paire de substitution : un demi-caractère deviendrait
  // un point de code isolé, que le backend ne saurait pas réencoder.
  const code = content.charCodeAt(limit - 1);
  const end = code >= 0xd800 && code <= 0xdbff ? limit - 1 : limit;
  return content.slice(0, end);
}

/**
 * Historique à envoyer : les tours **les plus récents** qui tiennent dans les
 * bornes du backend, jamais une conversation qui répondrait `422`.
 *
 * Le dernier tour (la question qu'on envoie) est toujours conservé — la saisie
 * est déjà bornée à `MAX_QUERY_LENGTH` par le champ. En remontant le fil, on
 * s'arrête au premier tour qui ferait dépasser le nombre de tours ou la taille
 * totale ; un tour `assistant` plus long que sa borne est tronqué plutôt
 * qu'abandonné. Un tour `assistant` resté en tête est retiré : une réponse
 * sans sa question n'éclaire pas le modèle, et certains fournisseurs exigent
 * qu'une conversation commence par l'utilisateur.
 *
 * Le modèle perd donc de vue le début d'une très longue conversation — c'est le
 * prix d'un coût par tour borné. La reformulation de requête côté serveur ne
 * lit de toute façon que les derniers échanges.
 */
export function boundChatHistory(turns: readonly ChatTurn[]): ChatTurn[] {
  const kept: ChatTurn[] = [];
  let length = 0;

  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn === undefined) continue;
    const limit = turn.role === 'assistant' ? MAX_ASSISTANT_TURN_LENGTH : MAX_QUERY_LENGTH;
    const content = clip(turn.content, limit);
    const isLatest = kept.length === 0;
    const full =
      kept.length >= MAX_CHAT_MESSAGES || length + content.length > MAX_CHAT_TRANSCRIPT_LENGTH;
    if (!isLatest && full) break;
    kept.unshift({ role: turn.role, content });
    length += content.length;
  }

  while (kept.length > 1 && kept[0]?.role === 'assistant') kept.shift();
  return kept;
}

/** Ce qu'un `422` dit d'un texte trop long, s'il le dit. */
export type TextTooLong =
  /** Un champ texte dépasse sa borne : `max` caractères. */
  | { kind: 'text'; max: number }
  /** L'historique dépasse le nombre de tours ou la taille totale. */
  | { kind: 'conversation' };

/**
 * Reconnaît un refus de taille dans le tableau `errors` d'un `422` Pydantic.
 *
 * Seuls les champs que l'utilisateur tape sont traduits : `string_too_long`
 * sur `query` ou sur le `content` d'un tour (borne dans `ctx.max_length`), et
 * `too_long`/`chat_transcript_too_long` sur `messages` (nombre de tours, taille
 * totale). Un filtre de recherche trop fourni n'est pas « un message trop
 * long » : il garde le message du backend. Rendre la borne réelle plutôt que la
 * constante locale garde un message juste même si le backend a bougé avant le
 * frontend.
 */
export function textTooLongOf(error: unknown): TextTooLong | null {
  if (!isApiError(error) || error.problem.status !== 422) return null;
  const raw = error.problem.errors;
  if (!Array.isArray(raw)) return null;

  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) continue;
    const { type, loc, ctx } = entry as { type?: unknown; loc?: unknown; ctx?: unknown };
    if (!Array.isArray(loc)) continue;
    const path = loc as unknown[];
    const field = path.at(-1);
    if (type === 'string_too_long' && (field === 'query' || field === 'content')) {
      const max = (ctx as { max_length?: unknown } | undefined)?.max_length;
      return { kind: 'text', max: typeof max === 'number' ? max : MAX_QUERY_LENGTH };
    }
    const onMessages = path.includes('messages');
    if (onMessages && (type === 'too_long' || type === 'chat_transcript_too_long')) {
      return { kind: 'conversation' };
    }
  }
  return null;
}

/**
 * Message à afficher pour une erreur de requête : le refus de taille traduit
 * s'il y en a un, sinon le texte du backend (`problemMessageOf`).
 *
 * Le `detail` d'un `422` est générique (« La requête ne respecte pas le schéma
 * attendu. ») et en français quelle que soit la langue de l'interface : pour
 * le seul refus qu'un utilisateur peut provoquer en tapant, on dit ce qui
 * cloche, dans sa langue.
 */
export function requestErrorMessage(error: unknown, t: Translate, locale: string): string {
  const tooLong = textTooLongOf(error);
  if (tooLong?.kind === 'text') {
    return t('error.textTooLong', { max: formatInteger(tooLong.max, locale) });
  }
  if (tooLong?.kind === 'conversation') return t('error.conversationTooLong');
  return problemMessageOf(error, t('error.unexpected'));
}
