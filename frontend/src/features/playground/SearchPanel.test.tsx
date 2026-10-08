/**
 * ADR-053 : borne de la requête de recherche — saisie limitée, et refus de
 * taille du backend affiché dans la langue de l'interface.
 */
import { fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MAX_QUERY_LENGTH } from '@/api/text-limits';
import { fr } from '@/i18n/locales/fr';
import { formatInteger } from '@/lib/format';
import { renderWithProviders } from '@/test/render';

import { SearchPanel } from './SearchPanel';
import { DEFAULT_RETRIEVAL_SETTINGS } from './settings';

const KB_ID = '33333333-3333-4333-8333-333333333333';

function tooLongResponse(): Response {
  return new Response(
    JSON.stringify({
      type: '/problems/validation-error',
      title: 'Unprocessable Content',
      status: 422,
      detail: 'La requête ne respecte pas le schéma attendu.',
      errors: [
        {
          type: 'string_too_long',
          loc: ['body', 'query'],
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

describe('SearchPanel — bornes de taille (ADR-053)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('affiche un refus de taille traduit, et borne la saisie', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(tooLongResponse())),
    );
    renderWithProviders(
      <SearchPanel knowledgeBaseId={KB_ID} settings={DEFAULT_RETRIEVAL_SETTINGS} />,
    );
    const textbox = screen.getByLabelText<HTMLTextAreaElement>(fr['playground.search.queryLabel']);
    expect(textbox.maxLength).toBe(MAX_QUERY_LENGTH);

    fireEvent.change(textbox, { target: { value: 'q'.repeat(MAX_QUERY_LENGTH) } });
    expect(screen.getByText(asRendered(fr['error.inputLimitReached']))).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: fr['playground.search.submit'] }));

    expect(await screen.findByText(asRendered(fr['error.textTooLong']))).toBeDefined();
    expect(screen.queryByText('La requête ne respecte pas le schéma attendu.')).toBeNull();
  });
});
