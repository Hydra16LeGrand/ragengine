/**
 * Couture asynchrone du dépôt de documents pendant la création d'une base
 * (choix explicite de l'utilisateur, cf. `KnowledgeBaseForm` et
 * `pendingUploads.ts`) :
 *  - la base est créée, **puis seulement ensuite** les fichiers mis en
 *    attente sont envoyés — jamais l'inverse, jamais avant que la base
 *    existe (aucun appel `.../documents/ingest` n'est même possible avant :
 *    il dépend de l'identifiant renvoyé par la création) ;
 *  - un envoi qui échoue après une création réussie ne fait pas perdre la
 *    base : l'utilisateur atterrit sur son détail, le fichier en échec y est
 *    listé avec une reprise ;
 *  - un échec de la création elle-même n'envoie jamais rien.
 *
 * Le détail de base réel (`KnowledgeBaseDetailPage`) monte cinq panneaux
 * dépendants du réseau sans harnais existant (même constat que
 * `KnowledgeBaseSiblingNav.test.tsx`) : la route `/knowledge-bases/:kbId` est
 * ici tenue par une sonde minimale qui monte le **vrai** `UploadPanel` — c'est
 * lui qui reprend et envoie les fichiers mis en attente, exactement comme sur
 * l'écran réel une fois la base créée.
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ProviderCatalog } from '@/api/schemas';
import { UploadPanel } from '@/features/ingestion';
import { fr } from '@/i18n/locales/fr';
import {
  documentRead,
  KNOWLEDGE_BASE_ID,
  knowledgeBaseRead,
  organizationRead,
} from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { KnowledgeBaseCreatePage } from './KnowledgeBaseCreatePage';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function problemResponse(status: number, title: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title, status }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
}

function providerCatalog(): ProviderCatalog {
  return {
    embeddings: [
      { provider_id: 'bge-m3', label: 'BGE-M3', is_local: true, dimensions: 1024, available: true },
    ],
    rerankers: [],
    llm: [],
    observability: { provider_id: 'langfuse', label: 'Langfuse', available: false },
    engine: { provider_id: 'ragflow', label: 'RAGFlow', is_fake: false, available: true },
  };
}

/** Remplace `KnowledgeBaseDetailPage` : seul `UploadPanel` importe pour ce test. */
function DetailProbe(): ReactNode {
  const { kbId } = useParams<{ kbId: string }>();
  if (kbId === undefined) return null;
  return <UploadPanel knowledgeBaseId={kbId} />;
}

function renderCreatePage(): void {
  renderWithProviders(
    <MemoryRouter initialEntries={['/knowledge-bases/new']}>
      <Routes>
        <Route path="/knowledge-bases/new" element={<KnowledgeBaseCreatePage />} />
        <Route path="/knowledge-bases/:kbId" element={<DetailProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function fillNameAndStage(
  user: ReturnType<typeof userEvent.setup>,
  filenames: string[],
): Promise<void> {
  await user.type(screen.getByLabelText(fr['kb.form.name']), 'Base de test');
  const input = screen.getByLabelText(fr['ingest.dropzone.browse']);
  const files = filenames.map(
    (name) => new File(['%PDF-1.7 …'], name, { type: 'application/pdf' }),
  );
  await user.upload(input, files);
}

function methodOf(init: RequestInit | undefined): string {
  return (init?.method ?? 'GET').toUpperCase();
}

function urlOf(input: string | URL | Request): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

describe('KnowledgeBaseCreatePage — création avec documents en attente', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('crée la base puis envoie les fichiers en attente, dans cet ordre', async () => {
    const created = knowledgeBaseRead({ id: KNOWLEDGE_BASE_ID });
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const method = methodOf(init);

        if (method === 'GET' && url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse(providerCatalog()));
        }
        if (method === 'GET' && url.includes('/v1/organizations')) {
          return Promise.resolve(jsonResponse({ data: [organizationRead()], next_cursor: null }));
        }
        if (method === 'POST' && url.endsWith('/v1/knowledge-bases')) {
          return Promise.resolve(jsonResponse(created, 201));
        }
        if (method === 'POST' && url.includes('/documents/ingest')) {
          // `current_job_id: null` : aucun suivi de job à simuler pour prouver
          // l'ordre création → envoi, seul ce que ce test vérifie.
          return Promise.resolve(jsonResponse(documentRead({ current_job_id: null }), 202));
        }
        if (method === 'GET' && url.includes(`/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents`)) {
          return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
        }
        throw new Error(`Requête non prévue par le test : ${method} ${url}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderCreatePage();
    await fillNameAndStage(user, ['un.pdf', 'deux.pdf']);

    await user.click(screen.getByRole('button', { name: fr['kb.create.submit'] }));

    // Preuve que la base a bien été atteinte : la sonde de détail, montée
    // seulement une fois la navigation faite, finit par afficher les deux
    // fichiers envoyés.
    await screen.findByText('un.pdf');
    await screen.findByText('deux.pdf');

    const creationIndex = fetchMock.mock.calls.findIndex(
      ([reqInput, reqInit]) =>
        methodOf(reqInit) === 'POST' && urlOf(reqInput).endsWith('/v1/knowledge-bases'),
    );
    const ingestIndexes = fetchMock.mock.calls
      .map(([reqInput, reqInit], index) =>
        methodOf(reqInit) === 'POST' && urlOf(reqInput).includes('/documents/ingest') ? index : -1,
      )
      .filter((index) => index !== -1);

    expect(creationIndex).toBeGreaterThanOrEqual(0);
    expect(ingestIndexes).toHaveLength(2);
    for (const ingestIndex of ingestIndexes) {
      expect(ingestIndex).toBeGreaterThan(creationIndex);
    }
  });

  it('échec de la création : aucun envoi n’a lieu', async () => {
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const method = methodOf(init);

        if (method === 'GET' && url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse(providerCatalog()));
        }
        if (method === 'GET' && url.includes('/v1/organizations')) {
          return Promise.resolve(jsonResponse({ data: [organizationRead()], next_cursor: null }));
        }
        if (method === 'POST' && url.endsWith('/v1/knowledge-bases')) {
          return Promise.resolve(problemResponse(500, 'Erreur serveur'));
        }
        throw new Error(`Requête non prévue par le test : ${method} ${url}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderCreatePage();
    await fillNameAndStage(user, ['un.pdf', 'deux.pdf']);

    await user.click(screen.getByRole('button', { name: fr['kb.create.submit'] }));

    await screen.findByText(fr['kb.form.errorTitle']);

    // Les fichiers restent en attente, visibles sur l'écran de création : la
    // tentative ratée ne les a pas perdus.
    expect(screen.getByText('un.pdf')).toBeDefined();
    expect(screen.getByText('deux.pdf')).toBeDefined();

    const ingestCalled = fetchMock.mock.calls.some(([reqInput]) =>
      urlOf(reqInput).includes('/documents/ingest'),
    );
    expect(ingestCalled).toBe(false);
  });

  it('un envoi qui échoue après la création laisse la base intacte, avec reprise possible', async () => {
    const created = knowledgeBaseRead({ id: KNOWLEDGE_BASE_ID });
    let ingestAttempts = 0;
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const method = methodOf(init);

        if (method === 'GET' && url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse(providerCatalog()));
        }
        if (method === 'GET' && url.includes('/v1/organizations')) {
          return Promise.resolve(jsonResponse({ data: [organizationRead()], next_cursor: null }));
        }
        if (method === 'POST' && url.endsWith('/v1/knowledge-bases')) {
          return Promise.resolve(jsonResponse(created, 201));
        }
        if (method === 'POST' && url.includes('/documents/ingest')) {
          ingestAttempts += 1;
          if (ingestAttempts === 1) return Promise.resolve(problemResponse(500, 'Erreur serveur'));
          return Promise.resolve(jsonResponse(documentRead({ current_job_id: null }), 202));
        }
        if (method === 'GET' && url.includes(`/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents`)) {
          return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
        }
        throw new Error(`Requête non prévue par le test : ${method} ${url}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderCreatePage();
    await fillNameAndStage(user, ['rapport.pdf']);

    await user.click(screen.getByRole('button', { name: fr['kb.create.submit'] }));

    // La base existe (sonde de détail atteinte) et le fichier y est bien
    // signalé en échec, pas silencieusement perdu.
    await screen.findByText('rapport.pdf');
    await screen.findByText(fr['ingest.state.uploadFailed']);
    expect(ingestAttempts).toBe(1);

    await user.click(screen.getByRole('button', { name: fr['ingest.item.retry'] }));

    // La reprise ré-appelle bien l'envoi, et cette fois-ci aboutit.
    await screen.findByText(fr['ingest.noJobTitle']);
    expect(ingestAttempts).toBe(2);
    expect(screen.queryByText(fr['ingest.state.uploadFailed'])).toBeNull();
  });
});
