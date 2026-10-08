/**
 * Couture asynchrone de `MembersPanel` : la ligne d'annuaire assemble le nom
 * du membre, le badge « Vous », le sélecteur de rôle et le bouton de retrait
 * dans une même rangée — exactement le regroupement dont l'empilement mobile
 * (`MembersPanel.module.css`, `@media (max-width: 30rem)`) dépend pour ne pas
 * écraser `.memberName` à quelques pixels de large. jsdom ne calcule aucune
 * mise en page (pas de retour à la ligne, pas de largeur réelle) : ce test ne
 * peut donc pas prouver le rendu visuel à 360px, seulement que tous les
 * éléments attendus sont bien présents et fonctionnels une fois la donnée
 * arrivée — non-régression fonctionnelle du cas qui a régressé (un membre
 * sans e-mail, propriétaire de sa propre base, donc « Vous »).
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SessionProvider } from '@/features/auth';
import { fr } from '@/i18n/locales/fr';
import {
  MEMBER_USER_ID,
  knowledgeBaseMemberRead,
  knowledgeBaseRead,
  principalRead,
} from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { MembersPanel } from './MembersPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function urlOf(input: string | URL | Request): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

function renderPanel(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <SessionProvider>
      <MembersPanel base={knowledgeBaseRead()} />
    </SessionProvider>,
  );
}

const OWNER_MEMBER = knowledgeBaseMemberRead({
  user_id: MEMBER_USER_ID,
  display_name: 'Camille',
  role: 'owner',
});

describe('MembersPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche un membre propriétaire — nom, badge « Vous », rôle et retrait tous présents', async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = urlOf(input);
      if (url.endsWith('/me')) return Promise.resolve(jsonResponse(principalRead()));
      if (url.includes('/members')) return Promise.resolve(jsonResponse([OWNER_MEMBER]));
      return Promise.resolve(jsonResponse([]));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();

    expect(await screen.findByText('Camille')).toBeDefined();
    expect(screen.getByText(fr['kb.members.you'])).toBeDefined();

    const roleSelect = screen.getByRole('combobox', { name: fr['kb.members.roleLabel'] });
    expect((roleSelect as HTMLSelectElement).value).toBe('owner');

    expect(screen.getByRole('button', { name: fr['kb.members.remove'] })).toBeDefined();
  });

  it('change le rôle d’un membre depuis la rangée de l’annuaire', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = urlOf(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/me')) return Promise.resolve(jsonResponse(principalRead()));
      if (method === 'PUT' && url.includes('/members/')) {
        return Promise.resolve(
          jsonResponse(
            knowledgeBaseMemberRead({
              user_id: MEMBER_USER_ID,
              display_name: 'Camille',
              role: 'editor',
            }),
          ),
        );
      }
      if (url.includes('/members')) {
        return Promise.resolve(
          jsonResponse([
            knowledgeBaseMemberRead({
              user_id: MEMBER_USER_ID,
              display_name: 'Camille',
              role: 'viewer',
            }),
          ]),
        );
      }
      return Promise.resolve(jsonResponse([]));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderPanel();

    const roleSelect = await screen.findByRole('combobox', { name: fr['kb.members.roleLabel'] });
    await user.selectOptions(roleSelect, fr['kb.role.editor']);

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
      expect(putCall).toBeDefined();
    });
  });

  it('retire un membre après confirmation', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = urlOf(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/me')) return Promise.resolve(jsonResponse(principalRead()));
      if (method === 'DELETE') return Promise.resolve(jsonResponse(undefined, 204));
      return Promise.resolve(jsonResponse([OWNER_MEMBER]));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderPanel();

    await screen.findByText('Camille');
    await user.click(screen.getByRole('button', { name: fr['kb.members.remove'] }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: fr['kb.members.removeConfirm'] }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
      expect(deleteCall).toBeDefined();
    });
  });
});
