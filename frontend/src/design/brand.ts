import { pluginAppName } from '@/plugins';

/** Marque du cœur auto-hébergeable. */
export const DEFAULT_APP_NAME = 'RAGEngine';

/**
 * Nom de marque affiché dans la SPA : la variable de build `VITE_APP_NAME`, sinon le nom
 * fourni par un greffon, sinon `RAGEngine`.
 *
 * Fonction, pas constante : les greffons importent des modules du cœur qui lisent ce nom ;
 * l'appeler au rendu, jamais à l'évaluation d'un module, tient le cycle d'imports hors de danger.
 */
export function getAppName(): string {
  const fromBuild = import.meta.env.VITE_APP_NAME?.trim();
  if (fromBuild) return fromBuild;
  return pluginAppName() ?? DEFAULT_APP_NAME;
}
