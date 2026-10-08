import { describe, expect, it } from 'vitest';

import { ApiError } from './client';
import type { ChatTurn } from './schemas';
import {
  boundChatHistory,
  MAX_ASSISTANT_TURN_LENGTH,
  MAX_CHAT_MESSAGES,
  MAX_CHAT_TRANSCRIPT_LENGTH,
  MAX_QUERY_LENGTH,
  textTooLongOf,
} from './text-limits';

function user(content: string): ChatTurn {
  return { role: 'user', content };
}

function assistant(content: string): ChatTurn {
  return { role: 'assistant', content };
}

function totalLength(turns: ChatTurn[]): number {
  return turns.reduce((sum, turn) => sum + turn.content.length, 0);
}

function unprocessable(errors: unknown[]): ApiError {
  return new ApiError({
    type: '/problems/validation-error',
    title: 'Unprocessable Content',
    status: 422,
    detail: 'La requête ne respecte pas le schéma attendu.',
    errors,
  });
}

describe('boundChatHistory', () => {
  it('rend une conversation courte telle quelle', () => {
    const turns = [user('Bonjour'), assistant('Bonjour !'), user('Quel délai ?')];

    expect(boundChatHistory(turns)).toEqual(turns);
  });

  it('garde les tours les plus récents au-delà du nombre de tours autorisé', () => {
    const turns: ChatTurn[] = [];
    for (let index = 0; index < 30; index += 1) {
      turns.push(user(`question ${String(index)}`), assistant(`réponse ${String(index)}`));
    }
    turns.push(user('dernière question'));

    const bounded = boundChatHistory(turns);

    expect(bounded.length).toBeLessThanOrEqual(MAX_CHAT_MESSAGES);
    expect(bounded.at(-1)).toEqual(user('dernière question'));
    // Jamais une réponse orpheline de sa question en tête.
    expect(bounded[0]?.role).toBe('user');
    expect(bounded.map((turn) => turn.content)).not.toContain('question 0');
  });

  it('retire les tours anciens pour tenir dans la taille totale', () => {
    const answer = 'r'.repeat(20_000);
    const turns = [
      user('q1'),
      assistant(answer),
      user('q2'),
      assistant(answer),
      user('q3'),
      assistant(answer),
      user('q4'),
      assistant(answer),
      user('q5'),
    ];

    const bounded = boundChatHistory(turns);

    expect(totalLength(bounded)).toBeLessThanOrEqual(MAX_CHAT_TRANSCRIPT_LENGTH);
    expect(bounded[0]?.role).toBe('user');
    expect(bounded.at(-1)).toEqual(user('q5'));
    expect(bounded.map((turn) => turn.content)).not.toContain('q1');
  });

  it('tronque une réponse plus longue que sa borne au lieu de la refuser', () => {
    const bounded = boundChatHistory([
      user('q1'),
      assistant('a'.repeat(MAX_ASSISTANT_TURN_LENGTH + 500)),
      user('q2'),
    ]);

    expect(bounded).toHaveLength(3);
    expect(bounded[1]?.content).toHaveLength(MAX_ASSISTANT_TURN_LENGTH);
  });

  it('ne coupe jamais un caractère hors plan de base en deux', () => {
    const content = 'a'.repeat(MAX_ASSISTANT_TURN_LENGTH - 1) + '😀' + 'b'.repeat(10);

    const clipped = boundChatHistory([user('q'), assistant(content), user('q2')])[1]?.content;

    expect(clipped).toHaveLength(MAX_ASSISTANT_TURN_LENGTH - 1);
    expect(clipped?.endsWith('a')).toBe(true);
  });
});

describe('textTooLongOf', () => {
  it('reconnaît une requête ou un tour trop long, avec la borne réelle du backend', () => {
    const error = unprocessable([
      {
        type: 'string_too_long',
        loc: ['body', 'messages', 0, 'content'],
        msg: 'String should have at most 4000 characters',
        ctx: { max_length: MAX_QUERY_LENGTH },
      },
    ]);

    expect(textTooLongOf(error)).toEqual({ kind: 'text', max: MAX_QUERY_LENGTH });
  });

  it('reconnaît un historique trop volumineux', () => {
    const error = unprocessable([
      { type: 'chat_transcript_too_long', loc: ['body', 'messages'], msg: '…' },
    ]);

    expect(textTooLongOf(error)).toEqual({ kind: 'conversation' });
  });

  it("ignore un refus qui ne porte pas sur un texte saisi par l'utilisateur", () => {
    const tooManyDocuments = unprocessable([
      { type: 'too_long', loc: ['body', 'document_ids'], msg: '…' },
    ]);
    const otherField = unprocessable([
      { type: 'string_too_long', loc: ['body', 'llm_provider_id'], msg: '…' },
    ]);

    expect(textTooLongOf(tooManyDocuments)).toBeNull();
    expect(textTooLongOf(otherField)).toBeNull();
    expect(textTooLongOf(new Error('réseau'))).toBeNull();
  });
});
