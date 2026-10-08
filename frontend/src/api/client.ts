/**
 * Client HTTP de la façade RAGEngine.
 *
 * Deux responsabilités, et rien d'autre :
 *  1. émettre une requête vers `/api/...` (même origine : le proxy Vite en dev,
 *     le reverse-proxy en production — aucune URL absolue en dur) ;
 *  2. transformer toute réponse d'erreur en `ApiError` porteuse d'un
 *     `ProblemDetail` conforme à la RFC 9457 (`application/problem+json`),
 *     enveloppe d'erreur unique du backend (CONTRIBUTING.md §4).
 *
 * Les erreurs réseau (backend éteint, DNS, CORS, timeout) sont normalisées
 * dans la même forme, avec `status: 0`, pour que l'UI n'ait qu'un seul type
 * d'erreur à gérer.
 */

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

export const PROBLEM_JSON_MEDIA_TYPE = 'application/problem+json';

/** Types `type` synthétisés côté client (le backend n'en produit jamais). */
export const CLIENT_PROBLEM_TYPES = {
  network: 'urn:ragengine:client:network-error',
  timeout: 'urn:ragengine:client:timeout',
  aborted: 'urn:ragengine:client:aborted',
  malformed: 'urn:ragengine:client:malformed-response',
} as const;

const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * Problem Details for HTTP APIs — RFC 9457.
 * Les membres d'extension (`errors`, `job_id`, …) sont autorisés : ils sont
 * lus dynamiquement, donc typés `unknown`.
 */
export interface ProblemDetail {
  /** URI identifiant le type de problème. `about:blank` par défaut. */
  type: string;
  /** Résumé court, stable, lisible par un humain. */
  title: string;
  /** Code HTTP. `0` pour une erreur locale (réseau, timeout, annulation). */
  status: number;
  /** Explication spécifique à cette occurrence. */
  detail?: string;
  /** URI de l'occurrence (ex. la ressource concernée). */
  instance?: string;
  /** Membres d'extension RFC 9457. */
  [extension: string]: unknown;
}

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: HttpMethod;
  /** Corps sérialisé en JSON (ou transmis tel quel s'il s'agit d'un FormData). */
  body?: unknown;
  query?: Record<string, QueryValue | readonly QueryValue[]>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Délai maximal avant abandon. `0` désactive le timeout. */
  timeoutMs?: number;
}

/** Erreur unique remontée par le client, toujours porteuse d'un ProblemDetail. */
export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly problem: ProblemDetail;
  readonly status: number;

  constructor(problem: ProblemDetail, options?: { cause?: unknown }) {
    super(problem.detail ?? problem.title, options);
    this.problem = problem;
    this.status = problem.status;
  }

  /** Le serveur n'a pas répondu (backend éteint, réseau, timeout, annulation). */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500;
  }

  get isServerError(): boolean {
    return this.status >= 500;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isProblemDetail(value: unknown): value is ProblemDetail {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.title === 'string' && typeof record.status === 'number';
}

/* -------------------------------------------------------------------------- */
/* Interne — partagé avec `sse.ts`                                             */
/* -------------------------------------------------------------------------- */
/*
 * Trois helpers sont exportés sans faire partie de l'API publique du module :
 * `sse.ts` (lecteur `text/event-stream` de `POST /retrieval/chat`) doit
 * construire ses URL, lire ses erreurs RFC 9457 et décrire ses pannes réseau
 * **exactement** comme `request()`. Les redéclarer là-bas donnerait deux
 * politiques d'erreur divergentes pour un même backend ; les exporter garde
 * une seule vérité.
 */

export function buildUrl(path: string, query: RequestOptions['query']): string {
  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item === undefined || item === null) continue;
      params.append(key, String(item));
    }
  }
  const search = params.toString();
  return search ? `${url}?${search}` : url;
}

/** Normalise une charge utile inconnue en ProblemDetail exploitable. */
function toProblemDetail(payload: unknown, status: number, fallbackTitle: string): ProblemDetail {
  if (typeof payload === 'object' && payload !== null) {
    const record = payload as Record<string, unknown>;
    return {
      ...record,
      type: typeof record.type === 'string' ? record.type : 'about:blank',
      title: typeof record.title === 'string' ? record.title : fallbackTitle,
      status: typeof record.status === 'number' ? record.status : status,
      ...(typeof record.detail === 'string' ? { detail: record.detail } : {}),
      ...(typeof record.instance === 'string' ? { instance: record.instance } : {}),
    };
  }

  return {
    type: 'about:blank',
    title: fallbackTitle,
    status,
    ...(typeof payload === 'string' && payload.length > 0 ? { detail: payload } : {}),
  };
}

function localProblem(type: string, title: string, detail: string): ProblemDetail {
  return { type, title, status: 0, detail };
}

function combineSignals(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal | undefined {
  const timeoutSignal = timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined;
  if (signal && timeoutSignal) return AbortSignal.any([signal, timeoutSignal]);
  return signal ?? timeoutSignal;
}

export async function readErrorProblem(response: Response): Promise<ProblemDetail> {
  const contentType = response.headers.get('content-type') ?? '';
  const fallbackTitle = response.statusText || `HTTP ${String(response.status)}`;

  if (contentType.includes('json')) {
    try {
      const payload: unknown = await response.json();
      return toProblemDetail(payload, response.status, fallbackTitle);
    } catch {
      return toProblemDetail(null, response.status, fallbackTitle);
    }
  }

  const text = await response.text().catch(() => '');
  return toProblemDetail(text, response.status, fallbackTitle);
}

async function readSuccessBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    // Réponse sans corps : les appelants typent alors `T = void`.
    return undefined as T;
  }

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('json')) {
    return (await response.text()) as T;
  }

  try {
    return (await response.json()) as T;
  } catch (cause) {
    throw new ApiError(
      localProblem(
        CLIENT_PROBLEM_TYPES.malformed,
        'Réponse illisible',
        'Le serveur a répondu avec un corps JSON invalide.',
      ),
      { cause },
    );
  }
}

/* -------------------------------------------------------------------------- */
/* API publique                                                                */
/* -------------------------------------------------------------------------- */

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, headers, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  const requestHeaders: Record<string, string> = {
    Accept: `application/json, ${PROBLEM_JSON_MEDIA_TYPE}`,
    ...(body !== undefined && !isFormData ? { 'Content-Type': 'application/json' } : {}),
    ...headers,
  };

  const requestSignal = combineSignals(signal, timeoutMs);

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: requestHeaders,
      credentials: 'same-origin',
      ...(body === undefined ? {} : { body: isFormData ? body : JSON.stringify(body) }),
      ...(requestSignal ? { signal: requestSignal } : {}),
    });
  } catch (cause) {
    throw new ApiError(describeFetchFailure(cause), { cause });
  }

  if (!response.ok) {
    throw new ApiError(await readErrorProblem(response));
  }

  return readSuccessBody<T>(response);
}

export function describeFetchFailure(cause: unknown): ProblemDetail {
  const name = cause instanceof Error ? cause.name : '';

  if (name === 'TimeoutError') {
    return localProblem(
      CLIENT_PROBLEM_TYPES.timeout,
      'Délai dépassé',
      'Le serveur n’a pas répondu dans le temps imparti.',
    );
  }
  if (name === 'AbortError') {
    return localProblem(
      CLIENT_PROBLEM_TYPES.aborted,
      'Requête annulée',
      'La requête a été interrompue.',
    );
  }
  return localProblem(
    CLIENT_PROBLEM_TYPES.network,
    'Serveur injoignable',
    'Aucune réponse de la façade. Le backend est-il démarré ?',
  );
}

/** Raccourcis par verbe. `T` est le type de la réponse attendue. */
export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<T> =>
    request<T>(path, { ...options, method: 'GET' }),

  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> =>
    request<T>(path, { ...options, method: 'POST', body }),

  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> =>
    request<T>(path, { ...options, method: 'PUT', body }),

  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> =>
    request<T>(path, { ...options, method: 'PATCH', body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<T> =>
    request<T>(path, { ...options, method: 'DELETE' }),
} as const;
