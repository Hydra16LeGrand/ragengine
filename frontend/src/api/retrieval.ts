/**
 * Retrieval — recherche hybride et chat cité (Schéma B).
 *
 * Deux endpoints, deux natures :
 *  - `POST /v1/retrieval/search` : requête/réponse JSON ordinaire ;
 *  - `POST /v1/retrieval/chat`   : flux `text/event-stream`, lu par `sse.ts`.
 *
 * La recherche est déclarée en **mutation** et non en query : elle est
 * déclenchée par une intention explicite (l'utilisateur soumet sa requête), et
 * son résultat ne doit ni se recharger au montage, ni se rejouer en arrière-plan
 * — on ne veut pas qu'une liste de passages change sous les yeux de quelqu'un
 * qui est en train de la lire pour évaluer la pertinence du corpus.
 */
import { mutationOptions } from '@tanstack/react-query';

import { ApiError, apiClient } from './client';
import type {
  RetrievalChatRequest,
  RetrievalResult,
  RetrievalSearchRequest,
  RetrievalSearchResponse,
} from './schemas';
import { streamSse } from './sse';

export const RETRIEVAL_SEARCH_PATH = '/v1/retrieval/search';
export const RETRIEVAL_CHAT_PATH = '/v1/retrieval/chat';

/** Une recherche peut mobiliser le GPU (reranking) : le délai par défaut est court. */
const SEARCH_TIMEOUT_MS = 60_000;

export function retrievalSearchMutationOptions() {
  return mutationOptions({
    mutationKey: ['retrieval', 'search'] as const,
    mutationFn: (request: RetrievalSearchRequest) =>
      apiClient.post<RetrievalSearchResponse>(RETRIEVAL_SEARCH_PATH, request, {
        timeoutMs: SEARCH_TIMEOUT_MS,
      }),
  });
}

/* -------------------------------------------------------------------------- */
/* Chat — flux SSE                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Réactions au flux de `POST /retrieval/chat`.
 *
 * L'ordre garanti par le backend est : `sources` (une fois) → `token`* →
 * `error`? **ou** `done`. `onError` et `onDone` s'excluent : recevoir `error`
 * signifie qu'il n'y aura pas de `done`, et c'est précisément ce qui distingue
 * une génération interrompue d'une génération terminée.
 */
export interface ChatStreamHandlers {
  /** Passages retenus, disponibles **avant** le premier token. */
  onSources?: (sources: RetrievalResult[]) => void;
  /** Fragment de texte généré. */
  onToken?: (token: string) => void;
  /** Panne du fournisseur LLM survenue en cours de flux (RFC 9457). */
  onError?: (error: ApiError) => void;
  /** Fin propre du flux. */
  onDone?: () => void;
}

/**
 * Consomme le flux de chat jusqu'à son terme.
 *
 * Les erreurs *avant* le premier octet (accès refusé, `llm_provider_id`
 * inconnu, base introuvable, 422) rejettent la promesse avec une `ApiError` —
 * elles n'atteignent jamais `onError`, réservé aux pannes survenues alors que
 * la réponse `200` est déjà partie. L'appelant distingue ainsi « la requête a
 * été refusée » de « la génération s'est interrompue en route », qui ne se
 * présentent pas du tout de la même façon à l'écran.
 *
 * `signal` interrompt le flux (démontage de l'écran, bouton « Arrêter ») sans
 * lever : l'annulation est un geste de l'utilisateur, pas un incident.
 */
export async function streamChat(
  request: RetrievalChatRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const events = await streamSse(RETRIEVAL_CHAT_PATH, {
    method: 'POST',
    body: request,
    ...(signal ? { signal } : {}),
  });

  try {
    for await (const event of events) {
      switch (event.event) {
        case 'sources':
          handlers.onSources?.(parseSources(event.data));
          break;
        case 'token':
          handlers.onToken?.(parseToken(event.data));
          break;
        case 'error':
          handlers.onError?.(new ApiError(parseProblem(event.data)));
          return;
        case 'done':
          handlers.onDone?.();
          return;
        default:
          // Évènement inconnu : ignoré plutôt que fatal, pour qu'un ajout au
          // contrat backend ne casse pas un client déployé.
          break;
      }
    }
  } catch (cause) {
    // Un abandon volontaire n'est pas une erreur à remonter.
    if (signal?.aborted === true) return;
    throw cause;
  }
}

/* -------------------------------------------------------------------------- */
/* Décodage des charges utiles SSE                                             */
/* -------------------------------------------------------------------------- */
/*
 * Chaque `data` est du JSON — y compris pour `token`, dont la charge utile est
 * une **chaîne JSON** et non le texte brut : `JSON.parse` est donc obligatoire,
 * sans quoi les guillemets et les échappements (`\n`, accents) s'afficheraient
 * tels quels dans la conversation.
 */

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function parseToken(raw: string): string {
  const value = parseJson(raw);
  return typeof value === 'string' ? value : '';
}

function parseSources(raw: string): RetrievalResult[] {
  const value = parseJson(raw);
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is RetrievalResult => isRetrievalResult(item));
}

function isRetrievalResult(value: unknown): value is RetrievalResult {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.chunk_id === 'string' && typeof record.content === 'string';
}

function parseProblem(raw: string): ApiError['problem'] {
  const value = parseJson(raw);
  if (typeof value === 'object' && value !== null) {
    const record = value as Record<string, unknown>;
    return {
      ...record,
      type: typeof record.type === 'string' ? record.type : 'about:blank',
      title: typeof record.title === 'string' ? record.title : 'Génération interrompue',
      status: typeof record.status === 'number' ? record.status : 500,
    };
  }
  return {
    type: 'about:blank',
    title: 'Génération interrompue',
    status: 500,
  };
}
