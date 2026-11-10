import assert from 'node:assert/strict'
import { test } from 'node:test'
import { PaneCache } from '../src/state/projectPanes.ts'
import type { ChatPane } from '../src/state/chatStore.ts'

function pane(text: string): ChatPane {
  return {
    nodes: [{ kind: 'user', id: text, text, steering: false, timestamp: 0 }],
    cards: {},
    currentAssistantId: null,
    hasMoreBefore: false,
    firstLoadedIndex: 0,
  }
}

test('the least recently used project is evicted beyond the limit', () => {
  const cache = new PaneCache(2)
  cache.set('/work/a', { session: '/sessions/a.jsonl', pane: pane('a') })
  cache.set('/work/b', { session: '/sessions/b.jsonl', pane: pane('b') })
  cache.get('/work/a')
  cache.set('/work/c', { session: '/sessions/c.jsonl', pane: pane('c') })

  assert.equal(cache.get('/work/b'), undefined)
  assert.equal(cache.get('/work/a')?.session, '/sessions/a.jsonl')
  assert.equal(cache.get('/work/c')?.session, '/sessions/c.jsonl')
})

test('setting the same project replaces its entry and refreshes its place', () => {
  const cache = new PaneCache(2)
  cache.set('/work/a', { session: '/sessions/a.jsonl', pane: pane('old') })
  cache.set('/work/b', { session: '/sessions/b.jsonl', pane: pane('b') })
  cache.set('/work/a', { session: '/sessions/a2.jsonl', pane: pane('new') })

  assert.equal(cache.get('/work/a')?.session, '/sessions/a2.jsonl')
  cache.set('/work/c', { session: '/sessions/c.jsonl', pane: pane('c') })
  assert.equal(cache.get('/work/b'), undefined)
  assert.notEqual(cache.get('/work/a'), undefined)
})
