import type { ReactNode } from 'react';

import type { MemberRole, Visibility } from '@/api/schemas';
import { Badge } from '@/components/Badge';
import { useT } from '@/i18n';

const LOCK_ICON = (
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" strokeLinecap="round">
    <rect x="3.75" y="7" width="8.5" height="6.25" rx="1.5" />
    <path d="M5.75 7V5.25a2.25 2.25 0 0 1 4.5 0V7" />
  </svg>
);

const GLOBE_ICON = (
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" strokeLinecap="round">
    <circle cx="8" cy="8" r="5.25" />
    <path d="M3 8h10M8 2.75c1.5 1.7 2.25 3.45 2.25 5.25S9.5 11.55 8 13.25C6.5 11.55 5.75 9.8 5.75 8S6.5 4.45 8 2.75Z" />
  </svg>
);

/** Visibilité d'une base : privée (cadenas) ou publique (globe). */
export function VisibilityBadge({ visibility }: { visibility: Visibility }): ReactNode {
  const t = useT();
  const isPublic = visibility === 'public';

  return (
    <Badge
      tone={isPublic ? 'accent' : 'neutral'}
      variant="outline"
      icon={isPublic ? GLOBE_ICON : LOCK_ICON}
    >
      {isPublic ? t('kb.visibility.public') : t('kb.visibility.private')}
    </Badge>
  );
}

/** Rôle d'un membre sur une base. */
export function MemberRoleBadge({ role }: { role: MemberRole }): ReactNode {
  const t = useT();
  const tone = role === 'owner' ? 'accent' : role === 'editor' ? 'success' : 'neutral';
  const label =
    role === 'owner'
      ? t('kb.role.owner')
      : role === 'editor'
        ? t('kb.role.editor')
        : t('kb.role.viewer');

  return <Badge tone={tone}>{label}</Badge>;
}
