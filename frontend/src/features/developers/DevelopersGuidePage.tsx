import type { ReactNode } from 'react';

import { Card, CardBody, CardHeader } from '@/components/Card';
import buttonStyles from '@/components/Button/Button.module.css';
import { PageHeader } from '@/components/PageHeader';
import { useT } from '@/i18n';
import { cx } from '@/lib/cx';

import styles from './DevelopersGuidePage.module.css';

/** URL du même préfixe que le client API (`@/api/*`) — pas de base absolue à
 * maintenir, la référence est servie par la même façade que la SPA appelle. */
const PUBLIC_REFERENCE_URL = '/api/v1/docs-public';

/**
 * Guide développeur (page publique, aucune authentification requise).
 *
 * Contenu narratif, volontairement non exhaustif — chaque appel détaillé ici
 * duplique en le résumant `docs/api-guide.md` (source de vérité rédactionnelle
 * du dépôt) ; l'exhaustivité de chaque endpoint, champ et code de retour vit
 * dans la référence Redoc filtrée (`PUBLIC_REFERENCE_URL`, `backend/app/api/
 * public_docs.py`), pas ici — dupliquer les deux dériverait tôt ou tard.
 */
export function DevelopersGuidePage(): ReactNode {
  const t = useT();

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t('developers.eyebrow')}
        title={t('developers.title')}
        description={t('developers.description')}
        actions={
          <a
            href={PUBLIC_REFERENCE_URL}
            target="_blank"
            rel="noreferrer"
            className={cx(buttonStyles.button, buttonStyles.primary, buttonStyles.md)}
          >
            {t('developers.referenceCta')}
          </a>
        }
      />

      <Card>
        <CardHeader
          title={t('developers.auth.title')}
          description={t('developers.auth.description')}
        />
        <CardBody>
          <code className={styles.code}>{t('developers.auth.apiKey')}</code>
          <p className={styles.note}>{t('developers.auth.apiKeyNote')}</p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title={t('developers.schemaA.title')}
          description={t('developers.schemaA.description')}
        />
        <CardBody>
          <ol className={styles.steps}>
            <li>{t('developers.schemaA.step1')}</li>
            <li>{t('developers.schemaA.step2')}</li>
            <li>{t('developers.schemaA.step3')}</li>
            <li>{t('developers.schemaA.step4')}</li>
            <li>{t('developers.schemaA.step5')}</li>
          </ol>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title={t('developers.schemaB.title')}
          description={t('developers.schemaB.description')}
        />
        <CardBody>
          <ul className={styles.list}>
            <li>{t('developers.schemaB.search')}</li>
            <li>{t('developers.schemaB.chat')}</li>
          </ul>
          <p className={styles.note}>{t('developers.schemaB.filters')}</p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title={t('developers.errors.title')}
          description={t('developers.errors.description')}
        />
      </Card>

      <Card>
        <CardHeader
          title={t('developers.providers.title')}
          description={t('developers.providers.description')}
        />
      </Card>
    </div>
  );
}
