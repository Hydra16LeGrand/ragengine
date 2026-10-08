import type { ReactNode, TextareaHTMLAttributes } from 'react';

import { cx } from '@/lib/cx';

import styles from './Textarea.module.css';

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

/** Zone de texte multiligne. Le libellé est porté par `<Field>`. */
export function Textarea({ rows = 4, className, ...rest }: TextareaProps): ReactNode {
  return <textarea rows={rows} className={cx(styles.textarea, className)} {...rest} />;
}
