/**
 * Couture asynchrone de `LlmKeysPanel` : état par fournisseur chargé depuis
 * l'API, déclaration puis retrait d'une clé personnelle — pas seulement le
 * rendu statique d'une liste vide.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { UserLlmKeyRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { renderWithProviders } from '@/test/render';

import { LlmKeysPanel } from './LlmKeysPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: status === 204 ? {} : { 'content-type': 'application/json' },
  });
}

function requestOf(input: string | URL | Request): { url: string } {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  return { url };
}

describe('LlmKeysPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche les trois fournisseurs sans clé par défaut', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([]))),
    );

    renderWithProviders(
      <MemoryRouter>
        <LlmKeysPanel />
      </MemoryRouter>,
    );

    expect(await screen.findByText(fr['settings.llmKeys.vendor.anthropic'])).toBeDefined();
    expect(screen.getByText(fr['settings.llmKeys.vendor.openai'])).toBeDefined();
    expect(screen.getByText(fr['settings.llmKeys.vendor.groq'])).toBeDefined();
    expect(screen.getAllByText(fr['settings.llmKeys.status.none'])).toHaveLength(3);
  });

  it('affiche une clé déjà déclarée comme active, avec sa date', async () => {
    const existing: UserLlmKeyRead = {
      provider: 'anthropic',
      created_at: '2026-08-30T10:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([existing]))),
    );

    renderWithProviders(
      <MemoryRouter>
        <LlmKeysPanel />
      </MemoryRouter>,
    );

    expect(await screen.findByText(fr['settings.llmKeys.status.active'])).toBeDefined();
    // Deux fournisseurs sur trois restent sans clé.
    expect(screen.getAllByText(fr['settings.llmKeys.status.none'])).toHaveLength(2);
  });

  it('déclare une clé personnelle sans recharger la page', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const { url } = requestOf(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'GET' && url.includes('/v1/me/llm-keys')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (method === 'PUT' && url.includes('/v1/me/llm-keys/anthropic')) {
        const saved: UserLlmKeyRead = {
          provider: 'anthropic',
          created_at: '2026-09-05T00:00:00Z',
          updated_at: '2026-09-05T00:00:00Z',
        };
        return Promise.resolve(jsonResponse(saved));
      }
      throw new Error(`Requête non prévue par le test : ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderWithProviders(
      <MemoryRouter>
        <LlmKeysPanel />
      </MemoryRouter>,
    );

    await screen.findByText(fr['settings.llmKeys.vendor.anthropic']);
    const [addButton] = screen.getAllByRole('button', { name: fr['settings.llmKeys.add'] });
    if (addButton === undefined) throw new Error('Bouton « Ajouter une clé » introuvable');
    await user.click(addButton);

    await user.type(screen.getByLabelText(fr['settings.llmKeys.keyLabel']), 'sk-test-123');
    await user.click(screen.getByRole('button', { name: fr['settings.llmKeys.save'] }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
      expect(putCall).toBeDefined();
    });
  });

  it('retire une clé personnelle après confirmation', async () => {
    const existing: UserLlmKeyRead = {
      provider: 'groq',
      created_at: '2026-08-30T10:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const { url } = requestOf(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'GET' && url.includes('/v1/me/llm-keys')) {
        return Promise.resolve(jsonResponse([existing]));
      }
      if (method === 'DELETE' && url.includes('/v1/me/llm-keys/groq')) {
        return Promise.resolve(jsonResponse(null, 204));
      }
      throw new Error(`Requête non prévue par le test : ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderWithProviders(
      <MemoryRouter>
        <LlmKeysPanel />
      </MemoryRouter>,
    );

    await screen.findByText(fr['settings.llmKeys.status.active']);
    await user.click(screen.getByRole('button', { name: fr['settings.llmKeys.remove.action'] }));

    const dialog = await screen.findByRole('dialog');
    await user.click(
      screen.getAllByRole('button', { name: fr['settings.llmKeys.remove.confirm'] })[0] ??
        (() => {
          throw new Error('Bouton de confirmation introuvable');
        })(),
    );
    expect(dialog).toBeDefined();

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
      expect(deleteCall).toBeDefined();
    });
  });
});
