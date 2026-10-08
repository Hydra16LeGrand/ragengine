/**
 * Montage d'un composant avec les fournisseurs de contexte de l'application.
 *
 * Les composants testés ici lisent `useT()` (i18n), `useToast()` et l'état
 * serveur de TanStack Query : les monter nus lèverait, ou pire, rendrait un
 * écran dégradé qu'on prendrait pour le vrai. On reproduit donc l'arbre réel
 * de `App.tsx`, à deux écarts près, tous deux propres au test :
 *  - un `QueryClient` **neuf par montage**, pour qu'aucun cache ne survive d'un
 *    test à l'autre ;
 *  - `retry: false`, pour qu'une erreur attendue échoue tout de suite au lieu
 *    de rejouer trois fois avec un délai exponentiel.
 *
 * `queryClient` (option) permet de fournir une instance déjà construite au
 * lieu d'en créer une neuve — nécessaire pour le seul module qui écrit dans
 * le cache **hors contexte React** : `createEvaluationMutationOptions`
 * (`src/api/evaluations.ts`) importe directement le singleton applicatif
 * (`@/api/query-client`) pour son `onSuccess`, plutôt que `useQueryClient()`.
 * En production ça fonctionne (`main.tsx` monte ce même singleton), mais un
 * test qui monterait un `QueryClient` isolé ne verrait jamais ces écritures :
 * la mutation réussirait sans que l'écran ne se mette jamais à jour, un faux
 * vert silencieux. `EvaluationsPanel.test.tsx` passe donc ce singleton
 * explicitement pour son scénario de lancement.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactNode } from 'react';

import { ToastProvider } from '@/components/Toast';
import { I18nProvider, type Locale } from '@/i18n';

export interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  /** Les libellés attendus par les assertions viennent de ce dictionnaire. */
  locale?: Locale;
  /** Voir la note ci-dessus : par défaut, une instance neuve et isolée. */
  queryClient?: QueryClient;
}

export function renderWithProviders(
  ui: ReactNode,
  { locale = 'fr', queryClient, ...options }: RenderWithProvidersOptions = {},
): RenderResult {
  const client =
    queryClient ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 0, gcTime: 0 },
        mutations: { retry: false },
      },
    });

  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <ToastProvider>{ui}</ToastProvider>
      </I18nProvider>
    </QueryClientProvider>,
    options,
  );
}
