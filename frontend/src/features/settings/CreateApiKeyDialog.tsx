import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { apiKeyKeys, createApiKeyMutationOptions } from '@/api/api-keys';
import { fieldErrorsOf, problemMessageOf } from '@/api/problem';
import type { ApiKeyCreate, ApiKeyCreated } from '@/api/schemas';
import { Button } from '@/components/Button';
import { Checkbox } from '@/components/Checkbox';
import { Dialog } from '@/components/Dialog';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { SegmentedControl } from '@/components/SegmentedControl';
import { useToast } from '@/components/Toast';
import { useI18n, useT } from '@/i18n';

import { WILDCARD_SCOPE, scopeOptions } from './apiKeyScopes';
import styles from './ApiKeys.module.css';

type ScopeMode = 'full' | 'custom';

export interface CreateApiKeyDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Dialogue de création — le point le plus sensible de l'écran.
 *
 * Deux phases dans la même modale : le formulaire, puis — après succès — la
 * révélation unique du jeton en clair. Fermer la modale (Échap, fond, ou le
 * bouton « terminer ») **efface** l'état local : le jeton n'existe nulle part
 * ailleurs que dans la réponse HTTP déjà reçue, jamais remis en cache
 * TanStack Query (cf. `api/api-keys.ts`). Une fois la modale refermée, il n'y
 * a plus aucun moyen de le retrouver — c'est délibéré, pas un bug.
 */
export function CreateApiKeyDialog({ open, onClose }: CreateApiKeyDialogProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [name, setName] = useState('');
  const [scopeMode, setScopeMode] = useState<ScopeMode>('full');
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    ...createApiKeyMutationOptions(),
    onSuccess: async (issued) => {
      // Seule la forme sans jeton (`ApiKeyRead`) rejoint le cache partagé :
      // la liste se met à jour, le `token` reste confiné à cet état local.
      await queryClient.invalidateQueries({ queryKey: apiKeyKeys.all });
      setCreated(issued);
    },
    onError: (error) => {
      notify({
        tone: 'danger',
        title: t('settings.apiKeys.create.errorTitle'),
        description: problemMessageOf(error, t('error.unexpected')),
      });
    },
  });

  const resetForm = (): void => {
    setName('');
    setScopeMode('full');
    setSelectedScopes([]);
    setCreated(null);
    setCopied(false);
    create.reset();
  };

  const handleClose = (): void => {
    onClose();
    resetForm();
  };

  const toggleScope = (scope: string): void => {
    setSelectedScopes((current) =>
      current.includes(scope) ? current.filter((value) => value !== scope) : [...current, scope],
    );
  };

  const customScopesInvalid = scopeMode === 'custom' && selectedScopes.length === 0;
  const canSubmit = name.trim().length > 0 && !customScopesInvalid;

  const onSubmit = (): void => {
    if (!canSubmit) return;
    const payload: ApiKeyCreate = {
      name: name.trim(),
      scopes: scopeMode === 'full' ? [WILDCARD_SCOPE] : selectedScopes,
    };
    create.mutate(payload);
  };

  const copyToken = (issuedToken: string): void => {
    navigator.clipboard
      .writeText(issuedToken)
      .then(() => {
        setCopied(true);
      })
      .catch(() => {
        notify({ tone: 'danger', title: t('settings.apiKeys.create.copyErrorTitle') });
      });
  };

  const fieldErrors = fieldErrorsOf(create.error);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={
        created !== null
          ? t('settings.apiKeys.create.revealTitle')
          : t('settings.apiKeys.create.title')
      }
      description={
        created !== null
          ? t('settings.apiKeys.create.revealDescription')
          : t('settings.apiKeys.create.description')
      }
      closeLabel={t('form.close')}
      footer={
        created !== null ? (
          <Button onClick={handleClose}>{t('settings.apiKeys.create.done')}</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={handleClose} disabled={create.isPending}>
              {t('form.cancel')}
            </Button>
            <Button loading={create.isPending} disabled={!canSubmit} onClick={onSubmit}>
              {t('settings.apiKeys.create.submit')}
            </Button>
          </>
        )
      }
    >
      {created !== null ? (
        <div className={styles.reveal}>
          <p className={styles.revealWarning} role="alert">
            {t('settings.apiKeys.create.revealWarning')}
          </p>
          <div className={styles.tokenBox}>
            <code className={styles.token}>{created.token}</code>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                copyToken(created.token);
              }}
            >
              {copied ? t('settings.apiKeys.create.copied') : t('settings.apiKeys.create.copy')}
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.form}>
          <Field label={t('settings.apiKeys.create.nameLabel')} error={fieldErrors.name} required>
            {(field) => (
              <Input
                {...field}
                name="name"
                required
                value={name}
                placeholder={t('settings.apiKeys.create.namePlaceholder')}
                onChange={(event) => {
                  setName(event.target.value);
                }}
              />
            )}
          </Field>

          <fieldset className={styles.scopeFieldset}>
            <legend className={styles.scopeLegend}>
              {t('settings.apiKeys.create.scopesLabel')}
            </legend>

            <SegmentedControl<ScopeMode>
              label={t('settings.apiKeys.create.scopesLabel')}
              value={scopeMode}
              onChange={setScopeMode}
              options={[
                { value: 'full', label: t('settings.apiKeys.create.scopeFull') },
                { value: 'custom', label: t('settings.apiKeys.create.scopeCustom') },
              ]}
            />

            <p className={styles.scopeHint}>
              {scopeMode === 'full'
                ? t('settings.apiKeys.create.scopeFullHint')
                : t('settings.apiKeys.create.scopeCustomHint')}
            </p>

            {scopeMode === 'custom' && (
              <div className={styles.scopeGrid}>
                {scopeOptions(t, locale).map((scope) => (
                  <Checkbox
                    key={scope.value}
                    label={scope.label}
                    checked={selectedScopes.includes(scope.value)}
                    onChange={() => {
                      toggleScope(scope.value);
                    }}
                  />
                ))}
              </div>
            )}

            {customScopesInvalid && (
              <p className={styles.scopeError} role="alert">
                {t('settings.apiKeys.create.scopeCustomEmpty')}
              </p>
            )}
          </fieldset>
        </div>
      )}
    </Dialog>
  );
}
