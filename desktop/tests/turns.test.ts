/** Turn grouping contract: the flat transcript node stream folds into user
 * turns without losing a node, including resumed and interrupted sessions. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { groupTurns } from '../src/state/turns.ts'
import type { AssistantNode, ToolNode, UsageNode, UserNode } from '../src/state/chatStore.ts'

const user = (id: string): UserNode => ({
  kind: 'user',
  id,
  text: 'go',
  steering: false,
  timestamp: 0,
})

const assistant = (id: string, streaming = false): AssistantNode => ({
  kind: 'assistant',
  id,
  text: 'done',
  thinking: null,
  thinkingRunning: false,
  streaming,
  timestamp: 0,
  model: '',
  provider: '',
  usage: null,
  durationMs: null,
})

const tool = (
  id: string,
  state: ToolNode['state'] = 'ok',
  durationMs: number | null = null,
): ToolNode => ({
  kind: 'tool',
  id,
  callId: `c-${id}`,
  name: 'read_file',
  argsPreview: '',
  args: {},
  state,
  progress: '',
  resultText: '',
  isError: false,
  durationMs,
  diff: null,
  details: {},
})

const usage = (id: string, turnMs: number | null): UsageNode => ({
  kind: 'usage',
  id,
  usage: null,
  turnMs,
  termination: null,
})

test('user rows start turns and every node renders exactly once', () => {
  const turns = groupTurns([user('u1'), assistant('a1'), tool('t1'), tool('t2'), user('u2'), assistant('a2')], false)
  assert.equal(turns.length, 2)
  assert.equal(turns[0].steps, 2)
  assert.deepEqual(
    turns[0].nodes.map((node) => node.id),
    ['a1', 't1', 't2'],
  )
  assert.equal(turns[1].steps, 0)
})

test('leading non-user nodes form their own turn and interrupted tools still count', () => {
  const turns = groupTurns([assistant('a1'), tool('t1', 'interrupted')], false)
  assert.equal(turns[0].user, null)
  assert.equal(turns[0].steps, 1)
})

test('the live turn is marked running while busy', () => {
  assert.equal(groupTurns([user('u1'), assistant('a1')], true)[0].running, true)
})

test('turn duration prefers tool time and falls back to the turn usage', () => {
  const timed = groupTurns(
    [user('u1'), tool('t1', 'ok', 400), tool('t2', 'ok', null), usage('x', 9000)],
    false,
  )[0]
  assert.equal(timed.durationMs, 400)
  const usageOnly = groupTurns([user('u1'), assistant('a1'), usage('x', 9000)], false)[0]
  assert.equal(usageOnly.durationMs, 9000)
})
