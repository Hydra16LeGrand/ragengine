/**
 * Couture asynchrone de `OrganizationMembersPanel` : l'annuaire est visible à
 * tout membre, mais seuls les contrôles (ajout, changement de rôle, retrait)
 * dépendent de `isOwner` — c'est exactement le point que ce test verrouille,
 * pas la seule fonction de rendu d'une ligne.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { OrganizationMemberRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { MEMBER_USER_ID, organizationMemberRead, organizationRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { OrganizationMembersPanel } from './OrganizationMembersPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function stubMembers(members: OrganizationMemberRead[]): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(members)));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('OrganizationMembersPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche l’annuaire à un membre non propriétaire, sans aucun contrôle de mutation', async () => {
    stubMembers([organizationMemberRead({ role: 'owner' })]);

    renderWithProviders(
      <OrganizationMembersPanel organization={organizationRead()} isOwner={false} />,
    );

    expect(await screen.findByText(MEMBER_USER_ID)).toBeDefined();
    expect(
      screen.queryByRole('button', { name: fr['settings.organizations.members.remove'] }),
    ).toBeNull();
    expect(screen.queryByLabelText(fr['settings.organizations.members.addLabel'])).toBeNull();
  });

  it('ajoute un membre — le propriétaire saisit un identifiant et un rôle', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'PUT' && url.includes('/members/')) {
        return Promise.resolve(jsonResponse(organizationMemberRead({ role: 'member' }), 201));
      }
      return Promise.resolve(jsonResponse([]));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderWithProviders(
      <OrganizationMembersPanel organization={organizationRead()} isOwner={true} />,
    );

    await user.type(
      screen.getByLabelText(fr['settings.organizations.members.addLabel']),
      MEMBER_USER_ID,
    );
    await user.click(
      screen.getByRole('button', { name: fr['settings.organizations.members.add'] }),
    );

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
      expect(putCall).toBeDefined();
    });
    expect(
      await screen.findByText(fr['settings.organizations.members.grantedTitle']),
    ).toBeDefined();
  });

  it('retire un membre après confirmation', async () => {
    const fetchMock = vi.fn((_input: string | URL | Request, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'DELETE') return Promise.resolve(jsonResponse(undefined, 204));
      return Promise.resolve(jsonResponse([organizationMemberRead()]));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderWithProviders(
      <OrganizationMembersPanel organization={organizationRead()} isOwner={true} />,
    );

    await screen.findByText(MEMBER_USER_ID);
    await user.click(
      screen.getByRole('button', { name: fr['settings.organizations.members.remove'] }),
    );

    const dialog = await screen.findByRole('dialog');
    await user.click(
      within(dialog).getByRole('button', {
        name: fr['settings.organizations.members.removeConfirm'],
      }),
    );

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
      expect(deleteCall).toBeDefined();
    });
  });
});
