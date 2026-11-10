import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MODE_KEY,
  MODEL_KEY,
  THINKING_KEY,
  loadPrefs,
  replayFrames,
  savePref,
  spawnArgs,
} from '../src/state/prefs.ts'

/** A minimal localStorage over a Map, installed for the duration. */
function withStorage(body: (store: Map<string, string>) => void): void {
  const store = new Map<string, string>()
  const fake = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  }
  const saved = (globalThis as { localStorage?: unknown }).localStorage
  ;(globalThis as { localStorage?: unknown }).localStorage = fake
  try {
    body(store)
  } finally {
    ;(globalThis as { localStorage?: unknown }).localStorage = saved
  }
}

test('loadPrefs returns nulls when storage is empty', () => {
  withStorage((store) => {
    store.clear()
    assert.deepEqual(loadPrefs(), { mode: null, model: null, thinking: null })
  })
})

test('savePref writes and loadPrefs reads back', () => {
  withStorage((store) => {
    savePref('mode', 'zen')
    savePref('model', 'glm-5.3')
    savePref('thinking', 'high')
    assert.deepEqual(loadPrefs(), { mode: 'zen', model: 'glm-5.3', thinking: 'high' })
    assert.equal(store.get(MODE_KEY), 'zen')
    assert.equal(store.get(MODEL_KEY), 'glm-5.3')
    assert.equal(store.get(THINKING_KEY), 'high')
  })
})

test('garbage values fall back to null instead of failing the spawn', () => {
  withStorage((store) => {
    store.set(MODE_KEY, 'turbo')
    store.set(THINKING_KEY, 'eleven')
    assert.deepEqual(loadPrefs(), { mode: null, model: null, thinking: null })
  })
})

test('savePref null clears the stored value', () => {
  withStorage((store) => {
    savePref('model', 'glm-5.3')
    savePref('model', null)
    assert.equal(store.has(MODEL_KEY), false)
    assert.equal(loadPrefs().model, null)
  })
})

test('spawnArgs pins the released-binary-safe defaults', () => {
  assert.deepEqual(spawnArgs(), ['--mode', 'ask', '--no-mcp'])
})

test('spawnArgs resumes a session on the child that replaces another project', () => {
  assert.deepEqual(spawnArgs('/sessions/c.jsonl'), [
    '--mode',
    'ask',
    '--no-mcp',
    '--resume',
    '/sessions/c.jsonl',
  ])
  assert.deepEqual(spawnArgs(null), ['--mode', 'ask', '--no-mcp'])
})

test('replayFrames order: mode, then model, thinking last', () => {
  const frames = replayFrames(
    { mode: 'zen', model: 'glm-5.3', thinking: 'high' },
    { mode: 'ask', model: null, thinking: 'off' },
  )
  assert.deepEqual(frames.map((frame) => frame.type), ['mode', 'model', 'command'])
  assert.deepEqual(frames[2], { type: 'command', name: 'thinking', argument: 'high' })
})

test('replayFrames skips choices the child already carries', () => {
  const frames = replayFrames(
    { mode: 'ask', model: 'glm-5.3', thinking: 'off' },
    { mode: 'ask', model: 'glm-5.3', thinking: 'off' },
  )
  assert.deepEqual(frames, [])
})

test('replayFrames treats a missing live thinking as off', () => {
  const frames = replayFrames({ mode: null, model: null, thinking: 'off' }, {})
  assert.deepEqual(frames, [])
  const high = replayFrames({ mode: null, model: null, thinking: 'high' }, {})
  assert.equal(high.length, 1)
})
