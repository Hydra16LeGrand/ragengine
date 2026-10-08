import type { ReactNode } from 'react';

import styles from './EmptyState.module.css';

/**
 * Illustrations maison — aucune banque d'images, aucun CDN, aucune icône
 * tierce (CONTRIBUTING.md §7). Toutes suivent la même main que la marque du
 * bandeau : trait seul, extrémités arrondies, viewBox 120×80, deux niveaux de
 * lecture (structure neutre + un élément accentué).
 */
export type IllustrationName = 'library' | 'documents' | 'lock' | 'search';

/** Rayonnage vide : trois volumes rangés, un emplacement libre en pointillés. */
function LibraryIllustration(): ReactNode {
  return (
    <>
      <path className={styles.structure} d="M14 62h92" />
      <rect className={styles.structure} x="22" y="30" width="14" height="32" rx="2.5" />
      <rect className={styles.structure} x="40" y="22" width="14" height="40" rx="2.5" />
      <path className={styles.structure} d="M26 38h6M44 30h6" />
      <rect
        className={styles.slot}
        x="58"
        y="34"
        width="16"
        height="28"
        rx="2.5"
        strokeDasharray="4 4"
      />
      <rect className={styles.accent} x="80" y="26" width="15" height="36" rx="2.5" />
      <path className={styles.accent} d="M84 34h7M84 40h4" />
    </>
  );
}

/** Feuillets superposés, coin replié — un document versé au corpus. */
function DocumentsIllustration(): ReactNode {
  return (
    <>
      <path
        className={styles.structure}
        d="M34 20h24l14 14v30a4 4 0 0 1-4 4H34a4 4 0 0 1-4-4V24a4 4 0 0 1 4-4Z"
      />
      <path className={styles.structure} d="M58 20v10a4 4 0 0 0 4 4h10" />
      <path className={styles.accent} d="M40 44h22M40 52h16M40 60h10" />
      <path className={styles.slot} d="M78 30h8a4 4 0 0 1 4 4v34a4 4 0 0 1-4 4H50" />
    </>
  );
}

/** Cadenas : espace privé, élévation vers la connexion. */
function LockIllustration(): ReactNode {
  return (
    <>
      <rect className={styles.structure} x="38" y="38" width="44" height="30" rx="5" />
      <path className={styles.accent} d="M48 38v-8a12 12 0 0 1 24 0v8" />
      <circle className={styles.accent} cx="60" cy="50" r="3.5" />
      <path className={styles.accent} d="M60 53.5V58" />
    </>
  );
}

/** Loupe sur des lignes de texte : une recherche sans résultat. */
function SearchIllustration(): ReactNode {
  return (
    <>
      <path className={styles.structure} d="M28 26h40M28 36h30M28 46h22" />
      <circle className={styles.accent} cx="72" cy="48" r="15" />
      <path className={styles.accent} d="m83 59 9 9" />
    </>
  );
}

const ILLUSTRATIONS: Record<IllustrationName, () => ReactNode> = {
  library: LibraryIllustration,
  documents: DocumentsIllustration,
  lock: LockIllustration,
  search: SearchIllustration,
};

export function Illustration({ name }: { name: IllustrationName }): ReactNode {
  const Drawing = ILLUSTRATIONS[name];
  return (
    <svg
      className={styles.illustration}
      viewBox="0 0 120 80"
      aria-hidden="true"
      focusable="false"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <Drawing />
    </svg>
  );
}
