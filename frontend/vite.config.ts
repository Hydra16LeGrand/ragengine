import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

/**
 * Configuration Vite de la SPA RAGEngine.
 *
 * Le dev-server proxifie `/api` vers la façade FastAPI afin que le navigateur
 * n'ait jamais à connaître l'origine du backend (même origine en dev comme en
 * prod → pas de CORS, pas d'URL absolue en dur dans le code applicatif).
 * La cible est surchargeable par `VITE_API_PROXY_TARGET` (cf. CONTRIBUTING.md §6 :
 * aucune URL en dur, tout vient de l'environnement).
 *
 * Le bloc `test` vit **ici** et non dans un `vitest.config.ts` séparé : ce
 * dernier prendrait la priorité sur ce fichier au lieu de s'y ajouter, et les
 * tests perdraient l'alias `@` et le plugin React résolus ci-dessous. Une
 * seule source de vérité de build, y compris pour les tests (ADR-032) — d'où
 * `defineConfig` importé de `vitest/config`, qui réexporte celui de Vite en
 * acceptant la clé `test`.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const configuredName = (env.VITE_APP_NAME ?? '').trim();
  const appName = configuredName.length > 0 ? configuredName : 'RAGEngine';
  const apiTarget = env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';

  return {
    plugins: [
      react(),
      {
        // Marque de la page statique (titre, description) : `VITE_APP_NAME`, sinon RAGEngine.
        name: 'ragengine-app-name',
        transformIndexHtml: (html: string) => html.replaceAll('__APP_NAME__', appName),
      },
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: true,
      port: 5173,
      strictPort: false,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 4173,
    },
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
      target: 'es2022',
    },
    test: {
      // Les tests sont posés à côté de ce qu'ils épinglent (`X.test.tsx` face à
      // `X.tsx`), cf. ADR-032 : ils héritent ainsi sans divergence possible du
      // `include` de `tsconfig.json` et des blocs `src/**` d'`eslint.config.js`.
      include: ['src/**/*.test.{ts,tsx}'],
      environment: 'jsdom',
      // `globals: false` (défaut) : chaque fichier importe `describe`/`it`/
      // `expect` de `vitest`. Aucun global implicite à déclarer côté ESLint ou
      // TypeScript — mais l'auto-nettoyage de Testing Library, qui s'accroche à
      // un `afterEach` global, doit alors être branché à la main.
      setupFiles: ['./src/test/setup.ts'],
      restoreMocks: true,
      unstubGlobals: true,
    },
  };
});
