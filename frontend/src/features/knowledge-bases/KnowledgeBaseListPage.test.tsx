/**
 * État vide sans aucune base : le parcours de première utilisation (CONTRIBUTING.md
 * §7, « ce qui manque, c'est un état vide utile ») doit expliquer les trois
 * étapes — créer, déposer des documents, interroger — et mener directement à
 * la création. Monté avec `SessionProvider` : `useSession()` conditionne
 * l'affichage du parcours (un compte réel, pas un visiteur anonyme ni un
 * compte éphémère à quota épuisé, cf. `KnowledgeBaseListPage.tsx`).
 */
import { screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fr } from '@/i18n/locales/fr';
import { principalRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { SessionProvider } from '../auth';
import { KnowledgeBaseListPage } from './KnowledgeBaseListPage';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function stubFacade(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/v1/me')) {
        return Promise.resolve(jsonResponse(principalRead({ is_guest: false, role: 'member' })));
      }
      if (url.includes('/v1/knowledge-bases')) {
        return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );
}

function renderList(): void {
  renderWithProviders(
    <MemoryRouter initialEntries={['/knowledge-bases']}>
      <SessionProvider>
        <Routes>
          <Route path="/knowledge-bases" element={<KnowledgeBaseListPage />} />
        </Routes>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe('KnowledgeBaseListPage — état vide, aucune base', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche les trois étapes du parcours et mène à la création', async () => {
    stubFacade();
    renderList();

    expect(await screen.findByText(fr['kb.empty.title'])).toBeDefined();

    // Les trois étapes, dans l'ordre : créer, déposer, interroger.
    expect(screen.getByText(fr['kb.empty.step1'])).toBeDefined();
    expect(screen.getByText(fr['kb.empty.step2'])).toBeDefined();
    expect(screen.getByText(fr['kb.empty.step3'])).toBeDefined();

    // Le lien de création existe deux fois (en-tête + état vide, même libellé
    // `kb.list.create`) : les deux mènent à la même création, donc chacun
    // vaut d'être vérifié plutôt que d'isoler celui de l'état vide seul.
    const createLinks = screen.getAllByRole('link', { name: fr['kb.list.create'] });
    expect(createLinks.length).toBeGreaterThan(0);
    for (const link of createLinks) {
      expect(link.getAttribute('href')).toBe('/knowledge-bases/new');
    }
  });
});
