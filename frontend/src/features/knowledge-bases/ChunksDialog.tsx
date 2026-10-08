import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { CHUNKS_DEFAULT_PAGE_SIZE, documentChunksQueryOptions } from '@/api/documents';
import { problemMessageOf } from '@/api/problem';
import type { DocumentChunkRead, DocumentChunksPage } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Checkbox } from '@/components/Checkbox';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { Skeleton } from '@/components/Skeleton';
import { useT, type Translate, type TranslationKey } from '@/i18n';

import styles from './ChunksDialog.module.css';

export interface ChunksDialogProps {
  documentId: string;
  filename: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Clés de traduction des méthodes de découpage RAGFlow (`CHUNK_METHODS` côté
 * backend, `app/providers/engine/base.py`) — treize valeurs connues. Une
 * valeur hors de cette table (moteur mis à jour, document indexé hors
 * RAGEngine) retombe sur la chaîne brute plutôt que de la masquer :
 * `methodUnknown` reste réservé à l'absence de valeur (`null`/`undefined`).
 */
const CHUNK_METHOD_LABEL_KEYS: Record<string, TranslationKey> = {
  naive: 'kb.documents.chunks.method.naive',
  table: 'kb.documents.chunks.method.table',
  picture: 'kb.documents.chunks.method.picture',
  qa: 'kb.documents.chunks.method.qa',
  resume: 'kb.documents.chunks.method.resume',
  manual: 'kb.documents.chunks.method.manual',
  book: 'kb.documents.chunks.method.book',
  laws: 'kb.documents.chunks.method.laws',
  presentation: 'kb.documents.chunks.method.presentation',
  paper: 'kb.documents.chunks.method.paper',
  email: 'kb.documents.chunks.method.email',
  one: 'kb.documents.chunks.method.one',
  tag: 'kb.documents.chunks.method.tag',
};

function chunkMethodLabel(t: Translate, method: string | null | undefined): string {
  if (!method) return t('kb.documents.chunks.methodUnknown');
  const key = CHUNK_METHOD_LABEL_KEYS[method];
  return key !== undefined ? t(key) : method;
}

function positionLabel(t: Translate, chunk: DocumentChunkRead): string | undefined {
  if (chunk.page_start == null) return undefined;
  if (chunk.page_end == null || chunk.page_end === chunk.page_start) {
    return t('kb.documents.chunks.pageSingle', { page: chunk.page_start });
  }
  return t('kb.documents.chunks.pageRange', { start: chunk.page_start, end: chunk.page_end });
}

interface ChunkCardProps {
  chunk: DocumentChunkRead;
  /** Rang global du fragment au sein du document — pas relatif à la page affichée. */
  number: number;
  /**
   * Document indexé en parent/enfant : `available === false` y désigne un
   * fragment **parent**, pas un fragment désactivé (ADR-033). Le moteur les
   * marque identiquement — seule la décision de precheck, remontée par l'API,
   * permet de les distinguer.
   */
  usesParentChild: boolean;
}

function ChunkCard({ chunk, number, usesParentChild }: ChunkCardProps): ReactNode {
  const t = useT();
  const page = positionLabel(t, chunk);
  const isParent = usesParentChild && !chunk.available;

  return (
    <li className={styles.chunk}>
      <div className={styles.chunkHeader}>
        <Badge tone="neutral" variant="outline">
          {t('kb.documents.chunks.number', { number })}
        </Badge>
        {page !== undefined && (
          <Badge tone="neutral" variant="outline">
            {page}
          </Badge>
        )}
        {isParent ? (
          <Badge tone="accent" title={t('kb.documents.chunks.parentHint')}>
            {t('kb.documents.chunks.parent')}
          </Badge>
        ) : (
          usesParentChild && (
            <Badge tone="neutral" title={t('kb.documents.chunks.childHint')}>
              {t('kb.documents.chunks.child')}
            </Badge>
          )
        )}
        {!usesParentChild && !chunk.available && (
          <Badge tone="warning">{t('kb.documents.chunks.disabled')}</Badge>
        )}
      </div>
      <p className={styles.chunkContent}>{chunk.content}</p>
      {(chunk.important_keywords ?? []).length > 0 && (
        <p className={styles.chunkMeta}>
          <span className={styles.chunkMetaLabel}>{t('kb.documents.chunks.keywords')}</span>
          {(chunk.important_keywords ?? []).join(', ')}
        </p>
      )}
      {(chunk.questions ?? []).length > 0 && (
        <p className={styles.chunkMeta}>
          <span className={styles.chunkMetaLabel}>{t('kb.documents.chunks.questions')}</span>
          {(chunk.questions ?? []).join(' · ')}
        </p>
      )}
    </li>
  );
}

interface ChunksListProps {
  page: DocumentChunksPage;
  currentPage: number;
  onPageChange: (page: number) => void;
  parentsOnly: boolean;
  onParentsOnlyChange: (value: boolean) => void;
}

interface ChunkPaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  /** Le même bloc est rendu deux fois ; seule sa marge diffère. */
  placement: 'top' | 'bottom';
}

/**
 * Contrôles de pagination des fragments — rendus **deux fois**, en tête et en
 * pied de la liste.
 *
 * Sur un document long, la liste dépasse largement la hauteur de la modale :
 * n'avoir les boutons qu'en bas oblige à parcourir toute la page de fragments
 * pour passer à la suivante, puis à remonter pour la lire. Les deux blocs
 * partagent le même état et les mêmes gestionnaires : il n'y a qu'une seule
 * logique de navigation, dupliquée nulle part.
 */
function ChunkPagination({
  currentPage,
  totalPages,
  onPageChange,
  placement,
}: ChunkPaginationProps): ReactNode {
  const t = useT();

  return (
    <div className={placement === 'top' ? styles.paginationTop : styles.pagination}>
      <Button
        variant="secondary"
        size="sm"
        disabled={currentPage <= 1}
        onClick={() => {
          onPageChange(currentPage - 1);
        }}
      >
        {t('kb.documents.chunks.previous')}
      </Button>
      <span className={styles.paginationLabel}>
        {t('kb.documents.chunks.pagination', { page: currentPage, totalPages })}
      </span>
      <Button
        variant="secondary"
        size="sm"
        disabled={currentPage >= totalPages}
        onClick={() => {
          onPageChange(currentPage + 1);
        }}
      >
        {t('kb.documents.chunks.next')}
      </Button>
    </div>
  );
}

/** Rendu de la page chargée : jamais appelé avec des données absentes. */
function ChunksList({
  page,
  currentPage,
  onPageChange,
  parentsOnly,
  onParentsOnlyChange,
}: ChunksListProps): ReactNode {
  const t = useT();

  // Case à cocher, montée en dehors du early-return ci-dessous : un filtre
  // qui ne retient rien (`page.total === 0`) doit rester réversible depuis
  // l'écran qui le dit, pas seulement depuis un état antérieur.
  const parentsOnlyToggle = page.document.uses_parent_child && (
    <Checkbox
      className={styles.parentsOnlyFilter}
      label={t('kb.documents.chunks.parentsOnly')}
      description={t('kb.documents.chunks.parentsOnlyHint')}
      checked={parentsOnly}
      onChange={(event) => {
        onParentsOnlyChange(event.target.checked);
      }}
    />
  );

  if (page.data.length === 0) {
    // `chunk_count` porte toujours le total du document, filtre ou non
    // (ADR-033) : un document réellement vide (rien à filtrer) se distingue
    // ainsi d'un filtre qui ne retient simplement aucun parent.
    const filteredToNothing = parentsOnly && page.document.chunk_count > 0;
    return (
      <>
        {filteredToNothing && parentsOnlyToggle}
        <EmptyState
          compact
          illustration="documents"
          title={t(
            filteredToNothing
              ? 'kb.documents.chunks.noParentsFoundTitle'
              : 'kb.documents.chunks.emptyTitle',
          )}
          description={t(
            filteredToNothing
              ? 'kb.documents.chunks.noParentsFoundDescription'
              : 'kb.documents.chunks.emptyDescription',
          )}
        />
      </>
    );
  }

  const totalPages = Math.max(1, Math.ceil(page.total / page.page_size));

  return (
    <>
      <p className={styles.summary}>
        {t('kb.documents.chunks.summary', {
          count: page.document.chunk_count,
          method: chunkMethodLabel(t, page.document.chunk_method),
        })}
      </p>
      {/* Sans cette note, une liste de fragments d'une ligne se lit comme un
          découpage cassé : c'est le retour d'usage qui a motivé l'ADR-033. */}
      {page.document.uses_parent_child && (
        <>
          <p className={styles.parentChildNote}>{t('kb.documents.chunks.parentChildNote')}</p>
          {parentsOnlyToggle}
        </>
      )}
      {totalPages > 1 && (
        <ChunkPagination
          currentPage={currentPage}
          totalPages={totalPages}
          onPageChange={onPageChange}
          placement="top"
        />
      )}
      <ul className={styles.list}>
        {page.data.map((chunk, index) => (
          // Rang global au sous-ensemble affiché (filtré ou non) : la
          // pagination est désormais toujours celle de ce qui s'affiche
          // réellement (ADR-033, backend repagine `parents_only` lui-même),
          // jamais celle d'un ensemble plus large que l'écran ne montre pas.
          <ChunkCard
            key={chunk.chunk_id}
            chunk={chunk}
            number={(page.page - 1) * page.page_size + index + 1}
            usesParentChild={page.document.uses_parent_child}
          />
        ))}
      </ul>
      {totalPages > 1 && (
        <ChunkPagination
          currentPage={currentPage}
          totalPages={totalPages}
          onPageChange={onPageChange}
          placement="bottom"
        />
      )}
    </>
  );
}

/**
 * Panneau des fragments indexés d'un document — pagination réelle côté moteur.
 *
 * `page` se réinitialise à 1 à chaque ouverture (`documentId` change ou la
 * modale se rouvre sur le même document) : rouvrir sur une page périmée d'un
 * passage précédent serait plus surprenant qu'utile.
 */
export function ChunksDialog({
  documentId,
  filename,
  open,
  onClose,
}: ChunksDialogProps): ReactNode {
  const t = useT();
  const [page, setPage] = useState(1);
  const [parentsOnly, setParentsOnly] = useState(false);
  // Réinitialise la page à chaque ouverture (ou changement de document) sans
  // passer par un effet : la remontée d'état pendant le rendu est le patron
  // React recommandé pour « ajuster un état quand une prop change », et évite
  // le rendu intermédiaire périmé qu'un `useEffect` produirait ici.
  const [resetKey, setResetKey] = useState<string | null>(null);
  const currentKey = open ? documentId : null;
  if (currentKey !== null && currentKey !== resetKey) {
    setResetKey(currentKey);
    setPage(1);
    setParentsOnly(false);
  }

  const { data, isPending, isError, error } = useQuery(
    documentChunksQueryOptions(documentId, page, CHUNKS_DEFAULT_PAGE_SIZE, open, parentsOnly),
  );

  // La pagination porte sur le sous-ensemble filtré (ADR-033, backend) : une
  // page valide avant la bascule peut ne plus l'être après (un total plus
  // petit). Revenir à la page 1 évite une page hors bornes plutôt qu'une
  // liste vide sans rapport avec ce que l'utilisateur vient de faire.
  const handleParentsOnlyChange = (value: boolean): void => {
    setParentsOnly(value);
    setPage(1);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('kb.documents.chunks.title')}
      description={filename}
      closeLabel={t('form.close')}
      className={styles.dialog}
    >
      {isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(error, t('error.unexpected'))}
        </p>
      ) : isPending ? (
        <ul className={styles.list} aria-busy="true">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className={styles.chunk}>
              <Skeleton width="6rem" height="1.1rem" />
              <Skeleton width="100%" />
              <Skeleton width="80%" />
            </li>
          ))}
        </ul>
      ) : (
        <ChunksList
          page={data}
          currentPage={page}
          onPageChange={setPage}
          parentsOnly={parentsOnly}
          onParentsOnlyChange={handleParentsOnlyChange}
        />
      )}
    </Dialog>
  );
}
