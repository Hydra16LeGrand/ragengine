import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import {
  llmKeyKeys,
  llmKeysQueryOptions,
  removeLlmKeyMutationOptions,
  setLlmKeyMutationOptions,
} from '@/api/llm-keys';
import { problemMessageOf } from '@/api/problem';
import type { LlmVendor, UserLlmKeyRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useI18n, useT } from '@/i18n';
import { formatDateTime } from '@/lib/format';

import styles from './LlmKeys.module.css';

const VENDORS: readonly LlmVendor[] = ['anthropic', 'openai', 'groq'];

/**
 * Clés LLM personnelles du compte : trois fournisseurs fixes (`LlmVendor`),
 * jamais une liste ouverte — inutile de paginer ou d'en créer plusieurs par
 * fournisseur, `PUT /me/llm-keys/{provider}` remplace silencieusement.
 *
 * Réservé aux comptes réels — la garde vit un cran au-dessus, dans
 * `RequireAccount` (route `/settings`) : ce composant suppose déjà un
 * principal habilité et ne revérifie rien.
 */
export function LlmKeysPanel(): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [editingVendor, setEditingVendor] = useState<LlmVendor | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [removingVendor, setRemovingVendor] = useState<LlmVendor | null>(null);

  const { data, isPending, isError, error } = useQuery(llmKeysQueryOptions());
  const keys = data ?? [];
  const byVendor = new Map<LlmVendor, UserLlmKeyRead>(keys.map((entry) => [entry.provider, entry]));

  const setKey = useMutation({
    ...setLlmKeyMutationOptions(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: llmKeyKeys.all });
      setEditingVendor(null);
      setApiKeyInput('');
      notify({ tone: 'success', title: t('settings.llmKeys.set.successTitle') });
    },
    onError: (setError) => {
      notify({
        tone: 'danger',
        title: t('settings.llmKeys.set.errorTitle'),
        description: problemMessageOf(setError, t('error.unexpected')),
      });
    },
  });

  const removeKey = useMutation({
    ...removeLlmKeyMutationOptions(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: llmKeyKeys.all });
      setRemovingVendor(null);
      notify({ tone: 'success', title: t('settings.llmKeys.remove.successTitle') });
    },
    onError: (removeError) => {
      notify({
        tone: 'danger',
        title: t('settings.llmKeys.remove.errorTitle'),
        description: problemMessageOf(removeError, t('error.unexpected')),
      });
    },
  });

  const startEditing = (vendor: LlmVendor): void => {
    setEditingVendor(vendor);
    setApiKeyInput('');
    setKey.reset();
  };

  const cancelEditing = (): void => {
    setEditingVendor(null);
    setApiKeyInput('');
    setKey.reset();
  };

  const submitKey = (vendor: LlmVendor): void => {
    if (apiKeyInput.trim().length === 0) return;
    setKey.mutate({ provider: vendor, apiKey: apiKeyInput.trim() });
  };

  return (
    <Card>
      <CardHeader
        title={t('settings.llmKeys.title')}
        description={t('settings.llmKeys.description')}
      />

      {isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(error, t('error.unexpected'))}
        </p>
      ) : isPending ? (
        <ul className={styles.list} role="list" aria-busy="true">
          {VENDORS.map((vendor) => (
            <li key={vendor} className={styles.row}>
              <Skeleton width="14rem" />
            </li>
          ))}
        </ul>
      ) : (
        <ul className={styles.list} role="list">
          {VENDORS.map((vendor) => {
            const entry = byVendor.get(vendor) ?? null;
            const isEditing = editingVendor === vendor;
            return (
              <li key={vendor} className={styles.row}>
                <div className={styles.rowHead}>
                  <div className={styles.rowIdentity}>
                    <span className={styles.name}>{t(`settings.llmKeys.vendor.${vendor}`)}</span>
                    {entry !== null ? (
                      <Badge tone="success">{t('settings.llmKeys.status.active')}</Badge>
                    ) : (
                      <Badge tone="neutral">{t('settings.llmKeys.status.none')}</Badge>
                    )}
                    {entry !== null && (
                      <span className={styles.meta}>
                        {t('settings.llmKeys.updated', {
                          date: formatDateTime(entry.updated_at, locale),
                        })}
                      </span>
                    )}
                  </div>

                  {!isEditing && (
                    <div className={styles.rowActions}>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          startEditing(vendor);
                        }}
                      >
                        {entry !== null ? t('settings.llmKeys.replace') : t('settings.llmKeys.add')}
                      </Button>
                      {entry !== null && (
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => {
                            setRemovingVendor(vendor);
                          }}
                        >
                          {t('settings.llmKeys.remove.action')}
                        </Button>
                      )}
                    </div>
                  )}
                </div>

                {isEditing && (
                  <div className={styles.form}>
                    <div className={styles.formField}>
                      <Field label={t('settings.llmKeys.keyLabel')}>
                        {(field) => (
                          <Input
                            {...field}
                            type="password"
                            autoComplete="off"
                            value={apiKeyInput}
                            placeholder={t('settings.llmKeys.keyPlaceholder')}
                            onChange={(event) => {
                              setApiKeyInput(event.target.value);
                            }}
                          />
                        )}
                      </Field>
                    </div>
                    <Button variant="secondary" onClick={cancelEditing} disabled={setKey.isPending}>
                      {t('form.cancel')}
                    </Button>
                    <Button
                      loading={setKey.isPending}
                      disabled={apiKeyInput.trim().length === 0}
                      onClick={() => {
                        submitKey(vendor);
                      }}
                    >
                      {t('settings.llmKeys.save')}
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Dialog
        open={removingVendor !== null}
        onClose={() => {
          setRemovingVendor(null);
        }}
        title={t('settings.llmKeys.remove.title')}
        description={
          removingVendor !== null
            ? t('settings.llmKeys.remove.description', {
                vendor: t(`settings.llmKeys.vendor.${removingVendor}`),
              })
            : undefined
        }
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setRemovingVendor(null);
              }}
              disabled={removeKey.isPending}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={removeKey.isPending}
              onClick={() => {
                if (removingVendor !== null) removeKey.mutate(removingVendor);
              }}
            >
              {t('settings.llmKeys.remove.confirm')}
            </Button>
          </>
        }
      >
        <p>{t('settings.llmKeys.remove.warning')}</p>
      </Dialog>
    </Card>
  );
}
