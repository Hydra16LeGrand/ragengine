/**
 * Ré-export nommé des types de ressources générés depuis l'OpenAPI du backend.
 *
 * `schema.ts` est produit par `npm run generate:api` et n'est jamais édité à la
 * main (cf. `README.md`). Écrire `components['schemas']['KnowledgeBaseRead']`
 * partout serait illisible : ce module est la seule indirection autorisée, et
 * il ne fait que renommer — aucun champ n'y est ajouté, retiré ni élargi.
 *
 * Conséquence voulue : si le backend change un contrat, l'erreur apparaît à la
 * compilation, ici et chez les appelants, pas à l'exécution.
 */
import type { components } from './schema';

type Schemas = components['schemas'];

/* --- Authentification & principal ---------------------------------------- */
export type PrincipalRead = Schemas['PrincipalRead'];
export type UserRead = Schemas['UserRead'];
export type UserCreate = Schemas['UserCreate'];
export type UserRole = Schemas['UserRole'];
export type SessionCreate = Schemas['SessionCreate'];
export type AuthMethod = Schemas['AuthMethod'];

/* --- Clés API -------------------------------------------------------------- */
export type ApiKeyRead = Schemas['ApiKeyRead'];
export type ApiKeyCreate = Schemas['ApiKeyCreate'];
export type ApiKeyCreated = Schemas['ApiKeyCreated'];
export type PageApiKeyRead = Schemas['Page_ApiKeyRead_'];

/* --- Clés LLM personnelles --------------------------------------------------- */
export type UserLlmKeyRead = Schemas['UserLlmKeyRead'];
export type UserLlmKeyCreate = Schemas['UserLlmKeyCreate'];
export type LlmVendor = UserLlmKeyRead['provider'];

/* --- Organisations --------------------------------------------------------- */
export type OrganizationRead = Schemas['OrganizationRead'];
export type OrganizationCreate = Schemas['OrganizationCreate'];
export type OrganizationUpdate = Schemas['OrganizationUpdate'];
export type OrganizationMemberRead = Schemas['OrganizationMemberRead'];
export type OrganizationMemberUpdate = Schemas['OrganizationMemberUpdate'];
export type OrganizationRole = Schemas['OrganizationRole'];
export type PageOrganizationRead = Schemas['Page_OrganizationRead_'];

/* --- Bases de connaissances ---------------------------------------------- */
export type KnowledgeBaseRead = Schemas['KnowledgeBaseRead'];
export type KnowledgeBaseCreate = Schemas['KnowledgeBaseCreate'];
export type KnowledgeBaseUpdate = Schemas['KnowledgeBaseUpdate'];
export type KnowledgeBaseMemberRead = Schemas['KnowledgeBaseMemberRead'];
export type KnowledgeBaseMemberUpdate = Schemas['KnowledgeBaseMemberUpdate'];
export type MemberRole = Schemas['MemberRole'];
export type Visibility = Schemas['Visibility'];
export type PageKnowledgeBaseRead = Schemas['Page_KnowledgeBaseRead_'];

/* --- Documents ------------------------------------------------------------ */
export type DocumentRead = Schemas['DocumentRead'];
export type DocumentUpdate = Schemas['DocumentUpdate'];
export type DocumentStatus = Schemas['DocumentStatus'];
export type PageDocumentRead = Schemas['Page_DocumentRead_'];

/* --- Fragments indexés ------------------------------------------------------ */
export type DocumentChunkRead = Schemas['DocumentChunkRead'];
export type DocumentChunksMeta = Schemas['DocumentChunksMeta'];
export type DocumentChunksPage = Schemas['DocumentChunksPage'];

/* --- Precheck & aiguillage humain ----------------------------------------- */
export type PrecheckDecision = Schemas['PrecheckDecision'];
export type DocumentPrecheckDecisionUpdate = Schemas['DocumentPrecheckDecisionUpdate'];

/* --- Jobs d'ingestion ------------------------------------------------------ */
export type IngestionJobRead = Schemas['IngestionJobRead'];
export type JobState = Schemas['JobState'];
export type JobError = Schemas['JobError'];
export type JobWarning = Schemas['JobWarning'];
export type PageIngestionJobRead = Schemas['Page_IngestionJobRead_'];

/* --- Retrieval ------------------------------------------------------------- */
export type RetrievalSearchRequest = Schemas['RetrievalSearchRequest'];
export type RetrievalSearchResponse = Schemas['RetrievalSearchResponse'];
export type RetrievalResult = Schemas['RetrievalResult'];
export type RetrievalChatRequest = Schemas['RetrievalChatRequest'];
export type ChatTurn = Schemas['ChatTurn'];

/* --- Évaluations ---------------------------------------------------------- */
export type EvaluationCreate = Schemas['EvaluationCreate'];
export type EvaluationRunRead = Schemas['EvaluationRunRead'];
export type EvaluationRunSummary = Schemas['EvaluationRunSummary'];
export type EvaluationRunStatus = Schemas['EvaluationRunStatus'];
export type EvaluationItemRead = Schemas['EvaluationItemRead'];
export type EvaluationsSummaryRead = Schemas['EvaluationsSummaryRead'];
export type KnowledgeBaseEvaluationSummary = Schemas['KnowledgeBaseEvaluationSummary'];
export type EvaluationsOverallSummary = Schemas['EvaluationsOverallSummary'];

/* --- Catalogue de briques ------------------------------------------------- */
export type ProviderCatalog = Schemas['ProviderCatalog'];
export type EmbeddingProviderStatus = Schemas['EmbeddingProviderStatus'];
export type RerankerProviderStatus = Schemas['RerankerProviderStatus'];
export type LLMProviderStatus = Schemas['LLMProviderStatus'];
export type ObservabilityProviderStatus = Schemas['ObservabilityProviderStatus'];

/* --- Infrastructure ------------------------------------------------------- */
export type HealthResponse = Schemas['HealthResponse'];
