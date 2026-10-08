import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { problemMessageOf } from '@/api/problem';
import { streamChat } from '@/api/retrieval';
import type {
  ChatTurn,
  LlmVendor,
  LLMProviderStatus,
  RetrievalChatRequest,
  RetrievalResult,
} from '@/api/schemas';
import { boundChatHistory, MAX_QUERY_LENGTH, requestErrorMessage } from '@/api/text-limits';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { EmptyState } from '@/components/EmptyState';
import { Field } from '@/components/Field';
import { Select } from '@/components/Select';
import { Textarea } from '@/components/Textarea';
import { useI18n, useT, type Translate } from '@/i18n';
import { cx } from '@/lib/cx';
import { formatInteger } from '@/lib/format';

import { PassageCard } from './PassageCard';
import type { RetrievalSettingsValue } from './settings';
import styles from './Playground.module.css';

/**
 * Cycle de vie d'un tour d'assistant.
 *
 * `retrieving` existe parce que les sources arrivent **avant** le premier
 * token : c'est un état visible, pas une transition instantanée — la recherche
 * hybride et le reranking peuvent prendre quelques secondes, et laisser une
 * bulle vide pendant ce temps donnerait l'impression que rien ne se passe.
 */
type AnswerState = 'retrieving' | 'streaming' | 'done' | 'error';

interface ChatEntry {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: RetrievalResult[];
  state?: AnswerState;
  /** Message d'erreur déjà lisible (RFC 9457 aplati). */
  error?: string;
  /**
   * L'erreur est-elle un refus **avant** le flux (droits, modèle inconnu) ou
   * une panne survenue en cours de génération ? Les deux ne se racontent pas de
   * la même façon : la première invite à corriger la demande, la seconde
   * signale que la réponse affichée est incomplète.
   */
  refused?: boolean;
}

let entrySequence = 0;
function nextEntryId(): string {
  entrySequence += 1;
  return `entry-${String(entrySequence)}`;
}

/**
 * Rappelle, sous le sélecteur de modèle, si le choix affiché s'applique
 * réellement — cf. le commentaire de `ChatPanelProps.personalKeyVendors`.
 * `undefined` (aucun fournisseur sélectionné) ne devrait pas se produire une
 * fois le panneau rendu (`available.length > 0` déjà garanti par l'appelant),
 * mais reste géré sans deviner : chaîne vide plutôt qu'une affirmation sur un
 * fournisseur qu'on ne connaît pas.
 */
function modelHintFor(
  provider: LLMProviderStatus | undefined,
  personalKeyVendors: ReadonlySet<LlmVendor>,
  forcedModelId: string | null | undefined,
  t: Translate,
): string {
  if (provider === undefined) return '';
  // Aucun modèle imposé par le serveur : le choix s'applique toujours, rien à rappeler.
  if (forcedModelId === null) return '';
  const vendor = t(`settings.llmKeys.vendor.${provider.vendor}`);
  return personalKeyVendors.has(provider.vendor)
    ? t('playground.chat.modelAppliesHint', { vendor })
    : t('playground.chat.modelOverriddenHint', { vendor });
}

export interface ChatPanelProps {
  knowledgeBaseId: string;
  settings: RetrievalSettingsValue;
  /** Catalogue réel : seuls les fournisseurs `available` sont proposés. */
  providers: LLMProviderStatus[];
  /**
   * Fournisseurs pour lesquels le compte a déclaré sa propre clé LLM
   * (`GET /me/llm-keys`, comptes réels uniquement — un compte éphémère ou un visiteur
   * anonyme n'en a jamais). Détermine si le modèle choisi ci-dessous
   * s'applique réellement : dès que la clé qui paie n'est pas la clé
   * personnelle de l'appelant, le backend impose son propre modèle
   * (`resolve_llm_funding`, addendum 2026-09-21 à ADR-054) — le sélecteur
   * reste utilisable dans tous les cas, mais son choix est silencieusement
   * ignoré hors de cet ensemble. Ensemble vide et non `undefined` : un appel
   * en cours de chargement ne doit jamais laisser croire, même
   * temporairement, qu'un choix s'applique alors que ce n'est pas encore su.
   */
  personalKeyVendors: ReadonlySet<LlmVendor>;
  /** `GET /providers` → `llm_forced_model_id` : `null` = aucun modèle imposé ; `undefined` = pas encore su. */
  forcedModelId?: string | null;
  onSwitchToSearch: () => void;
}

export function ChatPanel({
  knowledgeBaseId,
  settings,
  providers,
  personalKeyVendors,
  forcedModelId,
  onSwitchToSearch,
}: ChatPanelProps): ReactNode {
  const { t, locale } = useI18n();
  const available = providers.filter((provider) => provider.available);

  const [providerId, setProviderId] = useState<string>(() => available[0]?.provider_id ?? '');
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const inputId = useId();

  // On ne suit le flux que lorsqu'un tour s'ajoute : ramener la vue à chaque
  // token empêcherait de relire une réponse pendant qu'elle s'écrit.
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [entries.length]);

  // Second point d'ancrage, à l'arrivée du premier token : les sources
  // s'affichent avant la réponse et occupent de la hauteur. Sans ce rappel,
  // le texte commencerait à s'écrire hors du champ de vision — l'utilisateur
  // verrait ses sources, pas la réponse qu'il attend.
  const lastAnswerState = entries.at(-1)?.state;
  useEffect(() => {
    if (lastAnswerState !== 'streaming') return;
    bottom.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [lastAnswerState]);

  // Un flux laissé ouvert continuerait de consommer des tokens facturés après
  // le départ de l'utilisateur.
  useEffect(() => () => abort.current?.abort(), []);

  const update = useCallback((id: string, changes: Partial<ChatEntry>): void => {
    setEntries((previous) =>
      previous.map((entry) => (entry.id === id ? { ...entry, ...changes } : entry)),
    );
  }, []);

  const append = useCallback((id: string, token: string): void => {
    setEntries((previous) =>
      previous.map((entry) =>
        entry.id === id ? { ...entry, content: entry.content + token } : entry,
      ),
    );
  }, []);

  const send = useCallback(async (): Promise<void> => {
    const question = input.trim();
    if (question.length === 0 || streaming || providerId === '') return;

    // L'historique transmis ne retient que les tours réellement aboutis : une
    // réponse interrompue en cours de route n'est pas un tour d'assistant
    // valide, et le backend refuse d'ailleurs un contenu vide. Il est ensuite
    // borné aux tours récents (`boundChatHistory`) : une longue conversation
    // ne doit jamais finir en `422`.
    const history: ChatTurn[] = entries
      .filter((entry) => entry.role === 'user' || entry.state === 'done')
      .filter((entry) => entry.content.trim().length > 0)
      .map((entry) => ({ role: entry.role, content: entry.content }));

    const answerId = nextEntryId();
    setEntries((previous) => [
      ...previous,
      { id: nextEntryId(), role: 'user', content: question },
      { id: answerId, role: 'assistant', content: '', state: 'retrieving' },
    ]);
    setInput('');
    setStreaming(true);

    const controller = new AbortController();
    abort.current = controller;

    const request: RetrievalChatRequest = {
      knowledge_base_id: knowledgeBaseId,
      messages: boundChatHistory([...history, { role: 'user', content: question }]),
      top_k: settings.topK,
      rerank: settings.rerank,
      similarity_threshold: settings.similarityThreshold,
      // Le Playground n'expose pas encore de bouton d'accusé de réception
      // (ADR-026) : combiner reranking et seuil non nul y répond donc 422.
      acknowledge_threshold_risk: false,
      document_ids: settings.documentIds.length > 0 ? settings.documentIds : null,
      llm_provider_id: providerId,
      max_tokens: 1024,
    };

    // Objet mutable plutôt qu'un booléen : le drapeau est posé depuis des
    // rappels appelés pendant l'`await`, ce qu'un `let` ne laisse pas voir au
    // vérificateur de types (il le croirait figé à sa valeur initiale).
    const outcome = { settled: false };
    try {
      await streamChat(
        request,
        {
          onSources: (sources) => {
            update(answerId, { sources, state: 'streaming' });
          },
          onToken: (token) => {
            append(answerId, token);
          },
          onError: (error) => {
            outcome.settled = true;
            update(answerId, {
              state: 'error',
              error: problemMessageOf(error, t('error.unexpected')),
              refused: false,
            });
          },
          onDone: () => {
            outcome.settled = true;
            update(answerId, { state: 'done' });
          },
        },
        controller.signal,
      );
      // Flux clos sans `done` ni `error` : arrêt volontaire, ou coupure côté
      // serveur. Dans les deux cas la réponse existe mais s'arrête là.
      if (!outcome.settled) update(answerId, { state: 'done' });
    } catch (error) {
      // Refus avant le premier octet : rien n'a été généré.
      update(answerId, {
        state: 'error',
        error: requestErrorMessage(error, t, locale),
        refused: true,
      });
    } finally {
      abort.current = null;
      setStreaming(false);
    }
  }, [append, entries, input, knowledgeBaseId, locale, providerId, settings, streaming, t, update]);

  if (available.length === 0) {
    return (
      <div className={styles.panel}>
        <EmptyState
          illustration="lock"
          title={t('playground.chat.noProviderTitle')}
          description={t('playground.chat.noProviderDescription')}
          action={
            <Button variant="secondary" onClick={onSwitchToSearch}>
              {t('playground.chat.switchToSearch')}
            </Button>
          }
        />
        <p className={styles.providerHint}>{t('playground.chat.noProviderHint')}</p>
      </div>
    );
  }

  const selectedProvider = available.find((provider) => provider.provider_id === providerId);

  return (
    <div className={styles.panel}>
      <div className={styles.chatHead}>
        <Field
          label={t('playground.chat.model')}
          hint={modelHintFor(selectedProvider, personalKeyVendors, forcedModelId, t)}
        >
          {(field) => (
            <Select
              {...field}
              value={providerId}
              disabled={streaming}
              onChange={(event) => {
                setProviderId(event.target.value);
              }}
            >
              {available.map((provider) => (
                <option key={provider.provider_id} value={provider.provider_id}>
                  {provider.provider_id} · {provider.vendor}
                </option>
              ))}
            </Select>
          )}
        </Field>

        {entries.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            disabled={streaming}
            onClick={() => {
              setEntries([]);
            }}
          >
            {t('playground.chat.reset')}
          </Button>
        )}
      </div>

      <div className={styles.conversation}>
        {entries.length === 0 ? (
          <EmptyState
            illustration="documents"
            title={t('playground.chat.emptyTitle')}
            description={t('playground.chat.emptyDescription')}
          />
        ) : (
          entries.map((entry) =>
            entry.role === 'user' ? (
              <div key={entry.id} className={cx(styles.turn, styles.turnUser)}>
                <span className={styles.turnRole}>{t('playground.chat.you')}</span>
                <p className={styles.bubble}>{entry.content}</p>
              </div>
            ) : (
              <AnswerTurn key={entry.id} entry={entry} />
            ),
          )
        )}
        <div ref={bottom} />
      </div>

      <form
        className={styles.composer}
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <label className={styles.visuallyHidden} htmlFor={inputId}>
          {t('playground.chat.inputLabel')}
        </label>
        <Textarea
          id={inputId}
          rows={2}
          maxLength={MAX_QUERY_LENGTH}
          value={input}
          placeholder={t('playground.chat.placeholder')}
          disabled={streaming}
          onChange={(event) => {
            setInput(event.target.value);
          }}
          onKeyDown={(event) => {
            // Entrée envoie, Maj+Entrée passe à la ligne : la convention des
            // messageries, celle que les doigts connaissent déjà.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className={styles.composerActions}>
          {input.length >= MAX_QUERY_LENGTH && (
            <p className={styles.inputLimit} role="status">
              {t('error.inputLimitReached', { max: formatInteger(MAX_QUERY_LENGTH, locale) })}
            </p>
          )}
          {streaming ? (
            <Button
              variant="secondary"
              onClick={() => {
                abort.current?.abort();
              }}
            >
              {t('playground.chat.stop')}
            </Button>
          ) : (
            <Button type="submit" disabled={input.trim().length === 0}>
              {t('playground.chat.send')}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Tour d'assistant : sources, texte cité, état                                */
/* -------------------------------------------------------------------------- */

function AnswerTurn({ entry }: { entry: ChatEntry }): ReactNode {
  const t = useT();
  const [active, setActive] = useState<number | null>(null);
  const sources = entry.sources ?? [];
  const anchorPrefix = `${entry.id}-source`;

  return (
    <div className={cx(styles.turn, styles.turnAssistant)}>
      <span className={styles.turnRole}>{t('playground.chat.assistant')}</span>

      {entry.state === 'retrieving' && (
        <p className={styles.pending}>
          <span className={styles.pendingDots} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          {t('playground.chat.retrieving')}
        </p>
      )}

      {sources.length > 0 && (
        <section className={styles.sources} aria-label={t('playground.chat.sources')}>
          <div className={styles.sourcesHead}>
            <span className={styles.sourcesTitle}>{t('playground.chat.sources')}</span>
            <Badge tone="neutral">
              {t('playground.chat.sourcesCount', { count: sources.length })}
            </Badge>
          </div>
          <div className={styles.sourcesList}>
            {sources.map((source, index) => (
              <PassageCard
                key={source.chunk_id}
                rank={index + 1}
                result={source}
                variant="compact"
                highlighted={active === index + 1}
                anchorId={`${anchorPrefix}-${String(index + 1)}`}
              />
            ))}
          </div>
        </section>
      )}

      {entry.content.length > 0 && (
        <p className={styles.bubble}>
          <AnswerText
            content={entry.content}
            sourceCount={sources.length}
            anchorPrefix={anchorPrefix}
            onActivate={setActive}
          />
          {entry.state === 'streaming' && <span className={styles.caret} aria-hidden="true" />}
        </p>
      )}

      {entry.state === 'error' && (
        <div className={styles.errorBlock} role="alert">
          <p className={styles.errorTitle}>
            {entry.refused === true
              ? t('playground.chat.refusedTitle')
              : t('playground.chat.errorTitle')}
          </p>
          <p className={styles.errorText}>{entry.error ?? t('error.unexpected')}</p>
        </div>
      )}

      {entry.state === 'done' && entry.content.length === 0 && (
        <p className={styles.pending}>{t('playground.chat.emptyAnswer')}</p>
      )}
    </div>
  );
}

/** Découpe la réponse pour transformer chaque `[n]` en renvoi cliquable. */
const CITATION_PATTERN = /(\[\d+\])/g;

function AnswerText({
  content,
  sourceCount,
  anchorPrefix,
  onActivate,
}: {
  content: string;
  sourceCount: number;
  anchorPrefix: string;
  onActivate: (index: number | null) => void;
}): ReactNode {
  const t = useT();

  return (
    <>
      {content.split(CITATION_PATTERN).map((part, index) => {
        const match = /^\[(\d+)\]$/.exec(part);
        const cited = match === null ? null : Number.parseInt(match[1] ?? '', 10);

        // Un numéro hors bornes reste du texte : le modèle a pu citer un
        // passage qui n'existe pas, et fabriquer un lien mort serait pire que
        // de laisser voir la citation telle qu'elle a été produite.
        if (cited === null || Number.isNaN(cited) || cited < 1 || cited > sourceCount) {
          // Segments d'un même texte : leur position EST leur identité.
          return <span key={index}>{part}</span>;
        }

        return (
          <button
            key={index}
            type="button"
            className={styles.citation}
            aria-label={t('playground.chat.sourceLabel', { index: cited })}
            onMouseEnter={() => {
              onActivate(cited);
            }}
            onMouseLeave={() => {
              onActivate(null);
            }}
            onFocus={() => {
              onActivate(cited);
            }}
            onBlur={() => {
              onActivate(null);
            }}
            onClick={() => {
              onActivate(cited);
              document
                .getElementById(`${anchorPrefix}-${String(cited)}`)
                ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }}
          >
            {cited}
          </button>
        );
      })}
    </>
  );
}
