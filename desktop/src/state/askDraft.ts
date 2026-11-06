/** The ask card's per-request draft, owned by the store so a remount (or a
 * session snapshot refresh) cannot reset answers the user already picked.
 * Mirrors pi-desktop's pending-asks draft shape. */

import type { AskQuestion } from '../rpc/types'

/** Selecting the synthetic row toggles the free-text answer. */
export const CUSTOM_OPTION = '__ask_custom__'

export interface AskDraftAnswer {
  values: string[]
  customSelected: boolean
  customText: string
  skipped: boolean
}

export interface AskDraft {
  index: number
  answers: AskDraftAnswer[]
}

export type AskStatus = 'answered' | 'unanswered' | 'skipped'

export function emptyAskDraft(questions: AskQuestion[]): AskDraft {
  return {
    index: 0,
    answers: questions.map(() => ({
      values: [],
      customSelected: false,
      customText: '',
      skipped: false,
    })),
  }
}

/** Option labels plus the custom text, in selection order. */
export function answerValues(answer: AskDraftAnswer): string[] {
  return [
    ...answer.values,
    ...(answer.customSelected && answer.customText.trim()
      ? [answer.customText.trim()]
      : []),
  ]
}

export function draftStatuses(draft: AskDraft): AskStatus[] {
  return draft.answers.map((answer) =>
    answer.skipped
      ? 'skipped'
      : answerValues(answer).length > 0
        ? 'answered'
        : 'unanswered',
  )
}

/** Per-question selections for the wire; null = skipped or never answered. */
export function draftAnswers(draft: AskDraft): (string[] | null)[] {
  return draft.answers.map((answer) => {
    if (answer.skipped) return null
    const values = answerValues(answer)
    return values.length > 0 ? values : null
  })
}

function updateAnswer(
  draft: AskDraft,
  index: number,
  update: (answer: AskDraftAnswer) => AskDraftAnswer,
): AskDraft {
  return {
    ...draft,
    answers: draft.answers.map((answer, position) =>
      position === index ? update(answer) : answer,
    ),
  }
}

export function selectOption(
  draft: AskDraft,
  index: number,
  option: string,
  multi: boolean,
): AskDraft {
  return updateAnswer(draft, index, (answer) => {
    if (option === CUSTOM_OPTION) {
      return {
        ...answer,
        customSelected: !answer.customSelected,
        skipped: false,
        ...(multi ? {} : { values: [] }),
      }
    }
    if (multi) {
      return {
        ...answer,
        values: answer.values.includes(option)
          ? answer.values.filter((value) => value !== option)
          : [...answer.values, option],
        skipped: false,
      }
    }
    return {
      ...answer,
      values: [option],
      customSelected: false,
      customText: '',
      skipped: false,
    }
  })
}

export function setCustomText(
  draft: AskDraft,
  index: number,
  text: string,
): AskDraft {
  return updateAnswer(draft, index, (answer) => ({
    ...answer,
    customText: text,
    skipped: false,
  }))
}

export function skipAnswer(draft: AskDraft, index: number): AskDraft {
  return updateAnswer(draft, index, (answer) => ({
    ...answer,
    values: [],
    customSelected: false,
    skipped: true,
  }))
}

/** Mark the open question skipped, or just move past it when it is answered. */
export function advanceDraft(draft: AskDraft): AskDraft {
  const open = draft.answers[draft.index]
  const next =
    open !== undefined && answerValues(open).length === 0
      ? skipAnswer(draft, draft.index)
      : draft
  return { ...next, index: Math.min(next.index + 1, next.answers.length - 1) }
}

export function openQuestion(draft: AskDraft, index: number): AskDraft {
  const clamped = Math.min(Math.max(0, index), draft.answers.length - 1)
  return { ...draft, index: clamped }
}
