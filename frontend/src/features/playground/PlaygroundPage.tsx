import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';

import { isApiError } from '@/api/client';
import { indexedDocumentsSelectorQueryOptions } from '@/api/documents';
import { knowledgeBaseQueryOptions } from '@/api/knowledge-bases';
import { llmKeysQueryOptions } from '@/api/llm-keys';
import { problemMessageOf } from '@/api/problem';
import { providersQueryOptions } from '@/api/providers';
import type { LlmVendor } from '@/api/schemas';
import { Card } from '@/components/Card';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';
import { SegmentedControl } from '@/components/SegmentedControl';
import { Skeleton } from '@/components/Skeleton';
import { useSession } from '@/features/auth';
import { useT } from '@/i18n';

import { ChatPanel } from './ChatPanel';
import { RetrievalSettings } from './RetrievalSettings';
import { DEFAULT_RETRIEVAL_SETTINGS, type RetrievalSettingsValue } from './settings';
import { SearchPanel } from './SearchPanel';
import styles from './Playground.module.css';

type PlaygroundMode = 'search' | 'chat';

/**
 * Playground d'une base (Schéma B) — écran distinct de la configuration.
 *
 * Interroger un corpus et le configurer sont deux gestes qui n'ont ni le même
 * rythme ni le même public : le Playground s'utilise en boucle courte, la
 * configuration une fois pour toutes. Les mêler ferait de chaque essai une
 * modification potentielle de la base (décision déjà actée en Phase 6, tenue
 * ici).
 */
export function PlaygroundPage(): ReactNode {
  const { kbId } = useParams<{ kbId: string }>();
  if (kbId === undefined) return <Navigate to="/knowledge-bases" replace />;
  return <Playground kbId={kbId} key={kbId} />;
}

function Playground({ kbId }: { kbId: string }): ReactNode {
  const t = useT();
  const { isAuthenticated } = useSession();
  const [mode, setMode] = useState<PlaygroundMode>('search');
  const [settings, setSettings] = useState<RetrievalSettingsValue>(DEFAULT_RETRIEVAL_SETTINGS);

  const base = useQuery(knowledgeBaseQueryOptions(kbId));
  const indexedDocuments = useQuery(indexedDocumentsSelectorQueryOptions(kbId));
  const providers = useQuery(providersQueryOptions());
  // Réservé aux comptes réels (`llm-keys.ts`) : un compte éphémère ou un visiteur
  // anonyme n'a jamais de clé personnelle, inutile d'interroger l'endpoint —
  // `ChatPanel` reçoit alors un ensemble vide, qui impose honnêtement
  // l'avertissement « choix ignoré » pour tout fournisseur.
  const llmKeys = useQuery({ ...llmKeysQueryOptions(), enabled: isAuthenticated });
  const personalKeyVendors = new Set<LlmVendor>(llmKeys.data?.map((key) => key.provider) ?? []);

  if (base.isPending) return <PlaygroundSkeleton />;

  if (base.isError) {
    const notFound = isApiError(base.error) && base.error.status === 404;
    return (
      <div className={styles.page}>
        <EmptyState
          illustration={notFound ? 'search' : 'lock'}
          title={notFound ? t('kb.detail.notFoundTitle') : t('kb.detail.errorTitle')}
          description={
            notFound
              ? t('kb.detail.notFoundDescription')
              : problemMessageOf(base.error, t('error.unexpected'))
          }
          action={
            <Link to="/knowledge-bases" className={styles.backAction}>
              {t('kb.backToList')}
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('playground.eyebrow')}
        title={t('playground.title', { name: base.data.name })}
        description={t('playground.description')}
        breadcrumb={
          <Link to={`/knowledge-bases/${kbId}`} className={styles.backLink}>
            <svg
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9.5 4 5.5 8l4 4" />
            </svg>
            {t('playground.backToBase')}
          </Link>
        }
        actions={
          <SegmentedControl<PlaygroundMode>
            label={t('playground.mode.label')}
            value={mode}
            onChange={setMode}
            options={[
              {
                value: 'search',
                label: t('playground.mode.search'),
                icon: (
                  <svg
                    viewBox="0 0 16 16"
                    aria-hidden="true"
                    focusable="false"
                    strokeLinecap="round"
                  >
                    <circle cx="7" cy="7" r="4.25" />
                    <path d="m10.25 10.25 3 3" />
                  </svg>
                ),
              },
              {
                value: 'chat',
                label: t('playground.mode.chat'),
                icon: (
                  <svg
                    viewBox="0 0 16 16"
                    aria-hidden="true"
                    focusable="false"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M13.5 8.5c0 2.35-2.46 4.25-5.5 4.25a6.8 6.8 0 0 1-1.6-.19L3 13.5l.9-2.2A4.1 4.1 0 0 1 2.5 8.5c0-2.35 2.46-4.25 5.5-4.25s5.5 1.9 5.5 4.25Z" />
                  </svg>
                ),
              },
            ]}
          />
        }
      />

      {indexedDocuments.isPending ? (
        <Card>
          <Skeleton height="10rem" />
        </Card>
      ) : indexedDocuments.isSuccess && indexedDocuments.data.documents.length === 0 ? (
        // Rien n'empêche de questionner un corpus vide, mais le résultat
        // (zéro passage, toujours) n'apprendrait rien — mieux vaut le dire
        // d'emblée que laisser deviner pourquoi une recherche ne renvoie
        // jamais rien. `indexedDocuments.isSuccess` exclu une erreur réseau :
        // celle-là garde le comportement précédent (tableau vide silencieux),
        // pas cet écran qui affirmerait « rien d'indexé » à tort.
        <EmptyState
          illustration="documents"
          title={t('playground.noIndexed.title')}
          description={t('playground.noIndexed.description')}
          action={
            <Link to={`/knowledge-bases/${kbId}`} className={styles.backAction}>
              {t('playground.backToBase')}
            </Link>
          }
        />
      ) : (
        <div className={styles.layout}>
          <main className={styles.main}>
            {mode === 'search' ? (
              <SearchPanel knowledgeBaseId={kbId} settings={settings} />
            ) : (
              <ChatPanel
                knowledgeBaseId={kbId}
                settings={settings}
                providers={providers.data?.llm ?? []}
                personalKeyVendors={personalKeyVendors}
                forcedModelId={providers.data?.llm_forced_model_id}
                onSwitchToSearch={() => {
                  setMode('search');
                }}
              />
            )}
          </main>

          <aside className={styles.aside}>
            <Card>
              <RetrievalSettings
                value={settings}
                onChange={setSettings}
                indexedDocuments={indexedDocuments.data?.documents ?? []}
                indexedDocumentsTruncated={indexedDocuments.data?.truncated ?? false}
                rerankerAvailable={base.data.reranker_model_id != null}
              />
            </Card>
          </aside>
        </div>
      )}
    </div>
  );
}

function PlaygroundSkeleton(): ReactNode {
  return (
    <div className={styles.page} aria-busy="true">
      <div className={styles.headerSkeleton}>
        <Skeleton width="6rem" height="0.6rem" />
        <Skeleton width="20rem" height="1.75rem" />
        <Skeleton width="min(34rem, 90%)" height="0.75rem" />
      </div>
      <div className={styles.layout}>
        <Card>
          <Skeleton height="4rem" />
        </Card>
        <Card>
          <Skeleton height="12rem" />
        </Card>
      </div>
    </div>
  );
}
