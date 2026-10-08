/**
 * Ce que la liste des fragments **dit** d'un découpage parent/enfant (ADR-033).
 *
 * Le défaut corrigé ici n'était pas un défaut de rendu mais de sens : sur un
 * document indexé avec la granularité affinée, le moteur marque ses fragments
 * parents `available=false` — exactement comme un fragment désactivé. L'écran
 * les affichait donc en « Désactivé », et les enfants d'une seule ligne sans
 * aucune explication : lu par l'utilisateur, un découpage cassé.
 *
 * Le test se pose sur la couture asynchrone (CONTRIBUTING.md §7) — l'état de l'écran
 * une fois la page de fragments arrivée — et non sur une fonction pure : c'est
 * le drapeau `uses_parent_child` porté par la réponse HTTP qui arbitre le
 * rendu, et lui seul distingue un parent d'un fragment désactivé.
 */
import { screen, waitFor, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DocumentChunksPage } from '@/api/schemas';
import { fr } from '@/i18n/locales/fr';
import { DOCUMENT_ID } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { ChunksDialog } from './ChunksDialog';

function chunksPage(overrides: Partial<DocumentChunksPage> = {}): DocumentChunksPage {
  return {
    data: [
      {
        chunk_id: 'enfant-1',
        content: 'Contexte',
        page_start: 1,
        page_end: 1,
        important_keywords: [],
        questions: [],
        available: true,
      },
      {
        chunk_id: 'parent-1',
        content: 'Le passage entier, celui que la recherche renvoie réellement.',
        page_start: 1,
        page_end: 2,
        important_keywords: [],
        questions: [],
        available: false,
      },
    ],
    document: {
      chunk_method: 'naive',
      chunk_count: 2,
      token_count: 42,
      uses_parent_child: false,
    },
    total: 2,
    page: 1,
    page_size: 20,
    ...overrides,
  };
}

/**
 * `responder` accepte une page fixe ou une fonction de l'URL demandée — cette
 * seconde forme sert à distinguer une requête `parents_only=true` de son
 * pendant sans filtre, désormais deux appels réseau réels (ADR-033, le
 * backend repagine lui-même) plutôt qu'un filtrage local sur une même page.
 */
function stubChunks(responder: DocumentChunksPage | ((url: URL) => DocumentChunksPage)): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/chunks')) {
        const page =
          typeof responder === 'function'
            ? responder(new URL(url, window.location.href))
            : responder;
        return Promise.resolve(
          new Response(JSON.stringify(page), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
        );
      }
      throw new Error(`Requête non prévue par le test : ${url}`);
    }),
  );
}

function renderDialog(): RenderResult {
  return renderWithProviders(
    <ChunksDialog documentId={DOCUMENT_ID} filename="contrat.pdf" open onClose={vi.fn()} />,
  );
}

/** Les boutons « Suivant » réellement présents, dans l'ordre du document. */
function nextButtons(): Promise<HTMLButtonElement[]> {
  return screen.findAllByRole<HTMLButtonElement>('button', {
    name: fr['kb.documents.chunks.next'],
  });
}

function paginationLabel(page: number, totalPages: number): string {
  return fr['kb.documents.chunks.pagination']
    .replace('{page}', String(page))
    .replace('{totalPages}', String(totalPages));
}

describe('ChunksDialog', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('explique le découpage parent/enfant et nomme le fragment parent pour ce qu’il est', async () => {
    stubChunks(
      chunksPage({
        document: {
          chunk_method: 'naive',
          chunk_count: 2,
          token_count: 42,
          uses_parent_child: true,
        },
      }),
    );
    renderDialog();

    // La note d'explication précède la liste : sans elle, des fragments d'une
    // ligne se lisent comme un découpage raté.
    expect(await screen.findByText(fr['kb.documents.chunks.parentChildNote'])).toBeDefined();
    // Le fragment `available=false` est le passage restitué, pas un désactivé.
    expect(screen.getByText(fr['kb.documents.chunks.parent'])).toBeDefined();
    expect(screen.getByText(fr['kb.documents.chunks.child'])).toBeDefined();
    expect(screen.queryByText(fr['kb.documents.chunks.disabled'])).toBeNull();
  });

  it('demande au backend de repaginer sur les seuls parents quand le filtre est coché', async () => {
    // Deux réponses distinctes selon `parents_only` : le backend repagine
    // lui-même (ADR-033, incident du 2026-09-07 — une pagination calculée
    // sur l'ensemble complet rendait des pages vides ou un total erroné dès
    // qu'un lot de fragments ne contenait aucun parent).
    const full = chunksPage({
      document: { chunk_method: 'naive', chunk_count: 2, token_count: 42, uses_parent_child: true },
    });
    const parentsOnlyPage = chunksPage({
      data: full.data.filter((chunk) => !chunk.available),
      document: full.document,
      total: 1,
    });
    stubChunks((url) => (url.searchParams.get('parents_only') === 'true' ? parentsOnlyPage : full));
    renderDialog();

    expect(await screen.findByText('Contexte')).toBeDefined();
    expect(
      screen.getByText('Le passage entier, celui que la recherche renvoie réellement.'),
    ).toBeDefined();

    // Le nom accessible de la case concatène titre et description (tous deux
    // dans le même `<label>`, patron du composant `Checkbox`) : un seul
    // contrôle dans cette boîte de dialogue, pas besoin de nom exact.
    await userEvent.click(screen.getByRole('checkbox'));

    await waitFor(() => {
      expect(screen.queryByText('Contexte')).toBeNull();
    });
    expect(
      screen.getByText('Le passage entier, celui que la recherche renvoie réellement.'),
    ).toBeDefined();
  });

  it('revient à la page 1 en basculant le filtre, pour ne jamais retomber sur une page hors bornes', async () => {
    const full = chunksPage({
      document: { chunk_method: 'naive', chunk_count: 2, token_count: 42, uses_parent_child: true },
      total: 45,
    });
    const parentsOnlyPage = chunksPage({
      data: full.data.filter((chunk) => !chunk.available),
      document: full.document,
      total: 1,
    });
    stubChunks((url) => (url.searchParams.get('parents_only') === 'true' ? parentsOnlyPage : full));
    const user = userEvent.setup();
    renderDialog();

    const [topNext] = await nextButtons();
    if (topNext === undefined) throw new Error('Le bloc de navigation du haut est absent.');
    await user.click(topNext);
    await screen.findAllByText(paginationLabel(2, 3));

    await user.click(screen.getByRole('checkbox'));

    await waitFor(() => {
      expect(screen.queryByText(paginationLabel(2, 3))).toBeNull();
    });
  });

  it('garde la case accessible même quand le filtre ne retient aucun parent', async () => {
    // `chunk_count > 0` distingue « le document est vide » de « le filtre ne
    // retient rien » — sans cette distinction, l'utilisateur n'aurait aucun
    // moyen de décocher le filtre depuis cet écran.
    const full = chunksPage({
      document: { chunk_method: 'naive', chunk_count: 2, token_count: 42, uses_parent_child: true },
    });
    const emptyParentsPage = chunksPage({
      data: [],
      document: full.document,
      total: 0,
    });
    stubChunks((url) =>
      url.searchParams.get('parents_only') === 'true' ? emptyParentsPage : full,
    );
    renderDialog();

    await screen.findByText('Contexte');
    await userEvent.click(screen.getByRole('checkbox'));

    expect(await screen.findByText(fr['kb.documents.chunks.noParentsFoundTitle'])).toBeDefined();
    expect(screen.getByRole('checkbox')).toBeDefined();
  });

  it('ne propose pas le filtre « passages restitués » hors granularité affinée', async () => {
    stubChunks(chunksPage());
    renderDialog();

    await screen.findByText('Contexte');
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('garde le libellé « Désactivé » quand le document n’est pas en parent/enfant', async () => {
    stubChunks(chunksPage());
    renderDialog();

    expect(await screen.findByText(fr['kb.documents.chunks.disabled'])).toBeDefined();
    expect(screen.queryByText(fr['kb.documents.chunks.parentChildNote'])).toBeNull();
    expect(screen.queryByText(fr['kb.documents.chunks.parent'])).toBeNull();
    expect(screen.queryByText(fr['kb.documents.chunks.child'])).toBeNull();
  });

  it('traduit le nom interne « naive » en libellé compréhensible, jamais affiché brut', async () => {
    // `chunk_method="naive"` est le nom interne RAGFlow du parseur généraliste
    // (celui qui respecte `layout_recognize` et porte le parent/enfant) — pas
    // un abandon de la mise en page, contrairement à ce que le mot suggère.
    stubChunks(chunksPage({ document: { ...chunksPage().document, chunk_method: 'naive' } }));
    renderDialog();

    expect(
      await screen.findByText((content) =>
        content.includes(fr['kb.documents.chunks.method.naive']),
      ),
    ).toBeDefined();
    expect(screen.queryByText((content) => content.includes('naive'))).toBeNull();
  });

  it('retombe sur la chaîne brute pour une méthode de découpage non répertoriée', async () => {
    stubChunks(
      chunksPage({ document: { ...chunksPage().document, chunk_method: 'future_method_xyz' } }),
    );
    renderDialog();

    expect(
      await screen.findByText((content) => content.includes('future_method_xyz')),
    ).toBeDefined();
  });

  it('place les contrôles de navigation en tête **et** en pied de la liste', async () => {
    // Sur un document long, la liste dépasse la hauteur de la modale : des
    // boutons uniquement en bas obligent à la parcourir entière pour changer
    // de page. Les deux blocs encadrent donc la liste.
    stubChunks(chunksPage({ total: 45, page_size: 20 }));
    renderDialog();

    const [topNext, bottomNext, ...extra] = await nextButtons();
    expect(extra).toHaveLength(0);
    if (topNext === undefined || bottomNext === undefined) {
      throw new Error('Les deux blocs de navigation doivent encadrer la liste.');
    }
    expect(
      screen.getAllByRole('button', { name: fr['kb.documents.chunks.previous'] }),
    ).toHaveLength(2);

    const list = screen.getByRole('list');
    expect(list.compareDocumentPosition(topNext) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(
      list.compareDocumentPosition(bottomNext) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('navigue depuis le bloc du haut comme depuis celui du bas', async () => {
    // Même état, mêmes gestionnaires : le bloc ajouté n'a pas sa propre
    // logique de navigation, il pilote celle qui existait déjà.
    const user = userEvent.setup();
    stubChunks(chunksPage({ total: 45, page_size: 20 }));
    renderDialog();

    const [topNext] = await nextButtons();
    if (topNext === undefined) throw new Error('Le bloc de navigation du haut est absent.');
    await user.click(topNext);

    expect(await screen.findAllByText(paginationLabel(2, 3))).toHaveLength(2);
    // Première page quittée : le retour redevient possible des deux côtés.
    for (const previous of screen.getAllByRole<HTMLButtonElement>('button', {
      name: fr['kb.documents.chunks.previous'],
    })) {
      expect(previous.disabled).toBe(false);
    }
  });

  it('n’affiche aucun contrôle de navigation quand tout tient sur une page', async () => {
    stubChunks(chunksPage());
    renderDialog();

    await screen.findByRole('list');
    expect(screen.queryByRole('button', { name: fr['kb.documents.chunks.next'] })).toBeNull();
    expect(screen.queryByRole('button', { name: fr['kb.documents.chunks.previous'] })).toBeNull();
  });

  it('affiche « non déterminée » quand la méthode de découpage est absente', async () => {
    stubChunks(chunksPage({ document: { ...chunksPage().document, chunk_method: null } }));
    renderDialog();

    expect(
      await screen.findByText((content) =>
        content.includes(fr['kb.documents.chunks.methodUnknown']),
      ),
    ).toBeDefined();
  });
});
