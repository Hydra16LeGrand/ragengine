import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { knowledgeBasesQueryOptions } from '@/api/knowledge-bases';
import { problemMessageOf } from '@/api/problem';
import type { KnowledgeBaseRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Card } from '@/components/Card';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';
import { Skeleton } from '@/components/Skeleton';
import { useSession } from '@/features/auth';
import { useI18n, useT } from '@/i18n';
import { formatDate } from '@/lib/format';
import { pluginSlots } from '@/plugins';

import { useProviderLabel } from './useProviderLabel';
import { VisibilityBadge } from './VisibilityBadge';
import styles from './KnowledgeBaseListPage.module.css';

const SKELETON_ROWS = 4;

/**
 * Gardes de création des greffons (`KnowledgeBaseCreationGate`), imbriquées :
 * sans greffon, l'action est rendue telle quelle.
 */
function CreationGate({
  children,
  renderBlocked,
}: {
  children: ReactNode;
  renderBlocked: (reason: string) => ReactNode;
}): ReactNode {
  return pluginSlots('KnowledgeBaseCreationGate').reduceRight<ReactNode>(
    (inner, Gate) => <Gate renderBlocked={renderBlocked}>{inner}</Gate>,
    children,
  );
}

/**
 * Tableau de bord des bases de connaissances.
 *
 * Direction artistique : des **lignes alignées**, pas une grille de cartes.
 * Une base est un enregistrement dont on compare les attributs (visibilité,
 * modèles, volume, fraîcheur) ; en colonnes, l'œil balaie un attribut d'une
 * base à l'autre d'un seul mouvement, ce qu'une mosaïque de cartes interdit.
 */
export function KnowledgeBaseListPage(): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const { isAuthenticated, canWrite, userId, isLoading: sessionLoading } = useSession();

  const { data, isPending, error, isError } = useQuery(knowledgeBasesQueryOptions());
  const bases = data?.data ?? [];

  // Un greffon peut refuser la création avant même l'ouverture du formulaire
  // (`KnowledgeBaseCreationGate`, ADR-069) : un compte à plafond atteint ne
  // doit pas remplir une base entière pour découvrir le refus à la soumission.
  const createAction = canWrite ? (
    <CreationGate
      renderBlocked={(reason) => (
        <span className={styles.quotaReached} title={reason}>
          {reason}
        </span>
      )}
    >
      <Link to="/knowledge-bases/new" className={styles.primaryLink}>
        <svg
          viewBox="0 0 16 16"
          aria-hidden="true"
          focusable="false"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8 3.5v9M3.5 8h9" />
        </svg>
        {t('kb.list.create')}
      </Link>
    </CreationGate>
  ) : undefined;

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('kb.eyebrow')}
        title={isAuthenticated ? t('kb.list.title') : t('kb.list.titleAnonymous')}
        description={isAuthenticated ? t('kb.list.description') : t('kb.list.descriptionAnonymous')}
        actions={createAction}
      />

      {!sessionLoading && !isAuthenticated && (
        <Card tone="accent" className={styles.notice}>
          <div className={styles.noticeText}>
            <p className={styles.noticeTitle}>{t('kb.list.anonymousTitle')}</p>
            <p className={styles.noticeDescription}>{t('kb.list.anonymousDescription')}</p>
          </div>
          <Link to="/login" className={styles.noticeAction}>
            {t('nav.login')}
          </Link>
        </Card>
      )}

      {isError && (
        <Card tone="danger" role="alert">
          <p className={styles.errorTitle}>{t('kb.list.errorTitle')}</p>
          <p className={styles.errorDetail}>{problemMessageOf(error, t('error.unexpected'))}</p>
        </Card>
      )}

      {!isError && (
        <Card flush className={styles.table} aria-busy={isPending}>
          <div className={styles.columns} aria-hidden="true">
            <span>{t('kb.column.base')}</span>
            <span>{t('kb.column.visibility')}</span>
            <span>{t('kb.column.models')}</span>
            <span className={styles.numeric}>{t('kb.column.documents')}</span>
            <span className={styles.numeric}>{t('kb.column.updated')}</span>
          </div>

          {isPending ? (
            <LoadingRows />
          ) : bases.length === 0 ? (
            <EmptyState
              illustration="library"
              title={canWrite ? t('kb.empty.title') : t('kb.empty.anonymousTitle')}
              description={
                canWrite ? t('kb.empty.description') : t('kb.empty.anonymousDescription')
              }
              action={
                canWrite ? (
                  <CreationGate
                    renderBlocked={(reason) => (
                      <span className={styles.quotaReached}>{reason}</span>
                    )}
                  >
                    <Link to="/knowledge-bases/new" className={styles.primaryLink}>
                      {t('kb.list.create')}
                    </Link>
                  </CreationGate>
                ) : (
                  <Link to="/login" className={styles.primaryLink}>
                    {t('nav.login')}
                  </Link>
                )
              }
            >
              {/* Le parcours en trois étapes n'a de sens que pour qui peut
                  réellement le suivre : un visiteur anonyme, ou un compte que
                  la garde de création refuse, voit déjà l'explication qu'il lui
                  faut. Le dupliquer ici serait du bruit, pas une aide. */}
              {canWrite && (
                <CreationGate renderBlocked={() => null}>
                  <ol className={styles.onboardingSteps}>
                    <li className={styles.onboardingStep}>
                      <span className={styles.onboardingStepNumber}>1</span>
                      <span className={styles.onboardingStepText}>{t('kb.empty.step1')}</span>
                    </li>
                    <li className={styles.onboardingStep}>
                      <span className={styles.onboardingStepNumber}>2</span>
                      <span className={styles.onboardingStepText}>{t('kb.empty.step2')}</span>
                    </li>
                    <li className={styles.onboardingStep}>
                      <span className={styles.onboardingStepNumber}>3</span>
                      <span className={styles.onboardingStepText}>{t('kb.empty.step3')}</span>
                    </li>
                  </ol>
                </CreationGate>
              )}
            </EmptyState>
          ) : (
            <ul className={styles.rows} role="list">
              {bases.map((base) => (
                <KnowledgeBaseRow key={base.id} base={base} locale={locale} userId={userId} />
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function KnowledgeBaseRow({
  base,
  locale,
  userId,
}: {
  base: KnowledgeBaseRead;
  locale: string;
  userId: string | null;
}): ReactNode {
  const t = useT();
  const providerLabel = useProviderLabel();
  // Deux bases de propriétaires différents peuvent porter le même nom et le
  // même slug (seul (owner_id, slug) est unique, jamais globalement) : ce tag
  // est le seul indice qui les distingue dans une liste partagée.
  const isShared = userId !== null && base.owner_id !== userId;

  return (
    <li className={styles.row}>
      <Link to={`/knowledge-bases/${base.id}`} className={styles.rowLink}>
        <span className={styles.identity}>
          <span className={styles.nameRow}>
            <span className={styles.name}>{base.name}</span>
            {isShared && <Badge tone="neutral">{t('kb.list.shared')}</Badge>}
          </span>
          <span className={styles.slug}>{base.slug}</span>
          {base.description != null && base.description.length > 0 && (
            <span className={styles.description}>{base.description}</span>
          )}
        </span>

        <span className={styles.cell}>
          <VisibilityBadge visibility={base.visibility} />
        </span>

        <span className={styles.models}>
          <span className={styles.modelLine}>
            <span className={styles.modelRole}>{t('kb.model.embedding')}</span>
            <span className={styles.modelName}>{providerLabel(base.embedding_model_id)}</span>
          </span>
          <span className={styles.modelLine}>
            <span className={styles.modelRole}>{t('kb.model.reranker')}</span>
            <span className={styles.modelName}>
              {base.reranker_model_id == null
                ? t('kb.model.none')
                : providerLabel(base.reranker_model_id)}
            </span>
          </span>
        </span>

        <span className={styles.count}>
          <span className={styles.countValue}>{base.document_count}</span>
          <span className={styles.countLabel}>{t('kb.column.documents')}</span>
        </span>

        <span className={styles.updated}>{formatDate(base.updated_at, locale)}</span>
      </Link>
    </li>
  );
}

/** Squelettes calqués sur la grille réelle : aucun saut de mise en page. */
function LoadingRows(): ReactNode {
  return (
    <ul className={styles.rows} role="list">
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <li key={index} className={styles.row}>
          <div className={styles.rowLink}>
            <span className={styles.identity}>
              <Skeleton width="14rem" />
              <Skeleton width="8rem" height="0.6rem" />
              <Skeleton width="min(24rem, 80%)" height="0.6rem" />
            </span>
            <span className={styles.cell}>
              <Skeleton width="4.5rem" height="1.1rem" />
            </span>
            <span className={styles.models}>
              <Skeleton width="7rem" height="0.6rem" />
              <Skeleton width="6rem" height="0.6rem" />
            </span>
            <span className={styles.count}>
              <Skeleton width="2rem" />
            </span>
            <span className={styles.updated}>
              <Skeleton width="5rem" height="0.6rem" />
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
