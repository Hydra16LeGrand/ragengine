/**
 * Ce que la liste des documents dit des options de precheck retenues, une
 * fois l'indexation terminée (nouveau point, même esprit que ChunksDialog.test.tsx
 * pour le parent/enfant).
 *
 * Avant ce composant, un utilisateur ne savait plus quelles options il avait
 * cochées à l'écran d'aiguillage — seul cet écran, au moment de valider, le
 * disait. Le test porte sur la couture qui compte : ce que `precheck_decision`
 * (JSON opaque côté type généré) donne comme badges affichés, pas sur la
 * fonction pure de mise en forme isolée de son rendu.
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DocumentRead, IngestionJobRead, PageDocumentRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { documentRead, ingestionJob, JOB_ID, KNOWLEDGE_BASE_ID } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { DocumentsPanel } from './DocumentsPanel';

function documentsPage(documents: DocumentRead[]): PageDocumentRead {
  return { data: documents, next_cursor: null };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

/**
 * Façade simulée : la liste des documents, et — seulement si l'interface la
 * demande — la fiche du job qui porte le détail de l'issue.
 *
 * L'ordre des tests d'URL n'est pas indifférent : la liste vit sous
 * `…/knowledge-bases/{id}/documents`, qui contient `/documents`. Les jobs
 * passent donc en premier.
 */
function stubFacade(page: PageDocumentRead, job?: IngestionJobRead): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: string | URL | Request): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.includes('/v1/jobs/')) {
      if (job === undefined) throw new Error(`Job non prévu par le test : ${url}`);
      return Promise.resolve(jsonResponse(job));
    }
    if (url.includes('/documents')) return Promise.resolve(jsonResponse(page));
    throw new Error(`Requête non prévue par le test : ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function stubDocuments(page: PageDocumentRead): void {
  stubFacade(page);
}

function renderPanel(canEdit = false): void {
  renderWithProviders(<DocumentsPanel knowledgeBaseId={KNOWLEDGE_BASE_ID} canEdit={canEdit} />);
}

/** Requêtes déjà parties vers `GET /v1/jobs/{id}`. */
function jobRequestCount(fetchMock: ReturnType<typeof vi.fn>): number {
  return fetchMock.mock.calls.filter(([input]) => String(input).includes('/v1/jobs/')).length;
}

describe('DocumentsPanel — options de precheck retenues', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche l’option de granularité affinée pour un document indexé avec `use_parent_child`', async () => {
    stubDocuments(
      documentsPage([
        documentRead({
          status: 'indexed',
          chunk_count: 5,
          precheck_decision: {
            optimize_for_tables: false,
            heavy_ocr: false,
            use_parent_child: true,
          },
        }),
      ]),
    );
    renderPanel();

    expect(await screen.findByText(fr['precheck.decision.appliedParentChild'])).toBeDefined();
    expect(screen.queryByText(fr['precheck.decision.appliedDefault'])).toBeNull();
    expect(screen.queryByText(fr['precheck.decision.appliedTables'])).toBeNull();
    expect(screen.queryByText(fr['precheck.decision.appliedOcr'])).toBeNull();
  });

  it('nomme le template forcé, plutôt que d’annoncer les réglages par défaut', async () => {
    // Sans ce badge, un document découpé en `laws` à la demande de
    // l'utilisateur s'afficherait « Réglages par défaut du moteur » : un
    // mensonge, sur le seul écran où la décision reste consultable après coup.
    stubDocuments(
      documentsPage([
        documentRead({
          status: 'indexed',
          chunk_count: 545,
          precheck_decision: {
            optimize_for_tables: false,
            heavy_ocr: false,
            use_parent_child: false,
            chunk_method_override: 'laws',
          },
        }),
      ]),
    );
    renderPanel();

    expect(
      await screen.findByText(`${fr['precheck.decision.appliedChunkMethod']} · laws`),
    ).toBeDefined();
    expect(screen.queryByText(fr['precheck.decision.appliedDefault'])).toBeNull();
  });

  it('affiche l’état neutre quand aucune option n’était active', async () => {
    stubDocuments(
      documentsPage([
        documentRead({
          status: 'indexed',
          chunk_count: 3,
          precheck_decision: {
            optimize_for_tables: false,
            heavy_ocr: false,
            use_parent_child: false,
          },
        }),
      ]),
    );
    renderPanel();

    expect(await screen.findByText(fr['precheck.decision.appliedDefault'])).toBeDefined();
  });

  it('affiche aussi l’état neutre quand la décision est absente (jamais passé par l’aiguillage)', async () => {
    stubDocuments(
      documentsPage([
        documentRead({
          status: 'indexed',
          chunk_count: 1,
          precheck_decision: null,
        }),
      ]),
    );
    renderPanel();

    expect(await screen.findByText(fr['precheck.decision.appliedDefault'])).toBeDefined();
  });

  it('ne montre aucune option retenue tant que le document n’est pas indexé', () => {
    stubDocuments(
      documentsPage([
        documentRead({
          status: 'awaiting_review',
          precheck_decision: {
            optimize_for_tables: true,
            heavy_ocr: false,
            use_parent_child: false,
          },
        }),
      ]),
    );
    renderPanel();

    expect(screen.queryByText(fr['precheck.decision.appliedTables'])).toBeNull();
    expect(screen.queryByText(fr['precheck.decision.appliedDefault'])).toBeNull();
  });
});

/**
 * Ce que la liste dit de la **cause** d'un échec ou d'un avertissement, une
 * fois la session de versement terminée.
 *
 * Le défaut corrigé ici n'était dans aucun composant pris isolément : la cause
 * était enregistrée en base (`ingestion_jobs.error` / `.warning`) et bien
 * rendue — mais par `JobTracker` seul, donc uniquement tant que la file de
 * versement vivait à l'écran. Quitter la page ou la rafraîchir la rendait
 * inatteignable, et plusieurs échecs de la campagne de tests manuels ont été
 * remontés comme « sans cause visible »
 * (l'analyse interne des tests manuels §3).
 *
 * Le test se pose donc sur la couture réseau réelle — liste d'abord, fiche du
 * job seulement au dépli — et non sur la fonction de traduction des causes
 * prise à part : celle-ci rendrait le même verdict dans la version buguée.
 */
describe('DocumentsPanel — cause d’un échec ou d’un avertissement', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // Dump réel relevé en base sur `real-insee-fps2022.pdf` (quota gratuit Gemini
  // épuisé par l'ingestion en lot) : c'est cette chaîne-là que l'utilisateur
  // avait sous les yeux, quand il pouvait encore l'avoir.
  const RAW_QUOTA_MESSAGE =
    "12:10:44 Page(1~4): [ERROR]429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota'}}";

  it('traduit le quota épuisé en constat actionnable, sans perdre le message brut', async () => {
    const user = userEvent.setup();
    const fetchMock = stubFacade(
      documentsPage([
        documentRead({
          filename: 'real-insee-fps2022.pdf',
          status: 'failed',
          current_job_id: JOB_ID,
        }),
      ]),
      ingestionJob('failed', {
        error: {
          code: 'engine_processing_failed',
          message: RAW_QUOTA_MESSAGE,
          retryable: false,
        },
      }),
    );
    renderPanel();

    const toggle = await screen.findByRole('button', { name: fr['kb.documents.cause.show'] });

    // La fiche du job n'est demandée qu'au dépli : la liste se rend sans
    // jointure sur les jobs, et un corpus de 59 documents ne doit pas déclencher
    // 59 requêtes pour un détail que personne n'a ouvert.
    expect(jobRequestCount(fetchMock)).toBe(0);

    await user.click(toggle);

    // Le constat traduit, et le conseil qui va avec.
    expect(await screen.findByText(fr['ingest.cause.quotaExhausted.title'])).toBeDefined();
    expect(screen.getByText(fr['ingest.cause.quotaExhausted.advice'])).toBeDefined();

    // Le dump brut reste accessible : c'est lui qui porte le diagnostic.
    expect(screen.getByText(RAW_QUOTA_MESSAGE)).toBeDefined();
    expect(screen.getByText('engine_processing_failed')).toBeDefined();
  });

  it('restitue l’avertissement qualité d’un document indexé sans le moindre fragment', async () => {
    const user = userEvent.setup();
    stubFacade(
      documentsPage([
        documentRead({
          filename: '26-pdf-blank-page.pdf',
          status: 'indexed',
          chunk_count: 0,
          has_quality_warning: true,
          current_job_id: JOB_ID,
        }),
      ]),
      ingestionJob('completed', {
        warning: {
          code: 'zero_chunks',
          message: 'Aucun fragment n’a été produit pour ce document.',
          metrics: { chunk_count: 0 },
        },
      }),
    );
    renderPanel();

    await user.click(await screen.findByRole('button', { name: fr['kb.documents.cause.show'] }));

    expect(await screen.findByText(fr['ingest.qualityWarning.code.zero_chunks'])).toBeDefined();
    expect(screen.getByText(fr['ingest.qualityWarning.advice.zero_chunks'])).toBeDefined();
    expect(screen.getByText('Aucun fragment n’a été produit pour ce document.')).toBeDefined();
  });

  it('n’offre aucune consultation de cause sur un document indexé sans réserve', async () => {
    stubDocuments(
      documentsPage([documentRead({ status: 'indexed', chunk_count: 12, current_job_id: JOB_ID })]),
    );
    renderPanel();

    await screen.findByText(fr['document.status.indexed']);
    expect(screen.queryByRole('button', { name: fr['kb.documents.cause.show'] })).toBeNull();
  });

  it('dit explicitement quand le moteur n’a rien enregistré, plutôt que d’ouvrir un vide', async () => {
    const user = userEvent.setup();
    stubFacade(
      documentsPage([documentRead({ status: 'failed', current_job_id: JOB_ID })]),
      // Job en échec dont la cause n'a pas pu être écrite : le dépli ne doit pas
      // rester muet, sinon on remplace un angle mort par un autre.
      ingestionJob('failed'),
    );
    renderPanel();

    await user.click(await screen.findByRole('button', { name: fr['kb.documents.cause.show'] }));

    expect(await screen.findByText(fr['kb.documents.cause.none'])).toBeDefined();
  });
});

describe('DocumentsPanel — organisation par thème', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('propose un filtre seulement quand au moins un document porte un thème', async () => {
    stubDocuments(documentsPage([documentRead({ theme: null })]));
    renderPanel();

    await screen.findByText('rapport-annuel.pdf');
    expect(screen.queryByLabelText(fr['kb.documents.theme.filterLabel'])).toBeNull();
  });

  it('filtre la liste par thème, sans perdre les documents des autres thèmes ailleurs', async () => {
    const user = userEvent.setup();
    stubDocuments(
      documentsPage([
        documentRead({ id: 'doc-a', filename: 'contrat.pdf', theme: 'juridique' }),
        documentRead({ id: 'doc-b', filename: 'budget.pdf', theme: 'finance' }),
      ]),
    );
    renderPanel();
    await screen.findByText('contrat.pdf');
    expect(screen.getByText('budget.pdf')).toBeDefined();

    await user.selectOptions(
      screen.getByLabelText(fr['kb.documents.theme.filterLabel']),
      'juridique',
    );

    expect(screen.getByText('contrat.pdf')).toBeDefined();
    expect(screen.queryByText('budget.pdf')).toBeNull();
  });

  it('pose un thème via `PATCH`, seulement pour qui a le droit d’écrire', async () => {
    const user = userEvent.setup();
    const base = documentRead({ theme: null });
    // Le composant réagit au succès de la mutation en invalidant la liste :
    // le mock doit donc refléter l'écriture pour la relecture qui suit,
    // sinon le badge reviendrait à « Aucun thème » malgré la réussite.
    let currentTheme: string | null = null;
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? 'GET';
      if (method === 'PATCH' && url.includes(`/v1/documents/${base.id}`)) {
        const rawBody = typeof init?.body === 'string' ? init.body : '{}';
        const body = JSON.parse(rawBody) as { theme?: string | null };
        currentTheme = body.theme ?? null;
        return Promise.resolve(jsonResponse({ ...base, theme: currentTheme }));
      }
      if (url.includes('/documents')) {
        return Promise.resolve(jsonResponse(documentsPage([{ ...base, theme: currentTheme }])));
      }
      throw new Error(`Requête non prévue par le test : ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderPanel(true);
    await screen.findByText('rapport-annuel.pdf');
    expect(screen.getByText(fr['kb.documents.theme.none'])).toBeDefined();

    await user.click(screen.getByRole('button', { name: fr['kb.documents.theme.edit'] }));
    await user.type(
      screen.getByRole('textbox', { name: fr['kb.documents.theme.edit'] }),
      'juridique',
    );
    await user.click(screen.getByRole('button', { name: fr['kb.documents.theme.save'] }));

    await screen.findAllByText('juridique');
    expect(screen.queryByText(fr['kb.documents.theme.none'])).toBeNull();
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(patchCall).toBeDefined();
  });

  it('n’offre pas la modification du thème sans droit d’écriture sur la base', async () => {
    stubDocuments(documentsPage([documentRead({ theme: 'juridique' })]));
    renderPanel(false);

    await screen.findByText('rapport-annuel.pdf');
    expect(screen.getAllByText('juridique').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: fr['kb.documents.theme.edit'] })).toBeNull();
  });
});

/**
 * Audit dette technique 2026-09, motif A : au-delà de la première page (50
 * documents), un document devenait invisible et injoignable — aucune erreur,
 * aucun état vide, une réponse tronquée qui se faisait passer pour complète.
 *
 * `DocumentsPanel` pagine désormais réellement par curseur
 * (`knowledgeBaseDocumentsInfiniteQueryOptions`, `useInfiniteQuery`) : ces
 * tests reproduisent deux pages distinctes — la seconde n'apparaissant que
 * derrière `cursor=` dans la requête — et vérifient qu'un document de la
 * deuxième page reste atteignable, y compris par le filtre thème.
 */
describe('DocumentsPanel — pagination réelle au-delà de la première page', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubTwoPages(
    first: PageDocumentRead,
    second: PageDocumentRead,
  ): ReturnType<typeof vi.fn> {
    const fetchMock = vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/documents')) {
        return Promise.resolve(jsonResponse(url.includes('cursor=') ? second : first));
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('un document de la deuxième page devient visible via « Charger plus »', async () => {
    const user = userEvent.setup();
    stubTwoPages(
      {
        data: [documentRead({ id: 'doc-1', filename: 'page-1.pdf' })],
        next_cursor: 'curseur-page-2',
      },
      documentsPage([documentRead({ id: 'doc-2', filename: 'page-2.pdf' })]),
    );

    renderPanel();

    await screen.findByText('page-1.pdf');
    expect(screen.queryByText('page-2.pdf')).toBeNull();

    await user.click(await screen.findByRole('button', { name: fr['kb.documents.loadMore'] }));

    expect(await screen.findByText('page-2.pdf')).toBeDefined();
    // Plus de page suivante : le bouton s'efface plutôt que de rester affiché à vide.
    expect(screen.queryByRole('button', { name: fr['kb.documents.loadMore'] })).toBeNull();
  });

  it('le filtre par thème retrouve un document chargé depuis la deuxième page', async () => {
    const user = userEvent.setup();
    stubTwoPages(
      {
        data: [documentRead({ id: 'doc-1', filename: 'page-1.pdf', theme: 'finance' })],
        next_cursor: 'curseur-page-2',
      },
      documentsPage([documentRead({ id: 'doc-2', filename: 'page-2.pdf', theme: 'juridique' })]),
    );
    renderPanel();

    await screen.findByText('page-1.pdf');
    await user.click(await screen.findByRole('button', { name: fr['kb.documents.loadMore'] }));
    await screen.findByText('page-2.pdf');

    await user.selectOptions(
      screen.getByLabelText(fr['kb.documents.theme.filterLabel']),
      'juridique',
    );

    expect(screen.getByText('page-2.pdf')).toBeDefined();
    expect(screen.queryByText('page-1.pdf')).toBeNull();
  });
});
