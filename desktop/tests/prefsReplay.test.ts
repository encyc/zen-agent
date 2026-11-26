import test from 'node:test'
import assert from 'node:assert/strict'
import { restorePrefs } from '../src/state/prefsReplay.ts'
import { useCatalogStore, useNoticeStore, useStatusStore } from '../src/state/stores.ts'
import { loadPrefs, savePref } from '../src/state/prefs.ts'

/** A minimal localStorage over a Map, installed for the duration. */
async function withStorage(body: () => Promise<void>): Promise<void> {
  const store = new Map<string, string>()
  const fake = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  }
  const saved = (globalThis as { localStorage?: unknown }).localStorage
  ;(globalThis as { localStorage?: unknown }).localStorage = fake
  try {
    await body()
  } finally {
    ;(globalThis as { localStorage?: unknown }).localStorage = saved
  }
}

function liveStatus(modelRef: string, modelId: string): void {
  useStatusStore.getState().set({
    type: 'result',
    id: 's',
    ok: true,
    running: false,
    messages: 0,
    model: modelId,
    model_ref: modelRef,
    termination: null,
    mode: 'ask',
    thinking: 'off',
  } as never)
}

function fakeClient(reply: (payload: Record<string, unknown>) => Record<string, unknown>) {
  const sent: Record<string, unknown>[] = []
  return {
    sent,
    client: () => ({
      send: async (payload: Record<string, unknown>) => {
        sent.push(payload)
        return reply(payload)
      },
    }),
  }
}

test('a legacy bare pref is pinned to its one catalog match before replay', async () => {
  await withStorage(async () => {
    useCatalogStore
      .getState()
      .setModels([{ id: 'deepseek-flash', provider: 'deepseek' }] as never)
    // The child already runs the migrated model, so nothing needs replaying.
    liveStatus('deepseek/deepseek-flash', 'deepseek-flash')
    savePref('model', 'deepseek-flash')
    const fake = fakeClient(() => ({ type: 'result', ok: true }))

    await restorePrefs(() => ({
      client: fake.client,
      refreshStatus: async () => {},
      refreshCatalog: async () => {},
    }))

    assert.equal(loadPrefs().model, 'deepseek/deepseek-flash')
    assert.deepEqual(fake.sent, [])
  })
})

test('a genuinely duplicated legacy pref is refused once and dropped', async () => {
  await withStorage(async () => {
    useCatalogStore.getState().setModels([
      { id: 'deepseek-flash', provider: 'deepseek' },
      { id: 'deepseek-flash', provider: 'mirror' },
    ] as never)
    liveStatus('zai-coding-cn/glm-5.3', 'glm-5.3')
    savePref('model', 'deepseek-flash')
    useNoticeStore.getState().dismiss()
    const reason =
      "model ref 'deepseek-flash' names no provider; use provider/model-id"
    const fake = fakeClient(() => ({ type: 'result', ok: false, reason }))

    await restorePrefs(() => ({
      client: fake.client,
      refreshStatus: async () => {},
      refreshCatalog: async () => {},
    }))

    assert.deepEqual(fake.sent, [{ type: 'model', model_id: 'deepseek-flash' }])
    assert.equal(loadPrefs().model, null)
    assert.equal(useNoticeStore.getState().notice?.text, reason)
  })
})
