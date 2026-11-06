/** Ask-card draft contract: selection, custom text, skip and wire answers. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CUSTOM_OPTION,
  advanceDraft,
  answerValues,
  draftAnswers,
  draftStatuses,
  emptyAskDraft,
  openQuestion,
  selectOption,
  setCustomText,
  skipAnswer,
} from '../src/state/askDraft.ts'
import type { AskQuestion } from '../src/rpc/types.ts'

const questions: AskQuestion[] = [
  {
    id: 'db',
    question: 'Which store?',
    options: [{ label: 'SQLite' }, { label: 'Postgres' }],
    multi: false,
    recommended: 'SQLite',
  },
  {
    id: 'scope',
    question: 'What scope?',
    options: [{ label: 'Read' }, { label: 'Write' }],
    multi: true,
  },
]

test('a fresh draft opens the first question with no answers', () => {
  const draft = emptyAskDraft(questions)
  assert.equal(draft.index, 0)
  assert.deepEqual(draftStatuses(draft), ['unanswered', 'unanswered'])
  assert.deepEqual(draftAnswers(draft), [null, null])
})

test('single select replaces the choice and clears custom text', () => {
  let draft = emptyAskDraft(questions)
  draft = selectOption(draft, 0, 'Postgres', false)
  draft = selectOption(draft, 0, CUSTOM_OPTION, false)
  draft = setCustomText(draft, 0, 'DuckDB')
  draft = selectOption(draft, 0, 'SQLite', false)

  assert.deepEqual(draft.answers[0].values, ['SQLite'])
  assert.equal(draft.answers[0].customSelected, false)
  assert.equal(draft.answers[0].customText, '')
})

test('multi select toggles while custom text rides along', () => {
  let draft = emptyAskDraft(questions)
  draft = selectOption(draft, 1, 'Write', true)
  draft = selectOption(draft, 1, 'Read', true)
  draft = selectOption(draft, 1, CUSTOM_OPTION, true)
  draft = setCustomText(draft, 1, 'Audit log')

  assert.deepEqual(answerValues(draft.answers[1]), ['Write', 'Read', 'Audit log'])
  assert.deepEqual(draftAnswers(draft)[1], ['Write', 'Read', 'Audit log'])

  draft = selectOption(draft, 1, 'Read', true)
  assert.deepEqual(answerValues(draft.answers[1]), ['Write', 'Audit log'])
})

test('custom-only answers still count as answered, skips send null', () => {
  let draft = emptyAskDraft(questions)
  draft = selectOption(draft, 0, CUSTOM_OPTION, false)
  draft = setCustomText(draft, 0, 'Something else')
  draft = skipAnswer(draft, 1)

  assert.deepEqual(draftStatuses(draft), ['answered', 'skipped'])
  assert.deepEqual(draftAnswers(draft), [['Something else'], null])
})

test('next marks an empty question skipped and clamps navigation', () => {
  let draft = emptyAskDraft(questions)
  draft = openQuestion(draft, 9)
  assert.equal(draft.index, 1)

  draft = openQuestion(draft, -3)
  const advanced = advanceDraft(draft)
  assert.equal(advanced.index, 1)
  assert.equal(advanced.answers[0].skipped, true)

  const answered = advanceDraft(selectOption(draft, 0, 'SQLite', false))
  assert.equal(answered.answers[0].skipped, false)
})
