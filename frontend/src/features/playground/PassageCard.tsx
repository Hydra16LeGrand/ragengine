import { useId, useState, type ReactNode } from 'react';

import type { RetrievalResult } from '@/api/schemas';
import { useI18n, useT } from '@/i18n';
import { cx } from '@/lib/cx';
import { formatScore } from '@/lib/format';

import styles from './Playground.module.css';

export interface PassageCardProps {
  /** Rang 1-based : c'est le numéro que citent les réponses générées. */
  rank: number;
  result: RetrievalResult;
  /** `compact` : version citée dans une conversation, texte resserré. */
  variant?: 'full' | 'compact';
  /** Mis en évidence parce qu'une citation le désigne. */
  highlighted?: boolean;
  /** Ancre DOM, pour qu'une citation puisse amener l'écran jusqu'ici. */
  anchorId?: string;
}

/**
 * Un passage retourné par le moteur, avec la raison de sa présence.
 *
 * Les trois scores sont montrés ensemble et à la même échelle : le score
 * combiné seul ne dit pas *pourquoi* le passage est remonté. Voir qu'un extrait
 * doit sa place au vectoriel (proximité de sens) et pas aux termes (mots exacts
 * absents) est exactement ce qui permet de juger si le corpus répond bien — un
 * simple « 0.87 » n'apprend rien.
 */
export function PassageCard({
  rank,
  result,
  variant = 'full',
  highlighted = false,
  anchorId,
}: PassageCardProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  // Le repli ne concerne que la variante citée : en mode Recherche le passage
  // est déjà rendu en entier, il n'y a rien à déplier.
  const [expanded, setExpanded] = useState(false);
  const collapsible = variant === 'compact';
  const open = collapsible && expanded;
  const textId = useId();

  return (
    <article
      {...(anchorId === undefined ? {} : { id: anchorId })}
      className={cx(
        styles.passage,
        variant === 'compact' && styles.passageCompact,
        highlighted && styles.passageHighlighted,
      )}
    >
      <header className={styles.passageHead}>
        <span className={styles.rank} aria-label={t('playground.result.rank', { rank })}>
          {rank}
        </span>
        <span className={styles.passageSource} title={result.filename}>
          {result.filename}
        </span>
        <PageMark result={result} />
        <span className={styles.passageScore}>{formatScore(result.similarity, locale)}</span>
      </header>

      <p id={textId} className={cx(styles.passageText, open && styles.passageTextExpanded)}>
        {result.content}
      </p>

      {collapsible && (
        // Le bouton suit le texte qu'il régit : `aria-controls` est ce qui
        // relie les deux pour un lecteur d'écran, l'ordre du DOM ne suffisant
        // pas à désigner la cible d'un repli placé après elle.
        <button
          type="button"
          className={styles.passageToggle}
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => {
            setExpanded(!open);
          }}
        >
          {open ? t('playground.result.collapse') : t('playground.result.expand')}
        </button>
      )}

      {(variant === 'full' || open) && (
        <dl className={styles.scores}>
          <Score label={t('playground.score.combined')} value={result.similarity} tone="accent" />
          <Score label={t('playground.score.vector')} value={result.vector_similarity} />
          <Score label={t('playground.score.term')} value={result.term_similarity} />
        </dl>
      )}
    </article>
  );
}

/**
 * Position du passage dans son document — rien quand le moteur ne la connaît pas.
 *
 * L'absence est un cas courant et normal, pas une anomalie : seuls les formats
 * paginés (PDF) donnent une position exploitable. Les documents Word ou texte
 * en renvoient une aussi, mais c'est le **rang** du fragment, écarté côté
 * serveur (cf. `_page_range_of` dans l'adaptateur RAGFlow) — d'où un champ
 * absent plutôt qu'un « p. 1 » inventé. Ne rien afficher est ici la seule
 * option honnête : un numéro faux sur une citation ruinerait la vérifiabilité,
 * qui est tout l'intérêt de citer.
 */
function PageMark({ result }: { result: RetrievalResult }): ReactNode {
  const t = useT();
  const start = result.page_start;
  const end = result.page_end;
  if (start === null || start === undefined) return null;

  const single = end === null || end === undefined || end === start;
  return (
    <span
      className={styles.passagePage}
      aria-label={
        single
          ? t('playground.result.pageLabel', { page: start })
          : t('playground.result.pageRangeLabel', { start, end })
      }
    >
      {single
        ? t('playground.result.page', { page: start })
        : t('playground.result.pageRange', { start, end })}
    </span>
  );
}

function Score({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'accent';
}): ReactNode {
  const { locale } = useI18n();
  // Le moteur rend des scores dans [0, 1] ; on borne quand même l'affichage,
  // pour qu'un score hors échelle déborde du gabarit plutôt que de la carte.
  const ratio = Math.min(1, Math.max(0, value));

  return (
    <div className={styles.score}>
      <dt className={styles.scoreLabel}>{label}</dt>
      <dd className={styles.scoreValue}>
        <span className={styles.scoreTrack} aria-hidden="true">
          <span
            className={cx(styles.scoreFill, tone === 'accent' && styles.scoreFillAccent)}
            style={{ inlineSize: `${String(ratio * 100)}%` }}
          />
        </span>
        <span className={styles.scoreNumber}>{formatScore(value, locale)}</span>
      </dd>
    </div>
  );
}
