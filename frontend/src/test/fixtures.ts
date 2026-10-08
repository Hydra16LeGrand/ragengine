/**
 * Constructeurs d'objets pour les tests.
 *
 * Deux niveaux, volontairement distincts :
 *  - `precheckReport()` rend la forme **normalisée** (`PrecheckReport`,
 *    camelCase), celle que manipule l'interface ;
 *  - `rawPrecheckReport()`, `documentRead()` et `ingestionJob()` rendent la
 *    forme **transmise par la façade** (snake_case, JSON brut), celle que le
 *    test doit servir quand il fait passer la réponse par `fetch`.
 *
 * Les tests qui reproduisent un enchaînement réseau réel doivent partir du
 * second niveau : sauter la normalisation reviendrait à tester l'interface
 * contre des données qu'elle ne reçoit jamais.
 */
import type { PrecheckReport } from '@/api/precheck';
import type {
  DocumentRead,
  IngestionJobRead,
  JobState,
  KnowledgeBaseMemberRead,
  KnowledgeBaseRead,
  OrganizationMemberRead,
  OrganizationRead,
  PrincipalRead,
  UserRead,
} from '@/api/schemas';

/** Rapport normalisé, supporté et sans particularité, surchargeable champ à champ. */
export function precheckReport(overrides: Partial<PrecheckReport> = {}): PrecheckReport {
  return {
    supported: true,
    format: 'pdf',
    sizeBytes: 128_000,
    pageCount: 12,
    analyzedPageCount: 12,
    textCharacterCount: 24_000,
    isLikelyScanned: false,
    scannedPageRatio: 0,
    tableCount: 0,
    figureCount: 0,
    twoColumnPageCount: 0,
    detectedLanguages: ['fr'],
    error: null,
    ...overrides,
  };
}

/** Rapport tel que la façade le sérialise dans `documents.precheck_report` (JSONB). */
export function rawPrecheckReport(
  overrides: Record<string, unknown> = {},
): NonNullable<DocumentRead['precheck_report']> {
  // Surcharges libres (un test peut servir une forme dégradée) : d'où le cast.
  return {
    supported: true,
    format: 'pdf',
    size_bytes: 128_000,
    page_count: 12,
    analyzed_page_count: 12,
    text_character_count: 24_000,
    is_likely_scanned: false,
    scanned_page_ratio: 0,
    table_count: 0,
    figure_count: 0,
    detected_languages: ['fr'],
    error: null,
    ...overrides,
  } as NonNullable<DocumentRead['precheck_report']>;
}

export const DOCUMENT_ID = '11111111-1111-4111-8111-111111111111';
export const JOB_ID = '22222222-2222-4222-8222-222222222222';
export const KNOWLEDGE_BASE_ID = '33333333-3333-4333-8333-333333333333';

export function documentRead(overrides: Partial<DocumentRead> = {}): DocumentRead {
  return {
    id: DOCUMENT_ID,
    knowledge_base_id: KNOWLEDGE_BASE_ID,
    filename: 'rapport-annuel.pdf',
    content_type: 'application/pdf',
    size_bytes: 128_000,
    checksum_sha256: 'a'.repeat(64),
    storage_uri: 'file:///documents/rapport-annuel.pdf',
    status: 'awaiting_review',
    has_quality_warning: false,
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:05Z',
    ...overrides,
  };
}

export function knowledgeBaseRead(overrides: Partial<KnowledgeBaseRead> = {}): KnowledgeBaseRead {
  return {
    id: KNOWLEDGE_BASE_ID,
    slug: 'base-test',
    name: 'Base de test',
    description: null,
    owner_id: MEMBER_USER_ID,
    organization_id: ORGANIZATION_ID,
    visibility: 'private',
    embedding_model_id: 'bge-m3',
    reranker_model_id: null,
    observability_enabled: false,
    evaluation_enabled: false,
    ragflow_dataset_external_id: null,
    document_count: 0,
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:00Z',
    principal_role: 'owner',
    ...overrides,
  };
}

export function knowledgeBaseMemberRead(
  overrides: Partial<KnowledgeBaseMemberRead> = {},
): KnowledgeBaseMemberRead {
  return {
    knowledge_base_id: KNOWLEDGE_BASE_ID,
    user_id: MEMBER_USER_ID,
    display_name: 'Membre de test',
    role: 'viewer',
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:00Z',
    ...overrides,
  };
}

export function userRead(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: MEMBER_USER_ID,
    email: null,
    display_name: 'Compte éphémère',
    role: 'member',
    is_guest: true,
    is_active: true,
    expires_at: null,
    last_login_at: null,
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:00Z',
    ...overrides,
  };
}

/** Principal courant lu par `SessionProvider` (`GET /v1/me`). */
export function principalRead(overrides: Partial<PrincipalRead> = {}): PrincipalRead {
  return {
    auth_method: 'session',
    user: userRead(),
    role: 'member',
    is_guest: true,
    ...overrides,
  };
}

export const ORGANIZATION_ID = '44444444-4444-4444-8444-444444444444';
export const MEMBER_USER_ID = '55555555-5555-4555-8555-555555555555';

export function organizationRead(overrides: Partial<OrganizationRead> = {}): OrganizationRead {
  return {
    id: ORGANIZATION_ID,
    name: 'Équipe Data',
    slug: 'equipe-data',
    principal_role: 'owner',
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:00Z',
    ...overrides,
  };
}

export function organizationMemberRead(
  overrides: Partial<OrganizationMemberRead> = {},
): OrganizationMemberRead {
  return {
    organization_id: ORGANIZATION_ID,
    user_id: MEMBER_USER_ID,
    role: 'member',
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:00Z',
    ...overrides,
  };
}

export function ingestionJob(
  state: JobState,
  overrides: Partial<IngestionJobRead> = {},
): IngestionJobRead {
  return {
    id: JOB_ID,
    document_id: DOCUMENT_ID,
    knowledge_base_id: KNOWLEDGE_BASE_ID,
    state,
    progress_percent: 0,
    attempts: 1,
    created_at: '2026-08-11T10:00:00Z',
    updated_at: '2026-08-11T10:00:05Z',
    ...overrides,
  };
}
