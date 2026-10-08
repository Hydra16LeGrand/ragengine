/**
 * `KnowledgeBaseForm` (508 lignes, sans test dédié — audit dette technique
 * 2026-09, section Frontend). Le parcours de création avec dépôt de documents
 * (ADR-062) est déjà couvert bout en bout par `KnowledgeBaseCreatePage.test.tsx`
 * (ordre création → envoi, échec de création sans envoi, échec d'envoi avec
 * reprise) — on ne le reproduit pas ici. Ce fichier couvre ce que ce test de
 * page ne peut pas voir, propre au formulaire lui-même :
 *  - le catalogue de briques arrive après le montage (squelette → sélecteurs),
 *    et la sélection par défaut évite bien une brique indisponible ;
 *  - le sélecteur d'organisation, invisible pour un compte mono-organisation
 *    (seul cas exercé par le test de page), apparaît à partir de deux et
 *    présélectionne la plus ancienne ;
 *  - le retrait d'un fichier mis en attente avant la création (le test de
 *    page n'exerce que l'ajout puis l'envoi, jamais le retrait) ;
 *  - la validation de la configuration JSON avancée au moment de l'envoi.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { KnowledgeBaseFormValues } from './KnowledgeBaseForm';
import { KnowledgeBaseForm } from './KnowledgeBaseForm';
import { nextStagedFileId, type StagedFile } from '@/features/ingestion';
import { fr } from '@/i18n/locales/fr';
import { fieldLabelled } from '@/test/dom';
import { knowledgeBaseRead, organizationRead } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function urlOf(input: string | URL | Request): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

/** Un embedding indisponible en premier : la valeur par défaut doit le sauter. */
const PROVIDER_CATALOG = {
  embeddings: [
    {
      provider_id: 'legacy-embed',
      label: 'Legacy',
      is_local: true,
      dimensions: 768,
      available: false,
    },
    { provider_id: 'bge-m3', label: 'BGE-M3', is_local: true, dimensions: 1024, available: true },
  ],
  rerankers: [
    {
      provider_id: 'bge-reranker-v2-m3',
      label: 'BGE Reranker v2-M3',
      is_local: true,
      max_documents: 50,
      available: true,
    },
  ],
  llm: [],
  observability: { provider_id: 'langfuse', label: 'Langfuse', available: false },
};

/** Mock réseau minimal : catalogue toujours servi, organisations en option. */
function stubFetch(organizations?: unknown[]): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = urlOf(input);
      if (url.includes('/v1/providers')) return Promise.resolve(jsonResponse(PROVIDER_CATALOG));
      if (url.includes('/v1/organizations') && organizations !== undefined) {
        return Promise.resolve(jsonResponse({ data: organizations, next_cursor: null }));
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );
}

function noop(): void {
  // volontairement vide : props obligatoires non observées par le test courant
}

describe('KnowledgeBaseForm — catalogue des briques d’inférence', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('passe du squelette aux sélecteurs, saute une brique indisponible pour le défaut', async () => {
    stubFetch([organizationRead()]);

    renderWithProviders(
      <KnowledgeBaseForm
        mode="create"
        submitting={false}
        error={null}
        submitLabel="Créer la base"
        onSubmit={noop}
        onCancel={noop}
      />,
    );

    // Avant l'arrivée du catalogue : pas de sélecteur, un squelette à la place.
    expect(screen.queryByLabelText(fr['kb.form.embedding'])).toBeNull();

    const embeddingSelect = await screen.findByLabelText<HTMLSelectElement>(
      fr['kb.form.embedding'],
    );
    // `legacy-embed` est indisponible et listé en premier : le défaut doit le
    // sauter pour retenir `bge-m3`, la première brique réellement servie.
    expect(embeddingSelect.value).toBe('bge-m3');

    const embeddingOptions = within(embeddingSelect).getAllByRole('option');
    expect(embeddingOptions).toHaveLength(2);
    expect((embeddingOptions[0] as HTMLOptionElement | undefined)?.disabled).toBe(true);
    expect((embeddingOptions[1] as HTMLOptionElement | undefined)?.disabled).toBe(false);

    // Reranker : défaut sur la brique locale disponible, pas sur « Aucun ».
    // Champ facultatif (`optionalLabel`) : son nom accessible concatène
    // « Facultatif » au libellé (cf. `fieldLabelled`).
    const rerankerSelect = fieldLabelled('combobox', fr['kb.form.reranker']) as HTMLSelectElement;
    expect(rerankerSelect.value).toBe('bge-reranker-v2-m3');
  });
});

describe('KnowledgeBaseForm — organisation propriétaire', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche le sélecteur à partir de deux organisations, présélectionne la plus ancienne', async () => {
    // Convention serveur : la liste est triée du plus récent au plus ancien
    // (`created_at.desc()`) — l'organisation la plus ancienne est donc la
    // **dernière** du tableau, et c'est elle le défaut recherché.
    const recent = organizationRead({
      id: '99999999-9999-4999-8999-999999999999',
      name: 'Organisation récente',
    });
    const original = organizationRead({
      id: '88888888-8888-4888-8888-888888888888',
      name: 'Organisation historique',
    });
    stubFetch([recent, original]);

    renderWithProviders(
      <KnowledgeBaseForm
        mode="create"
        submitting={false}
        error={null}
        submitLabel="Créer la base"
        onSubmit={noop}
        onCancel={noop}
      />,
    );

    const orgSelect = await screen.findByLabelText<HTMLSelectElement>(fr['kb.form.organization']);
    expect(orgSelect.value).toBe(original.id);

    const options = within(orgSelect).getAllByRole('option');
    const defaultOption = options.find(
      (option) => (option as HTMLOptionElement).value === original.id,
    ) as HTMLOptionElement | undefined;
    const otherOption = options.find(
      (option) => (option as HTMLOptionElement).value === recent.id,
    ) as HTMLOptionElement | undefined;
    // La plus ancienne (présélectionnée) s'affiche sous un libellé générique,
    // pas sous son vrai nom — l'autre garde le sien.
    expect(defaultOption?.textContent).toBe(fr['kb.form.organizationDefault']);
    expect(otherOption?.textContent).toBe('Organisation récente');
  });
});

/**
 * `KnowledgeBaseForm` ne porte aucun état pour les fichiers en attente — ce
 * sont des props contrôlées (`stagedFiles`/`onStageFiles`/`onUnstageFile`),
 * portées par l'appelant (`KnowledgeBaseCreatePage` en production). Ce banc
 * reproduit ce câblage a minima pour tester le contrat du formulaire seul.
 */
function StagingHarness(): ReactNode {
  const [staged, setStaged] = useState<StagedFile[]>([]);

  return (
    <KnowledgeBaseForm
      mode="create"
      submitting={false}
      error={null}
      submitLabel="Créer la base"
      onSubmit={noop}
      onCancel={noop}
      stagedFiles={staged}
      onStageFiles={(files) => {
        setStaged((previous) => [
          ...previous,
          ...files.map<StagedFile>((file) => ({ id: nextStagedFileId(), file })),
        ]);
      }}
      onUnstageFile={(id) => {
        setStaged((previous) => previous.filter((item) => item.id !== id));
      }}
    />
  );
}

function removeButtonFor(filename: string): HTMLElement {
  const row = screen.getByText(filename).closest('li');
  if (row === null) throw new Error(`Ligne introuvable pour ${filename}`);
  return within(row).getByRole('button', { name: fr['ingest.item.remove'] });
}

describe('KnowledgeBaseForm — documents déposés avant la création (ADR-062)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('retire un fichier mis en attente sans toucher aux autres', async () => {
    stubFetch([organizationRead()]);
    const user = userEvent.setup();

    renderWithProviders(<StagingHarness />);
    await screen.findByLabelText(fr['kb.form.embedding']);

    const input = screen.getByLabelText(fr['ingest.dropzone.browse']);
    await user.upload(input, [
      new File(['%PDF-1.7'], 'un.pdf', { type: 'application/pdf' }),
      new File(['%PDF-1.7'], 'deux.pdf', { type: 'application/pdf' }),
    ]);

    expect(screen.getByText('un.pdf')).toBeDefined();
    expect(screen.getByText('deux.pdf')).toBeDefined();

    await user.click(removeButtonFor('un.pdf'));

    expect(screen.queryByText('un.pdf')).toBeNull();
    expect(screen.getByText('deux.pdf')).toBeDefined();
  });
});

describe('KnowledgeBaseForm — configuration des briques (JSON avancé)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('bloque un JSON invalide ou non-objet, transmet un objet valide dans `onSubmit`', async () => {
    stubFetch();
    const state: { submitted: KnowledgeBaseFormValues | null; calls: number } = {
      submitted: null,
      calls: 0,
    };

    renderWithProviders(
      <KnowledgeBaseForm
        mode="edit"
        initial={knowledgeBaseRead()}
        submitting={false}
        error={null}
        submitLabel="Enregistrer"
        onSubmit={(values) => {
          state.submitted = values;
          state.calls += 1;
        }}
        onCancel={noop}
      />,
    );

    // Mode édition : ni organisation ni documents à charger, mais le
    // catalogue reste requis pour les libellés des briques déjà choisies.
    await screen.findByLabelText(fr['kb.form.embedding']);
    fireEvent.click(screen.getByText(fr['kb.form.advancedTitle']));
    // Champ facultatif lui aussi : même repli par préfixe.
    const configField = fieldLabelled('textbox', fr['kb.form.providerConfig']);
    const submitButton = screen.getByRole('button', { name: 'Enregistrer' });

    fireEvent.change(configField, { target: { value: '{not valid json' } });
    fireEvent.click(submitButton);
    expect(await screen.findByText(fr['kb.form.configInvalid'])).toBeDefined();
    expect(state.calls).toBe(0);

    fireEvent.change(configField, { target: { value: '["pas", "un", "objet"]' } });
    fireEvent.click(submitButton);
    expect(await screen.findByText(fr['kb.form.configNotObject'])).toBeDefined();
    expect(state.calls).toBe(0);

    fireEvent.change(configField, { target: { value: '{"chunk_size": 512}' } });
    fireEvent.click(submitButton);
    await waitFor(() => {
      expect(state.calls).toBe(1);
    });
    expect(state.submitted?.provider_config).toEqual({ chunk_size: 512 });
    // L'erreur du tour précédent ne doit pas rester affichée sur un envoi accepté.
    expect(screen.queryByText(fr['kb.form.configNotObject'])).toBeNull();
  });
});
