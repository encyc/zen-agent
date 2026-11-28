/** Archiving a session from the UI: one `session/archive` frame; archiving the
 * session on screen also starts a fresh conversation (the archived row would
 * otherwise reappear as the current-session placeholder), and both listings
 * refresh afterwards. Restoring is the same frame with `archived: false`. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { _useBridge, type Bridge } from '../src/rpc/client.ts'
import { useAppStore, _resetClientForTests } from '../src/state/appStore.ts'
import { useArchivedStore, useSessionStore } from '../src/state/stores.ts'

const CURRENT = '/work/p/sessions/current.jsonl'
const OTHER = '/work/p/sessions/other.jsonl'
const ARCHIVED_ROW = {
  path: OTHER,
  name: 'other',
  title: 'other',
  project: 'p',
  project_path: '/work/p',
  messages: 2,
  current: false,
  pinned: false,
  archived: true,
  archived_at: 1700000000,
  created_at: 1,
  updated_at: 2,
  model: '',
  derived_from: '',
}

interface Recorded {
  bridge: Bridge
  payloads: Record<string, unknown>[]
}

function scripted(): Recorded {
  const payloads: Record<string, unknown>[] = []
  // State the real backend keeps: the resident session path and the archive
  // sidecar. The listings answer from it, so a refresh observes the change.
  let currentPath = CURRENT
  let archived: string[] = [OTHER]
  const row = (path: string, current: boolean, archivedFlag: boolean) => ({
    ...ARCHIVED_ROW,
    path,
    name: path.split('/').pop()?.replace(/\.jsonl$/, ''),
    current,
    archived: archivedFlag,
    archived_at: archivedFlag ? 1700000000 : 0,
  })
  const bridge: Bridge = {
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      const payload = (args?.payload ?? {}) as Record<string, unknown>
      payloads.push(payload)
      const id = String(payload.id ?? '')
      const ok = (extra: Record<string, unknown> = {}) =>
        ({ type: 'result', id, ok: true, ...extra }) as T
      if (payload.type === 'session' && payload.op === 'list') {
        const sessions =
          payload.archived === 'only'
            ? archived.map((path) => row(path, false, true))
            : [row(currentPath, true, archived.includes(currentPath))]
        return ok({ scope: payload.scope, sessions }) as T
      }
      if (payload.type === 'session' && payload.op === 'new') {
        currentPath = '/work/p/sessions/fresh.jsonl'
        return ok({ path: currentPath }) as T
      }
      if (payload.type === 'session' && payload.op === 'archive') {
        const path = String(payload.path)
        archived = payload.archived
          ? [...new Set([...archived, path])]
          : archived.filter((entry) => entry !== path)
        return ok({
          path,
          archived: payload.archived,
          archived_at: payload.archived ? 1700000000 : 0,
        }) as T
      }
      return ok() as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {},
  }
  return { bridge, payloads }
}

/** Only the session-op frames: refreshes fan out into transcript/status
 * fetches whose order is not what this behavior pins. */
function ops(payloads: Record<string, unknown>[]): string[] {
  return payloads
    .filter((payload) => payload.type === 'session')
    .map((payload) => `${payload.type}:${payload.op}`)
}

test('archiveSession sends one archive frame and refreshes both listings', async () => {
  const { bridge, payloads } = scripted()
  _useBridge(bridge)
  _resetClientForTests()
  useSessionStore.getState().setSessions([
    { ...ARCHIVED_ROW, path: CURRENT, name: 'current', current: true, archived: false, archived_at: 0 },
    { ...ARCHIVED_ROW, current: false },
  ])
  useSessionStore.getState().setCurrent(CURRENT)

  await useAppStore.getState().archiveSession(OTHER, true)

  assert.deepEqual(ops(payloads), [
    'session:archive',
    'session:list',
    'session:list',
  ])
  assert.equal(payloads[0]?.archived, true)
  assert.deepEqual(useArchivedStore.getState().archived, [ARCHIVED_ROW])
  // The archived session is not the one on screen: the current session stays.
  assert.equal(useSessionStore.getState().currentPath, CURRENT)
})

test('archiving the on-screen session also starts a fresh one', async () => {
  const { bridge, payloads } = scripted()
  _useBridge(bridge)
  _resetClientForTests()
  useSessionStore.getState().setCurrent(CURRENT)

  await useAppStore.getState().archiveSession(CURRENT, true)

  assert.deepEqual(ops(payloads), [
    'session:archive',
    'session:new',
    'session:list',
    'session:list',
  ])
  assert.equal(useSessionStore.getState().currentPath, '/work/p/sessions/fresh.jsonl')
})

test('restore is one archive frame with archived false and no switch', async () => {
  const { bridge, payloads } = scripted()
  _useBridge(bridge)
  _resetClientForTests()
  useSessionStore.getState().setCurrent(CURRENT)
  useArchivedStore.getState().setArchived([ARCHIVED_ROW])

  await useAppStore.getState().archiveSession(OTHER, false)

  assert.deepEqual(ops(payloads), ['session:archive', 'session:list', 'session:list'])
  assert.equal(payloads[0]?.archived, false)
  // The refresh observes the sidecar change: the listing is empty again.
  assert.deepEqual(useArchivedStore.getState().archived, [])
  assert.equal(useSessionStore.getState().currentPath, CURRENT)
})

test('refreshArchivedSessions fills the archived store from the only listing', async () => {
  const { bridge, payloads } = scripted()
  _useBridge(bridge)
  _resetClientForTests()
  // The bridge starts with OTHER archived (its default sidecar state).
  await useAppStore.getState().refreshArchivedSessions()

  const list = payloads.find((payload) => payload.op === 'list')
  assert.equal(list?.archived, 'only')
  assert.equal(list?.scope, 'all')
  assert.deepEqual(useArchivedStore.getState().archived, [ARCHIVED_ROW])
})
