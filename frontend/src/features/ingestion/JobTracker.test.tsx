/**
 * Test de non-régression du mécanisme de proposition du precheck (ADR-032).
 *
 * ## Pourquoi ce test vit ici et pas dans `PrecheckReview.test.tsx`
 *
 * Le défaut corrigé le 2026-08-11 (commit `379a054`, ADR-031 §Conséquences)
 * n'était **dans aucun des deux composants pris isolément** : il était dans
 * leur couture. `PrecheckReview` initialise ses cases avec
 * `useState(suggestion.…)`, ce qui n'est légitime que parce que `JobTracker`
 * lui pose une `key` qui change à l'arrivée du rapport ; la requête du document
 * n'étant activée qu'à `awaiting_review`, la première monture a lieu **avant**
 * que le rapport n'existe. Sans ce segment de `key`, l'état initial est figé
 * sur un rapport `null` — cases décochées, proposition visible dans le seul
 * badge.
 *
 * Un test qui monterait `PrecheckReview` seul en changeant sa prop `report` ne
 * pourrait donc **pas** distinguer la version buguée de la version corrigée :
 * le fichier n'a pas changé entre les deux. Le test doit reproduire
 * l'enchaînement réseau réel — job d'abord, rapport ensuite — d'où le montage
 * de `JobTracker` sur un `fetch` piloté par le test.
 */
import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { DocumentRead, IngestionJobRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { checkboxLabelled } from '@/test/dom';
import {
  DOCUMENT_ID,
  JOB_ID,
  documentRead,
  ingestionJob,
  rawPrecheckReport,
} from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { JobTracker } from './JobTracker';

/** Corps JSON d'une requête sortante — `apiClient` n'envoie que des chaînes. */
function parseBody(init: RequestInit | undefined): unknown {
  return typeof init?.body === 'string' ? JSON.parse(init.body) : null;
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

/**
 * Façade simulée où **le document se fait attendre**, comme en vrai.
 *
 * Le job répond tout de suite (`awaiting_review`), le document seulement quand
 * le test le décide : c'est cette fenêtre — écran d'aiguillage monté, rapport
 * pas encore arrivé — qui portait le défaut.
 */
function stubFacade(job: IngestionJobRead): { deliverDocument: (document: DocumentRead) => void } {
  let deliver: (document: DocumentRead) => void = () => undefined;
  const pendingDocument = new Promise<DocumentRead>((resolve) => {
    deliver = resolve;
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/v1/jobs/')) return jsonResponse(job);
      if (url.includes('/v1/documents/')) return jsonResponse(await pendingDocument);
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );

  return {
    deliverDocument: (document) => {
      deliver(document);
    },
  };
}

function renderTracker(): void {
  renderWithProviders(
    <JobTracker documentId={DOCUMENT_ID} jobId={JOB_ID} filename="rapport-annuel.pdf" />,
  );
}

describe('JobTracker — arrivée différée du rapport de precheck', () => {
  it('coche réellement les options proposées quand le rapport arrive après la monture', async () => {
    const facade = stubFacade(ingestionJob('awaiting_review'));
    renderTracker();

    // 1. L'écran d'aiguillage est monté, le rapport n'existe pas encore.
    //    `PrecheckReview` a déjà initialisé son état à ce stade.
    await screen.findByText(fr['precheck.analyzingTitle']);
    expect(screen.queryByRole('checkbox')).toBeNull();

    // 2. Le rapport arrive : PDF, deux tableaux détectés.
    facade.deliverDocument(
      documentRead({ precheck_report: rawPrecheckReport({ table_count: 2 }) }),
    );

    // 3. Le bloc de décision n'est rendu qu'une fois le rapport lisible : sa
    //    présence est le signal que l'étape 2 a été prise en compte.
    await screen.findByText(fr['precheck.decision.title']);

    // 4. Le point de tout l'exercice : la proposition doit être dans la **case**,
    //    pas seulement dans le badge à côté.
    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(true);
    expect(checkboxLabelled(fr['precheck.decision.tables']).checked).toBe(true);

    // `use_parent_child` n'est jamais suggérée par le precheck : elle reste
    // décochée, sinon la proposition serait devenue « tout cocher ».
    expect(checkboxLabelled(fr['precheck.decision.parentChild']).checked).toBe(false);
  });

  it('ne propose pas l’analyse de mise en page sur un tableur', async () => {
    // Garde-fou contre la correction paresseuse : ce qui doit remonter jusqu'à
    // la case, c'est `suggestedDecision(report)`, pas une constante `true`.
    const facade = stubFacade(ingestionJob('awaiting_review'));
    renderTracker();

    await screen.findByText(fr['precheck.analyzingTitle']);
    facade.deliverDocument(
      documentRead({
        filename: 'budget.xlsx',
        content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        precheck_report: rawPrecheckReport({
          format: 'spreadsheet',
          page_count: null,
          table_count: 4,
        }),
      }),
    );

    await screen.findByText(fr['precheck.decision.title']);

    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(false);
    expect(checkboxLabelled(fr['precheck.decision.tables']).checked).toBe(true);
  });
});

/**
 * Panneau « Paramètres avancés » (Phase 15) — **désactivé pour cette version**
 * (2026-09-06, incident réel : `laws` sur un PDF réel a produit un fragment
 * dépassant les limites du reranker en aval, `CUDA out of memory`).
 *
 * Ces tests gardent l'état de l'écran une fois le rapport arrivé, sur des
 * documents qui auraient proposé une sélection fine avant cette
 * désactivation — la preuve que ce n'est plus jamais le cas, pas la fonction
 * de filtrage elle-même (`precheck.test.ts` s'en charge).
 */
describe('JobTracker — sélection fine du template', () => {
  it('ne propose jamais de sélection fine, même sur un document qui l’aurait permis avant', async () => {
    // Désactivée pour cette version (2026-09-06), incident réel : `laws` sur
    // un PDF réel a produit un fragment de 41 264 caractères, jamais mesuré
    // aux limites du reranker en aval (`CUDA out of memory` sur un lot de
    // reranking le contenant). `rgpd.pdf` sans colonne aurait proposé `laws`/
    // `paper`/`manual` avant cette désactivation — plus aucun cas ne le fait.
    const facade = stubFacade(ingestionJob('awaiting_review'));
    renderWithProviders(<JobTracker documentId={DOCUMENT_ID} jobId={JOB_ID} filename="rgpd.pdf" />);

    await screen.findByText(fr['precheck.analyzingTitle']);
    facade.deliverDocument(
      documentRead({ filename: 'rgpd.pdf', precheck_report: rawPrecheckReport() }),
    );
    await screen.findByText(fr['precheck.decision.title']);

    expect(screen.queryByText(fr['precheck.advanced.title'])).toBeNull();
    expect(screen.getByText(fr['precheck.advanced.unavailable'])).toBeDefined();
  });

  it('n’en propose pas non plus sur un Markdown, qui l’aurait aussi permis avant (`qa`)', async () => {
    const facade = stubFacade(ingestionJob('awaiting_review'));
    renderWithProviders(<JobTracker documentId={DOCUMENT_ID} jobId={JOB_ID} filename="guide.md" />);

    await screen.findByText(fr['precheck.analyzingTitle']);
    facade.deliverDocument(
      documentRead({
        filename: 'guide.md',
        content_type: 'text/markdown',
        precheck_report: rawPrecheckReport({
          supported: false,
          format: 'unsupported',
          page_count: null,
          error: { code: 'unsupported_format', message: 'Format non analysé.' },
        }),
      }),
    );
    await screen.findByText(fr['precheck.decision.title']);

    // Rapport dégradé (`unsupported_format`) : ni le panneau ni le motif
    // « indisponible » n'apparaissent — un rapport dégradé porte déjà son
    // propre message, distinct de celui-ci.
    expect(screen.queryByText(fr['precheck.advanced.title'])).toBeNull();
    expect(screen.queryByText(fr['precheck.advanced.unavailable'])).toBeNull();
  });
});

/**
 * Actions groupées « … pour tous » — ce qui part réellement sur le réseau.
 *
 * Ces deux modes court-circuitent l'écran d'aiguillage : aucun clic ne les
 * déclenche, seul l'état `awaiting_review` le fait. La seule preuve utile est
 * donc la décision **transmise** — pas l'état d'une case, qui n'existe même pas
 * au moment où elle part. On l'observe sur le `PUT` intercepté.
 */
describe('JobTracker — décisions soumises en lot', () => {
  /** Façade qui répond à tout et retient le corps du `PUT` de décision. */
  function stubDecisionFacade(document: DocumentRead): { sent: () => unknown } {
    let body: unknown = null;

    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (url.includes('/precheck/decision')) {
          body = parseBody(init);
          return Promise.resolve(jsonResponse(document));
        }
        if (url.includes('/v1/jobs/')) {
          return Promise.resolve(jsonResponse(ingestionJob('awaiting_review')));
        }
        if (url.includes('/v1/documents/')) return Promise.resolve(jsonResponse(document));
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    return { sent: () => body };
  }

  function renderWithAutoDecision(mode: 'suggested' | 'none'): void {
    renderWithProviders(
      <JobTracker
        documentId={DOCUMENT_ID}
        jobId={JOB_ID}
        filename="rapport-annuel.pdf"
        autoDecision={mode}
      />,
    );
  }

  it('soumet la proposition de l’analyse quand le lot accepte', async () => {
    const facade = stubDecisionFacade(
      documentRead({ precheck_report: rawPrecheckReport({ table_count: 2 }) }),
    );
    renderWithAutoDecision('suggested');

    await vi.waitFor(() => {
      expect(facade.sent()).toEqual({
        decision: {
          optimize_for_tables: true,
          heavy_ocr: true,
          use_parent_child: false,
          chunk_method_override: null,
        },
      });
    });
  });

  it('soumet une décision vide quand le lot refuse — et lance quand même l’indexation', async () => {
    // « Refuser » porte sur les suggestions, pas sur l'ingestion : la décision
    // part, donc le document est indexé, sans aucune des options proposées.
    // Un refus qui n'enverrait rien laisserait le job bloqué en attente.
    const facade = stubDecisionFacade(
      documentRead({ precheck_report: rawPrecheckReport({ table_count: 2 }) }),
    );
    renderWithAutoDecision('none');

    await vi.waitFor(() => {
      expect(facade.sent()).toEqual({
        decision: {
          optimize_for_tables: false,
          heavy_ocr: false,
          use_parent_child: false,
          chunk_method_override: null,
        },
      });
    });
  });
});

/**
 * Lisibilité de la cause d'échec, pendant le versement lui-même.
 *
 * Le message rendu ici est celui que la façade recopie du moteur, tel quel. Le
 * même bloc (`JobOutcome`) sert à la liste des documents : ce test garde le
 * contrat côté suivi — constat traduit **au-dessus**, dump brut conservé
 * **dessous** —, celui de `DocumentsPanel.test.tsx` le garde côté relecture.
 */
describe('JobTracker — cause d’un échec', () => {
  it('traduit une cause reconnue et garde le message brut du moteur', async () => {
    const raw =
      "12:10:44 Page(1~4): [ERROR]429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota'}}";
    stubFacade(
      ingestionJob('failed', {
        error: { code: 'engine_processing_failed', message: raw, retryable: false },
      }),
    );
    renderTracker();

    expect(await screen.findByText(fr['ingest.cause.quotaExhausted.title'])).toBeDefined();
    expect(screen.getByText(fr['ingest.cause.quotaExhausted.advice'])).toBeDefined();
    expect(screen.getByText(raw)).toBeDefined();
  });

  it('affiche le message brut seul quand aucune cause n’est reconnue', async () => {
    // Garde-fou contre la correction paresseuse : une phrase d'attente
    // générique à la place d'une cause inconnue ferait perdre la seule
    // information réellement disponible.
    const raw = 'Échec de l’étape « indexing » : RuntimeError: connexion perdue';
    stubFacade(
      ingestionJob('failed', {
        error: { code: 'ingestion_step_failed', message: raw, retryable: false },
      }),
    );
    renderTracker();

    expect(await screen.findByText(raw)).toBeDefined();
    expect(screen.queryByText(fr['ingest.cause.rawToggle'])).toBeNull();
  });
});
