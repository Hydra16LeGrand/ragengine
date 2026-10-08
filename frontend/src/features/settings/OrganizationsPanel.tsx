import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { organizationsQueryOptions } from '@/api/organizations';
import { problemMessageOf } from '@/api/problem';
import type { OrganizationRead } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { EmptyState } from '@/components/EmptyState';
import { Skeleton } from '@/components/Skeleton';
import { useT } from '@/i18n';

import { CreateOrganizationDialog } from './CreateOrganizationDialog';
import styles from './Organizations.module.css';

/**
 * Organisations du compte : liste et création.
 *
 * La gestion fine (renommer, supprimer, membres) vit sur l'écran de détail
 * (`OrganizationDetailPage`) — cette liste ne fait qu'orienter, même
 * répartition que `KnowledgeBaseListPage`/`KnowledgeBaseDetailPage`.
 */
export function OrganizationsPanel(): ReactNode {
  const t = useT();
  const [creating, setCreating] = useState(false);

  const { data, isPending, isError, error } = useQuery(organizationsQueryOptions());
  const organizations = data?.data ?? [];

  return (
    <Card>
      <CardHeader
        eyebrow={t('settings.eyebrow')}
        title={t('settings.organizations.title')}
        description={t('settings.organizations.description')}
        actions={
          <Button
            onClick={() => {
              setCreating(true);
            }}
          >
            {t('settings.organizations.create')}
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
      ) : organizations.length === 0 ? (
        <EmptyState
          compact
          illustration="library"
          title={t('settings.organizations.emptyTitle')}
          description={t('settings.organizations.emptyDescription')}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setCreating(true);
              }}
            >
              {t('settings.organizations.create')}
            </Button>
          }
        />
      ) : (
        <ul className={styles.list} role="list">
          {organizations.map((organization) => (
            <OrganizationRow key={organization.id} organization={organization} />
          ))}
        </ul>
      )}

      <CreateOrganizationDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
      />
    </Card>
  );
}

function OrganizationRow({ organization }: { organization: OrganizationRead }): ReactNode {
  const t = useT();

  return (
    <li className={styles.row}>
      <Link to={`/settings/organizations/${organization.id}`} className={styles.rowLink}>
        <div className={styles.rowMain}>
          <div className={styles.rowIdentity}>
            <span className={styles.name}>{organization.name}</span>
            {organization.principal_role != null && (
              <Badge tone={organization.principal_role === 'owner' ? 'accent' : 'neutral'}>
                {t(`settings.organizations.role.${organization.principal_role}`)}
              </Badge>
            )}
          </div>
          <code className={styles.slug}>{organization.slug}</code>
        </div>
      </Link>
    </li>
  );
}
