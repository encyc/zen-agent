import assert from 'node:assert/strict'
import { test } from 'node:test'

import { goalObjective, goalSummary } from '../src/ui/chat/goalView.ts'

test('goalSummary counts turns without a ceiling', () => {
  const text = goalSummary({
    objective: 'fix tests',
    status: 'active',
    check: 'pytest -q',
    turns_used: 3,
    tokens_used: 100,
    wall_seconds: 10,
    last_verdict: 'not_met',
    last_reason: '2 failed',
    updated_at: 1,
  })
  assert.match(text, /fix tests · 3/)
  assert.doesNotMatch(text, /\//)
})

test('goalObjective extracts the objective from a continuation prompt', () => {
  const text = '[goal] Continue...\n\n<objective>\nfix tests\n</objective>\n'
  assert.equal(goalObjective(text), 'fix tests')
})
