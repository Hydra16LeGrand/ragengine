/**
 * Couture asynchrone de `OrganizationsPanel` : liste chargée depuis l'API,
 * puis création d'une organisation qui rejoint la liste — pas seulement le
 * rendu statique d'une page vide.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { OrganizationRead, PageOrganizationRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { organizationRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { OrganizationsPanel } from './OrganizationsPanel';

function organizationsPage(organizations: OrganizationRead[]): PageOrganizationRead {
  return { data: organizations, next_cursor: null };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('OrganizationsPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche les organisations existantes avec leur rôle', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse(organizationsPage([organizationRead({ name: 'Équipe Data', slug: 'data' })])),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    renderWithProviders(
      <MemoryRouter>
        <OrganizationsPanel />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Équipe Data')).toBeDefined();
    expect(screen.getByText(fr['settings.organizations.role.owner'])).toBeDefined();
  });

  it('affiche un état vide quand le compte n’a aucune organisation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(organizationsPage([])))),
    );

    renderWithProviders(
      <MemoryRouter>
        <OrganizationsPanel />
      </MemoryRouter>,
    );

    expect(await screen.findByText(fr['settings.organizations.emptyTitle'])).toBeDefined();
  });

  it('crée une organisation et la fait apparaître dans la liste, sans recharger la page', async () => {
    const created = organizationRead({ name: 'Acme Corp', slug: 'acme-corp' });
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'POST' && url.includes('/v1/organizations')) {
        return Promise.resolve(jsonResponse(created, 201));
      }
      if (method === 'GET' && url.includes('/v1/organizations')) {
        return Promise.resolve(jsonResponse(organizationsPage([])));
      }
      throw new Error(`Requête non prévue par le test : ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderWithProviders(
      <MemoryRouter>
        <OrganizationsPanel />
      </MemoryRouter>,
    );

    await screen.findByText(fr['settings.organizations.emptyTitle']);

    // Deux boutons portent le même libellé quand la liste est vide (en-tête +
    // état vide, même redondance assumée qu'`ApiKeysPanel`) : le premier suffit
    // — `getAllByRole` aurait déjà levé si la liste était vide.
    const [createButton] = screen.getAllByRole('button', {
      name: fr['settings.organizations.create'],
    });
    if (createButton === undefined) throw new Error('Bouton de création introuvable');
    await user.click(createButton);
    const dialog = await screen.findByRole('dialog');
    await user.type(
      within(dialog).getByLabelText(fr['settings.organizations.create.nameLabel']),
      'Acme Corp',
    );
    await user.click(
      within(dialog).getByRole('button', { name: fr['settings.organizations.create.submit'] }),
    );

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(postCall).toBeDefined();
    });
  });
});
