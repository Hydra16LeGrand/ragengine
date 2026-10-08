import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { queryClient } from '@/api/query-client';
import { ToastProvider } from '@/components/Toast';
import { getAppName } from '@/design/brand';
import { ThemeProvider } from '@/design/theme';
import { SessionProvider } from '@/features/auth';
import { I18nProvider } from '@/i18n';

import { App } from './App';

import '@/design/global.css';

// Le titre statique d'`index.html` vient de `VITE_APP_NAME` à la construction ; un greffon qui
// fournit sa marque la pose ici, avant le premier rendu.
document.title = getAppName();

const container = document.getElementById('root');
if (container === null) {
  throw new Error('Élément racine « #root » introuvable dans index.html.');
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      {/* `I18nProvider` englobe tout : les notifications et la coquille lisent
          leurs libellés via `useT()`. `SessionProvider` est sous le client de
          requêtes (il interroge `GET /v1/me`) et au-dessus du routeur, dont les
          gardes en dépendent. */}
      <I18nProvider>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <SessionProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </SessionProvider>
          </ToastProvider>
        </QueryClientProvider>
      </I18nProvider>
    </ThemeProvider>
  </StrictMode>,
);
