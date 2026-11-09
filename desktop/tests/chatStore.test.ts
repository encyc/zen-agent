/** Store-level contract tests for the transcript snapshot → chat nodes wiring.
 * Run with `node --test tests/chatStore.test.ts` (Node strips the types). */

import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { retryableNodeId, useChatStore } from '../src/state/chatStore.ts'
import type { TranscriptResult } from '../src/rpc/types.ts'

const empty: TranscriptResult = {
  type: 'result',
  id: '',
  ok: true,
  transcript: [],
  notices: [],
  compactions: [],
}

beforeEach(() => {
  useChatStore.getState().reset()
})

test('loadSnapshot adopts backend view ids and assistant metadata', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [
      {
        kind: 'user',
        id: 'v1',
        text: 'go',
        timestamp: 5,
        steering: true,
      },
      {
        kind: 'assistant',
        id: 'v2',
        text: 'done',
        timestamp: 6,
        model: 'deepseek-chat',
        provider: 'deepseek',
        duration_ms: 42,
        usage: {
          input: 3,
          output: 2,
          cache_read: 0,
          cache_write: 0,
          reasoning: null,
          cost: { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 },
        },
      },
    ],
  })

  const [user, assistant] = useChatStore.getState().nodes
  assert.equal(user.id, 'v1')
  assert.equal(user.kind === 'user' && user.steering, true)
  assert.equal(user.kind === 'user' && user.timestamp, 5)
  assert.equal(assistant.id, 'v2')
  assert.equal(assistant.kind === 'assistant' && assistant.model, 'deepseek-chat')
  assert.equal(assistant.kind === 'assistant' && assistant.durationMs, 42)
})

test('loadSnapshot keeps hydrated tool cards and the interrupted state', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [
      {
        kind: 'tool',
        call_id: 'c1',
        id: 'v1',
        name: 'edit_file',
        args_preview: '{}',
        args: {},
        details: { diff: '+x', exit_code: 0 },
        status: 'ok',
        progress: '',
        result_text: 'edited',
        is_error: false,
        duration_ms: 812,
        diff: '+x',
      },
      {
        kind: 'tool',
        call_id: 'c2',
        id: 'v2',
        name: 'bash',
        args_preview: '{}',
        args: {},
        details: {},
        status: 'interrupted',
        progress: '',
        result_text: '',
        is_error: false,
        duration_ms: null,
        diff: null,
      },
    ],
  })

  const [edited, abandoned] = useChatStore.getState().nodes
  assert.equal(edited.kind === 'tool' && edited.durationMs, 812)
  assert.equal(edited.kind === 'tool' && edited.diff, '+x')
  assert.equal(edited.kind === 'tool' && edited.details.exit_code, 0)
  assert.equal(abandoned.kind === 'tool' && abandoned.state, 'interrupted')
})

test('loadSnapshot keeps an in-stream compaction divider where it sits', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [
      { kind: 'user', id: 'v1', text: 'before the cut' },
      { kind: 'divider', id: 'v2', tokens_before: 3400, reason: 'manual' },
      { kind: 'user', id: 'v3', text: 'summary' },
    ],
    compactions: [
      { type: 'compaction', timestamp: 1, tokensBefore: 1200, reason: 'auto' },
      { type: 'compaction', timestamp: 2, tokensBefore: 3400, reason: 'manual' },
    ],
  })

  const nodes = useChatStore.getState().nodes
  assert.deepEqual(nodes.map((node) => node.kind), ['user', 'notice', 'user'])
  const divider = nodes[1]
  assert.equal(divider.kind === 'notice' && divider.variant, 'divider')
  assert.equal(divider.kind === 'notice' && divider.tokensBefore, 3400)
})

test('loadSnapshot falls back to a record-derived divider for legacy servers', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [{ kind: 'user', id: 'v1', text: 'summary' }],
    compactions: [
      { type: 'compaction', timestamp: 2, tokensBefore: 3400, reason: 'manual' },
    ],
  })

  const nodes = useChatStore.getState().nodes
  assert.deepEqual(nodes.map((node) => node.kind), ['notice', 'user'])
  assert.equal(nodes[0].kind === 'notice' && nodes[0].tokensBefore, 3400)
})

test('a live steering message_start marks the user row', () => {
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'user', text: 'read this too', steering: true },
  })

  const [node] = useChatStore.getState().nodes
  assert.equal(node.kind === 'user' && node.steering, true)
})

test('message_end drops an assistant row left with no text or thinking', () => {
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'assistant', text: '' },
  })
  useChatStore.getState().applyEvent({
    type: 'tool_execution_start',
    tool_call_id: 'c1',
    tool_name: 'bash',
    args: '{}',
  })
  useChatStore.getState().applyEvent({
    type: 'message_end',
    message: { role: 'assistant', text: '' },
  })

  const nodes = useChatStore.getState().nodes
  assert.deepEqual(
    nodes.map((node) => node.kind),
    ['tool'],
  )
})

test('finalize interrupts abandoned cards and drops an empty streaming row', () => {
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'assistant', text: '' },
  })
  useChatStore.getState().applyEvent({
    type: 'tool_execution_start',
    tool_call_id: 'c1',
    tool_name: 'bash',
    args: '{}',
  })
  useChatStore.getState().finalize()

  const nodes = useChatStore.getState().nodes
  assert.deepEqual(
    nodes.map((node) => node.kind),
    ['tool'],
  )
  assert.equal(nodes[0].kind === 'tool' && nodes[0].state, 'interrupted')
})

test('retryableNodeId targets the last settled assistant turn', () => {
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'user', text: 'go' },
  })
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'assistant', text: '' },
  })
  useChatStore.getState().applyEvent({
    type: 'message_update',
    assistant_event: { type: 'text_delta', delta: 'answer' },
  })
  useChatStore.getState().applyEvent({
    type: 'message_end',
    message: { role: 'assistant', text: 'answer' },
  })

  const nodes = useChatStore.getState().nodes
  const assistant = nodes.find((node) => node.kind === 'assistant')
  assert.equal(retryableNodeId(nodes), assistant?.id ?? null)
})

test('retryableNodeId ignores a streaming or empty turn', () => {
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'assistant', text: '' },
  })
  useChatStore.getState().applyEvent({
    type: 'message_update',
    assistant_event: { type: 'text_delta', delta: 'partial' },
  })

  assert.equal(retryableNodeId(useChatStore.getState().nodes), null)
  assert.equal(retryableNodeId([]), null)
})

test('streaming deltas land on the current assistant node after long history', () => {
  const transcript: TranscriptResult['transcript'] = Array.from(
    { length: 200 },
    (_, i) =>
      i % 2 === 0
        ? { kind: 'user', id: `u${i}`, text: `q${i}`, timestamp: i, steering: false }
        : {
            kind: 'assistant',
            id: `a${i}`,
            text: `t${i}`,
            timestamp: i,
            model: 'm',
            provider: 'p',
            duration_ms: 1,
            usage: {
              input: 0,
              output: 0,
              cache_read: 0,
              cache_write: 0,
              reasoning: null,
              cost: { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 },
            },
          },
  )
  useChatStore.getState().loadSnapshot({ ...empty, transcript })
  const before = useChatStore.getState().revision
  useChatStore.getState().applyEvent({
    type: 'message_start',
    message: { role: 'assistant', text: '' },
  })
  for (const delta of ['a', 'b', 'c']) {
    useChatStore.getState().applyEvent({
      type: 'message_update',
      assistant_event: { type: 'text_delta', delta },
    })
  }
  const { nodes, revision } = useChatStore.getState()
  const last = nodes[nodes.length - 1]
  assert.equal(last.kind === 'assistant' && last.text, 'abc')
  assert.ok(revision > before)
})

function userView(id: string): TranscriptResult['transcript'][number] {
  return { kind: 'user', id, text: `q-${id}`, timestamp: 1, steering: false }
}

test('a paged snapshot records how much history it left behind', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [userView('v4'), userView('v5')],
    hasMoreBefore: true,
  })
  const state = useChatStore.getState()
  assert.equal(state.hasMoreBefore, true)
  assert.equal(state.loadedViews, 2)
})

test('an old server without hasMoreBefore is treated as a complete snapshot', () => {
  useChatStore.getState().loadSnapshot({ ...empty, transcript: [userView('v1')] })
  assert.equal(useChatStore.getState().hasMoreBefore, false)
})

test('prepending an older page keeps the loaded tail and grows the view count', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [userView('v3'), userView('v4')],
    hasMoreBefore: true,
  })
  useChatStore.getState().prependOlder({
    ...empty,
    transcript: [userView('v1'), userView('v2')],
    hasMoreBefore: false,
  })

  const state = useChatStore.getState()
  assert.deepEqual(
    state.nodes.map((node) => node.id),
    ['v1', 'v2', 'v3', 'v4'],
  )
  assert.equal(state.loadedViews, 4)
  assert.equal(state.hasMoreBefore, false)
})

test('prepending an older tool page indexes its card for live patches', () => {
  useChatStore.getState().loadSnapshot({ ...empty, transcript: [userView('v3')] })
  useChatStore.getState().prependOlder({
    ...empty,
    transcript: [
      {
        kind: 'tool',
        id: 'v2',
        call_id: 'call-old',
        name: 'bash',
        args_preview: 'ls',
        args: {},
        status: 'ok',
      },
    ],
    hasMoreBefore: false,
  })

  useChatStore.getState().applyEvent({
    type: 'tool_execution_end',
    tool_call_id: 'call-old',
    is_error: false,
    result_text: 'late patch',
  })
  const tool = useChatStore.getState().nodes.find((node) => node.id === 'v2')
  assert.equal(tool?.kind === 'tool' && tool.resultText, 'late patch')
})
