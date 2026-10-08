/**
 * `KnowledgeBaseSiblingNav` isolée : navigation précédent/suivant entre
 * bases adjacentes (même ordre que `KnowledgeBaseListPage`). Testée à part de
 * `KnowledgeBaseDetailPage` — celle-ci monte cinq panneaux dépendants du
 * réseau (documents, membres, évaluations, providers) sans aucun
 * harnais existant ; dupliquer ce harnais pour ce seul ajout aurait retardé
 * la preuve du comportement réel qui compte ici : les liens et l'état
 * désactivé en bout de page chargée.
 */
import { screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { KnowledgeBaseRead } from '@/api/schemas';
import { renderWithProviders } from '@/test/render';

import { KnowledgeBaseSiblingNav } from './KnowledgeBaseDetailPage';

function minimalKb(id: string, name: string): KnowledgeBaseRead {
  return { id, name } as KnowledgeBaseRead;
}

describe('KnowledgeBaseSiblingNav', () => {
  it('lie vers la base précédente et la base suivante quand elles existent', () => {
    renderWithProviders(
      <MemoryRouter>
        <KnowledgeBaseSiblingNav
          previous={minimalKb('kb-1', 'Base précédente')}
          next={minimalKb('kb-3', 'Base suivante')}
        />
      </MemoryRouter>,
    );

    const previousLink = screen.getByRole('link', { name: /Précédente : Base précédente/ });
    expect(previousLink.getAttribute('href')).toBe('/knowledge-bases/kb-1');

    const nextLink = screen.getByRole('link', { name: /Suivante : Base suivante/ });
    expect(nextLink.getAttribute('href')).toBe('/knowledge-bases/kb-3');
  });

  it('désactive proprement précédent/suivant en bout de page chargée', () => {
    const { container } = renderWithProviders(
      <MemoryRouter>
        <KnowledgeBaseSiblingNav previous={undefined} next={undefined} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link')).toBeNull();

    const disabled = container.querySelectorAll('[aria-disabled="true"]');
    expect(disabled).toHaveLength(2);
    for (const element of disabled) {
      expect(element.getAttribute('tabIndex')).toBe('-1');
    }
  });
});
