/**
 * Couture asynchrone d'`EvaluationsPanel` (audit dette technique 2026-09,
 * section Frontend — 603 lignes, deuxième plus gros trou de couverture).
 *
 * Quatre coutures distinctes :
 *  - le sondage de l'historique (`knowledgeBaseEvaluationsQueryOptions`) et du
 *    détail d'un run déplié (`knowledgeBaseEvaluationQueryOptions`) démarre
 *    tant qu'un run est `pending`, et **s'arrête** dès sa résolution — sinon
 *    l'écran continuerait d'interroger la façade indéfiniment ;
 *  - un run résolu en échec s'affiche tel quel dans l'historique et son détail ;
 *  - un lancement synchrone réussi fige le score, ferme le bandeau d'attente ;
 *  - un refus de lancement affiche le bon conseil et laisse l'écran réutilisable.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { queryClient as appQueryClient } from '@/api/query-client';
import type { EvaluationRunRead, EvaluationRunSummary, LlmVendor } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { formatPercent } from '@/lib/format';
import { knowledgeBaseRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { EvaluationsPanel } from './EvaluationsPanel';

const KB_ID = '33333333-3333-4333-8333-333333333333';
const RUN_ID = '88888888-8888-4888-8888-888888888888';

/** Rythme de sondage réel (`EVALUATION_POLL_INTERVAL_MS`, src/api/evaluations.ts). */
const POLL_INTERVAL_MS = 4_000;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function problemResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title: 'Refus', status, detail }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
}

function urlOf(input: string | URL | Request): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

function methodOf(init: RequestInit | undefined): string {
  return (init?.method ?? 'GET').toUpperCase();
}

/** Catalogue sans fournisseur : suffisant partout sauf pour lancer un run. */
const EMPTY_CATALOG = {
  embeddings: [],
  rerankers: [],
  llm: [],
  observability: { provider_id: 'langfuse', label: 'Langfuse', available: false },
};

const PROVIDER = {
  provider_id: 'claude-test',
  label: 'Claude',
  vendor: 'anthropic',
  context_window: 200_000,
  supports_streaming: true,
  available: true,
};

function evaluationSummary(overrides: Partial<EvaluationRunSummary> = {}): EvaluationRunSummary {
  return {
    id: RUN_ID,
    knowledge_base_id: KB_ID,
    requested_by_id: null,
    llm_provider_id: 'provider-a',
    sample_size: 30,
    status: 'pending',
    item_count: 0,
    average_judge_score: null,
    retrieval_recall_rate: null,
    created_at: '2026-09-10T10:00:00Z',
    ...overrides,
  };
}

function evaluationDetail(overrides: Partial<EvaluationRunRead> = {}): EvaluationRunRead {
  return {
    id: RUN_ID,
    knowledge_base_id: KB_ID,
    requested_by_id: null,
    llm_provider_id: 'provider-a',
    sample_size: 30,
    status: 'pending',
    item_count: 0,
    average_judge_score: null,
    retrieval_recall_rate: null,
    created_at: '2026-09-10T10:00:00Z',
    items: [],
    ...overrides,
  };
}

/** Réduction locale d'un détail au résumé qu'en rendrait l'historique (sans `items`). */
function toSummary(run: EvaluationRunRead): EvaluationRunSummary {
  const { items: _items, ...summary } = run;
  return summary;
}

/**
 * Ligne d'historique portant `providerId` — le catalogue est vide dans ces
 * tests, `useProviderLabel` retombe donc sur l'identifiant brut, qui figure
 * dans le texte accessible du bouton (concaténation de son contenu).
 */
function evaluationRow(providerId: string): HTMLElement {
  return screen.getByRole('button', { name: (name) => name.includes(providerId) });
}

/**
 * Ensemble vide par défaut : la plupart de ces tests portent sur le cycle de
 * vie d'un run, pas sur l'honnêteté du sélecteur de modèle (couverte par son
 * propre `describe` ci-dessous) — un ensemble vide est le repli sûr (voir le
 * commentaire de `EvaluationsPanelProps.personalKeyVendors`).
 */
function renderPanel(
  base = knowledgeBaseRead({ evaluation_enabled: true }),
  personalKeyVendors: ReadonlySet<LlmVendor> = new Set<LlmVendor>(),
): void {
  renderWithProviders(
    <EvaluationsPanel
      base={base}
      canWrite
      personalKeyVendors={personalKeyVendors}
      onRequestEdit={() => undefined}
    />,
  );
}

/**
 * Ouvre la modale de lancement. Historique vide : le bouton existe à la fois
 * dans l'en-tête et dans l'état vide (deux façons d'atteindre le même geste) —
 * n'importe lequel des deux déclenche `setDialogOpen(true)`.
 */
async function openLaunchDialog(): Promise<void> {
  const buttons = await screen.findAllByRole('button', { name: fr['kb.evaluations.launch'] });
  const button = buttons[0];
  if (button === undefined) throw new Error('Bouton de lancement introuvable.');
  fireEvent.click(button);
}

describe('EvaluationsPanel — cycle de vie d’un run asynchrone', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  /** Flush microtâches + effets React sous horloge gelée (horloge simulée). */
  async function flush(ms = 0): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it('sonde la liste et le détail d’un run pending, puis arrête les deux à sa résolution', async () => {
    let listStatus: 'pending' | 'completed' = 'pending';
    let detailStatus: 'pending' | 'completed' = 'pending';
    let listCalls = 0;
    let detailCalls = 0;

    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes('/v1/providers')) return Promise.resolve(jsonResponse(EMPTY_CATALOG));
        if (url.endsWith(`/evaluations/${RUN_ID}`)) {
          detailCalls += 1;
          return Promise.resolve(
            jsonResponse(
              detailStatus === 'pending'
                ? evaluationDetail({ status: 'pending' })
                : evaluationDetail({
                    status: 'completed',
                    item_count: 1,
                    average_judge_score: 0.9,
                    retrieval_recall_rate: 1,
                    items: [
                      {
                        id: 'item-1',
                        source_document_id: '11111111-1111-4111-8111-111111111111',
                        question: 'Quelle est la politique de remboursement ?',
                        reference_answer: 'Remboursement sous 30 jours.',
                        generated_answer: 'Le client dispose de 30 jours.',
                        retrieval_rank: 1,
                        judge_score: 0.9,
                        judge_rationale: 'Réponse conforme à la référence.',
                        created_at: '2026-09-10T10:05:00Z',
                      },
                    ],
                  }),
            ),
          );
        }
        if (url.endsWith('/evaluations')) {
          listCalls += 1;
          return Promise.resolve(
            jsonResponse([
              evaluationSummary({
                status: listStatus,
                item_count: listStatus === 'pending' ? 0 : 1,
                average_judge_score: listStatus === 'pending' ? null : 0.9,
                retrieval_recall_rate: listStatus === 'pending' ? null : 1,
              }),
            ]),
          );
        }
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    renderPanel();
    await flush();

    expect(listCalls).toBe(1);
    expect(screen.getByText(fr['kb.evaluations.status.pending'])).toBeDefined();

    // Dépliage : le détail sonde indépendamment, sur son propre rythme.
    fireEvent.click(evaluationRow('provider-a'));
    await flush();
    expect(detailCalls).toBe(1);
    expect(screen.getByText(fr['kb.evaluations.itemsPendingTitle'])).toBeDefined();

    // Résolution côté serveur, observée au prochain sondage des deux requêtes.
    listStatus = 'completed';
    detailStatus = 'completed';
    await flush(POLL_INTERVAL_MS);
    // Un second flush, bref : react-query notifie ses observateurs via un
    // `setTimeout(…, 0)` interne (`notifyManager`), distinct du minuteur de
    // `refetchInterval` — sous horloge gelée, il faut l'écouler séparément
    // pour que le re-rendu suive la donnée déjà arrivée.
    await flush(50);

    expect(listCalls).toBe(2);
    expect(detailCalls).toBe(2);
    expect(screen.queryByText(fr['kb.evaluations.status.pending'])).toBeNull();
    expect(screen.queryByText(fr['kb.evaluations.itemsPendingTitle'])).toBeNull();
    expect(screen.getByText('Quelle est la politique de remboursement ?')).toBeDefined();

    // Plus rien à sonder : un intervalle de plus ne déclenche aucun appel.
    await flush(POLL_INTERVAL_MS);
    expect(listCalls).toBe(2);
    expect(detailCalls).toBe(2);
  });
});

describe('EvaluationsPanel — historique résolu', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche un run en échec dans l’historique, et son détail sans reproposer de sondage', async () => {
    let detailCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes('/v1/providers')) return Promise.resolve(jsonResponse(EMPTY_CATALOG));
        if (url.endsWith(`/evaluations/${RUN_ID}`)) {
          detailCalls += 1;
          return Promise.resolve(jsonResponse(evaluationDetail({ status: 'failed', items: [] })));
        }
        if (url.endsWith('/evaluations')) {
          return Promise.resolve(
            jsonResponse([evaluationSummary({ status: 'failed', llm_provider_id: 'provider-b' })]),
          );
        }
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    renderPanel();

    expect(await screen.findByText(fr['kb.evaluations.status.failed'])).toBeDefined();

    fireEvent.click(evaluationRow('provider-b'));
    expect(await screen.findByText(fr['kb.evaluations.itemsFailed'])).toBeDefined();
    expect(detailCalls).toBe(1);
  });
});

describe('EvaluationsPanel — lancement d’un run', () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    appQueryClient.clear();
  });

  /**
   * `createEvaluationMutationOptions` (src/api/evaluations.ts) écrit son
   * cache après succès via le **singleton applicatif** (`@/api/query-client`),
   * pas via `useQueryClient()` — voir la note de `renderWithProviders`. Sans
   * monter ce même singleton, l'historique ne refléterait jamais le run tout
   * juste lancé : la mutation réussirait, mais l'écran resterait figé sur
   * « Aucune évaluation lancée », un faux vert. `appQueryClient.clear()`
   * (ci-dessus et ci-dessous) garde ce partage sans état entre les tests.
   */
  function renderPanelWithAppClient(base = knowledgeBaseRead({ evaluation_enabled: true })): void {
    renderWithProviders(
      <EvaluationsPanel
        base={base}
        canWrite
        personalKeyVendors={new Set<LlmVendor>()}
        onRequestEdit={() => undefined}
      />,
      { queryClient: appQueryClient },
    );
  }

  it('lance un run synchrone : le run apparaît dans l’historique, score affiché', async () => {
    let created: EvaluationRunRead | null = null;

    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url = urlOf(input);
        const method = methodOf(init);
        if (url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse({ ...EMPTY_CATALOG, llm: [PROVIDER] }));
        }
        if (method === 'POST' && url.endsWith('/evaluations')) {
          const run = evaluationDetail({
            status: 'completed',
            sample_size: 3,
            item_count: 3,
            average_judge_score: 0.82,
            retrieval_recall_rate: 1,
            llm_provider_id: PROVIDER.provider_id,
            items: [],
          });
          created = run;
          return Promise.resolve(jsonResponse(run));
        }
        if (method === 'GET' && url.endsWith('/evaluations')) {
          return Promise.resolve(jsonResponse(created === null ? [] : [toSummary(created)]));
        }
        if (method === 'GET' && created !== null && url.endsWith(`/evaluations/${created.id}`)) {
          return Promise.resolve(jsonResponse(created));
        }
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    renderPanelWithAppClient();

    await openLaunchDialog();
    const providerSelect = await screen.findByRole('combobox', {
      name: fr['kb.evaluations.model'],
    });
    fireEvent.change(providerSelect, { target: { value: PROVIDER.provider_id } });
    fireEvent.click(screen.getByRole('button', { name: fr['kb.evaluations.submit'] }));

    // Le toast distingue bien la résolution immédiate de l'acceptation
    // asynchrone : c'est `created.status === 'completed'` qui choisit la
    // branche, pas `sample_size` — les deux textes ne se confondent pas.
    expect(await screen.findByText(fr['kb.evaluations.successTitle'])).toBeDefined();
    // Le run tout juste lancé rejoint l'historique (invalidation post-mutation)
    // et son score y est affiché — la preuve que l'écran n'est pas figé sur
    // l'état vide malgré le succès de la requête. `formatPercent` sépare le
    // nombre du signe par une espace fine insécable (Intl `fr`) : `getByText`
    // normalise le texte du DOM (espaces collapsées) mais pas la chaîne
    // cherchée, d'où ce repli.
    await waitFor(() => {
      expect(screen.getByText(formatPercent(0.82, 'fr').replace(/\s+/g, ' '))).toBeDefined();
    });
    expect(screen.queryByText(fr['kb.evaluations.runningTitle'])).toBeNull();
    expect(screen.queryByText(fr['kb.evaluations.submittingTitle'])).toBeNull();
  });

  it('affiche le conseil d’un refus 409, sans laisser le panneau bloqué', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url = urlOf(input);
        const method = methodOf(init);
        if (url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse({ ...EMPTY_CATALOG, llm: [PROVIDER] }));
        }
        if (method === 'POST' && url.endsWith('/evaluations')) {
          return Promise.resolve(problemResponse(409, 'Aucun document indexé.'));
        }
        if (method === 'GET' && url.endsWith('/evaluations'))
          return Promise.resolve(jsonResponse([]));
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    renderPanel();

    await openLaunchDialog();
    const providerSelect = await screen.findByRole('combobox', {
      name: fr['kb.evaluations.model'],
    });
    fireEvent.change(providerSelect, { target: { value: PROVIDER.provider_id } });
    fireEvent.click(screen.getByRole('button', { name: fr['kb.evaluations.submit'] }));

    expect(await screen.findByText(fr['kb.evaluations.runErrorTitle'])).toBeDefined();
    expect(screen.getByText(fr['kb.evaluations.hint409'])).toBeDefined();
    expect(screen.getByText('Aucun document indexé.')).toBeDefined();
    // Écran de nouveau utilisable : pas de bandeau d'attente orphelin, le
    // lancement reste proposé (toujours les deux mêmes points d'entrée).
    expect(screen.queryByText(fr['kb.evaluations.runningTitle'])).toBeNull();
    expect(
      screen.getAllByRole('button', { name: fr['kb.evaluations.launch'] }).length,
    ).toBeGreaterThan(0);
  });
});

/**
 * Le sélecteur de modèle de la modale de lancement reste utilisable même
 * quand le choix qu'il affiche n'est pas celui réellement facturé : dès que
 * la clé qui paie n'est pas la clé personnelle du compte qui lance le run, le
 * backend impose son propre modèle (`resolve_llm_funding`,
 * `EvaluationService._fund`, addendum 2026-09-21 à ADR-054). Sans indication,
 * la modale mentirait par omission. `personalKeyVendors` porte cette
 * information (cf. son commentaire sur `EvaluationsPanelProps`).
 */
describe('EvaluationsPanel — honnêteté du sélecteur de modèle (addendum 2026-09-21 à ADR-054)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubCatalogWithProvider(): void {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes('/v1/providers')) {
          return Promise.resolve(jsonResponse({ ...EMPTY_CATALOG, llm: [PROVIDER] }));
        }
        if (url.endsWith('/evaluations')) return Promise.resolve(jsonResponse([]));
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );
  }

  it('prévient que le choix est ignoré sans clé personnelle pour ce fournisseur', async () => {
    stubCatalogWithProvider();
    renderPanel(knowledgeBaseRead({ evaluation_enabled: true }), new Set<LlmVendor>());

    await openLaunchDialog();
    await screen.findByRole('combobox', { name: fr['kb.evaluations.model'] });

    const vendor = fr['settings.llmKeys.vendor.anthropic'];
    expect(
      screen.getByText(fr['kb.evaluations.modelOverriddenHint'].replace('{vendor}', vendor)),
    ).toBeDefined();
    expect(
      screen.queryByText(fr['kb.evaluations.modelAppliesHint'].replace('{vendor}', vendor)),
    ).toBeNull();
  });

  it('confirme que le choix s’applique dès qu’une clé personnelle est déclarée pour ce fournisseur', async () => {
    stubCatalogWithProvider();
    renderPanel(knowledgeBaseRead({ evaluation_enabled: true }), new Set<LlmVendor>(['anthropic']));

    await openLaunchDialog();
    await screen.findByRole('combobox', { name: fr['kb.evaluations.model'] });

    const vendor = fr['settings.llmKeys.vendor.anthropic'];
    expect(
      screen.getByText(fr['kb.evaluations.modelAppliesHint'].replace('{vendor}', vendor)),
    ).toBeDefined();
    expect(
      screen.queryByText(fr['kb.evaluations.modelOverriddenHint'].replace('{vendor}', vendor)),
    ).toBeNull();
  });
});
