import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type SubmitEvent, type ReactNode } from 'react';

import { isApiError } from '@/api/client';
import {
  knowledgeBaseKeys,
  knowledgeBaseMemberQueryOptions,
  knowledgeBaseMembersQueryOptions,
  removeKnowledgeBaseMemberMutationOptions,
  setKnowledgeBaseMemberMutationOptions,
} from '@/api/knowledge-bases';
import { problemMessageOf } from '@/api/problem';
import type { KnowledgeBaseMemberRead, KnowledgeBaseRead, MemberRole } from '@/api/schemas';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useSession } from '@/features/auth';
import { useT } from '@/i18n';

import styles from './MembersPanel.module.css';

const ROLES: readonly MemberRole[] = ['viewer', 'editor', 'owner'];

/**
 * Gestion des accès à une base — réservée au propriétaire.
 *
 * Deux blocs distincts : l'annuaire complet (`GET …/members`) affiche et
 * modifie les rôles déjà accordés, chaque ligne nommant son titulaire
 * (`display_name`, joint depuis `users` — jamais l'e-mail, que tous les membres
 * n'ont pas à connaître) ; le formulaire par identifiant reste nécessaire pour
 * **ajouter** un nouveau membre — l'API n'expose toujours aucune recherche de
 * comptes par nom ou e-mail, il faut donc connaître l'UUID de la personne à
 * inviter.
 */
export function MembersPanel({ base }: { base: KnowledgeBaseRead }): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const { userId: currentUserId } = useSession();

  const [userIdInput, setUserIdInput] = useState('');
  const [lookupId, setLookupId] = useState('');
  const [role, setRole] = useState<MemberRole>('viewer');
  // La ligne dont le retrait attend confirmation — l'objet entier, pas son seul
  // identifiant : la modale nomme la personne visée, un UUID ne le ferait pas.
  const [pendingRemoval, setPendingRemoval] = useState<KnowledgeBaseMemberRead | null>(null);

  const directory = useQuery(knowledgeBaseMembersQueryOptions(base.id));
  const membership = useQuery(
    knowledgeBaseMemberQueryOptions(base.id, lookupId, lookupId.length > 0),
  );

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.members(base.id) });
    await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.detail(base.id) });
    if (lookupId.length > 0) {
      await queryClient.invalidateQueries({
        queryKey: knowledgeBaseKeys.member(base.id, lookupId),
      });
    }
  };

  const setMember = useMutation({
    ...setKnowledgeBaseMemberMutationOptions(base.id),
    onSuccess: async (saved) => {
      await invalidate();
      // Un seul message pour l'ajout et pour le changement de rôle : la réponse
      // dit qui et quel rôle, ce qui décrit exactement les deux cas — les
      // distinguer demanderait de savoir si la ligne préexistait, une
      // information que le corps de la réponse ne porte pas (seul le code HTTP
      // 201/200 la porte, et le client ne l'expose pas).
      notify({
        tone: 'success',
        title: t('kb.members.grantedTitle'),
        description: t('kb.members.grantedDescription', {
          name: saved.display_name,
          role: t(`kb.role.${saved.role}`),
        }),
      });
      setUserIdInput('');
      setLookupId('');
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('kb.members.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
  });

  const removeMember = useMutation({
    ...removeKnowledgeBaseMemberMutationOptions(base.id),
    onSuccess: async () => {
      await invalidate();
      notify({ tone: 'success', title: t('kb.members.removedTitle') });
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('kb.members.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
    // Refermée dans les deux cas : un refus (retirer le dernier `owner`, par
    // exemple) est définitif, laisser la modale ouverte inviterait à réessayer
    // ce que le backend vient de refuser — le message, lui, reste dans le toast.
    onSettled: () => {
      setPendingRemoval(null);
    },
  });

  const onLookup = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setLookupId(userIdInput.trim());
  };

  const notAMember =
    membership.isError && isApiError(membership.error) && membership.error.status === 404;
  const member = membership.data;
  const members = directory.data ?? [];

  return (
    <Card>
      <CardHeader
        eyebrow={t('kb.detail.section.access')}
        title={t('kb.members.title')}
        description={t('kb.members.description')}
      />

      {directory.isError ? (
        <p className={styles.resultError} role="alert">
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
              isYou={entry.user_id === currentUserId}
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

      <form className={styles.lookup} onSubmit={onLookup}>
        <Field
          label={t('kb.members.lookupLabel')}
          hint={t('kb.members.lookupHint')}
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
        <Button
          type="submit"
          variant="secondary"
          loading={membership.isFetching && lookupId.length > 0}
          disabled={userIdInput.trim().length === 0}
        >
          {t('kb.members.lookupAction')}
        </Button>
      </form>

      {lookupId.length > 0 && !membership.isFetching && (
        <div className={styles.result}>
          {member !== undefined ? (
            <p className={styles.resultEmpty}>{t('kb.members.alreadyListed')}</p>
          ) : notAMember ? (
            <>
              <p className={styles.resultEmpty}>{t('kb.members.notMember')}</p>
              <div className={styles.resultActions}>
                <Select
                  aria-label={t('kb.members.roleLabel')}
                  value={role}
                  onChange={(event) => {
                    setRole(event.target.value as MemberRole);
                  }}
                >
                  {ROLES.map((value) => (
                    <option key={value} value={value}>
                      {t(`kb.role.${value}`)}
                    </option>
                  ))}
                </Select>
                <Button
                  loading={setMember.isPending}
                  onClick={() => {
                    setMember.mutate({ userId: lookupId, role });
                  }}
                >
                  {t('kb.members.grant')}
                </Button>
              </div>
            </>
          ) : (
            <p className={styles.resultError} role="alert">
              {problemMessageOf(membership.error, t('error.unexpected'))}
            </p>
          )}
        </div>
      )}

      <Dialog
        open={pendingRemoval !== null}
        onClose={() => {
          setPendingRemoval(null);
        }}
        title={t('kb.members.removeConfirmTitle')}
        description={
          pendingRemoval === null
            ? undefined
            : t('kb.members.removeConfirmDescription', { name: pendingRemoval.display_name })
        }
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
              {t('kb.members.removeConfirm')}
            </Button>
          </>
        }
      />
    </Card>
  );
}

/**
 * Une ligne de l'annuaire : le nom du compte, son rôle, son retrait.
 *
 * Les contrôles sont montrés sur **toute** ligne, `owner` comprise. Les masquer
 * pour le créateur de la base rendait son propre retrait impensable depuis
 * l'écran, alors que c'est précisément le refus du backend (`409`, une base
 * garde toujours un `owner`) qui porte l'invariant — le cacher ici le
 * dupliquait, mal, et privait l'utilisateur de l'explication.
 */
function MemberRow({
  member,
  isYou,
  changingRole,
  removing,
  onChangeRole,
  onRemove,
}: {
  member: KnowledgeBaseMemberRead;
  isYou: boolean;
  changingRole: boolean;
  removing: boolean;
  onChangeRole: (role: MemberRole) => void;
  onRemove: () => void;
}): ReactNode {
  const t = useT();

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <span className={styles.memberName}>
          {member.display_name}
          {isYou && <span className={styles.you}>{t('kb.members.you')}</span>}
        </span>
        <Select
          aria-label={t('kb.members.roleLabel')}
          value={member.role}
          disabled={changingRole}
          onChange={(event) => {
            onChangeRole(event.target.value as MemberRole);
          }}
        >
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {t(`kb.role.${value}`)}
            </option>
          ))}
        </Select>
      </div>
      <Button variant="danger" size="sm" loading={removing} onClick={onRemove}>
        {t('kb.members.remove')}
      </Button>
    </li>
  );
}
