/**
 * `KnowledgeBaseDetailPage` (481 lignes, sans test — audit dette technique
 * 2026-09, section Frontend). Couture propre à l'écran : chargement, états
 * d'erreur distincts (404 contre panne générique), bascule édition/vue —
 * l'équivalent le plus proche d'un « onglet » dans cet écran, qui remplace
 * l'ensemble des panneaux par le formulaire d'édition puis revient.
 *
 * Harnais partagé : `src/test/renderKnowledgeBaseDetail.tsx` (comblant le
 * constat de `KnowledgeBaseSiblingNav.test.tsx:1-9`, « aucun harnais
 * existant » pour les cinq panneaux dépendants du réseau de cet écran).
 */
import { fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fr } from '@/i18n/locales/fr';
import { knowledgeBaseRead, KNOWLEDGE_BASE_ID } from '@/test/fixtures';
import {
  jsonResponse,
  problemResponse,
  renderKnowledgeBaseDetail,
  stubKnowledgeBaseDetailFetch,
} from '@/test/renderKnowledgeBaseDetail';

/** Requête suspendue jusqu'à ce que le test décide de la résoudre. */
function deferredResponse(): { promise: Promise<Response>; resolve: (response: Response) => void } {
  let resolveFn: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((resolve) => {
    resolveFn = resolve;
  });
  return { promise, resolve: resolveFn };
}

/** N'intercepte que `GET .../knowledge-bases/{id}` — jamais ses sous-ressources. */
function isDetailRequest(url: string, method: string): boolean {
  return (
    method === 'GET' && (url.split('?')[0] ?? url).endsWith(`/knowledge-bases/${KNOWLEDGE_BASE_ID}`)
  );
}

describe('KnowledgeBaseDetailPage — chargement', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche le contenu réel seulement une fois le détail arrivé', async () => {
    const base = knowledgeBaseRead({
      id: KNOWLEDGE_BASE_ID,
      name: 'Documentation juridique',
      slug: 'doc-juridique',
    });
    const pending = deferredResponse();
    stubKnowledgeBaseDetailFetch(base, [
      { match: isDetailRequest, respond: () => pending.promise },
    ]);

    renderKnowledgeBaseDetail(KNOWLEDGE_BASE_ID);

    // Rien du contenu réel tant que la réponse n'est pas arrivée.
    expect(screen.queryByText('Documentation juridique')).toBeNull();

    pending.resolve(jsonResponse(base));

    expect(await screen.findByText('Documentation juridique')).toBeDefined();
    expect(screen.getByText('doc-juridique')).toBeDefined();
  });
});

describe('KnowledgeBaseDetailPage — états d’erreur', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('distingue une base introuvable (404) : titre dédié, retour vers la liste', async () => {
    const base = knowledgeBaseRead({ id: KNOWLEDGE_BASE_ID });
    stubKnowledgeBaseDetailFetch(base, [
      {
        match: isDetailRequest,
        respond: () => Promise.resolve(problemResponse(404, 'Base introuvable.')),
      },
    ]);

    renderKnowledgeBaseDetail(KNOWLEDGE_BASE_ID);

    expect(await screen.findByText(fr['kb.detail.notFoundTitle'])).toBeDefined();
    expect(screen.getByText(fr['kb.detail.notFoundDescription'])).toBeDefined();
    // Pas le message générique : les deux états ne doivent jamais se confondre.
    expect(screen.queryByText(fr['kb.detail.errorTitle'])).toBeNull();

    const backLink = screen.getByRole('link', { name: fr['kb.backToList'] });
    expect(backLink.getAttribute('href')).toBe('/knowledge-bases');
  });

  it('affiche une panne générique avec le message réel du problème, pas confondue avec un 404', async () => {
    const base = knowledgeBaseRead({ id: KNOWLEDGE_BASE_ID });
    stubKnowledgeBaseDetailFetch(base, [
      {
        match: isDetailRequest,
        respond: () => Promise.resolve(problemResponse(503, 'Le moteur RAGFlow est indisponible.')),
      },
    ]);

    renderKnowledgeBaseDetail(KNOWLEDGE_BASE_ID);

    expect(await screen.findByText(fr['kb.detail.errorTitle'])).toBeDefined();
    expect(screen.getByText('Le moteur RAGFlow est indisponible.')).toBeDefined();
    expect(screen.queryByText(fr['kb.detail.notFoundTitle'])).toBeNull();
  });
});

describe('KnowledgeBaseDetailPage — bascule édition / vue', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('« Modifier » remplace les panneaux par le formulaire, « Annuler » y revient', async () => {
    const base = knowledgeBaseRead({ id: KNOWLEDGE_BASE_ID, name: 'Base de test' });
    stubKnowledgeBaseDetailFetch(base);

    renderKnowledgeBaseDetail(KNOWLEDGE_BASE_ID);
    await screen.findByText(fr['kb.detail.configurationTitle']);

    // Vue : ni champ de formulaire, ni bouton d'annulation.
    expect(screen.queryByLabelText(fr['kb.form.name'])).toBeNull();

    // `EvaluationsPanel` propose aussi un bouton « Modifier » (même libellé,
    // évaluation désactivée par défaut sur la base de test) : on cible celui
    // de l'en-tête de page, sans ambiguïté sur lequel des deux est cliqué.
    const pageHeader = await screen.findByRole('banner');
    fireEvent.click(within(pageHeader).getByRole('button', { name: fr['kb.detail.edit'] }));

    // Édition : le formulaire est là, les panneaux de la vue ont disparu.
    expect(screen.getByLabelText<HTMLInputElement>(fr['kb.form.name']).value).toBe('Base de test');
    expect(screen.queryByText(fr['kb.detail.configurationTitle'])).toBeNull();
    expect(screen.queryByRole('button', { name: fr['kb.detail.edit'] })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: fr['form.cancel'] }));

    // Retour à la vue : le formulaire a disparu, la fiche est de nouveau là.
    expect(screen.queryByLabelText(fr['kb.form.name'])).toBeNull();
    expect(await screen.findByText(fr['kb.detail.configurationTitle'])).toBeDefined();
  });
});
