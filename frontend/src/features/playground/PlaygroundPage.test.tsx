/**
 * Régression : le Playground était bloqué par une garde d'auth (`RequireAuth`
 * sur `App.tsx`), avant même que cette page ne s'exécute — donc invisible
 * pour un visiteur anonyme, y compris sur une base `public` où le backend
 * l'autorise déjà (`RetrievalService.search`/`.chat`, ADR-005). La garde est
 * retirée dans `App.tsx` ; ce test porte sur ce que cette page fait une fois
 * atteinte, sans elle — `SessionProvider` répond `GET /v1/me` par `401`
 * (visiteur anonyme, jamais un principal authentifié) : la page ne doit rien
 * supposer sur l'identité du visiteur, seulement sur ce que le backend
 * répond pour cette base précise. `SessionProvider` lui-même reste requis
 * (`useSession()`, sélecteur de modèle honnête — addendum 2026-09-21 à
 * ADR-054), monté en production dès `main.tsx`.
 */
import { screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { KnowledgeBaseRead, PageDocumentRead, ProviderCatalog } from '@/api/schemas';
import { SessionProvider } from '@/features/auth';
import { fr } from '@/i18n/locales/fr';
import { documentRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { PlaygroundPage } from './PlaygroundPage';

const KB_ID = '33333333-3333-4333-8333-333333333333';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function publicKnowledgeBase(overrides: Partial<KnowledgeBaseRead> = {}): KnowledgeBaseRead {
  return {
    id: KB_ID,
    slug: 'demo-publique',
    name: 'Base démo publique',
    description: null,
    owner_id: '99999999-9999-4999-8999-999999999999',
    organization_id: '88888888-8888-4888-8888-888888888888',
    visibility: 'public',
    embedding_model_id: 'bge-m3',
    reranker_model_id: null,
    observability_enabled: false,
    evaluation_enabled: false,
    ragflow_dataset_external_id: null,
    document_count: 0,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    // `null` : l'accès ne vient que de la visibilité `public`, pas d'une
    // appartenance — exactement le principal anonyme visé par ce test.
    principal_role: null,
    ...overrides,
  };
}

function emptyDocuments(): PageDocumentRead {
  return { data: [], next_cursor: null };
}

function emptyProviders(): ProviderCatalog {
  return {
    embeddings: [],
    rerankers: [],
    llm: [],
    observability: { provider_id: 'langfuse', label: 'Langfuse', available: false },
    engine: { provider_id: 'ragflow', label: 'RAGFlow', is_fake: false, available: true },
  };
}

/** `401` anonyme, jamais un corps JSON : `meQueryOptions` le retraduit en principal `null`. */
function anonymousMe(): Response {
  return new Response(null, { status: 401 });
}

function stubFetch(kbResponse: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: string | URL | Request): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.endsWith('/v1/me')) return Promise.resolve(anonymousMe());
    if (url.includes('/v1/providers')) return Promise.resolve(jsonResponse(emptyProviders()));
    if (url.includes('/documents')) return Promise.resolve(jsonResponse(emptyDocuments()));
    if (url.includes(`/v1/knowledge-bases/${KB_ID}`)) return Promise.resolve(kbResponse);
    throw new Error(`Requête non prévue par le test : ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderPlayground(): void {
  renderWithProviders(
    <MemoryRouter initialEntries={[`/knowledge-bases/${KB_ID}/playground`]}>
      <SessionProvider>
        <Routes>
          <Route path="knowledge-bases/:kbId/playground" element={<PlaygroundPage />} />
        </Routes>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe('PlaygroundPage — accès anonyme (ADR-005)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('rend l’écran de recherche pour une base `public`, sans principal (visiteur anonyme)', async () => {
    stubFetch(jsonResponse(publicKnowledgeBase()));
    renderPlayground();

    // La présence de l'écran de recherche, atteint sans qu'aucun principal
    // authentifié n'ait jamais été simulé par ce test, est exactement la
    // preuve que la page ne dépend plus d'une garde de session pour
    // s'afficher — c'est le défaut corrigé.
    expect(await screen.findByText(fr['playground.mode.search'])).toBeDefined();
  });

  it('affiche « base introuvable » plutôt qu’une invitation à se connecter, sur une base `private` invisible', async () => {
    stubFetch(jsonResponse({ type: 'about:blank', title: 'Not Found', status: 404 }, 404));
    renderPlayground();

    // Le backend répond 404, jamais 403, pour une base privée invisible pour
    // ce principal — un refus explicite confirmerait son existence
    // (`knowledge_bases.py`). La page ne doit donc jamais distinguer ce cas
    // d'une base qui n'existe pas, ni proposer de connexion.
    expect(await screen.findByText(fr['kb.detail.notFoundTitle'])).toBeDefined();
  });
});

/**
 * Audit dette technique 2026-09, motif A : `RetrievalSettings` filtrait les
 * documents interrogeables sur la seule première page (50) de la liste — un
 * document indexé au-delà devenait impossible à sélectionner pour restreindre
 * une recherche, sans le moindre message. Le Playground parcourt désormais
 * tous les curseurs `status=indexed` (`indexedDocumentsSelectorQueryOptions`) :
 * ce test reproduit deux pages distinctes et vérifie que les deux deviennent
 * sélectionnables.
 */
describe('PlaygroundPage — sélection de documents au-delà de la première page', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('un document indexé de la deuxième page reste proposé au filtre de retrieval', async () => {
    const pageOne: PageDocumentRead = {
      data: [documentRead({ id: 'doc-1', filename: 'ancien.pdf', status: 'indexed' })],
      next_cursor: 'curseur-page-2',
    };
    const pageTwo: PageDocumentRead = {
      data: [documentRead({ id: 'doc-2', filename: 'recent.pdf', status: 'indexed' })],
      next_cursor: null,
    };
    const fetchMock = vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith('/v1/me')) return Promise.resolve(anonymousMe());
      if (url.includes('/v1/providers')) return Promise.resolve(jsonResponse(emptyProviders()));
      if (url.includes('/documents')) {
        return Promise.resolve(jsonResponse(url.includes('cursor=') ? pageTwo : pageOne));
      }
      if (url.includes(`/v1/knowledge-bases/${KB_ID}`)) {
        return Promise.resolve(jsonResponse(publicKnowledgeBase()));
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderPlayground();

    expect(await screen.findByText('ancien.pdf')).toBeDefined();
    expect(await screen.findByText('recent.pdf')).toBeDefined();
  });
});
