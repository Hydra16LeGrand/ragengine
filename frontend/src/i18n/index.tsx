import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { getAppName } from '@/design/brand';

import { en } from './locales/en';
import { fr, type Dictionary, type TranslationKey } from './locales/fr';

/**
 * Internationalisation minimale (Phase 0).
 *
 * Volontairement sans dépendance : un dictionnaire typé par locale, un hook
 * `useT()`, et une interpolation `{param}`. Suffisant pour tenir la règle
 * « aucune chaîne d'UI dispersée » (CONTRIBUTING.md §7) sans figer un choix de
 * librairie i18n avant d'en avoir le besoin réel (pluriels, dates, nombres).
 */

export const LOCALES = ['fr', 'en'] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'fr';

const dictionaries: Record<Locale, Dictionary> = { fr, en };

export type TranslateParams = Record<string, string | number>;
export type Translate = (key: TranslationKey, params?: TranslateParams) => string;

export interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
}

const INTERPOLATION_PATTERN = /\{(\w+)\}/g;

function interpolate(template: string, params: TranslateParams | undefined): string {
  if (params === undefined) return template;
  return template.replace(INTERPOLATION_PATTERN, (match: string, token: string): string => {
    const value = params[token];
    return value === undefined ? match : String(value);
  });
}

function createTranslate(locale: Locale): Translate {
  const dictionary = dictionaries[locale];
  // `{appName}` est disponible dans tout libellé : la marque est un réglage, jamais une chaîne figée.
  return (key, params) => interpolate(dictionary[key], { appName: getAppName(), ...params });
}

const I18nContext = createContext<I18nContextValue>({
  locale: DEFAULT_LOCALE,
  setLocale: () => undefined,
  t: createTranslate(DEFAULT_LOCALE),
});

export function I18nProvider({
  children,
  initialLocale = DEFAULT_LOCALE,
}: {
  children: ReactNode;
  initialLocale?: Locale;
}): ReactNode {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale): void => {
    setLocaleState(next);
    document.documentElement.lang = next;
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, t: createTranslate(locale) }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** Locale courante + moyen d'en changer. */
export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

/** Raccourci le plus courant : la fonction de traduction seule. */
export function useT(): Translate {
  return useContext(I18nContext).t;
}

export type { Dictionary, TranslationKey };
