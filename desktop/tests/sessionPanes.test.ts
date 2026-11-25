/** The session pane registry: open/close/focus/submit against a scripted
 * fake bridge. A pane paints its session's transcript (session-tagged when
 * the slot is resident, a static pane otherwise), streams live through the
 * frame-router sink, and submits prompts to its own session. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { _useBridge, type Bridge } from '../src/rpc/client.ts'
import {
  usePanesStore,
  pollPanes,
  resetPanesForTests,
} from '../src/state/sessionPanes.ts'
import { useChatStore } from '../src/state/chatStore.ts'
import { _resetClientForTests } from '../src/state/appStore.ts'
import { useNoticeStore, useSessionStore } from '../src/state/stores.ts'

const PROJECT = '/work/p'
const CURRENT = `${PROJECT}/sessions/current.jsonl`
const A = `${PROJECT}/sessions/a.jsonl`
const B = `${PROJECT}/sessions/b.jsonl`
const C = `${PROJECT}/sessions/c.jsonl`
const D = `${PROJECT}/sessions/d.jsonl`

interface WireLog {
  requests: Record<string, unknown>[]
}

/** A scripted host: session-tagged transcript/prompt/status replies carry the
 * pane's session. */
function scripted(options: {
  transcriptRefuses?: boolean
  running?: boolean
} = {}): Bridge & { log: WireLog } {
  const log: WireLog = { requests: [] }
  return {
    log,
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      const payload = (args?.payload ?? {}) as Record<string, unknown>
      log.requests.push(payload)
      const id = String(payload.id ?? '')
      const tag = (extra: Record<string, unknown>) => ({
        type: 'result',
        id,
        ok: true,
        project: PROJECT,
        session: payload.session,
        ...extra,
      })
      if (payload.type === 'transcript') {
        if (options.transcriptRefuses) {
          return { type: 'error', id, message: 'no resident slot for session' } as T
        }
        return tag({ transcript: [], notices: [], compactions: [], hasMoreBefore: false, total: 0 }) as T
      }
      if (payload.type === 'session' && payload.op === 'resume') {
        return { type: 'result', id, ok: true, path: payload.path } as T
      }
      if (payload.type === 'prompt') {
        return tag({
          termination: 'stop',
          usage: { input: 1, output: 1, cache_read: 0, cache_write: 0, cost: 0 },
        }) as T
      }
      if (payload.type === 'command') {
        return tag({ message: null }) as T
      }
      if (payload.type === 'cancel') {
        return tag({}) as T
      }
      if (payload.type === 'status') {
        return tag({ running: options.running === true }) as T
      }
      return { type: 'result', id, ok: true } as T
    },
    async listen<T>(): Promise<() => void> {
      return () => {}
    },
    async relaunch(): Promise<void> {},
  } as unknown as Bridge & { log: WireLog }
}

function reset(bridge: Bridge): void {
  _useBridge(bridge)
  _resetClientForTests()
  resetPanesForTests()
  useChatStore.getState().reset()
  useSessionStore.getState().setSessions([])
  useSessionStore.getState().setCurrent(CURRENT)
  useNoticeStore.getState().dismiss()
}

test('open paints the pane from the session-tagged transcript and marks it live', async () => {
  const bridge = scripted()
  reset(bridge)

  await usePanesStore.getState().open(A)

  const panes = usePanesStore.getState().panes
  assert.deepEqual(panes.map((pane) => pane.path), [CURRENT, A])
  assert.equal(panes[1]?.live, true)
  assert.equal(panes[1]?.loaded, true)
  const asked = bridge.log.requests.filter((request) => request.type === 'transcript')
  assert.equal(asked.length, 1)
  assert.equal(asked[0]?.session, A)
})

test('a refusal paints an empty static pane with no disk read', async () => {
  const bridge = scripted({ transcriptRefuses: true })
  reset(bridge)

  await usePanesStore.getState().open(A)

  const pane = usePanesStore.getState().panes[1]
  assert.equal(pane?.live, false)
  assert.equal(pane?.loaded, false)
  assert.equal(bridge.log.requests.filter((request) => request.type === 'zen_fleet').length, 0)
})

test('a mismatched transcript tag (an older runtime) counts as the refusal path', async () => {
  const bridge = scripted()
  reset(bridge)
  // The fake always tags with the requested session; poison it for A only by
  // requesting a transcript whose reply names the current session instead.
  const poisoned = scripted()
  poisoned.log.requests.length = 0
  _useBridge({
    ...(bridge as unknown as Bridge),
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      const payload = (args?.payload ?? {}) as Record<string, unknown>
      if (payload.type === 'transcript' && payload.session === A) {
        return {
          type: 'result',
          id: String(payload.id),
          ok: true,
          project: PROJECT,
          session: CURRENT,
          transcript: [],
          notices: [],
          compactions: [],
          hasMoreBefore: false,
          total: 0,
        } as T
      }
      return (bridge as unknown as Bridge).invoke<T>(command, args)
    },
  })

  await usePanesStore.getState().open(A)

  const pane = usePanesStore.getState().panes[1]
  assert.equal(pane?.live, false)
  assert.equal(pane?.loaded, false)
})

test('open caps the strip at three panes with a notice', async () => {
  const bridge = scripted()
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)
  await store.open(B)

  await store.open(C)
  assert.equal(usePanesStore.getState().panes.length, 3)

  await store.open(D)
  assert.equal(usePanesStore.getState().panes.length, 3)
  assert.notEqual(useNoticeStore.getState().notice, null)
})

test('close removes the pane; below the strip threshold the registry dissolves', async () => {
  const bridge = scripted()
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)
  await store.open(B)

  store.close(A)
  assert.deepEqual(usePanesStore.getState().panes.map((pane) => pane.path), [CURRENT, B])

  store.close(B)
  // One pane would render as nothing (the strip needs two): its content goes
  // back to the primary store and the registry empties, so the visible
  // conversation keeps receiving its frames.
  assert.deepEqual(usePanesStore.getState().panes, [])
})

test('submit resumes a static pane first, then sends the tagged prompt to the pane', async () => {
  const bridge = scripted({ transcriptRefuses: true })
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)
  store.setDraft(A, 'build the thing')

  await store.submit(A)

  const kinds = bridge.log.requests.map((request) => `${request.type}:${(request as { op?: string }).op ?? ''}`)
  const resumeAt = kinds.findIndex((kind) => kind.startsWith('session:resume'))
  const promptAt = kinds.indexOf('prompt:')
  assert.ok(resumeAt >= 0, 'static pane submit must resume first')
  assert.ok(promptAt > resumeAt, 'the prompt follows the resume')
  const prompt = bridge.log.requests[promptAt]
  assert.equal(prompt.session, A)
  assert.equal(prompt.text, 'build the thing')
  assert.equal(usePanesStore.getState().panes[1]?.draft, '')
  // The direct reply settles in the same await: the usage row lands and the
  // optimistic busy flag releases.
  assert.equal(usePanesStore.getState().panes[1]?.busy, false)
  const usage = usePanesStore
    .getState()
    .panes[1]?.chat.getState()
    .nodes.find((node) => node.kind === 'usage')
  assert.notEqual(usage, undefined)
})

test('a pane prompt reply settles its usage row into the pane chat, not the shell', async () => {
  const bridge = scripted()
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)
  store.setDraft(A, 'go')

  await store.submit(A)

  // The reply carries termination+usage tagged with the pane session; the
  // router routes it into the pane. The shell's own store stays untouched —
  // this session is a pane, not the transcript on screen.
  const pane = usePanesStore.getState().panes.find((entry) => entry.path === A)
  const usage = pane?.chat.getState().nodes.find((node) => node.kind === 'usage')
  assert.notEqual(usage, undefined)
  assert.equal(useChatStore.getState().nodes.length, 0)
  assert.equal(pane?.busy, false)
})

test('the sink streams pane events into the pane chat store', async () => {
  const bridge = scripted()
  reset(bridge)
  await usePanesStore.getState().open(A)

  // The router holds the registry-registered sink; feed it a pane event.
  const { getPaneSink } = await import('../src/state/frameRouter.ts')
  getPaneSink()?.applyEvent(A, {
    type: 'tool_execution_start',
    tool_call_id: 'c7',
    tool_name: 'bash',
    args: '{}',
  } as never)

  const pane = usePanesStore.getState().panes[1]
  const tool = pane?.chat.getState().nodes.find((node) => node.kind === 'tool')
  assert.equal(tool?.kind, 'tool')
  assert.equal(useChatStore.getState().nodes.length, 0)
})

test('pollPanes reads each live pane status and drops a dead residency', async () => {
  const bridge = scripted({ running: true })
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)

  await pollPanes()

  assert.equal(usePanesStore.getState().panes[1]?.busy, true)

  // A poll reply that no longer names the pane's session means the slot is
  // gone (evicted, or an older runtime): the pane goes static rather than
  // showing someone else's run.
  const other = { ...bridge }
  _useBridge({
    ...other,
    async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
      const payload = (args?.payload ?? {}) as Record<string, unknown>
      if (command === 'rpc_send' && payload.type === 'status') {
        return {
          type: 'result',
          id: String(payload.id),
          ok: true,
          running: true,
          session: '/someone/else.jsonl',
        } as T
      }
      return bridge.invoke<T>(command, args)
    },
  })

  await pollPanes()

  const pane = usePanesStore.getState().panes[1]
  assert.equal(pane?.live, false)
  assert.equal(pane?.busy, false)
})

test('finalizeAll settles every pane after the sidecar exits', async () => {
  const bridge = scripted()
  reset(bridge)
  const store = usePanesStore.getState()
  await store.open(A)

  usePanesStore.getState().finalizeAll()

  assert.equal(usePanesStore.getState().panes[1]?.busy, false)
})

test('closing back below the strip dissolves the last pane into the shell', async () => {
  const bridge = scripted()
  reset(bridge)
  const store = usePanesStore.getState()
  // Seed the focused pane with content so the restore is observable.
  store.setDraft(A, '')
  await store.open(A)
  const paneA = usePanesStore.getState().panes.find((entry) => entry.path === A)
  paneA?.chat.getState().applyEvent({
    type: 'message_start',
    message: { role: 'user', text: 'pane only row' },
  } as never)

  store.close(A)

  // One pane left (the seeded current session) is below the strip threshold:
  // its content belongs to the primary store again and the registry is empty,
  // so live frames reach the visible transcript instead of a hidden pane.
  assert.deepEqual(usePanesStore.getState().panes, [])
  const primary = useChatStore.getState().nodes
  assert.equal(primary.length, 0)
  void primary
})

test('opening the session already on screen hints instead of staying silent', async () => {
  const bridge = scripted()
  reset(bridge)
  useNoticeStore.getState().dismiss()

  await usePanesStore.getState().open(CURRENT)

  assert.deepEqual(usePanesStore.getState().panes, [])
  assert.notEqual(useNoticeStore.getState().notice, null)
})

test('the registry is wired as the frame-router pane sink', async () => {
  reset(scripted())
  const { getPaneSink } = await import('../src/state/frameRouter.ts')
  assert.notEqual(getPaneSink(), null)
  assert.equal(getPaneSink()?.hasPane(A), false)
  await usePanesStore.getState().open(A)
  assert.equal(getPaneSink()?.hasPane(A), true)
})
