/** The frame router: session-tagged frames land in the right transcript —
 * an open pane's store for pane sessions, the primary store for the session
 * on screen — while swarm/zen frames and other background sessions stay out.
 * The usage baseline moves here with the router (it is keyed per session). */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  ensureFrameWiring,
  getPaneSink,
  handleFrame,
  recordUsageBaseline,
  setPaneSink,
  type FrameHandling,
  type PaneSink,
} from '../src/state/frameRouter.ts'
import { useChatStore } from '../src/state/chatStore.ts'
import { useNoticeStore, useSessionStore } from '../src/state/stores.ts'

const PANE_SESSION = '/work/p/sessions/pane.jsonl'
const OTHER_SESSION = '/work/p/sessions/other.jsonl'
const PROJECT = '/work/p'

interface SinkLog {
  events: [string | null, { type: string }][]
  usage: [string | null, unknown][]
  failures: [string | null, string][]
  ended: number
}

function fakeSink(log: SinkLog, panes: string[] = [PANE_SESSION]): PaneSink {
  return {
    hasPane: (session) => session !== null && panes.includes(session),
    applyEvent: (session, event) => log.events.push([session, event]),
    applyUsage: (session, usage) => log.usage.push([session, usage]),
    paneFailure: (session, text) => log.failures.push([session, text]),
    sessionEnded: () => {
      log.ended += 1
    },
  }
}

function handling(calls: string[] = []): FrameHandling {
  return {
    get: () => ({
      project: null,
      refreshStatus: async () => {
        calls.push('status')
      },
      refreshSessions: async () => {
        calls.push('sessions')
      },
    }),
    set: () => {},
  }
}

function taggedEvent(session: string, type: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { type: 'event', project: PROJECT, session, event: { type, ...extra } }
}

function reset(): void {
  useChatStore.getState().reset()
  useSessionStore.getState().setCurrent(null)
  useNoticeStore.getState().dismiss()
  setPaneSink(null)
}

test('an event tagged with an open pane session mutates that pane only', () => {
  reset()
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log))
  const event = taggedEvent(PANE_SESSION, 'tool_execution_start', {
    tool_call_id: 'c1',
    tool_name: 'bash',
    args: '{}',
  })

  handleFrame(event, handling())

  assert.equal(log.events.length, 1)
  assert.equal(log.events[0]?.[0], PANE_SESSION)
  assert.equal(log.events[0]?.[1].type, 'tool_execution_start')
  assert.equal(useChatStore.getState().nodes.length, 0)
})

test('a background session without a pane only refreshes the sidebar', async () => {
  reset()
  useSessionStore.getState().setCurrent('/work/p/sessions/current.jsonl')
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log, []))
  const calls: string[] = []
  const frame = taggedEvent(OTHER_SESSION, 'agent_end')

  handleFrame(frame, handling(calls))

  assert.deepEqual(log.events, [])
  assert.deepEqual(calls, ['sessions'])
  assert.equal(useChatStore.getState().nodes.length, 0)
})

test('the on-screen session still streams into the primary store', () => {
  reset()
  useSessionStore.getState().setCurrent(OTHER_SESSION)
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log, [PANE_SESSION]))

  handleFrame(
    taggedEvent(OTHER_SESSION, 'message_start', { message: { role: 'assistant' } }),
    handling(),
  )

  assert.deepEqual(log.events, [])
  const assistant = useChatStore.getState().nodes[0]
  assert.equal(assistant?.kind, 'assistant')
})

test('a pane prompt result lands its usage delta in the pane, not the banner store', () => {
  reset()
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log))
  recordUsageBaseline(PANE_SESSION, {
    input: 10,
    output: 5,
    cache_read: 0,
    cache_write: 0,
    cost: 0.1,
  })

  handleFrame(
    {
      type: 'result',
      id: 'r1',
      ok: true,
      project: PROJECT,
      session: PANE_SESSION,
      termination: 'stop',
      usage: { input: 14, output: 7, cache_read: 0, cache_write: 0, cost: 0.2 },
    },
    handling(),
  )

  assert.equal(log.usage.length, 1)
  const delta = log.usage[0]?.[1] as { input: number; cost: number }
  assert.equal(delta.input, 4)
  assert.equal(delta.cost > 0, true)
  assert.equal(useNoticeStore.getState().notice, null)
})

test('a pane-addressed error frame becomes a pane failure row', () => {
  reset()
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log))

  handleFrame(
    { type: 'error', id: 'e1', message: 'session lease busy', project: PROJECT, session: PANE_SESSION },
    handling(),
  )

  assert.deepEqual(log.failures, [[PANE_SESSION, 'session lease busy']])
  assert.equal(useNoticeStore.getState().notice, null)
})

test('swarm and fleet frames are consumed before pane routing ever sees them', () => {
  reset()
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  setPaneSink(fakeSink(log))

  handleFrame(
    { type: 'zen_fleet', project: PROJECT, session: PANE_SESSION, enabled: true, roster: [] },
    handling(),
  )

  assert.deepEqual(log.events, [])
  assert.deepEqual(log.usage, [])
})

test('the registry hands the sink to the shell exit path', () => {
  reset()
  const log: SinkLog = { events: [], usage: [], failures: [], ended: 0 }
  const mySink = fakeSink(log)
  setPaneSink(mySink)

  // The router itself never calls sessionEnded; the shell's exit listener
  // reads the registry and settles every pane.
  assert.equal(getPaneSink(), mySink)
  assert.equal(log.ended, 0)
  getPaneSink()?.sessionEnded()
  assert.equal(log.ended, 1)
})

test('ensureFrameWiring registers the frame handler once and names the session provider', () => {
  reset()
  useSessionStore.getState().setCurrent(OTHER_SESSION)
  const wired: ((frame: unknown) => void)[] = []
  const listening: number[] = []
  let provider: (() => string | null) | null = null
  const fakeClient = {
    onFrame(handler: (frame: never) => void) {
      wired.push(handler as (frame: unknown) => void)
      return () => {
        wired.length = 0
      }
    },
    ensureListening() {
      listening.push(1)
      return Promise.resolve()
    },
    setSessionProvider(get: () => string | null) {
      provider = get
    },
  } as never

  ensureFrameWiring(fakeClient, handling())
  ensureFrameWiring(fakeClient, handling())

  assert.equal(wired.length, 1)
  // The bridge listeners must attach even on a client that never starts a
  // runtime (the adopt path) — broadcast frames depend on them.
  assert.equal(listening.length, 1)
  assert.equal(provider?.(), OTHER_SESSION)
})
