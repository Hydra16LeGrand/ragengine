import type { Dictionary } from './fr';

/**
 * Dictionnaire EN — miroir de `fr.ts`.
 * Le type `Dictionary` garantit l'exhaustivité : ajouter une clé côté FR sans
 * la traduire ici casse la compilation.
 */
export const en: Dictionary = {
  'app.name': '{appName}',
  'app.tagline': 'A sovereign, modular, turnkey enterprise RAG platform.',
  'app.skipToContent': 'Skip to main content',

  'nav.home': 'Home',
  'nav.primaryLabel': 'Main navigation',
  'nav.menuOpen': 'Open menu',
  'nav.menuClose': 'Close menu',
  'nav.knowledgeBases': 'Knowledge bases',
  'nav.evaluationsSummary': 'Evaluations',
  'nav.developers': 'Developers',
  'nav.settings': 'Settings',
  'nav.login': 'Sign in',
  'nav.logout': 'Sign out',

  'theme.label': 'Theme',
  'theme.light': 'Light',
  'theme.dark': 'Dark',
  'theme.system': 'System',

  /* --- Forms, shared elements --------------------------------------------- */
  'form.optional': 'Optional',
  'form.cancel': 'Cancel',
  'form.close': 'Close',
  'toast.dismiss': 'Dismiss notification',

  /* --- Authentication ------------------------------------------------------ */
  'auth.modeLabel': 'Sign in or sign up',
  'auth.mode.login': 'Sign in',
  'auth.mode.register': 'Create account',
  'auth.login.title': 'Welcome back',
  'auth.login.subtitle': 'Sign in to get back to your knowledge bases.',
  'auth.register.title': 'Create an account',
  'auth.register.subtitle':
    'An account lets you create knowledge bases, ingest documents and issue API keys.',
  'auth.field.displayName': 'Display name',
  'auth.field.displayNamePlaceholder': 'Alex Morgan',
  'auth.field.email': 'Email address',
  'auth.field.emailPlaceholder': 'you@organisation.com',
  'auth.field.password': 'Password',
  'auth.field.passwordHint': 'At least 8 characters.',
  'auth.field.confirmPassword': 'Confirm password',
  'auth.field.confirmPasswordMismatch': 'The two passwords do not match.',
  'auth.submit.login': 'Sign in',
  'auth.submit.register': 'Create my account',

  /* --- Knowledge bases: list ----------------------------------------------- */
  'kb.eyebrow': 'Knowledge bases',
  'kb.list.title': 'Your knowledge bases',
  'kb.list.titleAnonymous': 'Public knowledge bases',
  'kb.list.description':
    'Each base brings together a corpus, its embedding and reranking models, and its access rules.',
  'kb.list.descriptionAnonymous':
    'The corpora this deployment exposes publicly, browsable without an account.',
  'kb.list.create': 'New knowledge base',
  'kb.list.shared': 'Shared',
  'kb.list.errorTitle': 'Knowledge bases could not be loaded',
  'kb.list.anonymousTitle': 'You are browsing {appName} without signing in',
  'kb.list.anonymousDescription':
    'Only public knowledge bases are visible, read-only. Sign in to create your own and manage their access.',

  'kb.column.base': 'Base',
  'kb.column.visibility': 'Visibility',
  'kb.column.models': 'Providers',
  'kb.column.documents': 'Documents',
  'kb.column.updated': 'Updated',

  'kb.visibility.private': 'Private',
  'kb.visibility.public': 'Public',

  'kb.role.owner': 'Owner',
  'kb.role.editor': 'Editor',
  'kb.role.viewer': 'Viewer',

  'kb.model.embedding': 'Emb',
  'kb.model.reranker': 'Rrk',
  'kb.model.embeddingFull': 'Embedding model',
  'kb.model.rerankerFull': 'Reranker',
  'kb.model.none': 'None',

  'kb.empty.title': 'No knowledge base yet',
  'kb.empty.description':
    'A knowledge base is the corpus your searches query. Create the first one to start ingesting documents.',
  'kb.empty.step1': 'Create a base — your corpus, its AI building blocks.',
  'kb.empty.step2': 'Deposit documents — PDF, Word, Excel, images…',
  'kb.empty.step3': 'Query it — search or chat, cited answers.',
  'kb.empty.anonymousTitle': 'No public knowledge base to show',
  'kb.empty.anonymousDescription':
    'This deployment exposes no public knowledge base right now. Sign in to reach your own.',

  'kb.backToList': 'All knowledge bases',
  'kb.nav.previous': 'Previous base',
  'kb.nav.next': 'Next base',
  'kb.nav.previousTo': 'Previous: {name}',
  'kb.nav.nextTo': 'Next: {name}',
  'kb.nav.boundaryReached': 'End of loaded page — go back to the list to reach further.',

  /* --- Create / edit -------------------------------------------------------- */
  'kb.create.title': 'New knowledge base',
  'kb.create.description':
    'Declare the corpus and the inference providers that will serve it. Documents come next.',
  'kb.create.submit': 'Create knowledge base',
  'kb.create.successTitle': 'Knowledge base created',
  'kb.create.successDescription': '“{name}” is ready to receive documents.',

  'kb.edit.submit': 'Save changes',
  'kb.edit.successTitle': 'Changes saved',
  'kb.edit.successDescription': '“{name}” has been updated.',

  'kb.form.errorTitle': 'The knowledge base could not be saved',
  'kb.form.section.identity': 'Identity',
  'kb.form.identityTitle': 'Name and access',
  'kb.form.identityDescription':
    'What identifies this base in the interface, in the API and for your users.',
  'kb.form.name': 'Name',
  'kb.form.namePlaceholder': 'Legal documentation',
  'kb.form.slug': 'Short identifier',
  'kb.form.slugHint':
    'Derived from the name, editable. Lowercase, digits and dashes — this is what appears in URLs.',
  'kb.form.description': 'Description',
  'kb.form.descriptionHint': 'What the corpus contains, for whoever discovers it.',
  'kb.form.descriptionPlaceholder':
    'Framework agreements, amendments and case law applicable to public procurement.',
  'kb.form.organization': 'Owning organization',
  'kb.form.organizationDefault': 'None',
  'kb.form.organizationHint':
    'The workspace that administers this base (members, billing) — unrelated to its content. ' +
    '“None” is enough in the vast majority of cases — change it only if this base should ' +
    'belong to another one of yours.',
  'kb.form.organizationMissing':
    'You don’t belong to any organization: create one before you can create a knowledge base.',
  'kb.form.visibility': 'Visibility',
  'kb.form.visibilityPrivate': 'Only you and the members you designate can access it.',
  'kb.form.visibilityPublic': 'Anyone, even signed out, can browse this knowledge base.',

  'kb.form.section.providers': 'Inference providers',
  'kb.form.providersTitle': 'Models serving this base',
  'kb.form.providersDescription':
    'Chosen among the providers actually served by this deployment. Unavailable ones cannot be selected.',
  'kb.form.embedding': 'Embedding model',
  'kb.form.embeddingHint': 'It vectorises passages at indexing time as well as at search time.',
  'kb.form.embeddingLocked':
    'The embedding model is frozen after creation: changing it would invalidate every vector already indexed.',
  'kb.form.reranker': 'Reranker',
  'kb.form.rerankerHint': 'Reorders the retrieved passages. Optional — enabled per query.',
  'kb.form.dimensionsSuffix': ' dim.',
  'kb.form.local': 'local',
  'kb.form.remote': 'remote',
  'kb.form.unavailable': 'unavailable',

  'kb.form.section.documents': 'Documents',
  'kb.form.documentsTitle': 'Deposit documents (optional)',
  'kb.form.documentsDescription':
    'Choose or drag your files right now: they stay staged here, then leave just after the base is created — no need to come back to this screen afterwards.',
  'kb.form.documentsPendingHint':
    'Sent automatically once the base is created. You can track and retry each one from its page.',

  'kb.form.advancedTitle': 'Advanced options',
  'kb.form.advancedDescription':
    'Observability, evaluation and fine provider settings. Defaults suit most cases.',
  'kb.form.observability': 'Observability',
  'kb.form.observabilityHint': 'Traces searches on this base to Langfuse.',
  'kb.form.observabilityUnavailable':
    'No observability provider is configured on this deployment: the option will have no effect.',
  'kb.form.evaluation': 'Evaluation',
  'kb.form.evaluationHint': 'Allows generating evaluation sets from this corpus.',
  'kb.form.providerConfig': 'Provider configuration (JSON)',
  'kb.form.providerConfigHint':
    'JSON object passed through to the providers. Leave empty to keep the defaults.',
  'kb.form.configInvalid': 'Invalid JSON: check the syntax.',
  'kb.form.configNotObject': 'The configuration must be a JSON object.',

  /* --- Detail ---------------------------------------------------------------- */
  'kb.detail.section.configuration': 'Configuration',
  'kb.detail.configurationTitle': 'How this base is composed',
  'kb.detail.configurationDescription':
    'The providers and technical references of this corpus, as exposed by the facade.',
  'kb.detail.section.access': 'Access',
  'kb.detail.section.documents': 'Corpus',
  'kb.detail.section.quality': 'Quality',
  'kb.detail.edit': 'Edit',
  'kb.detail.delete': 'Delete',
  'kb.detail.created': 'Created on',
  'kb.detail.updated': 'Updated on',
  'kb.detail.engineDataset': 'Engine dataset',
  'kb.detail.engineDatasetPending': 'Created on first ingestion',
  'kb.detail.notFoundTitle': 'Knowledge base not found',
  'kb.detail.notFoundDescription':
    'This base does not exist, or it is not visible with your current access.',
  'kb.detail.errorTitle': 'This knowledge base cannot be displayed',

  'kb.delete.title': 'Delete this knowledge base?',
  'kb.delete.description': '“{name}” and its configuration will be permanently deleted.',
  'kb.delete.warning':
    'This cannot be undone: {count} document(s) and their indexing will be lost.',
  'kb.delete.confirm': 'Delete permanently',
  'kb.delete.successTitle': 'Knowledge base deleted',
  'kb.delete.errorTitle': 'Deletion failed',

  /* --- Members ---------------------------------------------------------------- */
  'kb.members.title': 'Members and roles',
  'kb.members.description':
    'The owner decides who reads, who feeds and who administers this knowledge base.',
  'kb.members.you': 'You',
  'kb.members.lookupLabel': 'User identifier',
  'kb.members.lookupHint': 'UUID of the account to add.',
  'kb.members.lookupAction': 'Look up',
  'kb.members.roleLabel': 'Role',
  'kb.members.notMember': 'This user is not a member of the knowledge base yet.',
  'kb.members.alreadyListed': 'This user is already listed in the directory above.',
  'kb.members.grant': 'Grant access',
  'kb.members.remove': 'Remove',
  'kb.members.grantedTitle': 'Role saved',
  'kb.members.grantedDescription': '{name} now holds the “{role}” role on this knowledge base.',
  'kb.members.removedTitle': 'Member removed',
  'kb.members.removeConfirmTitle': 'Remove this member?',
  'kb.members.removeConfirmDescription':
    '{name} will lose all access to this knowledge base. You can grant it again by adding them back.',
  'kb.members.removeConfirm': 'Remove member',
  'kb.members.errorTitle': 'Action failed',

  /* --- Documents --------------------------------------------------------------- */
  'kb.documents.title': 'Indexed documents',
  'kb.documents.description':
    'The files ingested into this corpus and their indexing status, as the facade knows them.',
  'kb.documents.emptyTitle': 'No document in this knowledge base',
  'kb.documents.emptyDescription':
    'The corpus is empty. Drop your first files above: each one is analysed before you decide how to index it.',
  'kb.documents.emptyAction': 'Deposit a document',
  'kb.documents.pages': '{count} page(s)',
  'kb.documents.chunkCount': '{count} chunk(s)',
  'kb.documents.open': 'Open',
  'kb.documents.qualityWarning': 'Quality to review',
  'kb.documents.qualityWarningHint':
    'The document is indexed, but extraction produced a suspicious result — open “Show the cause” for details.',
  'kb.documents.cause.show': 'Show the cause',
  'kb.documents.cause.hide': 'Hide the cause',
  'kb.documents.cause.region': 'Ingestion outcome cause for “{name}”',
  'kb.documents.cause.none':
    'The engine recorded neither a failure cause nor a warning for this document.',
  'kb.documents.delete': 'Delete',
  'kb.documents.deleteConfirmTitle': 'Delete this document?',
  'kb.documents.deleteConfirmDescription':
    '“{name}” will be removed from the ingestion engine, the index and storage. This action is irreversible.',
  'kb.documents.deleteConfirm': 'Delete permanently',
  'kb.documents.deletedTitle': 'Document deleted',
  'kb.documents.deleteErrorTitle': 'Could not delete document',
  'kb.documents.loadMore': 'Load more documents',

  // Theme: organize by subject within a base (set by the user, never inferred
  // — unlike languages detected at precheck).
  'kb.documents.theme.none': 'No theme',
  'kb.documents.theme.edit': 'Edit theme',
  'kb.documents.theme.placeholder': 'e.g. legal, finance…',
  'kb.documents.theme.save': 'Save',
  'kb.documents.theme.cancel': 'Cancel',
  'kb.documents.theme.saveErrorTitle': 'Theme not saved',
  'kb.documents.theme.filterLabel': 'Filter by theme',
  'kb.documents.theme.filterAll': 'All themes',

  'kb.documents.chunks.action': 'View chunks',
  'kb.documents.chunks.title': 'Indexed chunks',
  'kb.documents.chunks.summary': '{count} chunk(s) · method “{method}”',
  'kb.documents.chunks.methodUnknown': 'unknown',
  // Human-readable labels for RAGFlow's internal `chunk_method` names —
  // "naive" in particular does not mean "no layout" but names the general
  // parser, the one that honours `layout_recognize` and carries parent/child,
  // as opposed to specialised methods (QA, table…).
  'kb.documents.chunks.method.naive': 'General (adaptive layout)',
  'kb.documents.chunks.method.table': 'Table',
  'kb.documents.chunks.method.picture': 'Image',
  'kb.documents.chunks.method.qa': 'Question & answer',
  'kb.documents.chunks.method.resume': 'Resume',
  'kb.documents.chunks.method.manual': 'Manual (numbered sections)',
  'kb.documents.chunks.method.book': 'Book (chapters)',
  'kb.documents.chunks.method.laws': 'Legal text (articles)',
  'kb.documents.chunks.method.presentation': 'Presentation (slides)',
  'kb.documents.chunks.method.paper': 'Academic paper',
  'kb.documents.chunks.method.email': 'Email',
  'kb.documents.chunks.method.one': 'Whole document (single block)',
  'kb.documents.chunks.method.tag': 'Tags',
  'kb.documents.chunks.errorTitle': 'Chunks unavailable',
  'kb.documents.chunks.emptyTitle': 'No chunks',
  'kb.documents.chunks.emptyDescription':
    'Indexing completed without producing a single chunk — the quality guard already flagged this document.',
  'kb.documents.chunks.number': 'Chunk {number}',
  'kb.documents.chunks.pageSingle': 'Page {page}',
  'kb.documents.chunks.pageRange': 'Pages {start}–{end}',
  'kb.documents.chunks.keywords': 'Keywords',
  'kb.documents.chunks.questions': 'Generated questions',
  'kb.documents.chunks.disabled': 'Disabled',
  'kb.documents.chunks.parent': 'Returned passage',
  'kb.documents.chunks.parentHint':
    'This passage is never matched directly: it is what search returns as soon as one of its locator fragments matches.',
  'kb.documents.chunks.child': 'Locator',
  'kb.documents.chunks.childHint':
    'Short fragment used to pinpoint information. Search never returns it as such — it walks up to the passage that contains it.',
  'kb.documents.chunks.parentChildNote':
    'This document was indexed with sharpened granularity. The list therefore holds two kinds of fragments: short “locator” fragments used to pinpoint information, and the “returned passages” that contain them — the longer ones, which are what search actually returns. Very short fragments here are expected, not a broken split.',
  'kb.documents.chunks.parentsOnly': 'Show returned passages only',
  'kb.documents.chunks.parentsOnlyHint':
    'Hides locator fragments on this page — pagination still reflects the full set.',
  'kb.documents.chunks.noParentsFoundTitle': 'No returned passage',
  'kb.documents.chunks.noParentsFoundDescription':
    'This document has no parent fragments. Clear the filter to see locator fragments.',
  'kb.documents.chunks.pagination': 'Page {page} / {totalPages}',
  'kb.documents.chunks.previous': 'Previous',
  'kb.documents.chunks.next': 'Next',

  'document.status.pending': 'Pending',
  'document.status.processing': 'Processing',
  'document.status.awaitingReview': 'Review required',
  'document.status.indexed': 'Indexed',
  'document.status.failed': 'Failed',
  'document.status.cancelled': 'Cancelled',

  /* --- Evaluations -------------------------------------------------------------- */
  'kb.evaluations.title': 'Corpus evaluations',
  'kb.evaluations.description':
    'A question set is generated from your own documents, then every answer is graded by a judge model. Enough to measure what the base really answers.',
  'kb.evaluations.launch': 'Run an evaluation',

  'kb.evaluations.disabledTitle': 'Evaluation is disabled on this base',
  'kb.evaluations.disabledDescription':
    'A run generates questions and has them graded by an external model: the spend is never incurred without consent. Turn “Evaluation” on in the base’s advanced options to allow it.',

  'kb.evaluations.emptyTitle': 'No evaluation run yet',
  'kb.evaluations.emptyDescription':
    'Start a first run to find out whether the corpus answers accurately, and whether the right documents surface.',
  'kb.evaluations.emptyDescriptionReadOnly':
    'Results will appear here. Starting a run requires the editor role: it consumes generation budget.',

  'kb.evaluations.judgeScore': 'Judge score',
  'kb.evaluations.recall': 'Recall',
  'kb.evaluations.scoreUnavailable': 'not measured',
  'kb.evaluations.items': '{count} question(s)',
  'kb.evaluations.itemsEmpty': 'This run produced no question.',

  'kb.evaluations.item.index': 'Question {index}',
  'kb.evaluations.item.reference': 'Reference answer',
  'kb.evaluations.item.generated': 'Answer from the base',
  'kb.evaluations.item.rationale': 'Judge rationale',
  'kb.evaluations.item.rank': 'Rank {rank}',
  'kb.evaluations.item.rankMissing': 'Outside top-k',
  'kb.evaluations.item.rankTitle':
    'Position of the source document among the passages retrieved for this question.',
  'kb.evaluations.item.judge': 'Judge {score}',

  'kb.evaluations.dialogTitle': 'Run an evaluation',
  'kb.evaluations.dialogDescription':
    'One question is generated per sampled document, the base answers it, then a judge grades the gap with the reference answer.',
  'kb.evaluations.sampleSize': 'Documents sampled',
  'kb.evaluations.sampleSizeHint':
    'Up to 20: immediate run, one question generated per document. Beyond that: runs in the background (track its progress in the history below).',
  'kb.evaluations.sampleSizeHintAsync':
    'Beyond 20 documents, the run executes in the background: the dialog closes right away, track its progress in the history below.',
  'kb.evaluations.model': 'Generation and judge model',
  // Swapped depending on whether the selected provider has a personal key on
  // file for the account launching the run (`settings.llmKeys`): without
  // one, this choice is silently replaced by a deployment model (addendum
  // 2026-09-21 to ADR-054, `resolve_llm_funding`, reused as-is by
  // `EvaluationService`).
  'kb.evaluations.modelAppliesHint':
    'Personal {vendor} key configured: this choice applies. The same model writes the questions and grades the answers.',
  'kb.evaluations.modelOverriddenHint':
    'Without a personal {vendor} key, a deployment model is used instead of this choice. The same model writes the questions and grades the answers.',
  'kb.evaluations.noProvider':
    'This deployment has no generation provider configured. Evaluation needs one: with no API key, no run is possible.',
  'kb.evaluations.costWarning':
    'Each question triggers three model calls (generation, answer, grading) and consumes real budget. Start small.',
  'kb.evaluations.submit': 'Start the run',

  'kb.evaluations.runningTitle': 'Evaluation in progress…',
  'kb.evaluations.runningHint':
    '{count} document(s) sampled: writing the questions, answering them, then grading. The operation is synchronous and can take several tens of seconds — stay on this screen.',
  'kb.evaluations.submittingTitle': 'Submitting the run…',
  'kb.evaluations.submittingHint':
    '{count} document(s) sampled: the run executes in the background, this submission only takes a moment.',
  'kb.evaluations.successTitle': 'Evaluation complete',
  'kb.evaluations.successDescription': '{count} question(s) generated and graded.',
  'kb.evaluations.acceptedTitle': 'Run accepted',
  'kb.evaluations.acceptedDescription':
    '{count} document(s) sampled, running in the background. Track its progress in the history below.',

  'kb.evaluations.status.pending': 'In progress',
  'kb.evaluations.status.failed': 'Failed',

  'kb.evaluations.itemsPendingTitle': 'Generating…',
  'kb.evaluations.itemsPendingHint':
    'This run executes in the background; its questions will all appear here together once it resolves.',
  'kb.evaluations.itemsFailed':
    'This run failed before producing any question. Start a new one from “Run an evaluation”.',

  'kb.evaluations.runErrorTitle': 'The evaluation could not be started',
  'kb.evaluations.hint403':
    'Starting a run requires the editor role on this base: ask its owner for it.',
  'kb.evaluations.hint409':
    'No indexed document is available. Upload and index at least one document before evaluating.',
  'kb.evaluations.hint422':
    'Check that evaluation is enabled on the base and that the chosen model is properly configured in the deployment — or lower the sample size if this deployment’s asynchronous cap was exceeded.',

  /* --- Evaluations: cross-KB overview (Phase 12, Step 5) --------------------- */
  'evaluationsSummary.eyebrow': 'Overview',
  'evaluationsSummary.title': 'Evaluations',
  'evaluationsSummary.description':
    'Recall and judge score aggregated across every base you can access — not just the one you are viewing.',

  'evaluationsSummary.overallTitle': 'Overall',
  'evaluationsSummary.overallDescription':
    'Computed over every question across every measured base — never an average of each base taken separately.',
  'evaluationsSummary.statBases': 'Bases measured',
  'evaluationsSummary.statRuns': 'Runs',
  'evaluationsSummary.statItems': 'Questions',
  'evaluationsSummary.recallAtK': 'Recall@{k}',
  'evaluationsSummary.mrr': 'MRR',
  'evaluationsSummary.judgeScore': 'Judge score',
  'evaluationsSummary.scoreUnavailable': 'not measured',

  'evaluationsSummary.tableTitle': 'Breakdown by base',
  'evaluationsSummary.tableDescription':
    'One row per measured base — click through to its full history.',
  'evaluationsSummary.rowRuns': '{count} run(s)',
  'evaluationsSummary.rowItems': '{count} question(s)',

  'evaluationsSummary.emptyTitle': 'No evaluation measured yet',
  'evaluationsSummary.emptyDescription':
    'Launch an evaluation from a base to see its measurement appear here, and in the overview.',

  /* --- Ingestion: upload -------------------------------------------------------- */
  'ingest.section': 'Ingestion',
  'ingest.title': 'Ingest documents',
  'ingest.description':
    'Every file is analysed before indexing. You then decide how it should be split, document by document.',
  'ingest.dropzone.label': 'Drop your documents here',
  'ingest.dropzone.hint':
    'PDF, Word, Excel, PowerPoint, CSV/text, emails and images (OCR). Several at once: each one follows its own course.',
  'ingest.dropzone.browse': 'Browse your files',

  /* --- Ingestion: staging before confirmation --------------------------------- */
  'ingest.staging.title': 'Staged files',
  'ingest.staging.launch': 'Start ingestion',
  'ingest.staging.replaces': 'Will replace “{name}”',

  'ingest.queue.title': 'Ingestion queue',
  'ingest.queue.count': '{count} file(s)',
  'ingest.queue.clear': 'Clear finished',
  'ingest.item.remove': 'Remove from queue',
  'ingest.item.retry': 'Try again',
  'ingest.item.reviewAction': 'Set up indexing',
  'ingest.item.replacesNote': 'Replaces “{name}”',

  'ingest.state.pending': 'Waiting',
  'ingest.state.deleting': 'Removing the previous version',
  'ingest.state.deleteFailed': 'Removal failed',
  'ingest.state.uploading': 'Uploading',
  'ingest.state.uploadFailed': 'Upload failed',
  'ingest.state.analyzing': 'Preliminary analysis',
  'ingest.state.awaitingReview': 'Your review is required',
  'ingest.state.processing': 'Indexing',
  'ingest.state.completed': 'Indexed',
  'ingest.state.failed': 'Failed',
  'ingest.state.cancelled': 'Cancelled',

  'ingest.uploadErrorTitle': 'This file could not be uploaded',
  'ingest.deleteFailedTitle': 'The previous version could not be removed',
  'ingest.trackingErrorTitle': 'Tracking unavailable',
  'ingest.trackingErrorDescription':
    'The document was received, but the state of its processing could not be read back.',
  'ingest.noJobTitle': 'Document received, without a tracking job',
  'ingest.noJobDescription':
    'The facade accepted the file without attaching an ingestion job. Reload the page to find its state again.',
  'ingest.completedTitle': 'Document indexed',
  'ingest.completedDescription': '“{name}” can now be queried from the Playground.',
  'ingest.failedTitle': 'Ingestion failed',
  'ingest.error.retryable': 'Trying again has a chance of succeeding.',
  'ingest.attempts': 'Attempt {count}',

  /* --- Ingestion: replacing an existing document ------------------------------ */
  'ingest.replace.confirmTitle': 'Replace existing documents?',
  'ingest.replace.confirmDescription':
    'These files share their name with a document already indexed in this base. The previous version will be removed before the new one is indexed.',
  'ingest.replace.item': '“{incoming}” will replace “{existing}”',
  'ingest.replace.confirm': 'Confirm replacement',

  /* --- Ingestion: file already present, matched by checksum (ADR-014) -------- */
  'ingest.item.duplicateBadge': 'Already present',
  'ingest.item.duplicateNote':
    'Same content as “{name}”, already ingested into this knowledge base: no new document was created, and the existing one is what is tracked below. The name of the file you dropped will therefore not appear in the list.',

  /* --- Ingestion: batch-accept precheck suggestions ---------------------------- */
  'ingest.bulkAccept.action': 'Apply default suggestions to all',
  'ingest.bulkAccept.description':
    '{count} documents are awaiting your review, each with a setting already proposed — layout analysis included on PDFs, so a slower indexing run.',
  'ingest.bulkAccept.progress': 'Accepting… {settled}/{total}',
  'ingest.bulkAccept.successTitle': 'Suggestions accepted',
  'ingest.bulkAccept.successDescription': '{count} document(s) started indexing.',
  'ingest.bulkAccept.partialFailureTitle': 'Batch acceptance partially failed',
  'ingest.bulkAccept.partialFailureDescription':
    '{failed} of {total} document(s) could not be accepted automatically. Use individual review for those.',

  'ingest.bulkRefuse.action': 'Decline suggestions for all',
  'ingest.bulkRefuse.hint':
    'Starts indexing the pending documents with none of the proposed options: faster, but fragments lose the document’s reading order. This does not cancel ingestion — to drop a document, use “Cancel ingestion” on its row.',
  'ingest.bulkRefuse.successTitle': 'Suggestions declined',

  /* --- Ingestion: extraction quality guard (ADR-024) ------------------------- */
  'ingest.qualityWarningTitle': 'Quality to review',
  'ingest.qualityWarningIntro': 'Indexing succeeded, but extraction produced a suspicious result:',
  'ingest.qualityWarning.code.zero_chunks': 'No fragment produced',
  'ingest.qualityWarning.code.degenerate_content': 'Mostly empty fragments',
  'ingest.qualityWarning.code.low_source_coverage': 'Source text loss',
  'ingest.qualityWarning.metric.chunk_count': 'Fragments produced',
  'ingest.qualityWarning.metric.degenerate_chunk_pct': 'Share of degenerate fragments',
  'ingest.qualityWarning.metric.significant_char_total': 'Total significant characters',
  'ingest.qualityWarning.metric.source_coverage': 'Source text found in the fragments',
  'ingest.qualityWarning.advice.zero_chunks':
    'The document is listed but nothing will ever be retrieved from it: the page is empty, unreadable, or its text could not be extracted. Open the file to check, then reindex it with layout analysis enabled if its content is an image.',
  'ingest.qualityWarning.advice.degenerate_content':
    'Most fragments contain almost no usable text. Reindexing with layout analysis enabled often gives a better result on scanned documents.',
  'ingest.qualityWarning.advice.low_source_coverage':
    'A large share of the file’s text is missing from the indexed fragments: those passages will not be found by search. Review the fragments before relying on this document.',

  /* --- Ingestion: engine failures translated into actionable findings -------- */
  'ingest.cause.rawToggle': 'Technical message from the engine',
  'ingest.cause.quotaExhausted.title': 'Image description service quota exhausted',
  'ingest.cause.quotaExhausted.advice':
    'The AI provider that describes the images in the document refused further requests. Try again later — the quota refills —, ingest documents one at a time rather than in bulk, or configure a paid provider in the knowledge base settings.',
  'ingest.cause.unsupportedFormat.title': 'Format not supported by the engine',
  'ingest.cause.unsupportedFormat.advice':
    'The ingestion engine cannot read the format {format} yet. Convert the document to a supported format (PDF, DOCX, TXT, Markdown…) before uploading it again.',
  'ingest.cause.unsupportedFormat.formatFallback': 'of this file',
  'ingest.cause.unreadableImage.title': 'Unreadable image file',
  'ingest.cause.unreadableImage.advice':
    'The engine did not recognise this file as a usable image: it is most likely truncated, corrupted, or in a format variant it cannot decode. Open it to check, then upload a re-saved version.',

  /* --- Ingestion: job state machine --------------------------------------------- */
  'job.progress.label': 'Ingestion progress',
  'job.step.precheck': 'Analysis',
  'job.step.review': 'Review',
  'job.step.chunking': 'Chunking',
  'job.step.embedding': 'Embedding',
  'job.step.indexing': 'Indexing',

  'job.state.queued': 'Queued',
  'job.state.prechecking': 'Reading the document',
  'job.state.awaiting_review': 'Waiting for you',
  'job.state.chunking': 'Splitting into passages',
  'job.state.embedding': 'Computing vectors',
  'job.state.indexing': 'Writing to the index',
  'job.state.completed': 'Done',
  'job.state.failed': 'Interrupted',
  'job.state.cancelled': 'Cancelled',

  'job.cancel.action': 'Cancel ingestion',
  'job.cancel.title': 'Cancel this ingestion?',
  'job.cancel.description': '“{name}” will not be indexed. This action is final.',
  'job.cancel.confirm': 'Confirm cancellation',
  'job.cancel.errorTitle': 'Cancellation failed',

  /* --- Precheck: complexity report ---------------------------------------------- */
  'precheck.analyzingTitle': 'Analysing the document',
  'precheck.analyzingDescription':
    '{appName} spots tables, scanned pages and languages before letting you decide.',
  'precheck.reportTitle': 'Detected complexity',
  'precheck.reportDescription':
    'What the preliminary analysis found. These are clues, not a verdict: you may overrule them.',
  'precheck.report.detailsToggle': 'See analysis details',

  'precheck.format.pdf': 'PDF',
  'precheck.format.spreadsheet': 'Spreadsheet',
  'precheck.format.presentation': 'Slide deck',
  'precheck.format.unsupported': 'Unanalysed format',

  'precheck.metric.pages': 'Pages',
  'precheck.metric.pagesValue': '{count} page(s)',
  'precheck.metric.pagesAnalyzed': '{count} analysed',
  'precheck.metric.pagesUnknown': 'Not paginated',
  'precheck.metric.tables': 'Tables',
  'precheck.metric.tablesSampled': 'Tables — across the {count} analysed page(s)',
  'precheck.metric.tablesValue': '{count} table(s) found',
  'precheck.metric.tablesNone': 'No table found',
  'precheck.metric.columns': 'Layout',
  'precheck.metric.columnsValue': 'Side-by-side columns on {count} page(s)',
  'precheck.metric.columnsNone': 'Single-column text',
  'precheck.metric.figures': 'Figures',
  'precheck.metric.figuresSampled': 'Figures — across the {count} analysed page(s)',
  // Presence/absence only, never a count: on a mostly-scanned PDF, each full
  // page counts as "one figure" (`figure_count`,
  // `backend/app/ingestion/precheck/schemas.py`) — technically correct for
  // the precheck, but misleading against the intuitive sense of "an image".
  'precheck.metric.figuresDetected': 'Images detected',
  'precheck.metric.figuresNone': 'No image detected',
  'precheck.metric.text': 'Extractable text',
  'precheck.metric.textValue': '{count} characters read',
  'precheck.metric.textScanned': 'Text to be recovered by optical recognition',
  'precheck.metric.textScannedDetail': 'Expected on a scanned document: the text layer is empty.',
  'precheck.metric.scanned': 'Scanned document detected',
  'precheck.metric.scannedRatio': '{percent} of analysed pages carry no text layer',
  'precheck.metric.scannedNone': 'Text layer present',
  'precheck.metric.scannedNoneDetail': 'The text is readable without optical recognition.',
  'precheck.metric.languages': 'Detected languages',
  'precheck.metric.languagesEmpty': 'Too little text to decide',

  'precheck.degradedTitle': 'Incomplete preliminary analysis',
  'precheck.degradedDescription':
    'The clues below are missing: the ingestion engine will take over with its inherited settings.',
  'precheck.error.unsupported_format':
    '{appName} cannot analyse this format ahead of the ingestion engine.',
  'precheck.error.corrupted_file':
    'The content does not match the declared format, or the file is empty.',
  'precheck.error.extraction_failed':
    'The format is recognised but extraction failed: truncated or protected file.',
  'precheck.error.unknown': 'The preliminary analysis did not complete.',

  /* --- Precheck: routing decision ------------------------------------------------ */
  'precheck.decision.title': 'How should this document be indexed?',
  'precheck.decision.description':
    'Three settings are available. The first two are pre-selected — one from the analysis, the other because it is the best default; the last one is always yours to enable.',
  'precheck.decision.tables': 'Optimise for financial tables',
  'precheck.decision.tablesHint':
    'Splits the document row by row, suited to tabular data. A short value like “03-04” may then be read as a date.',
  'precheck.decision.ocr': 'Analyse the document’s layout',
  'precheck.decision.ocrHint':
    'Keeps the real page order and stops cutting sentences in half — sharper search. Slightly slower to index.',
  'precheck.decision.parentChild': 'Sharpen search granularity',
  'precheck.decision.parentChildHint':
    'Matches very precise passages while still returning their full context. Produces more fragments behind the scenes, without changing what you read.',
  'precheck.decision.ocrColumnsWarning':
    'This document has side-by-side columns. Layout analysis reads them line by line, jumping from one column to the other, which interleaves the sentences of both; without it, each column is read as one block. That is why it is not proposed here.',
  'precheck.decision.ocrColumnsWithTables':
    'This document has side-by-side columns **and** tables. It is therefore given a layout analysis that handles both: each column is read as one block, tables are still recognised, and every fragment keeps its page of origin. There is nothing for you to arbitrate here — indexing takes about as long as the usual analysis.',
  'precheck.advanced.title': 'Advanced settings',
  'precheck.advanced.description':
    'For those who already know how this document should be split. Nothing to open otherwise: the settings above are enough.',
  'precheck.advanced.unavailable':
    'No fine-grained chunking setting is offered for this document: its type or layout does not call for one.',
  'precheck.advanced.chunkMethod': 'Chunking method',
  'precheck.advanced.chunkMethodHint':
    'By default the method is chosen from the document analysis. Only the methods measured as useful on this kind of document are offered here; the others are offered nowhere.',
  'precheck.advanced.method.automatic': 'Automatic (recommended)',
  'precheck.advanced.method.naive': 'Paragraphs — the usual split',
  'precheck.advanced.method.laws': 'Numbered articles — for regulatory or contractual text',
  'precheck.advanced.method.paper': 'Article sections — for a scientific publication',
  'precheck.advanced.method.manual': 'Manual sections — for a guide or a procedure',
  'precheck.advanced.method.qa': 'Questions / answers — one fragment per pair',
  'precheck.decision.suggested': 'Suggested by the analysis',
  'precheck.decision.byDefault': 'Proposed by default',
  'precheck.decision.appliedChunkMethod': 'Forced chunking',
  'precheck.decision.submit': 'Start indexing',
  'precheck.decision.errorTitle': 'The decision could not be submitted',
  'precheck.decision.appliedTitle': 'Indexing started',
  'precheck.decision.appliedDescription': 'Chunking of “{name}” has begun.',
  'precheck.decision.applied': 'Settings applied',
  'precheck.decision.appliedTables': 'Table chunking',
  'precheck.decision.appliedOcr': 'Layout analysis',
  'precheck.decision.appliedParentChild': 'Sharpened granularity',
  'precheck.decision.appliedDefault': 'Engine defaults',

  /* --- Playground ----------------------------------------------------------------- */
  'playground.eyebrow': 'Playground',
  'playground.open': 'Open the Playground',
  'playground.title': 'Query “{name}”',
  'playground.description':
    'Put the corpus to the test: retrieve raw passages, or have a model answer while citing its sources.',
  'playground.backToBase': 'Back to the knowledge base',
  'playground.noIndexed.title': 'Nothing to query yet',
  'playground.noIndexed.description':
    'No document is indexed in this knowledge base yet. Deposit some from its page to query it here.',
  'playground.mode.label': 'Query mode',
  'playground.mode.search': 'Search',
  'playground.mode.chat': 'Chat',

  'playground.settings.title': 'Retrieval settings',
  'playground.settings.description':
    'Shared by both modes: chat relies on exactly the same hybrid search as Search mode.',
  'playground.settings.topK': 'Passages retrieved',
  'playground.settings.topKHint': 'How many fragments the engine returns, between 1 and 50.',
  'playground.settings.rerank': 'Reranking',
  'playground.settings.rerankHint':
    'Reorders passages with the base’s reranker. More accurate, slower.',
  'playground.settings.rerankUnavailable':
    'This knowledge base declares no reranker: the facade would reject the request.',
  'playground.settings.threshold': 'Similarity threshold',
  'playground.settings.thresholdHint':
    'Empty = engine default. Careful: turning reranking on changes the score scale, and a threshold tuned without it can empty the results.',
  'playground.settings.thresholdPlaceholder': 'automatic',
  'playground.settings.documents': 'Narrow the scope',
  'playground.settings.documentsAll': 'Whole corpus',
  'playground.settings.documentsSelected': '{count} document(s)',
  'playground.settings.documentsEmpty': 'No indexed document yet.',
  'playground.settings.documentsHint':
    'Only indexed documents can be queried. With no selection, the whole corpus is swept.',
  'playground.settings.documentsTruncated':
    'This base has more indexed documents than this screen can list; only the first {count} are offered here.',

  'playground.search.queryLabel': 'Your query',
  'playground.search.queryPlaceholder':
    'What are the early termination conditions of the framework agreement?',
  'playground.search.submit': 'Search',
  'playground.search.resultsTitle': 'Retrieved passages',
  'playground.search.resultsCount': '{count} passage(s)',
  'playground.search.emptyTitle': 'Ask your first question',
  'playground.search.emptyDescription':
    'Retrieved passages will appear here, with the breakdown of the scores that surfaced them.',
  'playground.search.noResultsTitle': 'No passage retained',
  'playground.search.noResultsDescription':
    'The engine found nothing above the threshold. Rephrase, raise the number of passages, or lower the threshold.',
  'playground.search.errorTitle': 'The search failed',

  'playground.result.rank': 'Passage {rank}',
  'playground.result.expand': 'Show the full passage',
  'playground.result.collapse': 'Collapse the passage',
  'playground.result.page': 'p. {page}',
  'playground.result.pageRange': 'pp. {start}-{end}',
  'playground.result.pageLabel': 'Page {page} of the document',
  'playground.result.pageRangeLabel': 'Pages {start} to {end} of the document',
  'playground.score.combined': 'Combined',
  'playground.score.vector': 'Vector',
  'playground.score.term': 'Terms',
  'playground.score.legend':
    'The combined score arbitrates between closeness of meaning (vector) and presence of the query words (terms).',

  'playground.chat.model': 'Generation model',
  // Swapped depending on whether the selected provider has a personal key on
  // file (`settings.llmKeys`): without one, this choice is silently replaced
  // by a deployment model (addendum 2026-09-21 to ADR-054,
  // `resolve_llm_funding`).
  'playground.chat.modelAppliesHint': 'Personal {vendor} key configured: this choice applies.',
  'playground.chat.modelOverriddenHint':
    'Without a personal {vendor} key, a deployment model is used instead of this choice.',
  'playground.chat.noProviderTitle': 'No generation provider available',
  'playground.chat.noProviderDescription':
    'This deployment holds no API key for Anthropic or OpenAI. Answer generation is externalised by design: with no key configured, chat stays idle.',
  'playground.chat.noProviderHint':
    'Search mode, on the other hand, runs entirely locally: it needs no external provider.',
  'playground.chat.switchToSearch': 'Switch to Search mode',
  'playground.chat.emptyTitle': 'Ask for what you are looking for',
  'playground.chat.emptyDescription':
    'The answer will rely solely on passages retrieved from this corpus, each cited by its number.',
  'playground.chat.inputLabel': 'Your question',
  'playground.chat.placeholder': 'Ask your question about this corpus…',
  'playground.chat.send': 'Send',
  'playground.chat.stop': 'Stop',
  'playground.chat.reset': 'New conversation',
  'playground.chat.you': 'You',
  'playground.chat.assistant': 'Answer',
  'playground.chat.retrieving': 'Retrieving passages…',
  'playground.chat.generating': 'Writing…',
  'playground.chat.sources': 'Cited sources',
  'playground.chat.sourcesCount': '{count} passage(s) handed to the model',
  'playground.chat.sourceLabel': 'Go to source {index}',
  'playground.chat.errorTitle': 'Generation interrupted',
  'playground.chat.refusedTitle': 'Request rejected',
  'playground.chat.emptyAnswer': 'The model produced no text.',

  /* --- Settings: shared shell -------------------------------------------- */
  'settings.eyebrow': 'Settings',
  'settings.pageTitle': 'Account settings',
  'settings.tabsLabel': 'Settings sections',
  'settings.tabs.apiKeys': 'API keys',
  'settings.tabs.organizations': 'Organizations',
  'settings.tabs.llmKeys': 'Personal LLM keys',

  /* --- Settings: API keys ---------------------------------------------------- */
  'settings.apiKeys.title': 'Your API keys',
  'settings.apiKeys.description':
    'Each key authenticates an integration on your behalf. The plain-text token is only ever shown once, at creation time.',
  'settings.apiKeys.create': 'New key',
  'settings.apiKeys.emptyTitle': 'No API keys yet',
  'settings.apiKeys.emptyDescription':
    'Create a key to authenticate your scripts with the "Authorization: Bearer <key>" header.',

  'settings.apiKeys.created': 'Created on {date}',
  'settings.apiKeys.lastUsed': 'Last used on {date}',
  'settings.apiKeys.neverUsed': 'Never used',
  'settings.apiKeys.expires': 'Expires on {date}',
  'settings.apiKeys.status.active': 'Active',
  'settings.apiKeys.status.revoked': 'Revoked',

  'settings.apiKeys.revoke.action': 'Revoke',
  'settings.apiKeys.revoke.title': 'Revoke this key?',
  'settings.apiKeys.revoke.description': '"{name}" will no longer authenticate any request.',
  'settings.apiKeys.revoke.warning':
    'This action is irreversible. Any integration using this key will lose access immediately.',
  'settings.apiKeys.revoke.confirm': 'Revoke permanently',
  'settings.apiKeys.revoke.successTitle': 'Key revoked',
  'settings.apiKeys.revoke.errorTitle': 'Revocation failed',

  'settings.apiKeys.create.title': 'New API key',
  'settings.apiKeys.create.description':
    'The full token will only be shown once, right after creation.',
  'settings.apiKeys.create.nameLabel': 'Name',
  'settings.apiKeys.create.namePlaceholder': 'CI integration, sync script…',
  'settings.apiKeys.create.scopesLabel': 'Scopes',
  'settings.apiKeys.create.scopeFull': 'Full access',
  'settings.apiKeys.create.scopeCustom': 'Custom scopes',
  'settings.apiKeys.create.scopeFullHint':
    'The key can call the entire API, just like your account.',
  'settings.apiKeys.create.scopeCustomHint':
    'Select only the scopes this integration actually needs.',
  'settings.apiKeys.create.scopeCustomEmpty': 'Select at least one scope, or choose "Full access".',
  'settings.apiKeys.create.submit': 'Create key',
  'settings.apiKeys.create.errorTitle': 'The key could not be created',
  'settings.apiKeys.create.revealTitle': 'Key created',
  'settings.apiKeys.create.revealDescription': 'Copy this token now: it will never be shown again.',
  'settings.apiKeys.create.revealWarning':
    'Once this window is closed, this token disappears from the interface for good — only revoking the key remains possible, never reading it again.',
  'settings.apiKeys.create.copy': 'Copy',
  'settings.apiKeys.create.copied': 'Copied',
  'settings.apiKeys.create.copyErrorTitle':
    'Automatic copy failed: select and copy the token manually.',
  'settings.apiKeys.create.done': "I've copied the key, done",

  'settings.apiKeys.scope.knowledgeBasesRead': 'Knowledge bases — read',
  'settings.apiKeys.scope.knowledgeBasesWrite': 'Knowledge bases — write',
  'settings.apiKeys.scope.documentsRead': 'Documents — read',
  'settings.apiKeys.scope.documentsWrite': 'Documents — write',
  'settings.apiKeys.scope.jobsRead': 'Ingestion jobs — read',
  'settings.apiKeys.scope.jobsWrite': 'Ingestion jobs — write',
  'settings.apiKeys.scope.retrievalSearch': 'Retrieval — search',
  'settings.apiKeys.scope.retrievalChat': 'Retrieval — chat',
  'settings.apiKeys.scope.evaluationsRead': 'Evaluations — read',
  'settings.apiKeys.scope.evaluationsWrite': 'Evaluations — write',
  'settings.apiKeys.scope.apiKeysRead': 'API keys — read',
  'settings.apiKeys.scope.apiKeysWrite': 'API keys — write',
  'settings.apiKeys.scope.organizationsRead': 'Organizations — read',
  'settings.apiKeys.scope.organizationsWrite': 'Organizations — write',
  'settings.apiKeys.scope.llmKeysRead': 'Personal LLM keys — read',
  'settings.apiKeys.scope.llmKeysWrite': 'Personal LLM keys — write',

  /* --- Settings: personal LLM keys ------------------------------------------- */
  'settings.llmKeys.title': 'Your personal LLM keys',
  'settings.llmKeys.description':
    'Used ahead of the service key for that provider, on the retrieval chat. The key itself is never shown again after it is saved.',

  'settings.llmKeys.vendor.anthropic': 'Anthropic',
  'settings.llmKeys.vendor.openai': 'OpenAI',
  'settings.llmKeys.vendor.groq': 'Groq',

  'settings.llmKeys.status.active': 'Personal key active',
  'settings.llmKeys.status.none': 'No personal key',
  'settings.llmKeys.updated': 'Saved on {date}',

  'settings.llmKeys.add': 'Add a key',
  'settings.llmKeys.replace': 'Replace',
  'settings.llmKeys.keyLabel': 'API key',
  'settings.llmKeys.keyPlaceholder': 'Paste the provider’s key',
  'settings.llmKeys.save': 'Save',

  'settings.llmKeys.set.successTitle': 'Key saved',
  'settings.llmKeys.set.errorTitle': 'Saving failed',

  'settings.llmKeys.remove.action': 'Remove',
  'settings.llmKeys.remove.title': 'Remove this personal key?',
  'settings.llmKeys.remove.description':
    'Chat calls for "{vendor}" will fall back to the service key, if one is configured.',
  'settings.llmKeys.remove.warning': 'This action is irreversible.',
  'settings.llmKeys.remove.confirm': 'Remove permanently',
  'settings.llmKeys.remove.successTitle': 'Key removed',
  'settings.llmKeys.remove.errorTitle': 'Removal failed',

  /* --- Settings: organizations --------------------------------------------- */
  'settings.organizations.title': 'Your organizations',
  'settings.organizations.description':
    'An organization groups accounts to share knowledge bases as a team.',
  'settings.organizations.create': 'New organization',
  'settings.organizations.emptyTitle': 'No organizations',
  'settings.organizations.emptyDescription':
    'Create an organization to share knowledge bases with other accounts.',
  'settings.organizations.role.owner': 'Owner',
  'settings.organizations.role.member': 'Member',
  'settings.organizations.backToList': 'Back to organizations',
  'settings.organizations.notFoundTitle': 'Organization not found',
  'settings.organizations.notFoundDescription':
    'This organization does not exist, or you are not a member of it.',
  'settings.organizations.errorTitle': 'This organization could not be loaded',

  'settings.organizations.create.title': 'New organization',
  'settings.organizations.create.description':
    'A unique identifier (the "slug") will be derived automatically from the name.',
  'settings.organizations.create.nameLabel': 'Name',
  'settings.organizations.create.namePlaceholder': 'Data team, Acme Corp…',
  'settings.organizations.create.submit': 'Create organization',
  'settings.organizations.create.successTitle': 'Organization created',
  'settings.organizations.create.successDescription': '"{name}" is ready.',
  'settings.organizations.create.errorTitle': 'The organization could not be created',

  'settings.organizations.detail.edit': 'Rename',
  'settings.organizations.detail.delete': 'Delete',

  'settings.organizations.edit.submit': 'Save',
  'settings.organizations.edit.successTitle': 'Organization renamed',
  'settings.organizations.edit.successDescription': 'It is now called "{name}".',
  'settings.organizations.edit.errorTitle': 'Renaming failed',

  'settings.organizations.delete.title': 'Delete this organization?',
  'settings.organizations.delete.description':
    '"{name}" and its member directory will be permanently deleted.',
  'settings.organizations.delete.warning':
    'Refused while the organization still owns knowledge bases — remove or transfer them first.',
  'settings.organizations.delete.confirm': 'Delete permanently',
  'settings.organizations.delete.successTitle': 'Organization deleted',
  'settings.organizations.delete.errorTitle': 'Deletion failed',

  'settings.organizations.section.members': 'Access',
  'settings.organizations.members.title': 'Members',
  'settings.organizations.members.description':
    'Visible to any member. Adding, changing a role, or removing a member is restricted to the owner.',
  'settings.organizations.members.roleLabel': 'Role',
  'settings.organizations.members.addLabel': 'User ID',
  'settings.organizations.members.addHint': 'UUID of the account to add.',
  'settings.organizations.members.add': 'Add',
  'settings.organizations.members.notFound': 'No account matches this identifier.',
  'settings.organizations.members.grantedTitle': 'Role saved',
  'settings.organizations.members.grantedDescription': 'The member now has the "{role}" role.',
  'settings.organizations.members.removedTitle': 'Member removed',
  'settings.organizations.members.errorTitle': 'Action failed',
  'settings.organizations.members.remove': 'Remove',
  'settings.organizations.members.removeConfirmTitle': 'Remove this member?',
  'settings.organizations.members.removeConfirmDescription':
    'They will immediately lose access to the organization and its knowledge bases.',
  'settings.organizations.members.removeConfirm': 'Remove member',

  /* --- API health ---------------------------------------------------------------- */
  'health.checking': 'Checking…',
  'health.online': 'API available',
  'health.offline': 'API unreachable',
  'health.version': 'Version {version}',

  'error.unexpected': 'An unexpected error occurred.',
  'error.network': 'The server could not be reached.',
  'error.textTooLong': 'This text is too long: {max} characters at most.',
  'error.conversationTooLong':
    'The conversation has grown too long to be sent. Please start a new one.',
  'error.inputLimitReached': '{max}-character limit reached.',

  'notFound.title': 'Page not found',
  'notFound.description': 'The requested address matches no {appName} screen.',
  'notFound.back': 'Back to knowledge bases',

  'developers.eyebrow': 'Developers',
  'developers.title': 'Integrate {appName} via the API',
  'developers.description':
    'Everything you need to ingest documents and query your knowledge bases from your own code. The exhaustive, always up-to-date reference for every endpoint lives in Redoc — this guide covers what you need to get started.',
  'developers.referenceCta': 'Open the full reference (Redoc)',

  'developers.auth.title': 'Authentication',
  'developers.auth.description':
    'Two ways to identify yourself: a session cookie (used by the web app) or an API key in the Authorization header, reserved for real accounts.',
  'developers.auth.apiKey': 'Authorization: Bearer rag_<your_key>',
  'developers.auth.apiKeyNote': 'The key is shown only once, at creation — save it immediately.',

  'developers.schemaA.title': 'Ingesting a document',
  'developers.schemaA.description':
    'Five calls, in order: create a base, send the file, follow the job until it awaits your decision, choose how to split it, then follow the job to completion.',
  'developers.schemaA.step1': 'Create a knowledge base',
  'developers.schemaA.step2':
    'Send the document (multipart) — returns immediately, processing continues in the background',
  'developers.schemaA.step3': 'Follow the job until it awaits your decision',
  'developers.schemaA.step4': 'Confirm how to split the document',
  'developers.schemaA.step5': 'Follow the job until "completed"',

  'developers.schemaB.title': 'Querying a base',
  'developers.schemaB.description':
    'Two endpoints: a plain search returning the matched passages, or a chat that generates an answer from those same passages, streamed as it is produced.',
  'developers.schemaB.search': 'Search — immediate JSON response',
  'developers.schemaB.chat': 'Chat — generated answer, streamed',
  'developers.schemaB.filters':
    'Results can be filtered by language, file type, or ingestion date.',

  'developers.errors.title': 'Errors',
  'developers.errors.description':
    'Every error follows the same shape (RFC 9457): a type, a title, an HTTP status, and a readable detail. A resource that exists but is invisible to you answers "not found", never "forbidden" — so its existence is never confirmed.',

  'developers.providers.title': 'Available providers',
  'developers.providers.description':
    'Before configuring a base, check the catalog of embedding, reranking, and generation models actually available in this environment — no account required.',
};
