/**
 * Greffons de la SPA, découverts dans `src/plugins/<nom>/index.ts(x)` (ADR-069).
 *
 * Le cœur n'importe aucun greffon par son nom : `import.meta.glob` les réunit
 * à la construction. Sans dossier de greffon, la liste est vide et chaque
 * emplacement rend le comportement du cœur — retirer un greffon, c'est retirer
 * son dossier, sans drapeau ni branche morte.
 *
 * Les accesseurs ci-dessous sont des **fonctions** : un greffon importe des
 * modules du cœur qui eux-mêmes lisent ces accesseurs ; les appeler au rendu,
 * jamais à l'évaluation d'un module, tient ce cycle d'imports hors de danger.
 */
import type { FrontendPlugin, PluginApiKeyScope, PluginNavEntry } from './types';

const modules = import.meta.glob<{ plugin: FrontendPlugin }>('./*/index.{ts,tsx}', {
  eager: true,
});

let discovered: readonly FrontendPlugin[] | null = null;
let override: readonly FrontendPlugin[] | null = null;

/** Greffons actifs, dans l'ordre de leur dossier. */
export function getPlugins(): readonly FrontendPlugin[] {
  if (override !== null) return override;
  discovered ??= Object.keys(modules)
    .sort()
    .map((path) => modules[path]?.plugin)
    .filter((plugin): plugin is FrontendPlugin => plugin !== undefined);
  return discovered;
}

/** Remplace les greffons découverts — réservé aux tests (`null` rétablit la découverte). */
export function overridePlugins(plugins: readonly FrontendPlugin[] | null): void {
  override = plugins;
}

/** Nom de marque fourni par un greffon, ou `undefined` (le cœur seul s'appelle RAGEngine). */
export function pluginAppName(): string | undefined {
  return getPlugins().find((plugin) => plugin.appName !== undefined)?.appName;
}

/** Un greffon sert-il des visiteurs sans compte ? Le cœur seul exige une connexion. */
export function admitsAnonymous(): boolean {
  return getPlugins().some((plugin) => plugin.admitsAnonymous === true);
}

export function pluginNavEntries(): readonly PluginNavEntry[] {
  return getPlugins().flatMap((plugin) => plugin.navEntries ?? []);
}

export function pluginApiKeyScopes(): readonly PluginApiKeyScope[] {
  return getPlugins().flatMap((plugin) => plugin.apiKeyScopes ?? []);
}

/** Composants d'un emplacement donné, dans l'ordre des greffons. */
export function pluginSlots<K extends keyof FrontendPlugin>(
  slot: K,
): NonNullable<FrontendPlugin[K]>[] {
  return getPlugins().flatMap((plugin) => {
    const value = plugin[slot];
    return value === undefined ? [] : [value as NonNullable<FrontendPlugin[K]>];
  });
}

export type * from './types';
