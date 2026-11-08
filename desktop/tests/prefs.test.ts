/** Session prefs: what survives the runtime process a project switch replaces.
 *
 * These are pure storage/replay rules, so they are pinned here rather than
 * through the app: the e2e suite covers what the user sees, this file covers
 * the decisions (what to keep, what to replay, in which order). */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  MODEL_KEY,
  MODE_KEY,
  THINKING_KEY,
  loadPrefs,
  replayFrames,
  savePref,
  spawnArgs,
} from '../src/state/prefs.ts'

function fakeStorage(): { store: Map<string, string>; restore(): void } {
  const store = new Map<string, string>()
  const target = globalThis as unknown as { localStorage?: unknown }
  const previous = target.localStorage
  target.localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size
    },
  }
  return {
    store,
    restore() {
      if (previous === undefined) delete target.localStorage
      else target.localStorage = previous
    },
  }
}

test('prefs round-trip through storage and clear on null', () => {
  const storage = fakeStorage()
  try {
    savePref('mode', 'zen')
    savePref('model', 'deepseek-pro')
    savePref('thinking', 'high')
    assert.deepEqual(loadPrefs(), { mode: 'zen', model: 'deepseek-pro', thinking: 'high' })
    assert.equal(storage.store.get(MODE_KEY), 'zen')

    savePref('model', null)
    assert.equal(loadPrefs().model, null)
    assert.equal(storage.store.has(MODEL_KEY), false)
  } finally {
    storage.restore()
  }
})

test('prefs ignore values the runtime would reject', () => {
  const storage = fakeStorage()
  try {
    storage.store.set(MODE_KEY, 'turbo')
    storage.store.set(THINKING_KEY, 'ludicrous')
    assert.deepEqual(loadPrefs(), { mode: null, model: null, thinking: null })
  } finally {
    storage.restore()
  }
})

test('a spawn keeps the released-binary-compatible default', () => {
  // Old zen binaries crash without an explicit mode, so the pref never rides
  // the command line; a stale pref must not be able to fail a spawn.
  assert.deepEqual(spawnArgs(), ['--mode', 'ask', '--no-mcp'])
})

test('replay sends only what differs, mode first and thinking last', () => {
  const frames = replayFrames(
    { mode: 'zen', model: 'm2', thinking: 'low' },
    { mode: 'ask', model: 'm1', thinking: 'high' },
  )
  assert.deepEqual(frames, [
    { type: 'mode', mode: 'zen' },
    { type: 'model', model_id: 'm2' },
    { type: 'command', name: 'thinking', argument: 'low' },
  ])
})

test('replay is silent when the child already matches', () => {
  assert.deepEqual(
    replayFrames(
      { mode: 'ask', model: 'm1', thinking: 'high' },
      { mode: 'ask', model: 'm1', thinking: 'high' },
    ),
    [],
  )
  assert.deepEqual(replayFrames({ mode: null, model: null, thinking: null }, {}), [])
})

test('a child reporting no thinking level counts as off', () => {
  assert.deepEqual(replayFrames({ mode: null, model: null, thinking: 'off' }, {}), [])
  assert.deepEqual(
    replayFrames({ mode: null, model: null, thinking: 'low' }, { thinking: null }),
    [{ type: 'command', name: 'thinking', argument: 'low' }],
  )
})
