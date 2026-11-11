import test from 'node:test'
import assert from 'node:assert/strict'

import { applyRunEvent } from '../src/state/swarmRun.ts'
import type { MessageView, ToolCallView } from '../src/rpc/types.ts'
import type { AgentEventFrame } from '../src/rpc/types.ts'

type RunView = MessageView | ToolCallView

const textDelta = (delta: string): AgentEventFrame => ({
  type: 'message_update',
  assistant_event: { type: 'text_delta', delta } as AgentEventFrame['assistant_event'],
})

test('text deltas accumulate into one streaming assistant row', () => {
  let rows: RunView[] = []
  rows = applyRunEvent(rows, { type: 'message_start', message: { role: 'assistant', text: '' } })
  rows = applyRunEvent(rows, textDelta('Hel'))
  rows = applyRunEvent(rows, textDelta('lo'))
  assert.equal(rows.length, 1)
  assert.equal(rows[0].kind, 'assistant')
  assert.equal((rows[0] as MessageView).text, 'Hello')
})

test('message_end settles the streaming row with the authoritative text', () => {
  let rows: RunView[] = []
  rows = applyRunEvent(rows, { type: 'message_start', message: { role: 'assistant', text: '' } })
  rows = applyRunEvent(rows, textDelta('partial'))
  rows = applyRunEvent(rows, {
    type: 'message_end',
    message: { role: 'assistant', text: 'partial and complete' },
  })
  assert.equal(rows.length, 1, 'no duplicate row after message_end')
  assert.equal((rows[0] as MessageView).text, 'partial and complete')
})

test('an empty assistant turn (tool-only) drops its row', () => {
  let rows: RunView[] = []
  rows = applyRunEvent(rows, { type: 'message_start', message: { role: 'assistant', text: '' } })
  rows = applyRunEvent(rows, { type: 'message_end', message: { role: 'assistant', text: '' } })
  assert.deepEqual(rows, [])
})

test('a user message row is appended', () => {
  const rows = applyRunEvent([], {
    type: 'message_start',
    message: { role: 'user', text: 'do the thing' },
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].kind, 'user')
  assert.equal((rows[0] as MessageView).text, 'do the thing')
})

test('a tool call streams start → update → end on one row keyed by call_id', () => {
  let rows: RunView[] = []
  rows = applyRunEvent(rows, {
    type: 'tool_execution_start',
    tool_call_id: 'c1',
    tool_name: 'bash',
    args: '{"command":"sleep 8"}',
  })
  assert.equal(rows.length, 1)
  const started = rows[0] as ToolCallView
  assert.equal(started.kind, 'tool')
  assert.equal(started.call_id, 'c1')
  assert.equal(started.status, 'running')

  rows = applyRunEvent(rows, {
    type: 'tool_execution_update',
    tool_call_id: 'c1',
    partial_result: 'waiting…',
  })
  assert.equal((rows[0] as ToolCallView).progress, 'waiting…')
  assert.equal(rows.length, 1, 'update patches, never appends')

  rows = applyRunEvent(rows, {
    type: 'tool_execution_end',
    tool_call_id: 'c1',
    result_text: 'done',
    is_error: false,
    duration_ms: 8000,
  })
  const ended = rows[0] as ToolCallView
  assert.equal(ended.status, 'ok')
  assert.equal(ended.result_text, 'done')
  assert.equal(ended.duration_ms, 8000)
  assert.equal(rows.length, 1)
})

test('a failed tool end settles as error', () => {
  let rows: RunView[] = []
  rows = applyRunEvent(rows, { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' })
  rows = applyRunEvent(rows, {
    type: 'tool_execution_end',
    tool_call_id: 'c1',
    result_text: 'boom',
    is_error: true,
  })
  assert.equal((rows[0] as ToolCallView).status, 'error')
  assert.equal((rows[0] as ToolCallView).is_error, true)
})

test('lifecycle events leave the rows untouched (same array identity)', () => {
  const rows: RunView[] = []
  for (const type of ['agent_start', 'turn_start', 'turn_end', 'agent_end'] as const) {
    assert.equal(applyRunEvent(rows, { type }), rows, `${type} must not rebuild rows`)
  }
})

test('a run joined mid-stream opens its row on the first delta', () => {
  // The panel opens while the role is already talking: no message_start ever
  // arrives for the in-flight turn, so the first delta must start the row.
  const rows = applyRunEvent([], textDelta('orphan'))
  assert.equal(rows.length, 1)
  assert.equal(rows[0].kind, 'assistant')
  assert.equal((rows[0] as MessageView).text, 'orphan')
})

test('a tool update for an unknown call is dropped, not invented', () => {
  const rows = applyRunEvent([], {
    type: 'tool_execution_update',
    tool_call_id: 'ghost',
    partial_result: 'x',
  })
  assert.deepEqual(rows, [])
})
