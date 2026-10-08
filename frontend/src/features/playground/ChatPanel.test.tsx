/**
 * ADR-053 : bornes de taille du chat du Playground, sur la couture asynchrone —
 * ce que l'écran envoie une fois la conversation avancée, et ce qu'il affiche
 * quand le backend refuse un texte trop long.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ChatTurn, LlmVendor, LLMProviderStatus, RetrievalChatRequest } from '@/api/schemas';
import { MAX_CHAT_MESSAGES, MAX_CHAT_TRANSCRIPT_LENGTH, MAX_QUERY_LENGTH } from '@/api/text-limits';
import { fr } from '@/i18n/locales/fr';
import { formatInteger } from '@/lib/format';
import { renderWithProviders } from '@/test/render';

import { ChatPanel } from './ChatPanel';
import { DEFAULT_RETRIEVAL_SETTINGS } from './settings';

const KB_ID = '33333333-3333-4333-8333-333333333333';

const PROVIDER: LLMProviderStatus = {
  provider_id: 'claude-test',
  label: 'Claude',
  vendor: 'anthropic',
  context_window: 200_000,
  supports_streaming: true,
  available: true,
};

/**
 * Flux SSE piloté par le test, un évènement à la fois — nécessaire pour
 * prouver un **ordre** (sources avant jetons, jetons dans l'ordre d'arrivée)
 * plutôt qu'un simple état final déjà correct par construction si le flux est
 * livré d'un bloc. `signal` reproduit le comportement réel de `fetch` : un
 * flux ouvert dont le contrôleur passe en erreur dès que l'appelant annule,
 * exactement ce que `streamChat` (src/api/retrieval.ts) attend pour
 * distinguer un abandon volontaire d'une panne.
 */
function controlledSseResponse(signal?: AbortSignal | null): {
  response: Response;
  push: (event: string, data: unknown) => void;
  close: () => void;
} {
  const encoder = new TextEncoder();
  let sink: ReadableStreamDefaultController<Uint8Array> | undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      sink = controller;
      if (signal === null || signal === undefined) return;
      if (signal.aborted) {
        controller.error(new DOMException('Annulé côté client.', 'AbortError'));
        return;
      }
      signal.addEventListener('abort', () => {
        controller.error(new DOMException('Annulé côté client.', 'AbortError'));
      });
    },
  });
  return {
    response: new Response(stream, {
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
    }),
    push: (event, data) => {
      sink?.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
    },
    close: () => {
      sink?.close();
    },
  };
}

const SOURCE = {
  chunk_id: 'chunk-1',
  document_id: '11111111-1111-4111-8111-111111111111',
  filename: 'rapport.pdf',
  content: 'Le capital est Paris.',
  similarity: 0.91,
  vector_similarity: 0.93,
  term_similarity: 0.7,
  page_start: 3,
  page_end: 3,
};

function sseResponse(events: { event: string; data: unknown }[]): Response {
  const text = events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('');
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(bytes);
        controller.close();
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  );
}

/** `422` tel que le rend la façade pour une requête trop longue (Pydantic → RFC 9457). */
function tooLongResponse(loc: (string | number)[]): Response {
  return new Response(
    JSON.stringify({
      type: '/problems/validation-error',
      title: 'Unprocessable Content',
      status: 422,
      detail: 'La requête ne respecte pas le schéma attendu.',
      errors: [
        {
          type: 'string_too_long',
          loc: ['body', ...loc],
          msg: `String should have at most ${String(MAX_QUERY_LENGTH)} characters`,
          ctx: { max_length: MAX_QUERY_LENGTH },
        },
      ],
    }),
    { status: 422, headers: { 'content-type': 'application/problem+json' } },
  );
}

/** `findByText` ne normalise pas le texte cherché. */
function asRendered(template: string): string {
  return template.replace('{max}', formatInteger(MAX_QUERY_LENGTH, 'fr')).replace(/\s+/g, ' ');
}

/**
 * Ensemble vide par défaut : ces tests portent sur le flux SSE, pas sur
 * l'honnêteté du sélecteur de modèle (couverte par son propre `describe`
 * ci-dessous) — un ensemble vide est le repli sûr (voir le commentaire de
 * `ChatPanelProps.personalKeyVendors`).
 */
function renderChat(
  personalKeyVendors: ReadonlySet<LlmVendor> = new Set<LlmVendor>(),
  forcedModelId?: string | null,
): void {
  renderWithProviders(
    <ChatPanel
      knowledgeBaseId={KB_ID}
      settings={DEFAULT_RETRIEVAL_SETTINGS}
      providers={[PROVIDER]}
      personalKeyVendors={personalKeyVendors}
      forcedModelId={forcedModelId}
      onSwitchToSearch={() => undefined}
    />,
  );
}

describe('ChatPanel — bornes de taille (ADR-053)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('une longue conversation reste dans les bornes du backend à chaque envoi', async () => {
    const sent: ChatTurn[][] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const body = JSON.parse(init?.body as string) as RetrievalChatRequest;
        sent.push(body.messages);
        return Promise.resolve(
          sseResponse([
            { event: 'sources', data: [] },
            { event: 'token', data: `réponse ${String(sent.length)} `.padEnd(3_000, 'r') },
            { event: 'done', data: '' },
          ]),
        );
      }),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);

    // 25 échanges : au-delà du nombre de tours autorisé (40) et de la taille totale.
    for (let index = 0; index < 25; index += 1) {
      fireEvent.change(textbox, { target: { value: `question ${String(index)}` } });
      fireEvent.keyDown(textbox, { key: 'Enter' });
      await waitFor(() => {
        expect(sent).toHaveLength(index + 1);
      });
      await waitFor(() => {
        expect(textbox.disabled).toBe(false);
      });
    }

    for (const messages of sent) {
      expect(messages.length).toBeLessThanOrEqual(MAX_CHAT_MESSAGES);
      const length = messages.reduce((sum, turn) => sum + turn.content.length, 0);
      expect(length).toBeLessThanOrEqual(MAX_CHAT_TRANSCRIPT_LENGTH);
      expect(messages[0]?.role).toBe('user');
    }
    expect(sent.at(-1)?.at(-1)).toEqual({ role: 'user', content: 'question 24' });
  });

  it('affiche un refus de taille traduit, et borne la saisie', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(tooLongResponse(['messages', 0, 'content']))),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    expect(textbox.maxLength).toBe(MAX_QUERY_LENGTH);

    fireEvent.change(textbox, { target: { value: 'x'.repeat(MAX_QUERY_LENGTH) } });
    expect(screen.getByText(asRendered(fr['error.inputLimitReached']))).toBeDefined();
    fireEvent.keyDown(textbox, { key: 'Enter' });

    expect(await screen.findByText(asRendered(fr['error.textTooLong']))).toBeDefined();
    expect(screen.queryByText('La requête ne respecte pas le schéma attendu.')).toBeNull();
  });
});

/**
 * Le sélecteur de modèle reste utilisable même quand le choix qu'il affiche
 * n'est pas celui réellement facturé : dès que la clé qui paie n'est pas la
 * clé personnelle de l'appelant, le backend impose son propre modèle
 * (`resolve_llm_funding`, addendum 2026-09-21 à ADR-054). Sans indication,
 * l'écran mentirait par omission. `personalKeyVendors` porte cette
 * information (cf. son commentaire sur `ChatPanelProps`).
 */
describe('ChatPanel — honnêteté du sélecteur de modèle (addendum 2026-09-21 à ADR-054)', () => {
  it('prévient que le choix est ignoré sans clé personnelle pour ce fournisseur', () => {
    renderChat(new Set());

    const vendor = fr['settings.llmKeys.vendor.anthropic'];
    expect(
      screen.getByText(fr['playground.chat.modelOverriddenHint'].replace('{vendor}', vendor)),
    ).toBeDefined();
    expect(
      screen.queryByText(fr['playground.chat.modelAppliesHint'].replace('{vendor}', vendor)),
    ).toBeNull();
  });

  it('ne dit rien quand le serveur n’impose aucun modèle (cœur seul)', () => {
    renderChat(new Set(), null);

    const vendor = fr['settings.llmKeys.vendor.anthropic'];
    expect(
      screen.queryByText(fr['playground.chat.modelOverriddenHint'].replace('{vendor}', vendor)),
    ).toBeNull();
    expect(
      screen.queryByText(fr['playground.chat.modelAppliesHint'].replace('{vendor}', vendor)),
    ).toBeNull();
  });

  it('confirme que le choix s’applique dès qu’une clé personnelle est déclarée pour ce fournisseur', () => {
    renderChat(new Set<LlmVendor>(['anthropic']));

    const vendor = fr['settings.llmKeys.vendor.anthropic'];
    expect(
      screen.getByText(fr['playground.chat.modelAppliesHint'].replace('{vendor}', vendor)),
    ).toBeDefined();
    expect(
      screen.queryByText(fr['playground.chat.modelOverriddenHint'].replace('{vendor}', vendor)),
    ).toBeNull();
  });
});

/**
 * `sources → token* → done|error` : la couture la plus fragile de l'écran
 * (voir l'audit de dette 2026-09 (interne), section Frontend). Chaque
 * test pilote le flux évènement par évènement, jamais livré d'un bloc, pour
 * vérifier un **ordre** et pas seulement un état final.
 */
describe('ChatPanel — couture SSE sources → token* → done|error', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche les sources avant tout jeton, puis concatène les jetons dans l’ordre du flux', async () => {
    const stream: { current: ReturnType<typeof controlledSseResponse> | null } = { current: null };
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        stream.current = controlledSseResponse(init?.signal);
        return Promise.resolve(stream.current.response);
      }),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    fireEvent.change(textbox, { target: { value: 'Quelle est la capitale ?' } });
    fireEvent.keyDown(textbox, { key: 'Enter' });
    await waitFor(() => {
      expect(stream.current).not.toBeNull();
    });

    // Avant les sources : l'état d'attente est visible, aucune réponse.
    await screen.findByText(fr['playground.chat.retrieving']);
    expect(screen.queryByText(fr['playground.chat.sources'])).toBeNull();

    stream.current?.push('sources', [SOURCE]);
    await screen.findByText(fr['playground.chat.sources']);
    expect(screen.getByText('rapport.pdf')).toBeDefined();
    expect(screen.queryByText(fr['playground.chat.retrieving'])).toBeNull();

    // Les jetons ne se concatènent que dans l'ordre où ils arrivent.
    stream.current?.push('token', 'Paris');
    await waitFor(() => {
      expect(screen.getByText('Paris', { exact: false })).toBeDefined();
    });
    stream.current?.push('token', ' est');
    await waitFor(() => {
      expect(screen.getByText('Paris est', { exact: false })).toBeDefined();
    });
    stream.current?.push('token', ' la capitale.');
    await waitFor(() => {
      expect(screen.getByText('Paris est la capitale.')).toBeDefined();
    });

    stream.current?.push('done', {});
    stream.current?.close();
  });

  it('`done` fige la réponse : saisie de nouveau disponible, contenu inchangé', async () => {
    const stream: { current: ReturnType<typeof controlledSseResponse> | null } = { current: null };
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        stream.current = controlledSseResponse(init?.signal);
        return Promise.resolve(stream.current.response);
      }),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    fireEvent.change(textbox, { target: { value: 'Bonjour' } });
    fireEvent.keyDown(textbox, { key: 'Enter' });
    await waitFor(() => {
      expect(stream.current).not.toBeNull();
    });

    stream.current?.push('sources', []);
    stream.current?.push('token', 'Bonjour');
    stream.current?.push('token', ' !');
    await waitFor(() => {
      expect(screen.getByText('Bonjour !')).toBeDefined();
    });
    // Toujours en cours : saisie verrouillée, bouton d'arrêt visible.
    expect(textbox.disabled).toBe(true);
    expect(screen.getByRole('button', { name: fr['playground.chat.stop'] })).toBeDefined();

    stream.current?.push('done', {});
    stream.current?.close();

    await waitFor(() => {
      expect(textbox.disabled).toBe(false);
    });
    expect(screen.getByRole('button', { name: fr['playground.chat.send'] })).toBeDefined();
    expect(screen.queryByRole('button', { name: fr['playground.chat.stop'] })).toBeNull();
    // La réponse déjà reçue reste affichée telle quelle.
    expect(screen.getByText('Bonjour !')).toBeDefined();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('une erreur en milieu de flux laisse l’écran cohérent : contenu partiel conservé, saisie réactivée', async () => {
    const stream: { current: ReturnType<typeof controlledSseResponse> | null } = { current: null };
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        stream.current = controlledSseResponse(init?.signal);
        return Promise.resolve(stream.current.response);
      }),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    fireEvent.change(textbox, { target: { value: 'Quelle est la capitale ?' } });
    fireEvent.keyDown(textbox, { key: 'Enter' });
    await waitFor(() => {
      expect(stream.current).not.toBeNull();
    });

    stream.current?.push('sources', [SOURCE]);
    await screen.findByText(fr['playground.chat.sources']);
    stream.current?.push('token', 'Réponse partielle avant la panne');
    await waitFor(() => {
      expect(screen.getByText('Réponse partielle avant la panne', { exact: false })).toBeDefined();
    });

    stream.current?.push('error', {
      type: 'urn:ragengine:llm:provider-failure',
      title: 'Fournisseur indisponible',
      status: 502,
      detail: 'Le fournisseur a retourné une erreur pendant la génération.',
    });

    const alert = await screen.findByRole('alert');
    // Panne en cours de flux : le libellé « refus » ne s'applique pas, c'est
    // une génération interrompue, pas une requête rejetée d'entrée.
    expect(screen.getByText(fr['playground.chat.errorTitle'])).toBeDefined();
    expect(screen.queryByText(fr['playground.chat.refusedTitle'])).toBeNull();
    expect(alert.textContent).toContain(
      'Le fournisseur a retourné une erreur pendant la génération.',
    );
    // Le contenu déjà reçu et les sources restent visibles : rien n'est effacé.
    expect(screen.getByText('Réponse partielle avant la panne', { exact: false })).toBeDefined();
    expect(screen.getByText('rapport.pdf')).toBeDefined();
    // L'écran redevient utilisable.
    expect(textbox.disabled).toBe(false);
    expect(screen.getByRole('button', { name: fr['playground.chat.send'] })).toBeDefined();
  });

  it('cliquer sur « Arrêter » interrompt proprement : rien d’orphelin, saisie de nouveau disponible', async () => {
    const stream: { current: ReturnType<typeof controlledSseResponse> | null } = { current: null };
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        stream.current = controlledSseResponse(init?.signal);
        return Promise.resolve(stream.current.response);
      }),
    );
    renderChat();
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    fireEvent.change(textbox, { target: { value: 'Question' } });
    fireEvent.keyDown(textbox, { key: 'Enter' });
    await waitFor(() => {
      expect(stream.current).not.toBeNull();
    });

    stream.current?.push('sources', []);
    stream.current?.push('token', 'Réponse partielle');
    await waitFor(() => {
      expect(screen.getByText('Réponse partielle', { exact: false })).toBeDefined();
    });

    fireEvent.click(screen.getByRole('button', { name: fr['playground.chat.stop'] }));

    await waitFor(() => {
      expect(textbox.disabled).toBe(false);
    });
    expect(screen.getByRole('button', { name: fr['playground.chat.send'] })).toBeDefined();
    expect(screen.queryByRole('button', { name: fr['playground.chat.stop'] })).toBeNull();
    // Arrêt volontaire : pas d'état d'erreur, le texte déjà reçu reste affiché.
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('Réponse partielle', { exact: false })).toBeDefined();
    // La conversation reprend normalement.
    fireEvent.change(textbox, { target: { value: 'Autre question' } });
    expect(textbox.value).toBe('Autre question');
  });

  it('démonter l’écran en cours de flux annule la requête (pas de flux ouvert après navigation)', async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        capturedSignal = init?.signal ?? undefined;
        const stream = controlledSseResponse(capturedSignal);
        stream.push('sources', []);
        stream.push('token', 'Réponse en cours');
        return Promise.resolve(stream.response);
      }),
    );
    const view = renderWithProviders(
      <ChatPanel
        knowledgeBaseId={KB_ID}
        settings={DEFAULT_RETRIEVAL_SETTINGS}
        providers={[PROVIDER]}
        personalKeyVendors={new Set<LlmVendor>()}
        onSwitchToSearch={() => undefined}
      />,
    );
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.chat.inputLabel']);
    fireEvent.change(textbox, { target: { value: 'Question' } });
    fireEvent.keyDown(textbox, { key: 'Enter' });

    await waitFor(() => {
      expect(screen.getByText('Réponse en cours', { exact: false })).toBeDefined();
    });
    expect(capturedSignal?.aborted).not.toBe(true);

    view.unmount();

    // Le flux abandonné à la navigation ne doit pas rester ouvert : sans quoi
    // il continuerait de consommer des tokens facturés après le départ de
    // l'utilisateur (commentaire de ChatPanel.tsx sur l'effet de nettoyage).
    expect(capturedSignal?.aborted).toBe(true);
  });
});
