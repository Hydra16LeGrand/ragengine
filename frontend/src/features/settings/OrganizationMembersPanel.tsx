import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type SubmitEvent, type ReactNode } from 'react';

import { isApiError } from '@/api/client';
import {
  organizationKeys,
  organizationMembersQueryOptions,
  removeOrganizationMemberMutationOptions,
  setOrganizationMemberMutationOptions,
} from '@/api/organizations';
import { problemMessageOf } from '@/api/problem';
import type { OrganizationMemberRead, OrganizationRead, OrganizationRole } from '@/api/schemas';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useT } from '@/i18n';

import styles from './Organizations.module.css';

const ROLES: readonly OrganizationRole[] = ['member', 'owner'];

/**
 * Membres d'une organisation.
 *
 * Contrairement à `MembersPanel` (KB), l'annuaire (`GET …/members`) est
 * ouvert à **tout** membre — seule la mutation (ajout, changement de rôle,
 * retrait) exige `owner` (ADR-022, `organizations.py` docstring). `isOwner`
 * gate donc uniquement les contrôles, jamais l'affichage de la liste.
 */
export function OrganizationMembersPanel({
  organization,
  isOwner,
}: {
  organization: OrganizationRead;
  isOwner: boolean;
}): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [userIdInput, setUserIdInput] = useState('');
  const [role, setRole] = useState<OrganizationRole>('member');
  const [pendingRemoval, setPendingRemoval] = useState<OrganizationMemberRead | null>(null);

  const directory = useQuery(organizationMembersQueryOptions(organization.id));

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: organizationKeys.members(organization.id) });
  };

  const setMember = useMutation({
    ...setOrganizationMemberMutationOptions(organization.id),
    onSuccess: async (saved) => {
      await invalidate();
      notify({
        tone: 'success',
        title: t('settings.organizations.members.grantedTitle'),
        description: t('settings.organizations.members.grantedDescription', {
          role: t(`settings.organizations.role.${saved.role}`),
        }),
      });
      setUserIdInput('');
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('settings.organizations.members.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
  });

  const removeMember = useMutation({
    ...removeOrganizationMemberMutationOptions(organization.id),
    onSuccess: async () => {
      await invalidate();
      notify({ tone: 'success', title: t('settings.organizations.members.removedTitle') });
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('settings.organizations.members.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
    onSettled: () => {
      setPendingRemoval(null);
    },
  });

  const onAdd = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const userId = userIdInput.trim();
    if (userId.length === 0) return;
    setMember.mutate({ userId, role });
  };

  const members = directory.data ?? [];
  const addErrorIsNotFound =
    setMember.isError && isApiError(setMember.error) && setMember.error.status === 404;

  return (
    <Card>
      <CardHeader
        eyebrow={t('settings.organizations.section.members')}
        title={t('settings.organizations.members.title')}
        description={t('settings.organizations.members.description')}
      />

      {directory.isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(directory.error, t('error.unexpected'))}
        </p>
      ) : directory.isPending ? (
        <ul className={styles.list} role="list" aria-busy="true">
          {Array.from({ length: 2 }, (_, index) => (
            <li key={index} className={styles.row}>
              <Skeleton width="14rem" />
              <Skeleton width="6rem" height="1.5rem" />
            </li>
          ))}
        </ul>
      ) : (
        <ul className={styles.list} role="list">
          {members.map((entry) => (
            <MemberRow
              key={entry.user_id}
              member={entry}
              isOwner={isOwner}
              changingRole={setMember.isPending && setMember.variables.userId === entry.user_id}
              removing={removeMember.isPending && removeMember.variables === entry.user_id}
              onChangeRole={(nextRole) => {
                setMember.mutate({ userId: entry.user_id, role: nextRole });
              }}
              onRemove={() => {
                setPendingRemoval(entry);
              }}
            />
          ))}
        </ul>
      )}

      {isOwner && (
        <form className={styles.lookup} onSubmit={onAdd}>
          <Field
            label={t('settings.organizations.members.addLabel')}
            hint={t('settings.organizations.members.addHint')}
            className={styles.lookupField}
          >
            {(field) => (
              <Input
                {...field}
                mono
                name="user_id"
                value={userIdInput}
                placeholder="00000000-0000-0000-0000-000000000000"
                onChange={(event) => {
                  setUserIdInput(event.target.value);
                }}
              />
            )}
          </Field>
          <Select
            aria-label={t('settings.organizations.members.roleLabel')}
            value={role}
            onChange={(event) => {
              setRole(event.target.value as OrganizationRole);
            }}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {t(`settings.organizations.role.${value}`)}
              </option>
            ))}
          </Select>
          <Button
            type="submit"
            loading={setMember.isPending}
            disabled={userIdInput.trim().length === 0}
          >
            {t('settings.organizations.members.add')}
          </Button>
        </form>
      )}

      {addErrorIsNotFound && (
        <p className={styles.resultError} role="alert">
          {t('settings.organizations.members.notFound')}
        </p>
      )}

      <Dialog
        open={pendingRemoval !== null}
        onClose={() => {
          setPendingRemoval(null);
        }}
        title={t('settings.organizations.members.removeConfirmTitle')}
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              disabled={removeMember.isPending}
              onClick={() => {
                setPendingRemoval(null);
              }}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={removeMember.isPending}
              onClick={() => {
                if (pendingRemoval !== null) removeMember.mutate(pendingRemoval.user_id);
              }}
            >
              {t('settings.organizations.members.removeConfirm')}
            </Button>
          </>
        }
      >
        <p>{t('settings.organizations.members.removeConfirmDescription')}</p>
      </Dialog>
    </Card>
  );
}

/** Une ligne de l'annuaire. Les contrôles ne sont montrés qu'à un `owner`. */
function MemberRow({
  member,
  isOwner,
  changingRole,
  removing,
  onChangeRole,
  onRemove,
}: {
  member: OrganizationMemberRead;
  isOwner: boolean;
  changingRole: boolean;
  removing: boolean;
  onChangeRole: (role: OrganizationRole) => void;
  onRemove: () => void;
}): ReactNode {
  const t = useT();

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <code className={styles.slug}>{member.user_id}</code>
        {isOwner ? (
          <Select
            aria-label={t('settings.organizations.members.roleLabel')}
            value={member.role}
            disabled={changingRole}
            onChange={(event) => {
              onChangeRole(event.target.value as OrganizationRole);
            }}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {t(`settings.organizations.role.${value}`)}
              </option>
            ))}
          </Select>
        ) : (
          <span>{t(`settings.organizations.role.${member.role}`)}</span>
        )}
      </div>
      {isOwner && (
        <Button variant="danger" size="sm" loading={removing} onClick={onRemove}>
          {t('settings.organizations.members.remove')}
        </Button>
      )}
    </li>
  );
}
