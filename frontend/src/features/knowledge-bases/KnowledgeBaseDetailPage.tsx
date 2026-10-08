import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';

import { isApiError } from '@/api/client';
import {
  deleteKnowledgeBaseMutationOptions,
  knowledgeBaseKeys,
  knowledgeBaseQueryOptions,
  knowledgeBasesQueryOptions,
  updateKnowledgeBaseMutationOptions,
} from '@/api/knowledge-bases';
import { llmKeysQueryOptions } from '@/api/llm-keys';
import { problemMessageOf } from '@/api/problem';
import type { KnowledgeBaseRead, KnowledgeBaseUpdate, LlmVendor } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useSession } from '@/features/auth';
import { UploadPanel } from '@/features/ingestion';
import { useI18n, useT } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import { pluginSlots } from '@/plugins';

import { DocumentsPanel } from './DocumentsPanel';
import { EvaluationsPanel } from './EvaluationsPanel';
import { KnowledgeBaseForm, type KnowledgeBaseFormValues } from './KnowledgeBaseForm';
import { MembersPanel } from './MembersPanel';
import { useProviderLabel } from './useProviderLabel';
import { VisibilityBadge } from './VisibilityBadge';
import styles from './KnowledgeBasePages.module.css';

/** Détail d'une base : métadonnées, édition, accès, documents. */
export function KnowledgeBaseDetailPage(): ReactNode {
  const { kbId } = useParams<{ kbId: string }>();

  if (kbId === undefined) return <Navigate to="/knowledge-bases" replace />;

  // `key` : changer de base remonte l'écran, donc réinitialise l'édition en
  // cours et les états locaux — jamais de formulaire hérité d'une autre base.
  return <KnowledgeBaseDetail kbId={kbId} key={kbId} />;
}

function KnowledgeBaseDetail({ kbId }: { kbId: string }): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const { isAuthenticated } = useSession();
  const { data: base, isPending, isError, error } = useQuery(knowledgeBaseQueryOptions(kbId));
  // Navigation précédent/suivant : même ordre que `KnowledgeBaseListPage`
  // (`created_at.desc(), id.desc()`, appliqué côté serveur,
  // `backend/app/domain/pagination.py::apply_cursor_pagination`) — même
  // requête, même cache TanStack Query, pas un second tri inventé ici. Une
  // seule page (taille par défaut de l'API) : au-delà, les boutons se
  // désactivent plutôt que de tenter de paginer plus loin — limite assumée
  // pour cette version, à revoir si la navigation doit un jour couvrir un
  // historique complet. Appelé avant tout retour anticipé (règle des Hooks).
  const { data: siblingsPage } = useQuery(knowledgeBasesQueryOptions());
  // Réservé aux comptes réels (`llm-keys.ts`) : un compte éphémère — jamais autorisé à
  // lancer une évaluation, `EvaluationService._authorize_spending` — ou un
  // visiteur anonyme n'a jamais de clé personnelle, inutile d'interroger
  // l'endpoint. `EvaluationsPanel` reçoit alors un ensemble vide, qui impose
  // honnêtement l'avertissement « choix ignoré » pour tout fournisseur.
  const llmKeys = useQuery({ ...llmKeysQueryOptions(), enabled: isAuthenticated });
  const personalKeyVendors = new Set<LlmVendor>(llmKeys.data?.map((key) => key.provider) ?? []);

  const update = useMutation({
    ...updateKnowledgeBaseMutationOptions(kbId),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.all });
      setEditing(false);
      notify({
        tone: 'success',
        title: t('kb.edit.successTitle'),
        description: t('kb.edit.successDescription', { name: updated.name }),
      });
    },
  });

  const remove = useMutation({
    ...deleteKnowledgeBaseMutationOptions(kbId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.all });
      setConfirmingDelete(false);
      notify({ tone: 'success', title: t('kb.delete.successTitle') });
      void navigate('/knowledge-bases', { replace: true });
    },
    onError: (deleteError) => {
      notify({
        tone: 'danger',
        title: t('kb.delete.errorTitle'),
        description: problemMessageOf(deleteError, t('error.unexpected')),
      });
    },
  });

  if (isPending) return <DetailSkeleton />;

  if (isError) {
    const notFound = isApiError(error) && error.status === 404;
    return (
      <div className={styles.page}>
        <EmptyState
          illustration={notFound ? 'search' : 'lock'}
          title={notFound ? t('kb.detail.notFoundTitle') : t('kb.detail.errorTitle')}
          description={
            notFound
              ? t('kb.detail.notFoundDescription')
              : problemMessageOf(error, t('error.unexpected'))
          }
          action={
            <Link to="/knowledge-bases" className={styles.primaryLink}>
              {t('kb.backToList')}
            </Link>
          }
        />
      </div>
    );
  }

  const siblings = siblingsPage?.data ?? [];
  const currentIndex = siblings.findIndex((sibling) => sibling.id === base.id);
  const previousBase = currentIndex > 0 ? siblings[currentIndex - 1] : undefined;
  const nextBase =
    currentIndex >= 0 && currentIndex < siblings.length - 1
      ? siblings[currentIndex + 1]
      : undefined;

  // `principal_role` porte le rôle réel du principal sur **cette** base
  // (défaut 2, plan interne) : contrairement à un test sur `owner_id`, il vaut
  // aussi `owner` pour un administrateur plateforme, qui n'est pourtant pas le
  // propriétaire enregistré — c'est exactement lui qui doit voir ces actions.
  const isOwner = base.principal_role === 'owner';
  // Éditer le contenu (versement, évaluation) demande `editor` au minimum sur
  // cette base précise — pas seulement un compte authentifié quelconque
  // (`canWrite` de session), qui laissait un `viewer` voir un bouton voué à un
  // `403` (plan interne, note de clôture Phase 8).
  const canEditBase = base.principal_role === 'editor' || base.principal_role === 'owner';

  const onSubmitEdit = (values: KnowledgeBaseFormValues): void => {
    const payload: KnowledgeBaseUpdate = {
      name: values.name,
      slug: values.slug,
      description: values.description.length > 0 ? values.description : null,
      visibility: values.visibility,
      reranker_model_id: values.reranker_model_id,
      provider_config: values.provider_config,
      observability_enabled: values.observability_enabled,
      evaluation_enabled: values.evaluation_enabled,
    };
    update.mutate(payload);
  };

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('kb.eyebrow')}
        title={base.name}
        description={base.description ?? undefined}
        breadcrumb={
          <div className={styles.breadcrumbRow}>
            <Link to="/knowledge-bases" className={styles.backLink}>
              <svg
                viewBox="0 0 16 16"
                aria-hidden="true"
                focusable="false"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9.5 4 5.5 8l4 4" />
              </svg>
              {t('kb.backToList')}
            </Link>
            <KnowledgeBaseSiblingNav previous={previousBase} next={nextBase} />
          </div>
        }
        meta={
          <>
            <VisibilityBadge visibility={base.visibility} />
            <code className={styles.slug}>{base.slug}</code>
            {base.observability_enabled && (
              <Badge tone="accent">{t('kb.form.observability')}</Badge>
            )}
            {base.evaluation_enabled && <Badge tone="accent">{t('kb.form.evaluation')}</Badge>}
          </>
        }
        actions={
          editing ? undefined : (
            <>
              {/* Interroger la base est l'action la plus fréquente : atteindre cet
                  écran prouve déjà un accès en lecture (membre, ou visiteur d'une
                  base `public`, ADR-005) — le backend applique le même seuil pour
                  interroger que pour lire, inutile de re-tester `canWrite` ici
                  (même défaut que Phase 8 pour `canEditBase` : une permission de
                  session ne doit pas se substituer à la permission réelle sur
                  cette base). */}
              <Link to={`/knowledge-bases/${base.id}/playground`} className={styles.primaryLink}>
                {t('playground.open')}
              </Link>
              {isOwner && (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setEditing(true);
                    }}
                  >
                    {t('kb.detail.edit')}
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => {
                      setConfirmingDelete(true);
                    }}
                  >
                    {t('kb.detail.delete')}
                  </Button>
                </>
              )}
            </>
          )
        }
      />

      {editing ? (
        <KnowledgeBaseForm
          mode="edit"
          initial={base}
          submitting={update.isPending}
          error={update.error}
          submitLabel={t('kb.edit.submit')}
          onSubmit={onSubmitEdit}
          onCancel={() => {
            setEditing(false);
            update.reset();
          }}
        />
      ) : (
        <div className={styles.panels}>
          {/* Ordre pensé pour un non-développeur (2026-09-06) : ce qu'on vient
              faire et vérifier d'abord (Versement, Corpus), ce qu'on en fait
              ensuite (sections des greffons), puis les détails techniques et
              administratifs qu'on consulte moins souvent (Configuration,
              Accès, Qualité). */}
          {/* Ancre ciblée par l'état vide de `DocumentsPanel` (« Déposer un
              document ») — sur un long écran mobile, le panneau de versement
              n'est pas forcément visible sans faire défiler jusqu'ici. */}
          {canEditBase && (
            <div id="kb-upload-panel" className={styles.uploadAnchor}>
              <UploadPanel knowledgeBaseId={base.id} />
            </div>
          )}
          <DocumentsPanel knowledgeBaseId={base.id} canEdit={canEditBase} />
          {/* Sections des greffons (ADR-069), entre ce que la base contient et
              sa configuration : chacune décide elle-même pour qui elle s'affiche. */}
          {pluginSlots('KnowledgeBaseSection').map((Section, index) => (
            <Section key={index} base={base} isOwner={isOwner} />
          ))}
          <ConfigurationCard base={base} locale={locale} />
          {/* Accès sous Configuration : qui peut voir/modifier la base
              intéresse moins, au premier regard, que ce que la base contient
              ou fait déjà. */}
          {isOwner && <MembersPanel base={base} />}
          {/* L'évaluation vient en dernier : elle mesure ce qui a été
              indexé, elle ne se lit pas avant de savoir ce qu'il contient. */}
          <EvaluationsPanel
            base={base}
            canWrite={canEditBase}
            personalKeyVendors={personalKeyVendors}
            onRequestEdit={() => {
              setEditing(true);
            }}
          />
        </div>
      )}

      <Dialog
        open={confirmingDelete}
        onClose={() => {
          setConfirmingDelete(false);
        }}
        title={t('kb.delete.title')}
        description={t('kb.delete.description', { name: base.name })}
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirmingDelete(false);
              }}
              disabled={remove.isPending}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              {t('kb.delete.confirm')}
            </Button>
          </>
        }
      >
        <p>{t('kb.delete.warning', { count: base.document_count })}</p>
      </Dialog>
    </div>
  );
}

/**
 * Navigation précédent/suivant vers les bases adjacentes (même ordre que la
 * liste). `undefined` = bout de la page chargée — rendu comme un repère
 * inerte (`aria-disabled`, `tabIndex={-1}`) plutôt qu'omis : un lecteur
 * d'écran doit savoir que l'action existe mais n'est pas franchissable ici,
 * pas la découvrir absente.
 */
export function KnowledgeBaseSiblingNav({
  previous,
  next,
}: {
  previous: KnowledgeBaseRead | undefined;
  next: KnowledgeBaseRead | undefined;
}): ReactNode {
  const t = useT();

  return (
    <nav className={styles.siblingNav} aria-label={t('kb.eyebrow')}>
      {previous !== undefined ? (
        <Link
          to={`/knowledge-bases/${previous.id}`}
          className={styles.siblingLink}
          aria-label={t('kb.nav.previousTo', { name: previous.name })}
          title={previous.name}
        >
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9.5 4 5.5 8l4 4" />
          </svg>
          {t('kb.nav.previous')}
        </Link>
      ) : (
        <span
          className={styles.siblingLinkDisabled}
          aria-disabled="true"
          tabIndex={-1}
          title={t('kb.nav.boundaryReached')}
        >
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9.5 4 5.5 8l4 4" />
          </svg>
          {t('kb.nav.previous')}
        </span>
      )}

      {next !== undefined ? (
        <Link
          to={`/knowledge-bases/${next.id}`}
          className={styles.siblingLink}
          aria-label={t('kb.nav.nextTo', { name: next.name })}
          title={next.name}
        >
          {t('kb.nav.next')}
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6.5 4 4 4-4 4" />
          </svg>
        </Link>
      ) : (
        <span
          className={styles.siblingLinkDisabled}
          aria-disabled="true"
          tabIndex={-1}
          title={t('kb.nav.boundaryReached')}
        >
          {t('kb.nav.next')}
          <svg
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6.5 4 4 4-4 4" />
          </svg>
        </span>
      )}
    </nav>
  );
}

/** Fiche de configuration : ce que la base est, en un coup d'œil. */
function ConfigurationCard({
  base,
  locale,
}: {
  base: KnowledgeBaseRead;
  locale: string;
}): ReactNode {
  const t = useT();
  const providerLabel = useProviderLabel();

  return (
    <Card>
      <CardHeader
        eyebrow={t('kb.detail.section.configuration')}
        title={t('kb.detail.configurationTitle')}
        description={t('kb.detail.configurationDescription')}
      />
      <dl className={styles.definitions}>
        <div className={styles.definition}>
          <dt>{t('kb.model.embeddingFull')}</dt>
          <dd>
            {providerLabel(base.embedding_model_id)}
            <code className={styles.identifier}>{base.embedding_model_id}</code>
          </dd>
        </div>
        <div className={styles.definition}>
          <dt>{t('kb.model.rerankerFull')}</dt>
          <dd>
            {base.reranker_model_id == null ? (
              t('kb.model.none')
            ) : (
              <>
                {providerLabel(base.reranker_model_id)}
                <code className={styles.identifier}>{base.reranker_model_id}</code>
              </>
            )}
          </dd>
        </div>
        <div className={styles.definition}>
          <dt>{t('kb.column.documents')}</dt>
          <dd>{base.document_count}</dd>
        </div>
        <div className={styles.definition}>
          <dt>{t('kb.detail.created')}</dt>
          <dd>{formatDateTime(base.created_at, locale)}</dd>
        </div>
        <div className={styles.definition}>
          <dt>{t('kb.detail.updated')}</dt>
          <dd>{formatDateTime(base.updated_at, locale)}</dd>
        </div>
        <div className={styles.definition}>
          <dt>{t('kb.detail.engineDataset')}</dt>
          <dd>
            {base.ragflow_dataset_external_id == null ? (
              <span className={styles.pendingValue}>{t('kb.detail.engineDatasetPending')}</span>
            ) : (
              <code className={styles.identifier}>{base.ragflow_dataset_external_id}</code>
            )}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

function DetailSkeleton(): ReactNode {
  return (
    <div className={styles.page} aria-busy="true">
      <div className={styles.headerSkeleton}>
        <Skeleton width="6rem" height="0.6rem" />
        <Skeleton width="18rem" height="1.75rem" />
        <Skeleton width="min(32rem, 90%)" height="0.75rem" />
      </div>
      <Card>
        <Skeleton width="10rem" />
        <div className={styles.skeletonGrid}>
          <Skeleton height="2.5rem" />
          <Skeleton height="2.5rem" />
          <Skeleton height="2.5rem" />
          <Skeleton height="2.5rem" />
        </div>
      </Card>
    </div>
  );
}
