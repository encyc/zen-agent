/** Two chat stores must behave as fully independent transcripts — the pane
 * registry gives every open session its own instance — while sharing the
 * module-level id sequence, so a node id minted in one pane can never be
 * minted again in another. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createChatStore, useChatStore, type AssistantNode } from '../src/state/chatStore.ts'
import type { AgentEventFrame } from '../src/rpc/types.ts'

function assistantStart(): AgentEventFrame {
  return {
    type: 'message_start',
    message: { role: 'assistant' },
  } as unknown as AgentEventFrame
}

function textDelta(text: string): AgentEventFrame {
  return {
    type: 'message_update',
    assistant_event: { type: 'text_delta', delta: text },
  } as unknown as AgentEventFrame
}

test('factory instances stream independently of the primary store', () => {
  const paneStore = createChatStore()

  paneStore.getState().applyEvent(assistantStart())
  paneStore.getState().applyEvent(textDelta('pane says hi'))

  const paneAssistant = paneStore
    .getState()
    .nodes.find((node): node is AssistantNode => node.kind === 'assistant')
  assert.equal(paneAssistant?.text, 'pane says hi')
  assert.equal(useChatStore.getState().nodes.length, 0)
})

test('instances share the id sequence: no duplicate node ids across stores', () => {
  const one = createChatStore()
  const two = createChatStore()

  one.getState().applyEvent(assistantStart())
  two.getState().applyEvent(assistantStart())

  const idOne = one.getState().nodes[0]?.id
  const idTwo = two.getState().nodes[0]?.id
  assert.notEqual(idOne, undefined)
  assert.notEqual(idOne, idTwo)
})

test('snapshot paging state is per instance', () => {
  const pane = createChatStore()
  pane.getState().loadSnapshot({
    type: 'result',
    id: '',
    ok: true,
    transcript: [],
    notices: [],
    compactions: [],
    hasMoreBefore: true,
    total: 30,
  })
  assert.equal(pane.getState().hasMoreBefore, true)
  assert.equal(useChatStore.getState().hasMoreBefore, false)
})
