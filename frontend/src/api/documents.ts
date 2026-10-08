/**
 * Documents d'une base de connaissances.
 *
 * Lecture (Phase 6) et versement (Phase 7) : upload multipart, relecture d'un
 * document pour son rapport de precheck, et transmission de la décision
 * d'aiguillage. Le suivi du job créé par l'upload vit dans `jobs.ts`.
 *
 * Trois façons de consommer la liste, jamais en tronquant une page côté
 * client (audit dette technique 2026-09, motif A) :
 *  - {@link knowledgeBaseDocumentsQueryOptions} — une page, pour un usage qui
 *    tolère de ne connaître qu'un sous-ensemble récent (ex. détection de
 *    doublon à l'upload, déjà documentée comme telle) ;
 *  - {@link knowledgeBaseDocumentsInfiniteQueryOptions} — pagination réelle
 *    (« charger plus »), pour un panneau qui doit pouvoir atteindre tous les
 *    documents ;
 *  - {@link indexedDocumentsSelectorQueryOptions} — parcours borné de tous les
 *    curseurs pour un sélecteur qui a besoin de la totalité (ou presque) d'un
 *    sous-ensemble filtré côté serveur.
 */
import { infiniteQueryOptions, mutationOptions, queryOptions } from '@tanstack/react-query';

import { API_BASE_URL, apiClient } from './client';
import { KNOWLEDGE_BASES_PATH } from './knowledge-bases';
import type {
  DocumentChunksPage,
  DocumentPrecheckDecisionUpdate,
  DocumentRead,
  DocumentStatus,
  DocumentUpdate,
  PageDocumentRead,
  PrecheckDecision,
} from './schemas';

export const DOCUMENTS_PATH = '/v1/documents';

/** Filtres serveur de `GET …/documents` — miroir de `DocumentListParams` (`routes/documents.py`). */
export interface DocumentListFilters {
  status?: DocumentStatus;
  theme?: string;
}

export const documentKeys = {
  all: ['documents'] as const,
  detail: (documentId: string) => ['documents', documentId] as const,
  /** Préfixe d'invalidation : couvre toute forme de liste (page unique, infinie, sélecteur) de cette base. */
  lists: (kbId: string) => ['documents', 'knowledge-base', kbId] as const,
  list: (kbId: string, filters: DocumentListFilters = {}) =>
    [...documentKeys.lists(kbId), filters] as const,
  infiniteList: (kbId: string, filters: DocumentListFilters = {}) =>
    [...documentKeys.lists(kbId), 'infinite', filters] as const,
  indexedSelector: (kbId: string) => [...documentKeys.lists(kbId), 'indexed-selector'] as const,
  chunks: (documentId: string, page: number, pageSize: number, parentsOnly: boolean) =>
    ['documents', documentId, 'chunks', page, pageSize, parentsOnly] as const,
};

const DEFAULT_PAGE_SIZE = 50;

/** Plafond de page accepté par la façade (`app/domain/pagination.py::MAX_PAGE_SIZE`) — un
 * parcours de curseurs y a intérêt : moins de requêtes pour un même nombre de documents. */
const MAX_SERVER_PAGE_SIZE = 100;

/**
 * Nombre maximal de pages parcourues par {@link indexedDocumentsSelectorQueryOptions}.
 *
 * Borne explicite, pas un `limit` remonté sans pagination : au-delà de
 * `MAX_SELECTOR_PAGES * MAX_SERVER_PAGE_SIZE` (1000) documents indexés dans
 * une base, le sélecteur cesse d'être exhaustif et le signale
 * (`truncated: true`) plutôt que de mentir par omission.
 */
const MAX_SELECTOR_PAGES = 10;

/**
 * Un versement n'est pas une requête d'API ordinaire : le fichier part sur le
 * réseau, puis la façade le hache et le stocke avant de répondre. Le délai de
 * 15 s de `client.ts`, calibré pour du JSON, abandonnerait un PDF volumineux
 * alors que tout se passe bien.
 */
const UPLOAD_TIMEOUT_MS = 180_000;

/**
 * Une page de documents — le premier lot le plus récent, par défaut.
 *
 * Angle mort assumé (documenté à chacun de ses appelants) : un document au-delà
 * de `limit` n'apparaît pas. Réservé aux usages qui tolèrent ce sous-ensemble
 * récent ; un panneau qui doit atteindre tous les documents utilise
 * {@link knowledgeBaseDocumentsInfiniteQueryOptions}.
 */
export function knowledgeBaseDocumentsQueryOptions(
  kbId: string,
  filters: DocumentListFilters = {},
  limit: number = DEFAULT_PAGE_SIZE,
) {
  return queryOptions({
    queryKey: documentKeys.list(kbId, filters),
    queryFn: ({ signal }) =>
      apiClient.get<PageDocumentRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}/documents`, {
        query: { limit, ...filters },
        signal,
      }),
  });
}

/**
 * Pagination réelle par curseur (« charger plus ») — `DocumentsPanel`.
 *
 * Contrairement à {@link knowledgeBaseDocumentsQueryOptions}, aucun document
 * n'est durablement hors d'atteinte : chaque page suivante reste à une
 * invocation de `fetchNextPage()`, tant que `next_cursor` n'est pas `null`.
 */
export function knowledgeBaseDocumentsInfiniteQueryOptions(
  kbId: string,
  filters: DocumentListFilters = {},
) {
  return infiniteQueryOptions({
    queryKey: documentKeys.infiniteList(kbId, filters),
    queryFn: ({ pageParam, signal }) =>
      apiClient.get<PageDocumentRead>(`${KNOWLEDGE_BASES_PATH}/${kbId}/documents`, {
        query: { limit: DEFAULT_PAGE_SIZE, cursor: pageParam, ...filters },
        signal,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

/** Résultat de {@link indexedDocumentsSelectorQueryOptions}. */
export interface IndexedDocumentsSelector {
  documents: DocumentRead[];
  /** `true` si `MAX_SELECTOR_PAGES` a été atteint avant d'épuiser les curseurs. */
  truncated: boolean;
}

/**
 * Tous les documents `indexed` d'une base, filtrés côté serveur puis parcourus
 * curseur après curseur jusqu'à épuisement ou jusqu'à `MAX_SELECTOR_PAGES` —
 * pour un sélecteur (Playground, `RetrievalSettings`) qui doit pouvoir
 * proposer n'importe quel document indexé, pas seulement les 50 plus récents.
 *
 * Le filtre `status=indexed` est posé en base (`DocumentListParams`), donc le
 * nombre de requêtes suit le nombre de documents *indexés*, pas le total de la
 * base. Borné et explicite plutôt qu'un `limit` unique remonté sans mesure :
 * `truncated` dit à l'appelant quand le sélecteur n'est plus exhaustif.
 */
export function indexedDocumentsSelectorQueryOptions(kbId: string) {
  return queryOptions({
    queryKey: documentKeys.indexedSelector(kbId),
    queryFn: async ({ signal }): Promise<IndexedDocumentsSelector> => {
      const documents: DocumentRead[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < MAX_SELECTOR_PAGES; page += 1) {
        const result = await apiClient.get<PageDocumentRead>(
          `${KNOWLEDGE_BASES_PATH}/${kbId}/documents`,
          {
            query: {
              limit: MAX_SERVER_PAGE_SIZE,
              cursor,
              status: 'indexed' satisfies DocumentStatus,
            },
            signal,
          },
        );
        documents.push(...result.data);
        if (result.next_cursor == null) return { documents, truncated: false };
        cursor = result.next_cursor;
      }
      return { documents, truncated: true };
    },
  });
}

/**
 * Document seul — c'est lui qui porte `precheck_report`, absent tant que
 * l'analyse préalable n'a pas eu lieu.
 */
export function documentQueryOptions(documentId: string, enabled = true) {
  return queryOptions({
    queryKey: documentKeys.detail(documentId),
    queryFn: ({ signal }) =>
      apiClient.get<DocumentRead>(`${DOCUMENTS_PATH}/${documentId}`, { signal }),
    enabled,
    // Le document est réécrit par le worker (statut, rapport) sans que la SPA
    // n'y soit pour rien : ce qui est en cache est périmé dès qu'il est lu.
    staleTime: 0,
  });
}

/**
 * URL des octets d'origine — à ouvrir directement (nouvel onglet), jamais à
 * fetch soi-même : le cookie de session suffit, et le navigateur gère déjà le
 * rendu `inline`/`attachment` posé par la façade.
 */
export function documentContentUrl(documentId: string): string {
  return `${API_BASE_URL}${DOCUMENTS_PATH}/${documentId}/content`;
}

/** Taille de page par défaut du panneau de fragments — miroir du défaut backend. */
export const CHUNKS_DEFAULT_PAGE_SIZE = 20;

/**
 * `GET …/documents/{id}/chunks` — pagination réelle déléguée au moteur
 * (`page`/`page_size`), pas par curseur : `total` porte le nombre de
 * fragments toutes pages confondues, ce que le panneau a besoin d'afficher.
 *
 * `parentsOnly` (ADR-033) fait porter `page`/`total` sur le seul sous-ensemble
 * filtré (parents d'un découpage parent/enfant) — le backend le repagine
 * lui-même, ce client n'a rien de plus à faire que le transmettre et laisser
 * la pagination déjà en place suivre les nombres qu'il reçoit.
 */
export function documentChunksQueryOptions(
  documentId: string,
  page: number,
  pageSize: number = CHUNKS_DEFAULT_PAGE_SIZE,
  enabled = true,
  parentsOnly = false,
) {
  return queryOptions({
    queryKey: documentKeys.chunks(documentId, page, pageSize, parentsOnly),
    queryFn: ({ signal }) =>
      apiClient.get<DocumentChunksPage>(`${DOCUMENTS_PATH}/${documentId}/chunks`, {
        query: { page, page_size: pageSize, parents_only: parentsOnly },
        signal,
      }),
    enabled,
  });
}

/**
 * `DELETE /v1/documents/{id}` — suppression physique, en cascade (moteur,
 * stockage, jobs). Irréversible : l'appelant est responsable de la
 * confirmation avant d'invoquer cette mutation.
 */
export function deleteDocumentMutationOptions() {
  return mutationOptions({
    mutationKey: ['documents', 'delete'] as const,
    mutationFn: (documentId: string) =>
      apiClient.delete<undefined>(`${DOCUMENTS_PATH}/${documentId}`),
  });
}

/* -------------------------------------------------------------------------- */
/* Versement                                                                   */
/* -------------------------------------------------------------------------- */

export interface IngestDocumentVariables {
  file: File;
  /** Nom à retenir ; à défaut, celui porté par le fichier. */
  filename?: string;
  /** Étiquette de thème, pour organiser la base par sujet — facultative. */
  theme?: string;
}

/**
 * `POST …/documents/ingest` — `multipart/form-data`, réponse `202`.
 *
 * Le `DocumentRead` rendu porte `current_job_id` : c'est la seule accroche
 * pour suivre l'ingestion ensuite.
 *
 * La `mutationKey` ne dépend que de la base : chaque fichier de la file est
 * envoyé par sa propre instance de mutation, indépendamment des autres —
 * l'échec de l'un n'annule ni ne bloque les suivants.
 */
export function ingestDocumentMutationOptions(kbId: string) {
  return mutationOptions({
    mutationKey: ['documents', 'ingest', kbId] as const,
    mutationFn: ({ file, filename, theme }: IngestDocumentVariables) => {
      const form = new FormData();
      form.append('file', file);
      if (filename !== undefined && filename.length > 0) form.append('filename', filename);
      if (theme !== undefined && theme.length > 0) form.append('theme', theme);

      return apiClient.post<DocumentRead>(
        `${KNOWLEDGE_BASES_PATH}/${kbId}/documents/ingest`,
        form,
        { timeoutMs: UPLOAD_TIMEOUT_MS },
      );
    },
  });
}

/* -------------------------------------------------------------------------- */
/* Aiguillage human-in-the-loop                                                */
/* -------------------------------------------------------------------------- */

/**
 * `PUT /v1/documents/{id}/precheck/decision` — débloque un job
 * `awaiting_review` et déclenche la suite du pipeline.
 */
export function precheckDecisionMutationOptions(documentId: string) {
  return mutationOptions({
    mutationKey: ['documents', documentId, 'precheck', 'decision'] as const,
    mutationFn: (decision: PrecheckDecision) => {
      const payload: DocumentPrecheckDecisionUpdate = { decision };
      return apiClient.put<DocumentRead>(
        `${DOCUMENTS_PATH}/${documentId}/precheck/decision`,
        payload,
      );
    },
  });
}

/* -------------------------------------------------------------------------- */
/* Mise à jour partielle                                                       */
/* -------------------------------------------------------------------------- */

/**
 * `PATCH /v1/documents/{id}` — aujourd'hui `filename` (non vidable) et
 * `theme` (vidable par `null`, pour organiser une base par sujet).
 */
export function updateDocumentMutationOptions(documentId: string) {
  return mutationOptions({
    mutationKey: ['documents', documentId, 'update'] as const,
    mutationFn: (payload: DocumentUpdate) =>
      apiClient.patch<DocumentRead>(`${DOCUMENTS_PATH}/${documentId}`, payload),
  });
}
