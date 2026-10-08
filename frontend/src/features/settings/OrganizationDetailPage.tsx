import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';

import { isApiError } from '@/api/client';
import {
  deleteOrganizationMutationOptions,
  organizationKeys,
  organizationQueryOptions,
  updateOrganizationMutationOptions,
} from '@/api/organizations';
import { fieldErrorsOf, problemMessageOf } from '@/api/problem';
import type { OrganizationUpdate } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { PageHeader } from '@/components/PageHeader';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useT } from '@/i18n';

import { OrganizationMembersPanel } from './OrganizationMembersPanel';
import styles from './Organizations.module.css';

/** Détail d'une organisation : identité, édition, membres. */
export function OrganizationDetailPage(): ReactNode {
  const { organizationId } = useParams<{ organizationId: string }>();

  if (organizationId === undefined) return <Navigate to="/settings/organizations" replace />;

  // `key` : changer d'organisation remonte l'écran, donc réinitialise
  // l'édition en cours — jamais un formulaire hérité d'une autre organisation
  // (même garde que `KnowledgeBaseDetailPage`).
  return <OrganizationDetail organizationId={organizationId} key={organizationId} />;
}

function OrganizationDetail({ organizationId }: { organizationId: string }): ReactNode {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const {
    data: organization,
    isPending,
    isError,
    error,
  } = useQuery(organizationQueryOptions(organizationId));

  const update = useMutation({
    ...updateOrganizationMutationOptions(organizationId),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: organizationKeys.all });
      setEditing(false);
      notify({
        tone: 'success',
        title: t('settings.organizations.edit.successTitle'),
        description: t('settings.organizations.edit.successDescription', { name: updated.name }),
      });
    },
    onError: (updateError) => {
      notify({
        tone: 'danger',
        title: t('settings.organizations.edit.errorTitle'),
        description: problemMessageOf(updateError, t('error.unexpected')),
      });
    },
  });

  const remove = useMutation({
    ...deleteOrganizationMutationOptions(organizationId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: organizationKeys.all });
      setConfirmingDelete(false);
      notify({ tone: 'success', title: t('settings.organizations.delete.successTitle') });
      void navigate('/settings/organizations', { replace: true });
    },
    onError: (deleteError) => {
      notify({
        tone: 'danger',
        title: t('settings.organizations.delete.errorTitle'),
        description: problemMessageOf(deleteError, t('error.unexpected')),
      });
      setConfirmingDelete(false);
    },
  });

  if (isPending) return <DetailSkeleton />;

  if (isError) {
    const notFound = isApiError(error) && error.status === 404;
    return (
      <div className={styles.page}>
        <EmptyState
          illustration={notFound ? 'search' : 'lock'}
          title={
            notFound
              ? t('settings.organizations.notFoundTitle')
              : t('settings.organizations.errorTitle')
          }
          description={
            notFound
              ? t('settings.organizations.notFoundDescription')
              : problemMessageOf(error, t('error.unexpected'))
          }
          action={
            <Link to="/settings/organizations" className={styles.backLink}>
              {t('settings.organizations.backToList')}
            </Link>
          }
        />
      </div>
    );
  }

  const isOwner = organization.principal_role === 'owner';
  const fieldErrors = fieldErrorsOf(update.error);

  const startEditing = (): void => {
    setNameInput(organization.name);
    setEditing(true);
  };

  const onSubmitEdit = (): void => {
    const name = nameInput.trim();
    if (name.length === 0) return;
    const payload: OrganizationUpdate = { name };
    update.mutate(payload);
  };

  return (
    <div className={styles.page}>
      <PageHeader
        title={organization.name}
        breadcrumb={
          <Link to="/settings/organizations" className={styles.backLink}>
            <svg
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9.5 4 5.5 8l4 4" />
            </svg>
            {t('settings.organizations.backToList')}
          </Link>
        }
        meta={
          <>
            {organization.principal_role != null && (
              <Badge tone={isOwner ? 'accent' : 'neutral'}>
                {t(`settings.organizations.role.${organization.principal_role}`)}
              </Badge>
            )}
            <code className={styles.slug}>{organization.slug}</code>
          </>
        }
        actions={
          editing || !isOwner ? undefined : (
            <>
              <Button variant="secondary" onClick={startEditing}>
                {t('settings.organizations.detail.edit')}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirmingDelete(true);
                }}
              >
                {t('settings.organizations.detail.delete')}
              </Button>
            </>
          )
        }
      />

      {editing ? (
        <div className={styles.form}>
          <Field
            label={t('settings.organizations.create.nameLabel')}
            error={fieldErrors.name}
            required
          >
            {(field) => (
              <Input
                {...field}
                name="name"
                required
                value={nameInput}
                onChange={(event) => {
                  setNameInput(event.target.value);
                }}
              />
            )}
          </Field>
          <div className={styles.resultActions}>
            <Button
              variant="secondary"
              disabled={update.isPending}
              onClick={() => {
                setEditing(false);
                update.reset();
              }}
            >
              {t('form.cancel')}
            </Button>
            <Button
              loading={update.isPending}
              disabled={nameInput.trim().length === 0}
              onClick={onSubmitEdit}
            >
              {t('settings.organizations.edit.submit')}
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.panels}>
          <OrganizationMembersPanel organization={organization} isOwner={isOwner} />
        </div>
      )}

      <Dialog
        open={confirmingDelete}
        onClose={() => {
          setConfirmingDelete(false);
        }}
        title={t('settings.organizations.delete.title')}
        description={t('settings.organizations.delete.description', { name: organization.name })}
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
              {t('settings.organizations.delete.confirm')}
            </Button>
          </>
        }
      >
        <p>{t('settings.organizations.delete.warning')}</p>
      </Dialog>
    </div>
  );
}

function DetailSkeleton(): ReactNode {
  return (
    <div className={styles.page} aria-busy="true">
      <Skeleton width="6rem" height="0.6rem" />
      <Skeleton width="18rem" height="1.75rem" />
      <Skeleton width="100%" height="8rem" />
    </div>
  );
}
