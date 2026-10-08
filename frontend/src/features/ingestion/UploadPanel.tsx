import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import {
  deleteDocumentMutationOptions,
  documentKeys,
  ingestDocumentMutationOptions,
  knowledgeBaseDocumentsQueryOptions,
} from '@/api/documents';
import { knowledgeBaseKeys } from '@/api/knowledge-bases';
import { problemMessageOf } from '@/api/problem';
import type { DocumentRead, IngestionJobRead, JobState } from '@/api/schemas';
import { Badge, type BadgeTone } from '@/components/Badge';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Dialog } from '@/components/Dialog';
import { Dropzone } from '@/components/Dropzone';
import { Skeleton } from '@/components/Skeleton';
import { useToast } from '@/components/Toast';
import { useI18n, useT, type TranslationKey } from '@/i18n';
import { formatBytes } from '@/lib/format';

import { JobTracker, type AutoDecisionMode } from './JobTracker';
import { takePendingUploads } from './pendingUploads';
import { nextStagedFileId } from './stagedFileId';
import { StagedFileRow, type StagedFile } from './StagedFileRow';
import styles from './Ingestion.module.css';

/**
 * Deux versements simultanés au plus.
 *
 * Le pipeline en aval est délibérément étroit (queue GPU à concurrence 1,
 * CONTRIBUTING.md §2) : lâcher dix envois d'un coup ne ferait rien aller plus vite,
 * saturerait la bande passante montante et rendrait l'état « en attente » de la
 * file purement décoratif. Deux fronts d'envoi occupent le réseau sans
 * dépasser ce que la façade absorbe.
 */
const MAX_CONCURRENT_UPLOADS = 2;

type UploadStatus = 'deleting' | 'delete_failed' | 'pending' | 'uploading' | 'failed' | 'sent';

/** Un document déjà présent, désigné par son identité côté façade. */
interface DocumentRef {
  documentId: string;
  filename: string;
}

interface QueueItem {
  /** Identifiant local : deux fichiers homonymes restent deux lignes distinctes. */
  id: string;
  file: File;
  status: UploadStatus;
  documentId: string | null;
  jobId: string | null;
  /** Dernier état **annoncé par la façade**, jamais déduit ici. */
  jobState: JobState | null;
  error: unknown;
  /**
   * Document existant que ce fichier remplace (correspondance exacte de nom,
   * confirmée par l'utilisateur avant l'envoi). `null` pour un versement
   * ordinaire.
   */
  replaces: DocumentRef | null;
  /**
   * Document déjà présent que la façade a **renvoyé au lieu d'en créer un**
   * (idempotence par empreinte, ADR-014). `null` pour un versement ordinaire.
   */
  duplicateOf: DocumentRef | null;
}

/**
 * Le document renvoyé par `POST …/documents/ingest` est-il un document
 * préexistant plutôt qu'un nouveau ?
 *
 * `POST …/documents/ingest` répond `202` avec un `DocumentRead` dans les deux
 * cas : la façade calcule bien un drapeau `created`
 * (`RegisteredDocument.created`, `app/services/documents.py`), mais le routeur
 * ne le sérialise pas — `DocumentRead.model_validate(registered.document)`
 * n'en garde aucune trace. L'information n'atteint donc jamais le client, et
 * l'utilisateur qui verse un doublon voit « indexé » sans analyse préalable
 * puis ne trouve pas son fichier dans la liste (le document renvoyé porte un
 * autre nom). Vécu comme un bug pendant la campagne de tests manuels
 * (l'analyse interne des tests manuels §4.1).
 *
 * Deux signaux la reconstituent **sans supposition** :
 *  1. le nom renvoyé diffère de celui envoyé — un document créé porte toujours
 *     le nom transmis (`resolve_filename` n'en retire que le chemin), donc un
 *     nom différent ne peut venir que d'une ligne préexistante ;
 *  2. l'identifiant renvoyé figurait **déjà** dans la base avant cet envoi —
 *     un identifiant fraîchement créé ne peut pas être dans une liste lue
 *     avant sa création.
 *
 * Angle mort résiduel, assumé : un fichier homonyme d'un document que la page
 * n'a pas lu (au-delà de la première page de la liste) passe inaperçu. Le
 * silence est alors celui d'avant — jamais une affirmation fausse.
 */
function duplicateReturned(
  sent: File,
  returned: DocumentRead,
  knownBefore: ReadonlySet<string>,
): DocumentRef | null {
  const renamed = returned.filename.trim() !== sent.name.trim();
  if (!renamed && !knownBefore.has(returned.id)) return null;
  return { documentId: returned.id, filename: returned.filename };
}

/**
 * Nom sous lequel ce versement doit être désigné une fois parti.
 *
 * Sur un doublon, c'est le nom du document **réellement suivi** : annoncer
 * « rapport-copie.pdf est interrogeable » serait faux, ce fichier-là n'existe
 * pas dans la base.
 */
function trackedName(item: QueueItem): string {
  return item.duplicateOf?.filename ?? item.file.name;
}

export interface UploadPanelProps {
  knowledgeBaseId: string;
}

/**
 * Versement de documents : dépôt, mise en attente, confirmation, puis file et
 * suivi par document.
 *
 * Deux listes distinctes, volontairement :
 *  - `staged` (mise en attente) est **purement locale** — ajouter ou retirer
 *    un fichier ici ne parle jamais au réseau. C'est la garantie centrale de
 *    cette étape : un fichier ajouté par erreur se retire sans conséquence.
 *  - `items` (la file réelle) est ce qui existe déjà : chaque ligne a **son
 *    propre appel `ingest` et son propre job**, un refus (format, quota,
 *    droits) n'interrompt ni n'annule les autres lignes.
 *
 * Le passage de l'une à l'autre se fait au clic sur « Lancer l'ingestion »,
 * après une éventuelle confirmation de remplacement (cf. `pendingReplacements`).
 */
export function UploadPanel({ knowledgeBaseId }: UploadPanelProps): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const { notify } = useToast();

  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [pendingReplacements, setPendingReplacements] = useState<
    { staged: StagedFile; existing: DocumentRead }[] | null
  >(null);

  // Garde-fou contre un double démarrage : en développement, React monte les
  // effets deux fois, et l'état « en cours d'envoi » n'est pas encore visible
  // du second passage.
  const started = useRef<Set<string>>(new Set());

  // Documents déjà présents dans cette base — même requête que `DocumentsPanel`
  // (même `queryKey`), donc partagée depuis le cache plutôt que redemandée.
  const documentsQuery = useQuery(knowledgeBaseDocumentsQueryOptions(knowledgeBaseId));
  const documentsByName = useMemo(() => {
    const map = new Map<string, DocumentRead>();
    for (const document of documentsQuery.data?.data ?? []) {
      // En cas d'anciens doublons de nom, le plus récent l'emporte : c'est lui
      // que le remplacement doit viser.
      map.set(document.filename, document);
    }
    return map;
  }, [documentsQuery.data]);

  // Identifiants connus de la base, tenus dans une référence et **lus au départ
  // de chaque envoi** (cf. `duplicateReturned`). Les lire au retour serait faux :
  // entre les deux, l'aboutissement d'un autre versement invalide la liste, qui
  // peut alors déjà contenir le document que cet envoi-ci vient de créer — et
  // un document neuf serait annoncé comme un doublon.
  const knownDocumentIds = useRef<ReadonlySet<string>>(new Set());
  useEffect(() => {
    knownDocumentIds.current = new Set(
      (documentsQuery.data?.data ?? []).map((document) => document.id),
    );
  }, [documentsQuery.data]);

  const upload = useMutation(ingestDocumentMutationOptions(knowledgeBaseId));
  // `mutateAsync` est appelé plusieurs fois en parallèle : l'état agrégé de la
  // mutation (`isPending`, `error`) n'aurait aucun sens ici, il est ignoré au
  // profit de l'état porté par chaque ligne.
  const { mutateAsync } = upload;

  const deleteDocument = useMutation(deleteDocumentMutationOptions());
  // Même raisonnement que `mutateAsync` ci-dessus : plusieurs remplacements
  // confirmés ensemble suppriment chacun leur ancien document en parallèle,
  // depuis cette même instance de mutation.
  const { mutateAsync: deleteAsync } = deleteDocument;

  const patch = useCallback((id: string, changes: Partial<QueueItem>): void => {
    setItems((previous) =>
      previous.map((item) => (item.id === id ? { ...item, ...changes } : item)),
    );
  }, []);

  /* --- Mise en attente : purement locale --------------------------------- */

  const stage = useCallback((files: File[]): void => {
    setStaged((previous) => [
      ...previous,
      ...files.map<StagedFile>((file) => ({ id: nextStagedFileId(), file })),
    ]);
  }, []);

  const unstage = useCallback((id: string): void => {
    setStaged((previous) => previous.filter((item) => item.id !== id));
  }, []);

  /* --- Confirmation puis départ réel -------------------------------------- */

  const commit = useCallback((toCommit: StagedFile[]): void => {
    if (toCommit.length === 0) return;
    setItems((previous) => [
      ...previous,
      ...toCommit.map<QueueItem>((item) => ({
        id: item.id,
        file: item.file,
        status: 'pending',
        documentId: null,
        jobId: null,
        jobState: null,
        error: null,
        replaces: null,
        duplicateOf: null,
      })),
    ]);
  }, []);

  // Fichiers mis en attente **avant** la création de cette base
  // (`KnowledgeBaseForm`, mode création) : repris et lancés directement, sans
  // repasser par `staged`/« Lancer l'ingestion » — l'utilisateur a déjà validé
  // une seule fois, au clic sur « Créer ». Synchronisation avec une source
  // externe à React (`pendingUploads.ts`, un module-level `Map`), pas un état
  // dérivable au rendu — un `useState(() => takePendingUploads(...))` serait
  // plus court, mais StrictMode invoque l'initialiseur deux fois **avant** de
  // committer, en gardant le second appel : la première lecture, réelle,
  // viderait la source, et le rendu conservé récupérerait `[]`. L'effet, lui,
  // exécute-nettoie-réexécute — le second passage trouve la source déjà vide
  // sans avoir perdu ce que le premier a réellement consommé.
  // `pendingUploadsConsumed` rend ce passage unique explicite plutôt que de
  // compter sur le seul auto-vidage de `takePendingUploads`, même garde-fou
  // que `started` un peu plus bas pour ce même double montage.
  const pendingUploadsConsumed = useRef(false);
  useEffect(() => {
    if (pendingUploadsConsumed.current) return;
    const pendingFiles = takePendingUploads(knowledgeBaseId);
    if (pendingFiles.length === 0) return;
    pendingUploadsConsumed.current = true;
    // Synchronisation délibérée avec le relais externe ci-dessus (cf.
    // commentaire), pas un état dérivable du rendu ; `commit` ne s'exécute
    // qu'une fois, gardé par la référence juste au-dessus.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    commit(pendingFiles.map((file) => ({ id: nextStagedFileId(), file })));
  }, [knowledgeBaseId, commit]);

  const commitReplacement = useCallback(
    (item: StagedFile, existing: DocumentRead): void => {
      setItems((previous) => [
        ...previous,
        {
          id: item.id,
          file: item.file,
          status: 'deleting',
          documentId: null,
          jobId: null,
          jobState: null,
          error: null,
          replaces: { documentId: existing.id, filename: existing.filename },
          duplicateOf: null,
        },
      ]);

      deleteAsync(existing.id)
        .then(() => {
          patch(item.id, { status: 'pending' });
        })
        .catch((error: unknown) => {
          patch(item.id, { status: 'delete_failed', error });
        });
    },
    [deleteAsync, patch],
  );

  const launchIngestion = useCallback((): void => {
    if (staged.length === 0) return;

    const replacements: { staged: StagedFile; existing: DocumentRead }[] = [];
    const direct: StagedFile[] = [];
    for (const item of staged) {
      const existing = documentsByName.get(item.file.name) ?? null;
      if (existing !== null) replacements.push({ staged: item, existing });
      else direct.push(item);
    }

    // Les fichiers sans collision partent immédiatement : ils n'ont aucune
    // raison d'attendre la confirmation des autres.
    commit(direct);

    if (replacements.length > 0) {
      setPendingReplacements(replacements);
      const replacingIds = new Set(replacements.map((entry) => entry.staged.id));
      setStaged((previous) => previous.filter((item) => replacingIds.has(item.id)));
    } else {
      setStaged([]);
    }
  }, [staged, documentsByName, commit]);

  const confirmReplacements = useCallback((): void => {
    if (pendingReplacements === null) return;
    for (const { staged: stagedItem, existing } of pendingReplacements) {
      commitReplacement(stagedItem, existing);
    }
    setStaged([]);
    setPendingReplacements(null);
  }, [pendingReplacements, commitReplacement]);

  const cancelReplacements = useCallback((): void => {
    // Annuler ne touche ni le réseau ni la liste de mise en attente : les
    // fichiers candidats au remplacement y restent, modifiables comme avant.
    setPendingReplacements(null);
  }, []);

  /* --- Pompe d'envoi : au plus `MAX_CONCURRENT_UPLOADS` fronts à la fois --- */
  useEffect(() => {
    const active = items.filter((item) => item.status === 'uploading').length;
    let slots = MAX_CONCURRENT_UPLOADS - active;
    if (slots <= 0) return;

    for (const item of items) {
      if (slots <= 0) break;
      if (item.status !== 'pending' || started.current.has(item.id)) continue;

      started.current.add(item.id);
      slots -= 1;
      patch(item.id, { status: 'uploading', error: null });

      // Photographie prise **avant** l'envoi : cf. `duplicateReturned`.
      const knownBefore = knownDocumentIds.current;
      const sent = item.file;

      mutateAsync({ file: sent })
        .then((document: DocumentRead) => {
          patch(item.id, {
            status: 'sent',
            documentId: document.id,
            jobId: document.current_job_id ?? null,
            duplicateOf: duplicateReturned(sent, document, knownBefore),
          });
        })
        .catch((error: unknown) => {
          patch(item.id, { status: 'failed', error });
        });
    }
  }, [items, mutateAsync, patch]);

  const retryUpload = useCallback(
    (id: string): void => {
      started.current.delete(id);
      patch(id, { status: 'pending', error: null, duplicateOf: null });
    },
    [patch],
  );

  const retryDelete = useCallback(
    (id: string, existingDocumentId: string): void => {
      patch(id, { status: 'deleting', error: null });
      deleteAsync(existingDocumentId)
        .then(() => {
          patch(id, { status: 'pending' });
        })
        .catch((error: unknown) => {
          patch(id, { status: 'delete_failed', error });
        });
    },
    [deleteAsync, patch],
  );

  const remove = useCallback((id: string): void => {
    started.current.delete(id);
    setItems((previous) => previous.filter((item) => item.id !== id));
  }, []);

  const isFinished = (item: QueueItem): boolean =>
    item.status === 'failed' ||
    item.status === 'delete_failed' ||
    item.jobState === 'completed' ||
    item.jobState === 'failed' ||
    item.jobState === 'cancelled';

  const clearFinished = useCallback((): void => {
    setItems((previous) => {
      const kept = previous.filter((item) => !isFinished(item));
      for (const item of previous) {
        if (!kept.includes(item)) started.current.delete(item.id);
      }
      return kept;
    });
  }, []);

  const onJobState = useCallback(
    (id: string, state: JobState): void => {
      patch(id, { jobState: state });
    },
    [patch],
  );

  const onJobSettled = useCallback(
    (item: QueueItem, job: IngestionJobRead): void => {
      // Le corpus a changé : la liste des documents de la base doit repartir du
      // serveur, pas du cache d'avant le versement.
      void queryClient.invalidateQueries({ queryKey: documentKeys.all });
      void queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.all });

      if (job.state === 'completed') {
        notify({
          tone: 'success',
          title: t('ingest.completedTitle'),
          description: t('ingest.completedDescription', { name: trackedName(item) }),
        });
      } else if (job.state === 'failed') {
        notify({
          tone: 'danger',
          title: t('ingest.failedTitle'),
          description: job.error?.message ?? t('error.unexpected'),
        });
      }
    },
    [notify, queryClient, t],
  );

  /* --- Actions groupées : accepter ou refuser les suggestions ------------- */

  // Deux actions symétriques sur la même mécanique : `suggested` soumet ce que
  // l'analyse propose, `none` soumet une décision vide. Toutes deux **lancent
  // l'indexation** — elles décident du *comment*, jamais du *si*. Renoncer à
  // indexer reste l'annulation du job, action distincte et définitive, confirmée
  // document par document (`JobTracker`), jamais en lot.
  const [autoDecisions, setAutoDecisions] = useState<ReadonlyMap<string, AutoDecisionMode>>(
    new Map(),
  );
  const [bulkAccept, setBulkAccept] = useState<{
    status: 'idle' | 'running' | 'done';
    mode: AutoDecisionMode;
    total: number;
    settled: number;
    failed: number;
  }>({ status: 'idle', mode: 'suggested', total: 0, settled: 0, failed: 0 });
  const bulkBatch = useRef<Set<string>>(new Set());

  const awaitingReviewCount = items.filter((item) => item.jobState === 'awaiting_review').length;

  const submitForAll = useCallback(
    (mode: AutoDecisionMode): void => {
      const targets = items.filter((item) => item.jobState === 'awaiting_review');
      if (targets.length === 0) return;

      bulkBatch.current = new Set(targets.map((item) => item.id));
      setAutoDecisions((previous) => {
        const next = new Map(previous);
        for (const item of targets) next.set(item.id, mode);
        return next;
      });
      setBulkAccept({ status: 'running', mode, total: targets.length, settled: 0, failed: 0 });
    },
    [items],
  );

  const onAutoDecisionSettled = useCallback((id: string, outcome: 'success' | 'error'): void => {
    if (!bulkBatch.current.has(id)) return;
    bulkBatch.current.delete(id);
    setBulkAccept((previous) => {
      const settled = previous.settled + 1;
      const failed = previous.failed + (outcome === 'error' ? 1 : 0);
      const status = bulkBatch.current.size === 0 ? 'done' : 'running';
      return { ...previous, settled, failed, status };
    });
  }, []);

  // Notifie l'issue de l'action groupée une fois tous les documents du lot
  // réglés — un échec partiel se voit, il ne se devine pas dans la liste.
  const notifiedBulkDone = useRef(false);
  useEffect(() => {
    if (bulkAccept.status !== 'done') {
      notifiedBulkDone.current = false;
      return;
    }
    if (notifiedBulkDone.current) return;
    notifiedBulkDone.current = true;

    if (bulkAccept.failed > 0) {
      notify({
        tone: 'danger',
        title: t('ingest.bulkAccept.partialFailureTitle'),
        description: t('ingest.bulkAccept.partialFailureDescription', {
          failed: bulkAccept.failed,
          total: bulkAccept.total,
        }),
      });
    } else {
      notify({
        tone: 'success',
        title:
          bulkAccept.mode === 'suggested'
            ? t('ingest.bulkAccept.successTitle')
            : t('ingest.bulkRefuse.successTitle'),
        description: t('ingest.bulkAccept.successDescription', { count: bulkAccept.total }),
      });
    }
  }, [bulkAccept, notify, t]);

  const finishedCount = items.filter(isFinished).length;

  return (
    <Card>
      <CardHeader
        eyebrow={t('ingest.section')}
        title={t('ingest.title')}
        description={t('ingest.description')}
        actions={
          finishedCount > 0 ? (
            <Button variant="ghost" size="sm" onClick={clearFinished}>
              {t('ingest.queue.clear')}
            </Button>
          ) : undefined
        }
      />

      <div className={styles.panelBody}>
        <Dropzone
          multiple
          label={t('ingest.dropzone.label')}
          hint={t('ingest.dropzone.hint')}
          browseLabel={t('ingest.dropzone.browse')}
          onFiles={stage}
        />

        {staged.length > 0 && (
          <section className={styles.queue} aria-label={t('ingest.staging.title')}>
            <div className={styles.queueHead}>
              <h3 className={styles.queueTitle}>{t('ingest.staging.title')}</h3>
              <span className={styles.queueCount}>
                {t('ingest.queue.count', { count: staged.length })}
              </span>
            </div>

            <ul className={styles.items} role="list">
              {staged.map((item) => (
                <StagedFileRow
                  key={item.id}
                  item={item}
                  replaces={documentsByName.get(item.file.name) ?? null}
                  onRemove={() => {
                    unstage(item.id);
                  }}
                />
              ))}
            </ul>

            <div className={styles.stagingActions}>
              <Button onClick={launchIngestion}>{t('ingest.staging.launch')}</Button>
            </div>
          </section>
        )}

        {items.length > 0 && (
          <section className={styles.queue} aria-label={t('ingest.queue.title')}>
            <div className={styles.queueHead}>
              <h3 className={styles.queueTitle}>{t('ingest.queue.title')}</h3>
              <span className={styles.queueCount}>
                {t('ingest.queue.count', { count: items.length })}
              </span>
            </div>

            {awaitingReviewCount >= 2 && (
              <div className={styles.bulkAccept}>
                <p className={styles.bulkAcceptText}>
                  {bulkAccept.status === 'running'
                    ? t('ingest.bulkAccept.progress', {
                        settled: bulkAccept.settled,
                        total: bulkAccept.total,
                      })
                    : t('ingest.bulkAccept.description', { count: awaitingReviewCount })}
                </p>
                <div className={styles.bulkAcceptActions}>
                  {/* Le refus est **aussi** une indexation : sans ce rappel, le
                      mot « refuser » se lit comme « annuler l'ingestion », ce
                      que ce bouton ne fait pas. */}
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={bulkAccept.status === 'running'}
                    title={t('ingest.bulkRefuse.hint')}
                    onClick={() => {
                      submitForAll('none');
                    }}
                  >
                    {t('ingest.bulkRefuse.action')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={bulkAccept.status === 'running'}
                    onClick={() => {
                      submitForAll('suggested');
                    }}
                  >
                    {t('ingest.bulkAccept.action')}
                  </Button>
                </div>
              </div>
            )}

            <ul className={styles.items} role="list">
              {items.map((item) => (
                <QueueRow
                  key={item.id}
                  item={item}
                  autoDecision={autoDecisions.get(item.id) ?? null}
                  onRetryUpload={() => {
                    retryUpload(item.id);
                  }}
                  onRetryDelete={() => {
                    if (item.replaces !== null) retryDelete(item.id, item.replaces.documentId);
                  }}
                  onRemove={() => {
                    remove(item.id);
                  }}
                  onJobState={(state) => {
                    onJobState(item.id, state);
                  }}
                  onJobSettled={(job) => {
                    onJobSettled(item, job);
                  }}
                  onAutoDecisionSettled={(outcome) => {
                    onAutoDecisionSettled(item.id, outcome);
                  }}
                />
              ))}
            </ul>
          </section>
        )}
      </div>

      <Dialog
        open={pendingReplacements !== null}
        onClose={cancelReplacements}
        title={t('ingest.replace.confirmTitle')}
        description={t('ingest.replace.confirmDescription')}
        closeLabel={t('form.close')}
        footer={
          <>
            <Button variant="secondary" onClick={cancelReplacements}>
              {t('form.cancel')}
            </Button>
            <Button variant="danger" onClick={confirmReplacements}>
              {t('ingest.replace.confirm')}
            </Button>
          </>
        }
      >
        <ul className={styles.replaceList} role="list">
          {(pendingReplacements ?? []).map((entry) => (
            <li key={entry.staged.id} className={styles.replaceListItem}>
              {t('ingest.replace.item', {
                incoming: entry.staged.file.name,
                existing: entry.existing.filename,
              })}
            </li>
          ))}
        </ul>
      </Dialog>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Une ligne de la file réelle                                                */
/* -------------------------------------------------------------------------- */

interface QueueRowProps {
  item: QueueItem;
  autoDecision: AutoDecisionMode | null;
  onRetryUpload: () => void;
  onRetryDelete: () => void;
  onRemove: () => void;
  onJobState: (state: JobState) => void;
  onJobSettled: (job: IngestionJobRead) => void;
  onAutoDecisionSettled: (outcome: 'success' | 'error') => void;
}

const JOB_STATE_TONES: Record<JobState, BadgeTone> = {
  queued: 'neutral',
  prechecking: 'accent',
  awaiting_review: 'warning',
  chunking: 'accent',
  embedding: 'accent',
  indexing: 'accent',
  completed: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

const JOB_STATE_SUMMARIES: Record<JobState, TranslationKey> = {
  queued: 'ingest.state.analyzing',
  prechecking: 'ingest.state.analyzing',
  awaiting_review: 'ingest.state.awaitingReview',
  chunking: 'ingest.state.processing',
  embedding: 'ingest.state.processing',
  indexing: 'ingest.state.processing',
  completed: 'ingest.state.completed',
  failed: 'ingest.state.failed',
  cancelled: 'ingest.state.cancelled',
};

function QueueRow({
  item,
  autoDecision,
  onRetryUpload,
  onRetryDelete,
  onRemove,
  onJobState,
  onJobSettled,
  onAutoDecisionSettled,
}: QueueRowProps): ReactNode {
  const t = useT();
  const { locale } = useI18n();

  const tone: BadgeTone =
    item.status === 'failed' || item.status === 'delete_failed'
      ? 'danger'
      : item.status === 'deleting'
        ? 'accent'
        : item.jobState !== null
          ? JOB_STATE_TONES[item.jobState]
          : item.status === 'uploading'
            ? 'accent'
            : 'neutral';

  const label: TranslationKey =
    item.status === 'delete_failed'
      ? 'ingest.state.deleteFailed'
      : item.status === 'deleting'
        ? 'ingest.state.deleting'
        : item.status === 'failed'
          ? 'ingest.state.uploadFailed'
          : item.jobState !== null
            ? JOB_STATE_SUMMARIES[item.jobState]
            : item.status === 'uploading'
              ? 'ingest.state.uploading'
              : item.status === 'sent'
                ? 'ingest.state.analyzing'
                : 'ingest.state.pending';

  return (
    <li className={styles.item}>
      <div className={styles.itemHead}>
        <span className={styles.itemIcon} aria-hidden="true">
          <svg viewBox="0 0 20 20" focusable="false" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 2.5h5L15 6.5v11H6z" />
            <path d="M11 2.5v4h4" />
          </svg>
        </span>

        <div className={styles.itemIdentity}>
          <span className={styles.itemName} title={item.file.name}>
            {item.file.name}
          </span>
          <span className={styles.itemSize}>{formatBytes(item.file.size, locale)}</span>
        </div>

        {item.duplicateOf !== null && (
          <Badge tone="neutral">{t('ingest.item.duplicateBadge')}</Badge>
        )}

        <Badge tone={tone}>{t(label)}</Badge>

        <button
          type="button"
          className={styles.itemRemove}
          title={t('ingest.item.remove')}
          onClick={onRemove}
        >
          <span className={styles.visuallyHidden}>{t('ingest.item.remove')}</span>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" strokeLinecap="round">
            <path d="m5 5 6 6m0-6-6 6" />
          </svg>
        </button>
      </div>

      {item.replaces !== null && (
        <p className={styles.itemReplacesNote}>
          {t('ingest.item.replacesNote', { name: item.replaces.filename })}
        </p>
      )}

      {item.duplicateOf !== null && (
        // `role="status"` : la ligne est déjà à l'écran quand la note y
        // apparaît, une fois la réponse de la façade revenue — sans annonce,
        // un lecteur d'écran ne signalerait rien.
        <p className={styles.itemDuplicateNote} role="status">
          {t('ingest.item.duplicateNote', { name: item.duplicateOf.filename })}
        </p>
      )}

      {item.status === 'deleting' && (
        <div className={styles.itemBody} aria-busy="true">
          <Skeleton height="0.5rem" />
        </div>
      )}

      {item.status === 'delete_failed' && (
        <div className={styles.itemBody}>
          <div className={styles.failure} role="alert">
            <p className={styles.failureTitle}>{t('ingest.deleteFailedTitle')}</p>
            <p className={styles.failureText}>
              {problemMessageOf(item.error, t('error.unexpected'))}
            </p>
            <div className={styles.failureActions}>
              <Button variant="secondary" size="sm" onClick={onRetryDelete}>
                {t('ingest.item.retry')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {item.status === 'uploading' && (
        <div className={styles.itemBody} aria-busy="true">
          <Skeleton height="0.5rem" />
        </div>
      )}

      {item.status === 'failed' && (
        <div className={styles.itemBody}>
          <div className={styles.failure} role="alert">
            <p className={styles.failureTitle}>{t('ingest.uploadErrorTitle')}</p>
            <p className={styles.failureText}>
              {problemMessageOf(item.error, t('error.unexpected'))}
            </p>
            <div className={styles.failureActions}>
              <Button variant="secondary" size="sm" onClick={onRetryUpload}>
                {t('ingest.item.retry')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {item.status === 'sent' && item.documentId !== null && item.jobId === null && (
        <div className={styles.itemBody}>
          <div className={styles.failure}>
            <p className={styles.failureTitle}>{t('ingest.noJobTitle')}</p>
            <p className={styles.failureText}>{t('ingest.noJobDescription')}</p>
          </div>
        </div>
      )}

      {item.status === 'sent' && item.documentId !== null && item.jobId !== null && (
        <div className={styles.itemBody}>
          <JobTracker
            documentId={item.documentId}
            jobId={item.jobId}
            filename={trackedName(item)}
            onStateChange={onJobState}
            onSettled={onJobSettled}
            autoDecision={autoDecision}
            onAutoDecisionSettled={onAutoDecisionSettled}
          />
        </div>
      )}
    </li>
  );
}
