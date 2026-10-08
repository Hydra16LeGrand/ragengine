import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { apiKeyKeys, apiKeysQueryOptions, revokeApiKeyMutationOptions } from '@/api/api-keys';
import { problemMessageOf } from '@/api/problem';
import type { ApiKeyRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { EmptyState } from '@/components/EmptyState';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useI18n, useT } from '@/i18n';
import { formatDateTime } from '@/lib/format';

import { CreateApiKeyDialog } from './CreateApiKeyDialog';
import styles from './ApiKeys.module.css';

/**
 * Clés API du compte : liste, création, révocation.
 *
 * Réservé aux comptes réels — la garde vit un cran au-dessus, dans
 * `RequireAccount` (route `/settings`) : ce composant suppose déjà un
 * principal habilité et ne revérifie rien.
 */
export function ApiKeysPanel(): ReactNode {
  const t = useT();
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [creating, setCreating] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const { data, isPending, isError, error } = useQuery(apiKeysQueryOptions());
  const keys = data?.data ?? [];
  const revokingKey = keys.find((entry) => entry.id === revokingId) ?? null;

  const revoke = useMutation({
    ...revokeApiKeyMutationOptions(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: apiKeyKeys.all });
      setRevokingId(null);
      notify({ tone: 'success', title: t('settings.apiKeys.revoke.successTitle') });
    },
    onError: (revokeError) => {
      notify({
        tone: 'danger',
        title: t('settings.apiKeys.revoke.errorTitle'),
        description: problemMessageOf(revokeError, t('error.unexpected')),
      });
    },
  });

  return (
    <Card>
      <CardHeader
        eyebrow={t('settings.eyebrow')}
        title={t('settings.apiKeys.title')}
        description={t('settings.apiKeys.description')}
        actions={
          <Button
            onClick={() => {
              setCreating(true);
            }}
          >
            {t('settings.apiKeys.create')}
          </Button>
        }
      />

      {isError ? (
        <p className={styles.error} role="alert">
          {problemMessageOf(error, t('error.unexpected'))}
        </p>
      ) : isPending ? (
        <ul className={styles.list} role="list" aria-busy="true">
          {Array.from({ length: 2 }, (_, index) => (
            <li key={index} className={styles.row}>
              <Skeleton width="14rem" />
              <Skeleton width="6rem" height="1.5rem" />
            </li>
          ))}
        </ul>
      ) : keys.length === 0 ? (
        <EmptyState
          compact
          illustration="lock"
          title={t('settings.apiKeys.emptyTitle')}
          description={t('settings.apiKeys.emptyDescription')}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setCreating(true);
              }}
            >
              {t('settings.apiKeys.create')}
            </Button>
          }
        />
      ) : (
        <ul className={styles.list} role="list">
          {keys.map((entry) => (
            <ApiKeyRow
              key={entry.id}
              apiKey={entry}
              locale={locale}
              revoking={revoke.isPending && revoke.variables === entry.id}
              onRevoke={() => {
                setRevokingId(entry.id);
              }}
            />
          ))}
        </ul>
      )}

      <CreateApiKeyDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
      />

      <Dialog
        open={revokingKey !== null}
        onClose={() => {
          setRevokingId(null);
        }}
        title={t('settings.apiKeys.revoke.title')}
        description={
          revokingKey !== null
            ? t('settings.apiKeys.revoke.description', { name: revokingKey.name })
            : undefined
        }
        closeLabel={t('form.close')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setRevokingId(null);
              }}
              disabled={revoke.isPending}
            >
              {t('form.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={revoke.isPending}
              onClick={() => {
                if (revokingId !== null) revoke.mutate(revokingId);
              }}
            >
              {t('settings.apiKeys.revoke.confirm')}
            </Button>
          </>
        }
      >
        <p>{t('settings.apiKeys.revoke.warning')}</p>
      </Dialog>
    </Card>
  );
}

/** Une ligne de la liste : identité, traces d'usage, statut et révocation. */
function ApiKeyRow({
  apiKey,
  locale,
  revoking,
  onRevoke,
}: {
  apiKey: ApiKeyRead;
  locale: string;
  revoking: boolean;
  onRevoke: () => void;
}): ReactNode {
  const t = useT();
  const revoked = apiKey.revoked_at !== null;

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <div className={styles.rowIdentity}>
          <span className={styles.name}>{apiKey.name}</span>
          <code className={styles.prefix}>{apiKey.prefix}…</code>
        </div>
        <div className={styles.meta}>
          <span>
            {t('settings.apiKeys.created', { date: formatDateTime(apiKey.created_at, locale) })}
          </span>
          <span>
            {apiKey.last_used_at != null
              ? t('settings.apiKeys.lastUsed', {
                  date: formatDateTime(apiKey.last_used_at, locale),
                })
              : t('settings.apiKeys.neverUsed')}
          </span>
          {apiKey.expires_at != null && (
            <span>
              {t('settings.apiKeys.expires', { date: formatDateTime(apiKey.expires_at, locale) })}
            </span>
          )}
        </div>
      </div>

      <div className={styles.rowActions}>
        {revoked ? (
          <Badge tone="neutral">{t('settings.apiKeys.status.revoked')}</Badge>
        ) : (
          <>
            <Badge tone="success">{t('settings.apiKeys.status.active')}</Badge>
            <Button variant="danger" size="sm" loading={revoking} onClick={onRevoke}>
              {t('settings.apiKeys.revoke.action')}
            </Button>
          </>
        )}
      </div>
    </li>
  );
}
