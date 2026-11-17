/** The detached session window's boot: adopt the running sidecar (never a
 * second one), pin the project and session from the query override, load
 * that session's transcript — and leave the shared localStorage defaults
 * (LAST_CWD_KEY) alone so the main window's default project survives. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { _useBridge, type Bridge } from '../src/rpc/client.ts'
import { parseBootOverride } from '../src/state/bootMode.ts'
import { bootSessionWindow } from '../src/state/sessionBoot.ts'
import { useAppStore } from '../src/state/appStore.ts'
import { useChatStore } from '../src/state/chatStore.ts'
import { useSessionStore } from '../src/state/stores.ts'

const SESSION = '/work/p/sessions/b.jsonl'
const PROJECT = '/work/p'

interface Scripted {
  bridge: Bridge
  requests: Record<string, unknown>[]
  started: { value: boolean }
}

function scripted(options: { alive?: boolean } = {}): Scripted {
  const requests: Record<string, unknown>[] = []
  const started = { value: false }
  const bridge: Bridge = {
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      if (command === 'rpc_alive') return (options.alive !== false) as T
      if (command === 'rpc_start') {
        started.value = true
        return { pid: 7 } as T
      }
      const payload = (args?.payload ?? {}) as Record<string, unknown>
      requests.push(payload)
      const id = String(payload.id ?? '')
      const ok = (extra: Record<string, unknown> = {}) => ({
        type: 'result',
        id,
        ok: true,
        ...extra,
      })
      if (payload.type === 'hello') return ok({ protocol_version: 3, active: '/other/q' }) as T
      if (payload.type === 'project' && payload.op === 'open') {
        return ok({
          root: PROJECT,
          key: 'p',
          session: payload.resume === SESSION ? SESSION : '/other.jsonl',
          active: PROJECT,
        }) as T
      }
      if (payload.type === 'session' && payload.op === 'resume') {
        return ok({ path: payload.path }) as T
      }
      if (payload.type === 'transcript') {
        return {
          type: 'result',
          id,
          ok: true,
          project: PROJECT,
          session: payload.session,
          transcript: [],
          notices: [],
          compactions: [],
          hasMoreBefore: false,
          total: 0,
        } as T
      }
      if (payload.type === 'status') {
        return ok({ running: false, session: payload.session }) as T
      }
      if (payload.type === 'session' && payload.op === 'list') {
        // The host marks the resident slot's session current; the opened
        // session is that one.
        return ok({ sessions: [{ path: SESSION, current: true }] }) as T
      }
      return ok() as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {},
  }
  return { bridge, requests, started }
}

function reset(): void {
  useAppStore.setState({
    connection: 'idle',
    project: null,
    switching: null,
    exitCode: null,
    startFailure: null,
    pid: null,
    busy: false,
    stderrTail: [],
  })
  useChatStore.getState().reset()
  useSessionStore.getState().setCurrent(null)
  useSessionStore.getState().setSessions([])
}

test('parseBootOverride reads the query and rejects garbage', () => {
  assert.deepEqual(
    parseBootOverride('?boot=session&session=%2Fwork%2Fp%2Fsessions%2Fb.jsonl&project=%2Fwork%2Fp'),
    { kind: 'session', session: '/work/p/sessions/b.jsonl', project: '/work/p' },
  )
  assert.equal(parseBootOverride('?boot=session&session='), null)
  assert.equal(parseBootOverride('?boot=main'), null)
  assert.equal(parseBootOverride(''), null)
})

test('bootSessionWindow adopts the alive runtime without spawning another', async () => {
  const fake = scripted({ alive: true })
  _useBridge(fake.bridge)
  reset()

  await bootSessionWindow({
    kind: 'session',
    session: SESSION,
    project: PROJECT,
  })

  assert.equal(fake.started.value, false, 'the sidecar was already running')
  assert.equal(useAppStore.getState().connection, 'ready')
  assert.equal(useAppStore.getState().project, PROJECT, 'the query project is pinned, not hello’s')
  assert.equal(useSessionStore.getState().currentPath, SESSION)
  // The boot never writes LAST_CWD_KEY — structurally: sessionBoot.ts has no
  // localStorage access at all (see the module docstring).
})

test('a dead runtime falls back to rpc_start, then pins the session', async () => {
  const fake = scripted({ alive: false })
  _useBridge(fake.bridge)
  reset()

  await bootSessionWindow({ kind: 'session', session: SESSION, project: PROJECT })

  assert.equal(fake.started.value, true)
  assert.equal(useAppStore.getState().pid, 7)
  assert.equal(useSessionStore.getState().currentPath, SESSION)
})

test('when the slot holds another session, an explicit resume follows the open', async () => {
  const fake = scripted({ alive: true })
  _useBridge(fake.bridge)
  reset()
  // The scripted open answers with '/other.jsonl' unless resume === SESSION;
  // flip it to force the follow-up resume.
  _useBridge({
    ...fake.bridge,
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      if (command === 'rpc_send') {
        const payload = (args?.payload ?? {}) as Record<string, unknown>
        if (payload.type === 'project' && payload.op === 'open') {
          const id = String(payload.id)
          return {
            type: 'result',
            id,
            ok: true,
            root: PROJECT,
            session: '/other.jsonl',
            active: PROJECT,
          } as T
        }
      }
      return fake.bridge.invoke<T>(command, args)
    },
  })

  await bootSessionWindow({ kind: 'session', session: SESSION, project: PROJECT })

  const resumes = fake.requests.filter(
    (request) => request.type === 'session' && request.op === 'resume',
  )
  assert.equal(resumes.length, 1)
  assert.equal(resumes[0]?.path, SESSION)
  assert.equal(useSessionStore.getState().currentPath, SESSION)
})
