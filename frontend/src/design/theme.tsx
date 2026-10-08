import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

/**
 * Gestion du thème clair / sombre / système.
 *
 * Contrat (partagé avec le script inline de `index.html`, qui pose l'attribut
 * `data-theme` AVANT le premier paint pour éviter tout flash) :
 *  - `localStorage['ragengine.theme']` contient la PRÉFÉRENCE : light | dark | system ;
 *  - `<html data-theme>` contient le thème RÉSOLU : light | dark.
 *
 * Toute modification de ces deux constantes doit être répercutée dans
 * `index.html` (le script y est volontairement dupliqué : il doit s'exécuter
 * avant le bundle).
 */

export const THEME_STORAGE_KEY = 'ragengine.theme';
export const THEME_ATTRIBUTE = 'data-theme';
const DARK_MEDIA_QUERY = '(prefers-color-scheme: dark)';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_PREFERENCES: readonly ThemePreference[] = ['light', 'dark', 'system'];

export interface ThemeContextValue {
  /** Ce que l'utilisateur a choisi (peut valoir « system »). */
  preference: ThemePreference;
  /** Ce qui est réellement appliqué au document. */
  resolvedTheme: ResolvedTheme;
  /** Thème actuel du système d'exploitation. */
  systemTheme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** Bascule directe entre clair et sombre (sort du mode « system »). */
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system';
}

function readStoredPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : 'system';
  } catch {
    // Stockage indisponible (mode privé, cookies bloqués) : repli silencieux.
    return 'system';
  }
}

function writeStoredPreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Stockage indisponible : la préférence ne survivra pas au rechargement.
  }
}

function getSystemTheme(): ResolvedTheme {
  return window.matchMedia(DARK_MEDIA_QUERY).matches ? 'dark' : 'light';
}

/* -------------------------------------------------------------------------- */
/* Stores externes (useSyncExternalStore) : thème système + préférence stockée */
/* -------------------------------------------------------------------------- */

function subscribeToSystemTheme(onStoreChange: () => void): () => void {
  const mediaQuery = window.matchMedia(DARK_MEDIA_QUERY);
  mediaQuery.addEventListener('change', onStoreChange);
  return () => {
    mediaQuery.removeEventListener('change', onStoreChange);
  };
}

/**
 * Notifie les abonnés quand la préférence change : événement custom pour
 * l'onglet courant, événement `storage` pour les autres onglets.
 */
const PREFERENCE_EVENT = 'ragengine:theme-preference';

function subscribeToPreference(onStoreChange: () => void): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === THEME_STORAGE_KEY) onStoreChange();
  };
  window.addEventListener('storage', onStorage);
  window.addEventListener(PREFERENCE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener('storage', onStorage);
    window.removeEventListener(PREFERENCE_EVENT, onStoreChange);
  };
}

function emitPreferenceChange(): void {
  window.dispatchEvent(new Event(PREFERENCE_EVENT));
}

/** Applique le thème résolu au document (et à la barre d'adresse mobile). */
function applyResolvedTheme(resolvedTheme: ResolvedTheme): void {
  const root = document.documentElement;
  if (root.getAttribute(THEME_ATTRIBUTE) !== resolvedTheme) {
    root.setAttribute(THEME_ATTRIBUTE, resolvedTheme);
  }

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    const canvas = getComputedStyle(root).getPropertyValue('--color-canvas').trim();
    if (canvas) meta.setAttribute('content', canvas);
  }
}

/* -------------------------------------------------------------------------- */
/* Provider                                                                    */
/* -------------------------------------------------------------------------- */

export function ThemeProvider({ children }: { children: ReactNode }): ReactNode {
  const systemTheme = useSyncExternalStore<ResolvedTheme>(
    subscribeToSystemTheme,
    getSystemTheme,
    () => 'light',
  );
  const preference = useSyncExternalStore<ThemePreference>(
    subscribeToPreference,
    readStoredPreference,
    () => 'system',
  );

  const resolvedTheme: ResolvedTheme = preference === 'system' ? systemTheme : preference;

  // `useLayoutEffect` s'exécute avant le paint : le changement d'attribut ne
  // produit donc aucun clignotement intermédiaire.
  useLayoutEffect(() => {
    applyResolvedTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setPreference = useCallback((next: ThemePreference): void => {
    writeStoredPreference(next);
    emitPreferenceChange();
  }, []);

  const toggleTheme = useCallback((): void => {
    setPreference(resolvedTheme === 'dark' ? 'light' : 'dark');
  }, [resolvedTheme, setPreference]);

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolvedTheme, systemTheme, setPreference, toggleTheme }),
    [preference, resolvedTheme, systemTheme, setPreference, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** Accès au thème courant. Doit être appelé sous un `<ThemeProvider>`. */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === null) {
    throw new Error('useTheme() doit être utilisé à l’intérieur d’un <ThemeProvider>.');
  }
  return context;
}
