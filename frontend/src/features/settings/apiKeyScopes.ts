import type { Locale, Translate, TranslationKey } from '@/i18n';
import { pluginApiKeyScopes } from '@/plugins';

/**
 * Catalogue des portées proposées à la création d'une clé API.
 *
 * Duplique volontairement `app.domain.scopes.ApiKeyScope` (backend) : aucun
 * endpoint n'expose ce catalogue aujourd'hui (`GET /providers` ne porte que
 * les briques d'inférence, pas les portées de clé). Une divergence n'est pas
 * dangereuse — le backend rejette toute portée inconnue en `422`
 * (`ApiKeyCreate._validate_scopes`) — seulement une option absente ou en trop
 * à l'écran. À tenir à jour si `ApiKeyScope` évolue. Les portées d'un greffon
 * (`apiKeyScopes`, ADR-069) s'ajoutent à la suite (`scopeOptions`).
 */
export interface ApiKeyScopeOption {
  value: string;
  labelKey: TranslationKey;
}

export const API_KEY_SCOPES: readonly ApiKeyScopeOption[] = [
  { value: 'knowledge-bases:read', labelKey: 'settings.apiKeys.scope.knowledgeBasesRead' },
  { value: 'knowledge-bases:write', labelKey: 'settings.apiKeys.scope.knowledgeBasesWrite' },
  { value: 'documents:read', labelKey: 'settings.apiKeys.scope.documentsRead' },
  { value: 'documents:write', labelKey: 'settings.apiKeys.scope.documentsWrite' },
  { value: 'jobs:read', labelKey: 'settings.apiKeys.scope.jobsRead' },
  { value: 'jobs:write', labelKey: 'settings.apiKeys.scope.jobsWrite' },
  { value: 'retrieval:search', labelKey: 'settings.apiKeys.scope.retrievalSearch' },
  { value: 'retrieval:chat', labelKey: 'settings.apiKeys.scope.retrievalChat' },
  { value: 'evaluations:read', labelKey: 'settings.apiKeys.scope.evaluationsRead' },
  { value: 'evaluations:write', labelKey: 'settings.apiKeys.scope.evaluationsWrite' },
  { value: 'api-keys:read', labelKey: 'settings.apiKeys.scope.apiKeysRead' },
  { value: 'api-keys:write', labelKey: 'settings.apiKeys.scope.apiKeysWrite' },
  { value: 'organizations:read', labelKey: 'settings.apiKeys.scope.organizationsRead' },
  { value: 'organizations:write', labelKey: 'settings.apiKeys.scope.organizationsWrite' },
  { value: 'llm-keys:read', labelKey: 'settings.apiKeys.scope.llmKeysRead' },
  { value: 'llm-keys:write', labelKey: 'settings.apiKeys.scope.llmKeysWrite' },
] as const;

/** Portées proposées, libellées dans la locale courante : celles du cœur, puis des greffons. */
export function scopeOptions(t: Translate, locale: Locale): { value: string; label: string }[] {
  return [
    ...API_KEY_SCOPES.map((scope) => ({ value: scope.value, label: t(scope.labelKey) })),
    ...pluginApiKeyScopes().map((scope) => ({ value: scope.value, label: scope.label(locale) })),
  ];
}

/** Portée joker : équivaut à un compte réel sans restriction (défaut backend). */
export const WILDCARD_SCOPE = '*';
