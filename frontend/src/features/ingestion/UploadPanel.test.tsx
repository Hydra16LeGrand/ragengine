/**
 * Ce que la file de versement dit d'un fichier reconnu identique à un document
 * déjà présent (idempotence par empreinte, ADR-014).
 *
 * ## Pourquoi ce test monte `UploadPanel` entier
 *
 * La déduplication est **invisible dans la réponse** : `POST …/documents/ingest`
 * répond `202` avec un `DocumentRead` que le fichier ait été créé ou non — la
 * façade calcule bien `RegisteredDocument.created`, mais le routeur ne le
 * sérialise pas. Le seul indice est que le document renvoyé n'est pas celui
 * qu'on a envoyé, ce qui ne se constate qu'en confrontant la requête à sa
 * réponse : ni le composant de ligne seul, ni une fonction pure isolée ne
 * voient cet enchaînement. D'où le montage complet, dépôt du fichier compris.
 *
 * Le scénario est celui vécu pendant la campagne de tests manuels
 * (l'analyse interne des tests manuels §4.1) : `29-pdf-duplicate-of-01.pdf`
 * est versé, `01-pdf-single-column.pdf` est renvoyé, et l'utilisateur ne
 * retrouve aucune entrée à son nom — vécu comme un bug faute d'être dit.
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DocumentRead } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import {
  documentRead,
  ingestionJob,
  JOB_ID,
  KNOWLEDGE_BASE_ID,
  rawPrecheckReport,
} from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { UploadPanel } from './UploadPanel';

const EXISTING = documentRead({
  filename: '01-pdf-single-column.pdf',
  status: 'indexed',
  current_job_id: JOB_ID,
  chunk_count: 8,
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

/**
 * Façade simulée. `ingested` est ce que le versement renvoie : le document
 * **existant** pour un doublon, un document neuf sinon.
 *
 * Les tests d'URL vont du plus spécifique au plus général — `…/documents/ingest`
 * contient `/documents`, et la liste aussi.
 */
function stubFacade(ingested: DocumentRead): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/ingest')) return Promise.resolve(jsonResponse(ingested, 202));
      if (url.includes('/v1/jobs/'))
        return Promise.resolve(jsonResponse(ingestionJob('completed')));
      if (url.includes('/documents')) {
        return Promise.resolve(jsonResponse({ data: [EXISTING], next_cursor: null }));
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );
}

/** Dépose un fichier puis lance réellement le versement. */
async function ingest(user: ReturnType<typeof userEvent.setup>, name: string): Promise<void> {
  const input = screen.getByLabelText(fr['ingest.dropzone.browse']);
  await user.upload(input, new File(['%PDF-1.7 …'], name, { type: 'application/pdf' }));
  await user.click(await screen.findByRole('button', { name: fr['ingest.staging.launch'] }));
}

function duplicateNoteFor(name: string): string {
  return fr['ingest.item.duplicateNote'].replace('{name}', name);
}

describe('UploadPanel — fichier déjà présent sous la même empreinte', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('dit que le fichier versé est un doublon, et lequel a été retenu', async () => {
    const user = userEvent.setup();
    stubFacade(EXISTING);
    renderWithProviders(<UploadPanel knowledgeBaseId={KNOWLEDGE_BASE_ID} />);

    await ingest(user, '29-pdf-duplicate-of-01.pdf');

    expect(await screen.findByText(duplicateNoteFor('01-pdf-single-column.pdf'))).toBeDefined();
    expect(screen.getByText(fr['ingest.item.duplicateBadge'])).toBeDefined();
  });

  it('nomme le document réellement suivi, pas le fichier déposé', async () => {
    // Sans cela l'écran annonce « 29-pdf-duplicate-of-01.pdf est interrogeable
    // depuis le Playground » — un document de ce nom n'existe nulle part.
    const user = userEvent.setup();
    stubFacade(EXISTING);
    renderWithProviders(<UploadPanel knowledgeBaseId={KNOWLEDGE_BASE_ID} />);

    await ingest(user, '29-pdf-duplicate-of-01.pdf');

    const completed = await screen.findAllByText(
      fr['ingest.completedDescription'].replace('{name}', '01-pdf-single-column.pdf'),
    );
    expect(completed.length).toBeGreaterThan(0);
  });

  it('offre le refus en pendant de l’acceptation, et le refus soumet une décision vide', async () => {
    // Remontée : « de la même manière qu'il y a "Accepter les suggestions pour
    // tous", il faudrait un autre bouton qui puisse également refuser ». Le
    // refus porte sur les **suggestions**, pas sur l'ingestion : chaque document
    // reçoit une décision vide et part s'indexer. Ne rien envoyer les laisserait
    // bloqués en attente de revue, ce qui serait le vrai piège.
    const user = userEvent.setup();
    const decisions: unknown[] = [];
    const documents = [
      documentRead({
        id: '55555555-5555-4555-8555-555555555555',
        filename: 'note-a.pdf',
        current_job_id: 'job-a',
        precheck_report: rawPrecheckReport({ table_count: 2 }),
      }),
      documentRead({
        id: '66666666-6666-4666-8666-666666666666',
        filename: 'note-b.pdf',
        current_job_id: 'job-b',
        precheck_report: rawPrecheckReport({ table_count: 2 }),
      }),
    ];
    let ingested = 0;

    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request, init?: RequestInit): Promise<Response> => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (url.includes('/ingest')) {
          const document = documents[Math.min(ingested, documents.length - 1)];
          ingested += 1;
          return Promise.resolve(jsonResponse(document, 202));
        }
        if (url.includes('/precheck/decision')) {
          decisions.push(typeof init?.body === 'string' ? JSON.parse(init.body) : null);
          return Promise.resolve(jsonResponse(documents[0]));
        }
        if (url.includes('/v1/jobs/')) {
          return Promise.resolve(jsonResponse(ingestionJob('awaiting_review')));
        }
        if (url.includes('/v1/documents/')) {
          const match = documents.find((document) => url.includes(document.id));
          return Promise.resolve(jsonResponse(match ?? documents[0]));
        }
        if (url.includes('/documents')) {
          return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
        }
        throw new Error(`Requête non prévue par le test : ${url}`);
      }),
    );

    renderWithProviders(<UploadPanel knowledgeBaseId={KNOWLEDGE_BASE_ID} />);

    const input = screen.getByLabelText(fr['ingest.dropzone.browse']);
    await user.upload(input, [
      new File(['%PDF-1.7 a'], 'note-a.pdf', { type: 'application/pdf' }),
      new File(['%PDF-1.7 b'], 'note-b.pdf', { type: 'application/pdf' }),
    ]);
    await user.click(await screen.findByRole('button', { name: fr['ingest.staging.launch'] }));

    // Deux documents en attente de revue : la barre d'action groupée apparaît,
    // et elle porte désormais les deux gestes.
    const refuse = await screen.findByRole('button', { name: fr['ingest.bulkRefuse.action'] });
    expect(screen.getByRole('button', { name: fr['ingest.bulkAccept.action'] })).toBeDefined();

    await user.click(refuse);

    await vi.waitFor(() => {
      expect(decisions).toHaveLength(2);
    });
    for (const decision of decisions) {
      expect(decision).toEqual({
        decision: {
          optimize_for_tables: false,
          heavy_ocr: false,
          use_parent_child: false,
          chunk_method_override: null,
        },
      });
    }
  });

  it('ne crie pas au doublon sur un versement ordinaire', async () => {
    // Garde-fou contre le faux positif, plus grave que l'angle mort d'origine :
    // annoncer « rien n'a été créé » sur un document neuf ferait douter d'une
    // indexation pourtant réussie.
    const user = userEvent.setup();
    const created = documentRead({
      id: '44444444-4444-4444-8444-444444444444',
      filename: '02-pdf-multi-column-cv-fr.pdf',
      status: 'pending',
      current_job_id: JOB_ID,
    });
    stubFacade(created);
    renderWithProviders(<UploadPanel knowledgeBaseId={KNOWLEDGE_BASE_ID} />);

    await ingest(user, '02-pdf-multi-column-cv-fr.pdf');

    // Le suivi est bien parti — sans quoi l'absence de note ne prouverait rien.
    await screen.findByText(fr['ingest.completedDescription'].replace('{name}', created.filename));
    expect(screen.queryByText(fr['ingest.item.duplicateBadge'])).toBeNull();
  });
});
