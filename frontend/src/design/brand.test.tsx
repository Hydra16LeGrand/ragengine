import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider, useT } from '@/i18n';
import { overridePlugins } from '@/plugins';

import { getAppName } from './brand';

function Title(): React.ReactNode {
  const t = useT();
  return <h1>{t('developers.title')}</h1>;
}

describe('marque configurable', () => {
  afterEach(() => {
    overridePlugins(null);
    vi.unstubAllEnvs();
  });

  it('le cœur seul s’appelle RAGEngine, dans le nom comme dans les libellés', () => {
    overridePlugins([]);
    vi.stubEnv('VITE_APP_NAME', '');

    expect(getAppName()).toBe('RAGEngine');
    render(
      <I18nProvider initialLocale="en">
        <Title />
      </I18nProvider>,
    );
    expect(screen.getByRole('heading').textContent).toBe('Integrate RAGEngine via the API');
  });

  it('un greffon fournit sa marque, la variable de build l’emporte', () => {
    overridePlugins([{ name: 'demo', appName: 'Marque Démo' }]);
    vi.stubEnv('VITE_APP_NAME', '');
    expect(getAppName()).toBe('Marque Démo');

    vi.stubEnv('VITE_APP_NAME', 'Mon Moteur');
    expect(getAppName()).toBe('Mon Moteur');
    render(
      <I18nProvider initialLocale="fr">
        <Title />
      </I18nProvider>,
    );
    expect(screen.getByRole('heading').textContent).toBe('Intégrer Mon Moteur par l’API');
  });
});
