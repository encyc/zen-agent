import assert from 'node:assert/strict'
import { test } from 'node:test'

import { toolCue } from '../src/ui/toolState.ts'

test('a finished call shows a check mark', () => {
  assert.deepEqual(toolCue('ok'), { kind: 'ok' })
})

test('a timed-out or stopped command never reads as a clean run', () => {
  // `bash` keeps is_error false for these (command failure is information),
  // so the status alone decides what the row says.
  assert.deepEqual(toolCue('timed_out'), { kind: 'failed', label: 'tool.timed_out' })
  assert.deepEqual(toolCue('interrupted'), { kind: 'stopped', label: 'tool.stopped' })
  assert.deepEqual(toolCue('error'), { kind: 'failed', label: 'tool.failed' })
})

test('a running call keeps the live dot', () => {
  assert.deepEqual(toolCue('running'), { kind: 'running' })
})
