/**
 * Menu mobile de l'en-tête (2026-09-06) — un utilisateur qui teste sur
 * téléphone (accès LAN, `server.host: true`) trouvait la barre de navigation
 * illisible : jusqu'à 5 liens, le logo et les actions de compte alignés en
 * une seule rangée, sans aucun repli en dessous de 768px.
 *
 * Le test se pose sur la couture asynchrone (CONTRIBUTING.md §7) — l'état de
 * l'écran une fois la session résolue — pas sur une fonction pure : c'est le
 * principal (`GET /v1/me`) qui arbitre quels liens apparaissent
 * (`isAuthenticated`), dans les deux mises en page. Sans greffon (`test/setup.ts`) :
 * les entrées d'un greffon sont vérifiées dans ses propres tests.
 *
 * `renderWithProviders` (test/render.tsx) ne monte ni routeur ni
 * `SessionProvider` : reconstruit à la main.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PrincipalRead } from '@/api/schemas';
import { ThemeProvider } from '@/design/theme';
import { SessionProvider } from '@/features/auth';
import { I18nProvider } from '@/i18n';
import { fr } from '@/i18n/locales/fr';

import { AppLayout } from './AppLayout';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

function principal(role: 'member' | 'admin' = 'member'): PrincipalRead {
  return {
    auth_method: 'session',
    is_guest: false,
    role,
    user: {
      id: '77777777-7777-4777-8777-777777777777',
      email: 'camille@example.com',
      display_name: 'Camille',
      role,
      is_guest: false,
      is_active: true,
      expires_at: null,
      last_login_at: null,
      created_at: '2026-09-05T00:00:00Z',
      updated_at: '2026-09-05T00:00:00Z',
    },
  };
}

function stubMe(value: PrincipalRead | null): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/v1/me')) {
        return Promise.resolve(
          value === null ? new Response(null, { status: 401 }) : jsonResponse(value),
        );
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );
}

/** `ThemeProvider` (via `ThemeToggle`, monté par `AppLayout`) lit `matchMedia` — absent de jsdom. */
function stubMatchMedia(): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(
      (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
        }) as unknown as MediaQueryList,
    ),
  );
}

function renderLayout(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <ThemeProvider>
          <MemoryRouter initialEntries={['/']}>
            <SessionProvider>
              <Routes>
                <Route element={<AppLayout />}>
                  {/* `*` plutôt que `index` : un clic sur un lien du tiroir
                      navigue réellement (`/knowledge-bases`, `/admin`…) —
                      sans route de repli, React Router ne matche rien et
                      démonte tout l'arbre, `AppLayout` compris. */}
                  <Route path="*" element={<p>Contenu de la page</p>} />
                </Route>
              </Routes>
            </SessionProvider>
          </MemoryRouter>
        </ThemeProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe('AppLayout — menu mobile', () => {
  beforeEach(() => {
    stubMatchMedia();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('replié par défaut, s’ouvre au clic du bouton bascule', async () => {
    const user = userEvent.setup();
    stubMe(principal());
    renderLayout();
    // Attendre une décision liée à la session (ici : Paramètres, réservé à un
    // compte authentifié) prouve que `GET /v1/me` s'est résolu — un simple
    // délai fixe est raciness selon la vitesse de la machine, `findBy…`
    // interroge jusqu'à ce que ce soit vrai, jamais avant.
    await screen.findByText(fr['nav.settings']);

    const toggle = screen.getByRole('button', { name: fr['nav.menuOpen'] });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    // Replié : les liens du tiroir n'existent pas dans le DOM (démontage
    // conditionnel), seule la barre desktop (masquée en CSS, pas en DOM) les
    // porte encore une fois — deux occurrences attendues une fois ouvert.
    expect(screen.getAllByText(fr['nav.knowledgeBases'])).toHaveLength(1);

    await user.click(toggle);

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: fr['nav.menuClose'] })).toBeDefined();
    expect(screen.getAllByText(fr['nav.knowledgeBases'])).toHaveLength(2);
  });

  it('se referme sur Échap et rend le focus au bouton bascule', async () => {
    const user = userEvent.setup();
    stubMe(principal());
    renderLayout();
    await screen.findByText(fr['nav.settings']);

    const toggle = screen.getByRole('button', { name: fr['nav.menuOpen'] });
    await user.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    await user.keyboard('{Escape}');

    expect(
      screen.getByRole('button', { name: fr['nav.menuOpen'] }).getAttribute('aria-expanded'),
    ).toBe('false');
    expect(document.activeElement).toBe(toggle);
  });

  it('se referme au clic sur un lien du tiroir', async () => {
    const user = userEvent.setup();
    stubMe(principal());
    renderLayout();
    await screen.findByText(fr['nav.settings']);

    await user.click(screen.getByRole('button', { name: fr['nav.menuOpen'] }));
    const [, drawerLink] = screen.getAllByText(fr['nav.knowledgeBases']);
    if (drawerLink === undefined) throw new Error('Lien du tiroir introuvable.');
    await user.click(drawerLink);

    expect(screen.getByRole('button', { name: fr['nav.menuOpen'] })).toBeDefined();
  });

  it('propose Paramètres à un compte authentifié', async () => {
    stubMe(principal());
    renderLayout();
    await screen.findByText(fr['nav.settings']);

    expect(screen.getAllByText(fr['nav.settings'])).toHaveLength(1); // barre desktop uniquement
  });

  it('ne propose pas Paramètres à un visiteur anonyme', async () => {
    stubMe(null);
    renderLayout();
    await screen.findByText(fr['nav.login']);

    expect(screen.queryByText(fr['nav.settings'])).toBeNull();
  });

  it('le tiroir porte les mêmes liens que la barre desktop, dans le même ordre', async () => {
    const user = userEvent.setup();
    stubMe(principal('admin'));
    renderLayout();
    await screen.findByRole('link', { name: fr['nav.settings'] });
    await user.click(screen.getByRole('button', { name: fr['nav.menuOpen'] }));

    const expectedLabels = [
      fr['nav.knowledgeBases'],
      fr['nav.evaluationsSummary'],
      fr['nav.developers'],
      fr['nav.settings'],
    ];
    // Deux `<nav>` portent le même nom accessible (barre desktop + tiroir) :
    // le second à apparaître dans le DOM est le tiroir, monté après coup.
    const [, drawer] = screen.getAllByRole('navigation', { name: fr['nav.primaryLabel'] });
    if (drawer === undefined) throw new Error('Le tiroir mobile est introuvable.');
    const drawerLabels = within(drawer)
      .getAllByRole('link')
      .map((link) => link.textContent);

    expect(drawerLabels).toEqual(expectedLabels);
  });

  it('le tiroir porte aussi l’identité et la déconnexion, à côté de celles de l’en-tête', async () => {
    // « Se déconnecter » en toutes lettres à côté du logo et du sélecteur de
    // thème débordait l'en-tête sur téléphone (constaté en conditions
    // réelles, 2026-09-07) — désormais dupliqué dans le tiroir, `.headerAccount`
    // masque l'original par CSS sous 48rem (non observable ici, cf. le
    // commentaire équivalent pour les liens ci-dessus : deux copies dans le
    // DOM est le patron déjà établi par ce fichier, pas une régression).
    const user = userEvent.setup();
    stubMe(principal('member'));
    renderLayout();
    await screen.findByRole('button', { name: fr['nav.logout'] });
    await user.click(screen.getByRole('button', { name: fr['nav.menuOpen'] }));

    expect(screen.getAllByRole('button', { name: fr['nav.logout'] })).toHaveLength(2);
  });
});
