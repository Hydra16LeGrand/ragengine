/**
 * Lecteur de flux `text/event-stream` (SSE) au-dessus de `fetch()`.
 *
 * ## Pourquoi ce fichier existe
 *
 * `POST /api/v1/retrieval/chat` répond en SSE. L'API native `EventSource` du
 * navigateur ne peut pas la consommer : elle n'émet que des `GET`, sans corps
 * ni en-têtes personnalisés. Le flux est donc lu à la main sur le
 * `ReadableStream` de la réponse `fetch`.
 *
 * Aucune dépendance npm n'est ajoutée pour cela (CONTRIBUTING.md §7) : le format est
 * une grammaire de quelques lignes, et une librairie tierce apporterait une
 * politique d'erreur concurrente de celle de `client.ts`.
 *
 * ## Ce que le lecteur garantit
 *
 * 1. **Mêmes règles que `client.ts`** : `credentials: 'same-origin'`, erreurs
 *    normalisées en `ApiError` porteuse d'un `ProblemDetail` RFC 9457, pannes
 *    locales en `status: 0`. Les helpers sont importés, jamais réécrits.
 * 2. **Erreur avant le premier octet ⇒ exception**, pas un évènement. Le
 *    backend garantit que tout refus (accès, `llm_provider_id` inconnu, 422…)
 *    est une réponse HTTP classique : le `await` de `streamSse` rejette donc,
 *    et l'appelant n'a pas à inventer un état d'erreur « dans le flux » pour ce
 *    cas-là. Une erreur *pendant* le flux, elle, arrive en évènement `error` —
 *    le code HTTP `200` est déjà parti, il ne peut plus changer.
 * 3. **Pas de délai d'expiration implicite.** Une génération peut durer une
 *    minute sans émettre : le timeout de 15 s de `client.ts` la tuerait. Seul
 *    l'`AbortSignal` de l'appelant interrompt le flux.
 *
 * ## Grammaire implémentée (WHATWG HTML, § server-sent events)
 *
 * - une ligne `champ: valeur` (l'espace après `:` est optionnel et retiré) ;
 * - champs reconnus : `event`, `data`, `id`, `retry` ; les autres sont ignorés ;
 * - plusieurs lignes `data` sont jointes par `\n` ;
 * - une ligne vide termine l'évènement et le distribue ;
 * - une ligne commençant par `:` est un commentaire (keep-alive) et est ignorée ;
 * - fins de ligne `\n`, `\r\n` ou `\r` — toutes acceptées.
 *
 * Le backend n'émet aujourd'hui qu'une seule ligne `data` par évènement
 * (`json.dumps` ne produit pas de saut de ligne), mais respecter la grammaire
 * complète coûte dix lignes et évite une régression silencieuse le jour où un
 * `data` multiligne apparaîtra.
 */

import {
  ApiError,
  buildUrl,
  describeFetchFailure,
  readErrorProblem,
  type HttpMethod,
} from './client';

/** Évènement SSE distribué, champs déjà assemblés. */
export interface SseEvent {
  /** Valeur du champ `event`, ou `'message'` par défaut (conforme à la spec). */
  event: string;
  /** Lignes `data` jointes par `\n`. Chaîne vide si l'évènement n'en portait aucune. */
  data: string;
  /** Dernier `id` reçu, s'il y en a eu un. */
  id?: string;
}

export interface StreamSseOptions {
  method?: HttpMethod;
  /** Corps sérialisé en JSON. */
  body?: unknown;
  headers?: Record<string, string>;
  /** Seul moyen d'interrompre le flux : il n'y a pas de timeout implicite. */
  signal?: AbortSignal;
}

const SSE_MEDIA_TYPE = 'text/event-stream';

/**
 * Ouvre le flux et rend un générateur asynchrone d'évènements.
 *
 * La promesse rendue par cette fonction se résout **une fois les en-têtes
 * reçus et validés** : c'est là que se manifestent les refus du backend. Elle
 * rejette alors avec une `ApiError`, avant que le moindre évènement n'existe.
 */
export async function streamSse(
  path: string,
  options: StreamSseOptions = {},
): Promise<AsyncGenerator<SseEvent, void, undefined>> {
  const { method = 'POST', body, headers, signal } = options;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, undefined), {
      method,
      headers: {
        Accept: SSE_MEDIA_TYPE,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...headers,
      },
      credentials: 'same-origin',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    throw new ApiError(describeFetchFailure(cause), { cause });
  }

  if (!response.ok) {
    throw new ApiError(await readErrorProblem(response));
  }

  if (response.body === null) {
    throw new ApiError({
      type: 'urn:ragengine:client:malformed-response',
      title: 'Réponse illisible',
      status: 0,
      detail: 'Le serveur a accepté la requête sans ouvrir de flux.',
    });
  }

  return readEvents(response.body);
}

/** Découpe le flux d'octets en évènements. Extrait pour rester testable seul. */
async function* readEvents(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();

  let buffer = '';
  let dataLines: string[] = [];
  let eventName = '';
  let lastId: string | undefined;

  /** Assemble l'évènement courant, ou `null` s'il n'y avait rien à distribuer. */
  const flush = (): SseEvent | null => {
    if (dataLines.length === 0 && eventName === '') return null;
    const event: SseEvent = {
      event: eventName === '' ? 'message' : eventName,
      data: dataLines.join('\n'),
      ...(lastId === undefined ? {} : { id: lastId }),
    };
    dataLines = [];
    eventName = '';
    return event;
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();

      if (done) {
        // Fin de flux : le dernier évènement peut ne pas être suivi de la ligne
        // vide qui le termine normalement. On vide le tampon — reste compris,
        // car une dernière ligne sans saut final resterait sinon non lue — puis
        // on distribue ce qui est complet.
        buffer += decoder.decode();
        const { lines, rest } = splitLines(buffer);
        for (const line of lines) {
          if (line === '') {
            const event = flush();
            if (event !== null) yield event;
            continue;
          }
          consumeLine(line);
        }
        if (rest !== '') consumeLine(rest);
        const trailing = flush();
        if (trailing !== null) yield trailing;
        return;
      }

      buffer += decoder.decode(value, { stream: true });

      const { lines, rest } = splitLines(buffer);
      buffer = rest;

      for (const line of lines) {
        if (line === '') {
          const event = flush();
          if (event !== null) yield event;
          continue;
        }
        consumeLine(line);
      }
    }
  } finally {
    // Libère la connexion même si l'appelant abandonne le générateur (`break`,
    // démontage du composant) : sans cela le flux resterait ouvert côté serveur.
    reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }

  function consumeLine(line: string): void {
    if (line.startsWith(':')) return; // commentaire / keep-alive

    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? '' : line.slice(separator + 1);
    if (value.startsWith(' ')) value = value.slice(1);

    switch (field) {
      case 'event':
        eventName = value;
        break;
      case 'data':
        dataLines.push(value);
        break;
      case 'id':
        // La spec impose d'ignorer un `id` contenant un octet nul.
        if (!value.includes('\0')) lastId = value;
        break;
      default:
        // `retry` et tout champ inconnu : sans effet pour un flux non reconnecté.
        break;
    }
  }
}

/**
 * Découpe un tampon en lignes complètes.
 *
 * Un `\r` final est **conservé dans le reste** : il peut être la première
 * moitié d'un `\r\n` coupé entre deux morceaux du flux, et le traiter tout de
 * suite fabriquerait une ligne vide fantôme — donc un évènement distribué trop
 * tôt, amputé de ses lignes suivantes.
 */
function splitLines(buffer: string): { lines: string[]; rest: string } {
  const lines: string[] = [];
  let index = 0;

  for (;;) {
    const nextBreak = findLineBreak(buffer, index);
    if (nextBreak === null) break;

    const { start, end } = nextBreak;
    if (end > buffer.length) break; // `\r` en toute fin : on attend la suite
    lines.push(buffer.slice(index, start));
    index = end;
  }

  return { lines, rest: buffer.slice(index) };
}

function findLineBreak(buffer: string, from: number): { start: number; end: number } | null {
  for (let index = from; index < buffer.length; index += 1) {
    const char = buffer[index];
    if (char === '\n') return { start: index, end: index + 1 };
    if (char === '\r') {
      // `\r` en dernière position : indécidable tant que l'octet suivant manque.
      if (index === buffer.length - 1) return { start: index, end: buffer.length + 1 };
      return { start: index, end: buffer[index + 1] === '\n' ? index + 2 : index + 1 };
    }
  }
  return null;
}
