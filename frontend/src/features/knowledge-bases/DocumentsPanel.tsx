import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useMemo, useState, type ReactNode } from 'react';

import {
  deleteDocumentMutationOptions,
  documentContentUrl,
  documentKeys,
  knowledgeBaseDocumentsInfiniteQueryOptions,
  updateDocumentMutationOptions,
} from '@/api/documents';
import { jobQueryOptions } from '@/api/jobs';
import { knowledgeBaseKeys } from '@/api/knowledge-bases';
import { problemMessageOf } from '@/api/problem';
import type { DocumentRead, DocumentStatus } from '@/api/schemas';
import { Badge, type BadgeTone } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { Input } from '@/components/Input';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { JobFailurePanel, QualityWarning } from '@/features/ingestion/JobOutcome';
import { JobTracker } from '@/features/ingestion/JobTracker';
import { useI18n, useT, type TranslationKey } from '@/i18n';
import { formatBytes, formatDate } from '@/lib/format';

import { ChunksDialog } from './ChunksDialog';
import styles from './DocumentsPanel.module.css';

const STATUS_TONES: Record<DocumentStatus, BadgeTone> = {
  pending: 'neutral',
  processing: 'accent',
  awaiting_review: 'warning',
  indexed: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

const STATUS_LABELS: Record<DocumentStatus, TranslationKey> = {
  pending: 'document.status.pending',
  processing: 'document.status.processing',
  awaiting_review: 'document.status.awaitingReview',
  indexed: 'document.status.indexed',
  failed: 'document.status.failed',
  cancelled: 'document.status.cancelled',
};

interface AppliedPrecheckOptions {
  optimizeForTables: boolean;
  heavyOcr: boolean;
  useParentChild: boolean;
  /** Template forcé au panneau « Paramètres avancés » (Phase 15) ; `null` si laissé au routage. */
  chunkMethodOverride: string | null;
}

/**
 * Décision de precheck retenue pour un document, réduite à trois booléens sûrs.
 *
 * `precheck_decision` est typé `{ [key: string]: unknown } | null` côté API
 * généré (`dict[str, Any]` côté backend, `app/domain/schemas/document.py`) —
 * mais en pratique toujours soit `null`, soit un
 * `PrecheckDecision.model_dump()` exact (`app/services/documents.py::
 * record_precheck_decision`, seul écrivain du champ). On resserre le typage
 * ici, à la lecture, plutôt que le contrat d'API lui-même : `null` couvre
 * aussi un document jamais passé par l'aiguillage explicite — indexé avec les
 * réglages par défaut du moteur — même lecture que `_uses_parent_child` côté
 * backend (`app/services/documents.py`).
 */
function appliedPrecheckOptions(
  decision: DocumentRead['precheck_decision'],
): AppliedPrecheckOptions {
  const record = decision ?? {};
  return {
    optimizeForTables: record.optimize_for_tables === true,
    heavyOcr: record.heavy_ocr === true,
    useParentChild: record.use_parent_child === true,
    chunkMethodOverride:
      typeof record.chunk_method_override === 'string' ? record.chunk_method_override : null,
  };
}

/**
 * Rappelle, une fois le document indexé, quelles options de precheck ont été
 * retenues — jusqu'ici seul l'écran d'aiguillage le disait, au moment de
 * cocher, sans trace ensuite. Seules les options actives sont montrées ; un
 * document sans aucune d'elles affiche un état neutre plutôt que rien, pour
 * que l'absence soit aussi une information et pas une case vide ambiguë.
 */
function AppliedOptions({ decision }: { decision: DocumentRead['precheck_decision'] }): ReactNode {
  const t = useT();
  const { optimizeForTables, heavyOcr, useParentChild, chunkMethodOverride } =
    appliedPrecheckOptions(decision);

  if (!optimizeForTables && !heavyOcr && !useParentChild && chunkMethodOverride === null) {
    return (
      <span className={styles.appliedOptions}>
        <Badge tone="neutral" variant="outline">
          {t('precheck.decision.appliedDefault')}
        </Badge>
      </span>
    );
  }

  return (
    <span className={styles.appliedOptions} aria-label={t('precheck.decision.applied')}>
      {optimizeForTables && (
        <Badge tone="accent" variant="outline">
          {t('precheck.decision.appliedTables')}
        </Badge>
      )}
      {heavyOcr && (
        <Badge tone="accent" variant="outline">
          {t('precheck.decision.appliedOcr')}
        </Badge>
      )}
      {useParentChild && (
        <Badge tone="accent" variant="outline">
          {t('precheck.decision.appliedParentChild')}
        </Badge>
      )}
      {chunkMethodOverride !== null && (
        <Badge tone="accent" variant="outline">
          {`${t('precheck.decision.appliedChunkMethod')} · ${chunkMethodOverride}`}
        </Badge>
      )}
    </span>
  );
}

type TrackedDocument = DocumentRead & { current_job_id: string };

/** États pour lesquels un job actif existe encore : le badge seul mentirait. */
function isTracked(document: DocumentRead): document is TrackedDocument {
  return (
    document.current_job_id != null &&
    (document.status === 'pending' ||
      document.status === 'processing' ||
      document.status === 'awaiting_review')
  );
}

/**
 * Le document porte-t-il une issue à expliquer — échec, ou avertissement
 * qualité sur une indexation par ailleurs réussie ?
 *
 * `current_job_id` est la condition d'accès : c'est `GET /v1/jobs/{id}` qui
 * porte le détail (`error`, `warning`), jamais `DocumentRead`. Le champ n'est
 * remis à `null` qu'à la suppression du document (`services/documents.py`), il
 * est donc toujours présent sur un document en échec.
 */
function hasOutcomeToExplain(document: DocumentRead): document is TrackedDocument {
  return (
    document.current_job_id != null &&
    (document.status === 'failed' || document.has_quality_warning)
  );
}

/**
 * Cause d'un échec ou d'un avertissement, relue **après coup**.
 *
 * Défaut corrigé le 2026-08-22 : la cause était bien enregistrée en base et
 * bien affichée — mais uniquement par `JobTracker`, donc uniquement pendant la
 * session de versement. Quitter l'écran ou rafraîchir la rendait définitivement
 * inatteignable, et plusieurs échecs de la campagne de tests manuels ont été
 * remontés comme « sans cause visible » alors que la cause existait
 * (l'analyse interne des tests manuels §3).
 *
 * La requête n'est montée qu'au dépli : un aller-retour par document en échec,
 * seulement quand on le demande, plutôt qu'un champ de plus sur `DocumentRead`
 * — la liste se rend sans jointure sur les jobs, et ce contrat-là ne bouge pas
 * (CONTRIBUTING.md §4). Le job est terminal dans les deux cas d'ouverture, donc
 * `jobQueryOptions` n'y déclenche aucun sondage répété.
 */
function DocumentOutcome({ document }: { document: TrackedDocument }): ReactNode {
  const t = useT();
  const job = useQuery(jobQueryOptions(document.current_job_id));

  if (job.isPending) {
    return (
      <div className={styles.outcomeLoading} aria-busy="true">
        <Skeleton height="0.7rem" />
        <Skeleton width="70%" height="0.7rem" />
      </div>
    );
  }

  if (job.isError) {
    return (
      <p className={styles.error} role="alert">
        {problemMessageOf(job.error, t('error.unexpected'))}
      </p>
    );
  }

  const { error, warning } = job.data;

  if (error == null && warning == null) {
    return <p className={styles.outcomeEmpty}>{t('kb.documents.cause.none')}</p>;
  }

  return (
    <>
      {error != null && (
        <JobFailurePanel
          error={error}
          filename={document.filename}
          title={t('ingest.failedTitle')}
        />
      )}
      {warning != null && <QualityWarning warning={warning} />}
    </>
  );
}

/**
 * Thème d'un document, affiché en badge ; éditable en place pour qui a le
 * droit d'écrire sur la base.
 *
 * Saisi par l'utilisateur, jamais déduit (contrairement aux langues
 * détectées au precheck) — d'où l'édition en texte libre plutôt qu'un
 * catalogue fermé : personne d'autre que l'utilisateur ne connaît ses thèmes
 * à l'avance.
 */
function ThemeControl({
  document,
  canEdit,
  onSaved,
}: {
  document: DocumentRead;
  canEdit: boolean;
  onSaved: () => void;
}): ReactNode {
  const t = useT();
  const { notify } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(document.theme ?? '');
  const fieldId = useId();

  const update = useMutation({
    ...updateDocumentMutationOptions(document.id),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
    onError: (updateError) => {
      notify({
        tone: 'danger',
        title: t('kb.documents.theme.saveErrorTitle'),
        description: problemMessageOf(updateError, t('error.unexpected')),
      });
    },
  });

  if (!editing) {
    return (
      <span className={styles.itemActions}>
        <Badge tone={document.theme != null ? 'accent' : 'neutral'} variant="outline">
          {document.theme ?? t('kb.documents.theme.none')}
        </Badge>
        {canEdit && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setDraft(document.theme ?? '');
              setEditing(true);
            }}
          >
            {t('kb.documents.theme.edit')}
          </Button>
        )}
      </span>
    );
  }

  return (
    <span className={styles.itemActions}>
      <Input
        id={fieldId}
        aria-label={t('kb.documents.theme.edit')}
        value={draft}
        // `MAX_THEME_LENGTH` côté backend : au-delà, `PATCH` répondrait `422`.
        maxLength={200}
        placeholder={t('kb.documents.theme.placeholder')}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        disabled={update.isPending}
      />
      <Button
        variant="primary"
        size="sm"
        loading={update.isPending}
        onClick={() => {
          update.mutate({ theme: draft.trim().length > 0 ? draft.trim() : null });
        }}
      >
        {t('kb.documents.theme.save')}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={update.isPending}
        onClick={() => {
          setEditing(false);
        }}
      >
        {t('kb.documents.theme.cancel')}
      </Button>
    </span>
  );
}

interface DocumentRowProps {
  document: DocumentRead;
  /** Rôle d'écriture sur la base — conditionne la suppression. */
  canEdit: boolean;
  /** Suppression de **ce** document en cours. */
  deleting: boolean;
  onInspectChunks: () => void;
  onRequestDelete: () => void;
  onThemeSaved: () => void;
}

/**
 * Ligne d'un document au repos (job terminal ou absent).
 *
 * Quand le document porte une issue à expliquer, la cause vit dans un dépli
 * sous la ligne plutôt que dans une boîte de dialogue : plusieurs échecs d'un
 * même versement se comparent alors d'un coup d'œil, sans ouvrir-fermer une
 * fenêtre par document.
 */
function DocumentRow({
  document,
  canEdit,
  deleting,
  onInspectChunks,
  onRequestDelete,
  onThemeSaved,
}: DocumentRowProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const regionId = useId();
  const explainable = hasOutcomeToExplain(document);

  return (
    <li className={styles.item}>
      <div className={styles.itemRow}>
        <div className={styles.itemMain}>
          <span className={styles.filename}>{document.filename}</span>
          <span className={styles.meta}>
            <span>{formatBytes(document.size_bytes, locale)}</span>
            {document.page_count != null && (
              <span>{t('kb.documents.pages', { count: document.page_count })}</span>
            )}
            {document.chunk_count != null && (
              <span>{t('kb.documents.chunkCount', { count: document.chunk_count })}</span>
            )}
            {(document.detected_languages ?? []).length > 0 && (
              <span className={styles.languages}>
                {(document.detected_languages ?? []).join(' · ').toUpperCase()}
              </span>
            )}
            <span>{formatDate(document.created_at, locale)}</span>
          </span>
          <ThemeControl document={document} canEdit={canEdit} onSaved={onThemeSaved} />
          {document.status === 'indexed' && (
            <AppliedOptions decision={document.precheck_decision} />
          )}
        </div>

        <div className={styles.itemActions}>
          <a
            href={documentContentUrl(document.id)}
            target="_blank"
            rel="noreferrer"
            className={styles.openLink}
          >
            {t('kb.documents.open')}
          </a>
          <Badge tone={STATUS_TONES[document.status]}>{t(STATUS_LABELS[document.status])}</Badge>
          {document.has_quality_warning && (
            <Badge tone="warning" title={t('kb.documents.qualityWarningHint')}>
              {t('kb.documents.qualityWarning')}
            </Badge>
          )}
          {explainable && (
            <Button
              variant="secondary"
              size="sm"
              aria-expanded={expanded}
              aria-controls={regionId}
              onClick={() => {
                setExpanded((previous) => !previous);
              }}
            >
              {expanded ? t('kb.documents.cause.hide') : t('kb.documents.cause.show')}
            </Button>
          )}
          {document.status === 'indexed' && (
            <Button variant="secondary" size="sm" onClick={onInspectChunks}>
              {t('kb.documents.chunks.action')}
            </Button>
          )}
          {canEdit && (
            <Button variant="danger" size="sm" loading={deleting} onClick={onRequestDelete}>
              {t('kb.documents.delete')}
            </Button>
          )}
        </div>
      </div>

      {explainable && expanded && (
        <section
          id={regionId}
          className={styles.outcome}
          aria-label={t('kb.documents.cause.region', { name: document.filename })}
        >
          <DocumentOutcome document={document} />
        </section>
      )}
    </li>
  );
}

export interface DocumentsPanelProps {
  knowledgeBaseId: string;
  /** Rôle d'écriture sur **cette** base précise — conditionne suppression et suivi. */
  canEdit: boolean;
}

/**
 * Documents d'une base : liste, ouverture des octets d'origine, suppression.
 *
 * Un document dont le job n'a pas atteint un état terminal réutilise
 * `JobTracker` (le même composant que la file de versement) plutôt qu'un badge
 * statique : sans ça, actualiser la page pendant une ingestion en cours perdait
 * la progression par étape et figeait l'écran sur « Traitement ».
 */
export function DocumentsPanel({ knowledgeBaseId, canEdit }: DocumentsPanelProps): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [pendingDeletion, setPendingDeletion] = useState<DocumentRead | null>(null);
  const [inspectedChunks, setInspectedChunks] = useState<DocumentRead | null>(null);
  const [themeFilter, setThemeFilter] = useState<string>('');

  const { data, isPending, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useInfiniteQuery(knowledgeBaseDocumentsInfiniteQueryOptions(knowledgeBaseId));

  const invalidate = async (): Promise<void> => {
    // Préfixe : couvre cette page-unique éventuellement encore en cache
    // ailleurs (`UploadPanel`) autant que cette pagination infinie.
    await queryClient.invalidateQueries({ queryKey: documentKeys.lists(knowledgeBaseId) });
    await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.all });
  };

  const deleteDocument = useMutation({
    ...deleteDocumentMutationOptions(),
    onSuccess: async () => {
      await invalidate();
      notify({ tone: 'success', title: t('kb.documents.deletedTitle') });
    },
    onError: (deleteError) => {
      notify({
        tone: 'danger',
        title: t('kb.documents.deleteErrorTitle'),
        description: problemMessageOf(deleteError, t('error.unexpected')),
      });
    },
    onSettled: () => {
      setPendingDeletion(null);
    },
  });

  const allDocuments = useMemo(() => data?.pages.flatMap((page) => page.data) ?? [], [data]);
  const availableThemes = useMemo(
    () =>
      Array.from(
        new Set(
          allDocuments
            .map((document) => document.theme)
            .filter((theme): theme is string => theme != null),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [allDocuments],
  );
  const documents =
    themeFilter.length > 0
      ? allDocuments.filter((document) => document.theme === themeFilter)
      : allDocuments;

  return (
    <Card>
      <CardHeader
        eyebrow={t('kb.detail.section.documents')}
        title={t('kb.documents.title')}
        description={t('kb.documents.description')}
      />

      {availableThemes.length > 0 && (
        <Select
          aria-label={t('kb.documents.theme.filterLabel')}
          value={themeFilter}
          onChange={(event) => {
            setThemeFilter(event.target.value);
          }}
        >
          <option value="">{t('kb.documents.theme.filterAll')}</option>
          {availableThemes.map((theme) => (
            <option key={theme} value={theme}>
              {theme}
            </option>
          ))}
        </Select>
      )}

      {isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(error, t('error.unexpected'))}
        </p>
      ) : isPending ? (
        <ul className={styles.list} role="list" aria-busy="true">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className={styles.item}>
              <div className={styles.itemMain}>
                <Skeleton width="16rem" />
                <Skeleton width="9rem" height="0.6rem" />
              </div>
              <Skeleton width="5rem" height="1.1rem" />
            </li>
          ))}
        </ul>
      ) : documents.length === 0 ? (
        <EmptyState
          compact
          illustration="documents"
          title={t('kb.documents.emptyTitle')}
          description={t('kb.documents.emptyDescription')}
          // Sans droit d'écriture, il n'y a pas de panneau de versement à
          // rejoindre : l'ancre n'existerait pas, le bouton mènerait nulle part.
          action={
            canEdit ? (
              <a href="#kb-upload-panel" className={styles.emptyAction}>
                {t('kb.documents.emptyAction')}
              </a>
            ) : undefined
          }
        />
      ) : (
        <ul className={styles.list} role="list">
          {documents.map((document) =>
            isTracked(document) ? (
              <li key={document.id} className={styles.itemTracked}>
                <div className={styles.trackedHeader}>
                  <span className={styles.filename}>{document.filename}</span>
                  <a
                    href={documentContentUrl(document.id)}
                    target="_blank"
                    rel="noreferrer"
                    className={styles.openLink}
                  >
                    {t('kb.documents.open')}
                  </a>
                </div>
                <JobTracker
                  documentId={document.id}
                  jobId={document.current_job_id}
                  filename={document.filename}
                  onSettled={() => {
                    void invalidate();
                  }}
                />
              </li>
            ) : (
              <DocumentRow
                key={document.id}
                document={document}
                canEdit={canEdit}
                deleting={deleteDocument.isPending && pendingDeletion?.id === document.id}
                onInspectChunks={() => {
                  setInspectedChunks(document);
                }}
                onRequestDelete={() => {
                  setPendingDeletion(document);
                }}
                onThemeSaved={() => {
                  void invalidate();
                }}
              />
            ),
          )}
        </ul>
      )}

      {hasNextPage && (
        <Button
          variant="secondary"
          loading={isFetchingNextPage}
          onClick={() => {
            void fetchNextPage();
          }}
        >
          {t('kb.documents.loadMore')}
        </Button>
      )}

      <Dialog
        open={pendingDeletion !== null}
        onClose={() => {
          setPendingDeletion(null);
        }}
        title={t('kb.documents.deleteConfirmTitle')}
        description={
          pendingDeletion === null
            ? undefined
            : t('kb.documents.deleteConfirmDescription', { name: pendingDeletion.filename })
        }
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              disabled={deleteDocument.isPending}
              onClick={() => {
                setPendingDeletion(null);
              }}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={deleteDocument.isPending}
              onClick={() => {
                if (pendingDeletion !== null) deleteDocument.mutate(pendingDeletion.id);
              }}
            >
              {t('kb.documents.deleteConfirm')}
            </Button>
          </>
        }
      />

      <ChunksDialog
        documentId={inspectedChunks?.id ?? ''}
        filename={inspectedChunks?.filename ?? ''}
        open={inspectedChunks !== null}
        onClose={() => {
          setInspectedChunks(null);
        }}
      />
    </Card>
  );
}
