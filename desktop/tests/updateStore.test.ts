/** When to look for a release at startup. Kept pure so the decision can be
 * tested without the Tauri updater plugin; the store below is the thin glue
 * that talks to the plugin and to localStorage. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { bootUpdateCheck } from '../src/state/updateStore.ts'
import { _useBridge } from '../src/rpc/client.ts'

const base = {
  current: '0.8.1',
  settings: { updateChecks: true },
  envDisabled: false,
  busy: false,
  pendingApprovals: 0,
}

test('the env switch and the settings switch both silence the boot check', () => {
  assert.equal(bootUpdateCheck({ ...base, envDisabled: true }), 'skip')
  assert.equal(bootUpdateCheck({ ...base, settings: { updateChecks: false } }), 'skip')
})

test('a fresh cache answers without touching the network', () => {
  assert.equal(
    bootUpdateCheck({ ...base, cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null } }),
    'cache',
  )
})

test('a stale cache triggers one network check', () => {
  assert.equal(
    bootUpdateCheck({ ...base, cache: { checkedAt: 0, latest: '0.9.0', notified: null } }),
    'network',
  )
})

test('a missing cache triggers one network check', () => {
  assert.equal(bootUpdateCheck(base), 'network')
})

test('install() relaunches through the RPC bridge seam', async () => {
  ;(globalThis as Record<string, unknown>).__APP_VERSION__ ??= '0.8.4'
  const { useUpdateStore, _usePluginLoader } = await import('../src/state/updateStore.ts')
  _usePluginLoader(async () => ({
    check: async () => ({
      version: '0.9.0',
      downloadAndInstall: async () => {},
    }),
  }))
  const calls: string[] = []
  _useBridge({
    async invoke<T>(command: string): Promise<T> {
      calls.push(`invoke:${command}`)
      return {} as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {
      calls.push('relaunch')
    },
  })
  useUpdateStore.setState({
    cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null },
    phase: 'available',
    pending: null,
    selfUpdatable: true,
    message: null,
  })

  await useUpdateStore.getState().install()

  assert.deepEqual(calls, ['relaunch'])
  assert.equal(useUpdateStore.getState().phase, 'installing')
})

test('install() re-checks when the pending handle was lost to a reload', async () => {
  ;(globalThis as Record<string, unknown>).__APP_VERSION__ ??= '0.8.4'
  const { useUpdateStore, _usePluginLoader } = await import('../src/state/updateStore.ts')
  let downloads = 0
  _usePluginLoader(async () => ({
    check: async () => ({
      version: '0.9.0',
      downloadAndInstall: async () => {
        downloads += 1
      },
    }),
  }))
  _useBridge({
    async invoke<T>(): Promise<T> {
      return {} as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {},
  })
  useUpdateStore.setState({
    cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null },
    phase: 'available',
    pending: null,
    selfUpdatable: true,
    message: null,
  })
  await useUpdateStore.getState().install()
  assert.equal(downloads, 1)
})

/** Boot reads both policy flags from the shell in one invoke: the old pair of
 * `updates_*` commands is gone, and a removal here would silently re-enable
 * updates under ZENAGENT_NO_UPDATE_CHECK. */
function stubBootGlobals(): Record<string, unknown> {
  const globals = globalThis as Record<string, unknown>
  globals.__APP_VERSION__ ??= '0.8.4'
  globals.__UPDATE_ENABLED__ = true
  globals.localStorage = {
    getItem: () => JSON.stringify({ checkedAt: Date.now(), latest: '0.8.4', notified: null }),
    setItem: () => {},
    removeItem: () => {},
  }
  return globals
}

function recordCommands(answer: { updatesDisabled: boolean; selfUpdatable: boolean }): {
  commands: string[]
} {
  const commands: string[] = []
  _useBridge({
    async invoke<T>(command: string): Promise<T> {
      commands.push(command)
      return answer as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {},
  })
  return { commands }
}

test('start() reads both boot flags from a single get_initial_state call', async () => {
  stubBootGlobals()
  const { commands } = recordCommands({ updatesDisabled: false, selfUpdatable: false })
  const { useUpdateStore } = await import('../src/state/updateStore.ts')
  useUpdateStore.setState({ selfUpdatable: true, phase: 'idle', cache: null, message: null, pending: null })

  await useUpdateStore.getState().start()

  assert.deepEqual(commands, ['get_initial_state'])
  assert.equal(useUpdateStore.getState().selfUpdatable, false)
})

test('an env-disabled shell skips the check after that one round-trip', async () => {
  stubBootGlobals()
  const { commands } = recordCommands({ updatesDisabled: true, selfUpdatable: true })
  const { useUpdateStore } = await import('../src/state/updateStore.ts')
  useUpdateStore.setState({ selfUpdatable: true, phase: 'idle', cache: null, message: null, pending: null })

  await useUpdateStore.getState().start()

  assert.deepEqual(commands, ['get_initial_state'])
  assert.equal(useUpdateStore.getState().phase, 'idle')
})
