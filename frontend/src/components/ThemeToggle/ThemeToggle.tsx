import type { ReactNode } from 'react';

import { THEME_PREFERENCES, useTheme, type ThemePreference } from '@/design/theme';
import { useT, type TranslationKey } from '@/i18n';
import { cx } from '@/lib/cx';

import styles from './ThemeToggle.module.css';

const LABEL_KEYS: Record<ThemePreference, TranslationKey> = {
  light: 'theme.light',
  dark: 'theme.dark',
  system: 'theme.system',
};

/** Icônes maison (aucune librairie, aucun CDN) — 24×24, trait courant. */
const ICONS: Record<ThemePreference, ReactNode> = {
  light: (
    <>
      <circle cx="12" cy="12" r="4.25" />
      <path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
    </>
  ),
  dark: <path d="M20 14.4A8.4 8.4 0 0 1 9.6 4a8.4 8.4 0 1 0 10.4 10.4Z" />,
  system: (
    <>
      <rect x="2.75" y="4.75" width="18.5" height="12.5" rx="1.75" />
      <path d="M8.5 20.5h7" />
    </>
  ),
};

/**
 * Sélecteur de thème : clair / sombre / système.
 *
 * Implémenté avec de vrais boutons radio (un seul choix possible, navigation
 * aux flèches et annonce vocale natives), stylés via le label parent.
 */
export function ThemeToggle(): ReactNode {
  const t = useT();
  const { preference, setPreference } = useTheme();

  return (
    <fieldset className={styles.group}>
      <legend className="visually-hidden">{t('theme.label')}</legend>
      {THEME_PREFERENCES.map((value) => {
        const label = t(LABEL_KEYS[value]);
        return (
          <label
            key={value}
            className={cx(styles.option, preference === value && styles.optionActive)}
            title={label}
          >
            <input
              className={styles.input}
              type="radio"
              name="theme-preference"
              value={value}
              checked={preference === value}
              onChange={() => {
                setPreference(value);
              }}
            />
            <svg
              className={styles.icon}
              viewBox="0 0 24 24"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
            >
              {ICONS[value]}
            </svg>
            <span className="visually-hidden">{label}</span>
          </label>
        );
      })}
    </fieldset>
  );
}
