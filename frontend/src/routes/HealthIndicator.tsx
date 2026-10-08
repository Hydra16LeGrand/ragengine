import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { healthQueryOptions } from '@/api/health';
import { StatusDot, type StatusTone } from '@/components/StatusDot';
import { useT } from '@/i18n';

import styles from './HealthIndicator.module.css';

/**
 * Disponibilité de la façade, en pied de page.
 *
 * Remplace la carte de démonstration de la Phase 0 : dans un outil
 * d'exploitation, l'état du backend doit rester visible en permanence et
 * discret, pas occuper un écran d'accueil.
 */
export function HealthIndicator(): ReactNode {
  const t = useT();
  const { data, isPending, isError } = useQuery(healthQueryOptions());

  const tone: StatusTone = isPending ? 'pending' : isError ? 'danger' : 'success';
  const label = isPending
    ? t('health.checking')
    : isError
      ? t('health.offline')
      : t('health.online');

  return (
    <span className={styles.indicator}>
      <StatusDot tone={tone} />
      <span>{label}</span>
      {data?.version != null && (
        <span className={styles.version}>{t('health.version', { version: data.version })}</span>
      )}
    </span>
  );
}
