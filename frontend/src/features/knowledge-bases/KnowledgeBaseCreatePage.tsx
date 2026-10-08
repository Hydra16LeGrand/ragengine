import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { createKnowledgeBaseMutationOptions, knowledgeBaseKeys } from '@/api/knowledge-bases';
import type { KnowledgeBaseCreate } from '@/api/schemas';
import { PageHeader } from '@/components/PageHeader';
import { useToast } from '@/components/Toast';
import { nextStagedFileId, stashPendingUploads, type StagedFile } from '@/features/ingestion';
import { useT } from '@/i18n';

import { KnowledgeBaseForm, type KnowledgeBaseFormValues } from './KnowledgeBaseForm';
import styles from './KnowledgeBasePages.module.css';

/**
 * Création d'une base — écran dédié plutôt que modale.
 *
 * Le formulaire porte une section avancée dépliable et une explication par
 * champ : dans une modale, tout cela défilerait dans une fenêtre contrainte,
 * ne survivrait pas à un rechargement et ne serait pas partageable par lien.
 * Les modales restent réservées aux décisions courtes (confirmer, retirer).
 */
export function KnowledgeBaseCreatePage(): ReactNode {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  // Purement local tant que la base n'existe pas — aucun appel réseau
  // n'accompagne l'ajout ou le retrait d'un fichier ici (même garantie que
  // `UploadPanel.staged`). Survit à un échec de création : rien n'est
  // recopié depuis une réponse serveur, il n'y a donc rien à reconstituer.
  const [staged, setStaged] = useState<StagedFile[]>([]);

  const stage = useCallback((files: File[]): void => {
    setStaged((previous) => [
      ...previous,
      ...files.map<StagedFile>((file) => ({ id: nextStagedFileId(), file })),
    ]);
  }, []);

  const unstage = useCallback((id: string): void => {
    setStaged((previous) => previous.filter((item) => item.id !== id));
  }, []);

  const create = useMutation({
    ...createKnowledgeBaseMutationOptions(),
    onSuccess: async (base) => {
      await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.all });
      notify({
        tone: 'success',
        title: t('kb.create.successTitle'),
        description: t('kb.create.successDescription', { name: base.name }),
      });
      // La base existe désormais : un échec d'envoi qui suit ne la remet pas
      // en cause. Les fichiers sont déposés ici et repris par `UploadPanel`
      // dès son montage sur l'écran de détail (`pendingUploads.ts`) — c'est là
      // que la file, la progression et la reprise par fichier échoué vivent
      // déjà, sans les reconstruire une seconde fois pour ce seul parcours.
      stashPendingUploads(
        base.id,
        staged.map((item) => item.file),
      );
      void navigate(`/knowledge-bases/${base.id}`, { replace: true });
    },
  });

  const onSubmit = (values: KnowledgeBaseFormValues): void => {
    const payload: KnowledgeBaseCreate = {
      name: values.name,
      slug: values.slug.length > 0 ? values.slug : null,
      description: values.description.length > 0 ? values.description : null,
      visibility: values.visibility,
      organization_id: values.organization_id,
      embedding_model_id: values.embedding_model_id,
      reranker_model_id: values.reranker_model_id,
      provider_config: values.provider_config,
      observability_enabled: values.observability_enabled,
      evaluation_enabled: values.evaluation_enabled,
    };
    create.mutate(payload);
  };

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('kb.eyebrow')}
        title={t('kb.create.title')}
        description={t('kb.create.description')}
        breadcrumb={
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
        }
      />

      <KnowledgeBaseForm
        mode="create"
        submitting={create.isPending}
        error={create.error}
        submitLabel={t('kb.create.submit')}
        onSubmit={onSubmit}
        onCancel={() => {
          void navigate('/knowledge-bases');
        }}
        stagedFiles={staged}
        onStageFiles={stage}
        onUnstageFile={unstage}
      />
    </div>
  );
}
