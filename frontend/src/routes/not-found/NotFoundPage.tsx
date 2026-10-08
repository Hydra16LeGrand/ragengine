import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { useT } from '@/i18n';

import styles from './NotFoundPage.module.css';

export function NotFoundPage(): ReactNode {
  const t = useT();

  return (
    <div className={styles.page}>
      <p className={styles.code}>404</p>
      <h1>{t('notFound.title')}</h1>
      <p className={styles.description}>{t('notFound.description')}</p>
      <Link to="/knowledge-bases" className={styles.link}>
        ← {t('notFound.back')}
      </Link>
    </div>
  );
}
