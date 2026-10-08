/// <reference types="vite/client" />

/**
 * Désactive l'index signature `[key: string]: any` de `import.meta.env` :
 * toute variable d'environnement doit être déclarée ci-dessous, sinon elle
 * échoue à la compilation (pas de `any` implicite, cf. CONTRIBUTING.md §7).
 */
interface ViteTypeOptions {
  strictImportMetaEnv: unknown;
}

interface ImportMetaEnv {
  /** Préfixe de l'API. Défaut `/api` (proxyfié vers la façade FastAPI). */
  readonly VITE_API_BASE_URL?: string;
  /** Cible du proxy de dev — lue uniquement par `vite.config.ts`. */
  readonly VITE_API_PROXY_TARGET?: string;
  /** Nom de marque affiché dans la SPA et l'onglet. Défaut : celui du greffon, sinon `RAGEngine`. */
  readonly VITE_APP_NAME?: string;
}
