/**
 * Harnais pour `KnowledgeBaseDetailPage` — écran qui monte cinq panneaux
 * dépendants du réseau (documents, versement, membres, évaluations)
 * en plus de sa propre requête de détail et de la liste des bases adjacentes
 * (`KnowledgeBaseSiblingNav`). Constat déjà écrit dans
 * `KnowledgeBaseSiblingNav.test.tsx:1-9` : « aucun harnais existant ». Celui-ci
 * fournit une réponse par défaut, silencieusement correcte, pour chacune de
 * ces requêtes annexes, afin qu'un test de couture sur l'écran lui-même (son
 * chargement, ses états d'erreur, la bascule édition) n'ait pas à toutes les
 * reconstruire — seul le point precis observé par le test a besoin d'être
 * personnalisé via `handlers`.
 *
 * `SessionProvider` est monté (requis par `MembersPanel`, via `useSession()`),
 * avec un principal authentifié par défaut : deviner un compte éphémère ici masquerait
 * silencieusement les panneaux réservés au propriétaire dans un test qui ne
 * s'y attend pas.
 */
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import { KnowledgeBaseDetailPage } from '@/features/knowledge-bases/KnowledgeBaseDetailPage';
import { SessionProvider } from '@/features/auth';

import { principalRead } from './fixtures';
import { renderWithProviders } from './render';

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

export function problemResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title: 'Erreur', status, detail }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
}

export function urlOf(input: string | URL | Request): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

export function methodOf(init: RequestInit | undefined): string {
  return (init?.method ?? 'GET').toUpperCase();
}

/** Catalogue vide : suffisant partout où seul son absence de crash importe. */
const EMPTY_CATALOG = {
  embeddings: [],
  rerankers: [],
  llm: [],
  observability: { provider_id: 'langfuse', label: 'Langfuse', available: false },
};

const EMPTY_PAGE = { data: [], next_cursor: null };

/** Une requête interceptée avant les défauts — `undefined` pour laisser passer. */
export interface DetailFetchHandler {
  match: (url: string, method: string) => boolean;
  respond: (
    url: string,
    method: string,
    init: RequestInit | undefined,
  ) => Response | Promise<Response>;
}

/**
 * Pose le `fetch` global : `handlers` (dans l'ordre) d'abord, puis les
 * défauts (détail de `base`, liste des bases pour la navigation adjacente,
 * catalogue vide, pages vides pour documents/membres/évaluations).
 */
export function stubKnowledgeBaseDetailFetch(
  base: { id: string },
  handlers: DetailFetchHandler[] = [],
): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request, init?: RequestInit): Response | Promise<Response> => {
      const url = urlOf(input);
      const method = methodOf(init);
      const path = url.split('?')[0] ?? url;

      for (const handler of handlers) {
        if (handler.match(url, method)) return handler.respond(url, method, init);
      }

      if (method === 'GET' && path.endsWith('/v1/me')) {
        return jsonResponse(principalRead());
      }
      // Sans clé personnelle par défaut : suffisant tant qu'un test ne
      // personnalise pas explicitement le sélecteur de modèle de
      // `EvaluationsPanel` — n'est de toute façon interrogé que pour un
      // principal authentifié (`KnowledgeBaseDetailPage`, `isAuthenticated`),
      // jamais pour le compte éphémère par défaut ci-dessus.
      if (method === 'GET' && path.endsWith('/v1/me/llm-keys')) {
        return jsonResponse([]);
      }
      if (method === 'GET' && path.includes('/v1/providers')) {
        return jsonResponse(EMPTY_CATALOG);
      }
      if (method === 'GET' && path.includes(`/knowledge-bases/${base.id}/documents`)) {
        return jsonResponse(EMPTY_PAGE);
      }
      if (method === 'GET' && path.includes(`/knowledge-bases/${base.id}/members`)) {
        return jsonResponse([]);
      }
      if (method === 'GET' && path.includes(`/knowledge-bases/${base.id}/evaluations`)) {
        return jsonResponse([]);
      }
      if (method === 'GET' && path.endsWith(`/knowledge-bases/${base.id}`)) {
        return jsonResponse(base);
      }
      if (method === 'GET' && path.endsWith('/v1/knowledge-bases')) {
        return jsonResponse(EMPTY_PAGE);
      }
      throw new Error(`Requête non prévue par le test : ${method} ${url}`);
    }),
  );
}

/**
 * Monte l'écran réel sur la route `/knowledge-bases/:kbId`, avec un repère
 * texte sur `/knowledge-bases` pour prouver une navigation de retour (lien
 * « Toutes les bases », suppression réussie) sans reconstruire la liste.
 */
export function renderKnowledgeBaseDetail(kbId: string): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/knowledge-bases/${kbId}`]}>
      <SessionProvider>
        <Routes>
          <Route path="/knowledge-bases/:kbId" element={<KnowledgeBaseDetailPage />} />
          <Route path="/knowledge-bases" element={<p>Liste des bases</p>} />
        </Routes>
      </SessionProvider>
    </MemoryRouter>,
  );
}
