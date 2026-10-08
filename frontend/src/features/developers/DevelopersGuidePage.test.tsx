import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { getAppName } from '@/design/brand';
import { fr } from '@/i18n/locales/fr';
import { renderWithProviders } from '@/test/render';

import { DevelopersGuidePage } from './DevelopersGuidePage';

describe('DevelopersGuidePage', () => {
  it('affiche le titre, les deux parcours de référence, et un lien vers la référence complète', () => {
    renderWithProviders(<DevelopersGuidePage />);

    expect(
      screen.getByText(fr['developers.title'].replace('{appName}', getAppName())),
    ).toBeDefined();
    expect(screen.getByText(fr['developers.schemaA.title'])).toBeDefined();
    expect(screen.getByText(fr['developers.schemaB.title'])).toBeDefined();

    const referenceLink = screen.getByRole('link', { name: fr['developers.referenceCta'] });
    expect(referenceLink.getAttribute('href')).toBe('/api/v1/docs-public');
    expect(referenceLink.getAttribute('target')).toBe('_blank');
    expect(referenceLink.getAttribute('rel')).toBe('noreferrer');
  });
});
