import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { createOrganizationMutationOptions, organizationKeys } from '@/api/organizations';
import { fieldErrorsOf, problemMessageOf } from '@/api/problem';
import type { OrganizationCreate } from '@/api/schemas';
import { Button } from '@/components/Button';
import { Dialog } from '@/components/Dialog';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { useToast } from '@/components/Toast';
import { useT } from '@/i18n';

import styles from './Organizations.module.css';

export interface CreateOrganizationDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Dialogue de création — un seul champ. Le slug (unique globalement, à la
 * différence du slug d'une base de connaissances) est dérivé du nom côté
 * serveur : l'exposer ici n'ajouterait rien tant qu'un conflit n'a pas eu
 * lieu, auquel cas l'erreur `409` guide déjà l'utilisateur vers un autre nom.
 */
export function CreateOrganizationDialog({
  open,
  onClose,
}: CreateOrganizationDialogProps): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { notify } = useToast();

  const [name, setName] = useState('');

  const create = useMutation({
    ...createOrganizationMutationOptions(),
    onSuccess: async (organization) => {
      await queryClient.invalidateQueries({ queryKey: organizationKeys.all });
      notify({
        tone: 'success',
        title: t('settings.organizations.create.successTitle'),
        description: t('settings.organizations.create.successDescription', {
          name: organization.name,
        }),
      });
      handleClose();
      void navigate(`/settings/organizations/${organization.id}`);
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('settings.organizations.create.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
  });

  const resetForm = (): void => {
    setName('');
    create.reset();
  };

  const handleClose = (): void => {
    onClose();
    resetForm();
  };

  const canSubmit = name.trim().length > 0;

  const onSubmit = (): void => {
    if (!canSubmit) return;
    const payload: OrganizationCreate = { name: name.trim() };
    create.mutate(payload);
  };

  const fieldErrors = fieldErrorsOf(create.error);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={t('settings.organizations.create.title')}
      description={t('settings.organizations.create.description')}
      closeLabel={t('form.close')}
      footer={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={create.isPending}>
            {t('form.cancel')}
          </Button>
          <Button loading={create.isPending} disabled={!canSubmit} onClick={onSubmit}>
            {t('settings.organizations.create.submit')}
          </Button>
        </>
      }
    >
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
              value={name}
              placeholder={t('settings.organizations.create.namePlaceholder')}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
