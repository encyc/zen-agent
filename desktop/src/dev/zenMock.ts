/** Scripted `zen_fleet` frames for the dev/e2e bridge: a mutable in-memory
 * fleet that mirrors the host's wire contract (enable/disable/state/transcript,
 * see cli/rpc/ops_zen.py) without a real zen process. */

import type { RosterItem } from '../rpc/types.ts'

export interface ZenMockState {
  /** Whether the fleet is enabled; the fake flips it on enable/disable. */
  zenEnabled?: boolean
  /** Overrides the built-in router+worker roster served while enabled. */
  zenRoster?: RosterItem[]
  /** When true, enable refuses with the host's zen_busy refusal (busy slot). */
  zenBusy?: boolean
}

const ROUTER: RosterItem = {
  path: '/e2e/.zenagent/sessions/router.jsonl',
  title: 'router',
  focus: '',
  role: 'router',
  status: 'idle',
  last_report: '',
}

const WORKER: RosterItem = {
  path: '/e2e/.zenagent/sessions/w1.jsonl',
  title: 'w1',
  focus: 'research',
  role: 'worker',
  status: 'idle',
  last_report: '',
}

function roster(state: ZenMockState): RosterItem[] {
  return state.zenRoster ?? [ROUTER, WORKER]
}

export function handleZenFleet(
  state: ZenMockState,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const op = String(payload.op ?? '')
  const ok = (fields: Record<string, unknown>) => ({
    type: 'result',
    id: payload.id,
    ok: true,
    ...fields,
  })

  if (op === 'enable') {
    if (state.zenBusy === true) {
      return { type: 'error', id: payload.id, code: 'zen_busy', message: 'zen needs an idle session' }
    }
    state.zenEnabled = true
    return ok({ enabled: true, router: ROUTER.path, session: ROUTER.path, roster: roster(state) })
  }
  if (op === 'disable') {
    state.zenEnabled = false
    return ok({ enabled: false, session: '/e2e/.zenagent/sessions/prev.jsonl', roster: [] })
  }
  if (op === 'state') {
    const enabled = state.zenEnabled === true
    return ok({ enabled, router: enabled ? ROUTER.path : '', roster: enabled ? roster(state) : [] })
  }
  if (op === 'transcript') {
    const rows = [
      {
        kind: 'assistant',
        id: 'v1',
        text: 'worker 报告',
        thinking: null,
        timestamp: 0,
        model: 'mock',
        provider: 'mock',
        usage: null,
        duration_ms: 0,
      },
    ]
    return ok({ transcript: rows, notices: [], compactions: [], hasMoreBefore: false, total: rows.length })
  }
  return { type: 'error', id: payload.id, message: `unknown zen_fleet op: ${op}` }
}