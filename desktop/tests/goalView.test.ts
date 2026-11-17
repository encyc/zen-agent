import assert from 'node:assert/strict'
import { test } from 'node:test'

import { goalLimit, goalObjective, goalSummary } from '../src/ui/chat/goalView.ts'

test('goalSummary renders status and budget', () => {
  const text = goalSummary({
    objective: 'fix tests',
    status: 'active',
    check: 'pytest -q',
    turns_used: 3,
    max_turns: 40,
    tokens_used: 100,
    max_tokens: 2000,
    wall_seconds: 10,
    max_wall_seconds: 3600,
    last_verdict: 'not_met',
    last_reason: '2 failed',
    updated_at: 1,
  })
  assert.match(text, /3\/40/)
  assert.match(text, /fix tests/)
})

test('goalObjective extracts the objective from a continuation prompt', () => {
  const text = '[goal] Continue...\n\n<objective>\nfix tests\n</objective>\n'
  assert.equal(goalObjective(text), 'fix tests')
})

test('goalLimit marks a zero ceiling as unlimited', () => {
  assert.equal(goalLimit(0), '∞')
  assert.equal(goalLimit(40), '40')
})
