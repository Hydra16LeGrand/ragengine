import js from '@eslint/js';
import prettierConfig from 'eslint-config-prettier';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * ESLint « flat config ».
 * Ordre : base JS → TS type-aware → React → hooks → fast-refresh → prettier (dernier,
 * il ne fait que désactiver les règles de mise en forme gérées par Prettier).
 */
export default tseslint.config(
  {
    // `src/api/schema.ts` est généré depuis l'OpenAPI du backend
    // (`npm run generate:api`) : il n'est ni écrit ni corrigé à la main. Même
    // règle pour les types générés d'un greffon (`src/plugins/*/api/schema.ts`).
    ignores: [
      'dist/**',
      'node_modules/**',
      'coverage/**',
      'src/api/schema.ts',
      'src/plugins/*/api/schema.ts',
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,

  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // Code applicatif (navigateur).
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.browser,
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat['jsx-runtime'].rules,
      ...reactHooks.configs['recommended-latest'].rules,

      // TypeScript remplit ce rôle : pas de propTypes.
      'react/prop-types': 'off',

      // Fast Refresh : un module = des composants. Les hooks et les tables de
      // constantes des modules « providers » (thème, i18n) sont explicitement
      // tolérés : les regrouper avec leur provider vaut mieux qu'un fichier
      // supplémentaire par constante.
      'react-refresh/only-export-components': [
        'error',
        {
          allowConstantExport: true,
          allowExportNames: [
            'useTheme',
            'useT',
            'useI18n',
            'useToast',
            'useSession',
            'THEME_PREFERENCES',
            'LOCALES',
          ],
        },
      ],

      // Interdictions du brief : pas de `any`, pas de `@ts-ignore`.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/ban-ts-comment': [
        'error',
        { 'ts-expect-error': 'allow-with-description', 'ts-ignore': true },
      ],
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-console': ['error', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'always', { null: 'ignore' }],
    },
  },

  // Fichiers de configuration Node (ESM).
  {
    files: ['*.config.{js,ts}', 'vite.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },

  // Les fichiers JS (config ESLint incluse) ne sont pas dans le programme TS.
  {
    files: ['**/*.js'],
    extends: [tseslint.configs.disableTypeChecked],
  },

  prettierConfig,
);
