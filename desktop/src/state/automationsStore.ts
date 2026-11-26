/** Automations tab state: the cross-project list, plus the three mutations.
 *
 * The daemon owns scheduling; this store owns only the definitions and the
 * "run it now" mailbox. A poll keeps `next_due`/last-result fresh — 2 seconds is
 * the resolution the design promised, and it is cheap: the server reads two
 * small JSON files per project.
 */

import { create } from 'zustand'

import { useAppStore } from './appStore.ts'
import type { AutomationRow } from './automations.ts'

const REQUEST_TIMEOUT_MS = 8000
const POLL_MS = 2000

export interface AutomationsResult {
  ok?: boolean
  jobs: AutomationRow[]
  warnings: string[]
  /** Set when an op deliberately did nothing — currently "run" with no daemon. */
  skipped?: string
}

interface AutomationsState {
  rows: AutomationRow[]
  warnings: string[]
  failed: boolean
  loading: boolean
  /** Reload every project's automations. */
  refresh: () => Promise<void>
  /** Ask the daemon to run one now. */
  runNow: (project: string, name: string) => Promise<void>
  setEnabled: (project: string, name: string, enabled: boolean) => Promise<void>
  remove: (project: string, name: string) => Promise<void>
  add: (input: {
    project: string
    name: string
    prompt: string
    schedule: string
    timeout_s?: number
  }) => Promise<void>
}

export const useAutomationsStore = create<AutomationsState>((set, get) => ({
  rows: [],
  warnings: [],
  failed: false,
  loading: false,

  refresh: async () => {
    set({ loading: true })
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<AutomationsResult>({ type: 'automations', op: 'list_all' }, REQUEST_TIMEOUT_MS)
      set({
        rows: result.jobs ?? [],
        warnings: result.warnings ?? [],
        failed: false,
        loading: false,
      })
    } catch {
      // Keep the last list: a transient failure should not empty the page.
      set({ failed: true, loading: false })
    }
  },

  runNow: async (project, name) => {
    const result = await useAppStore
      .getState()
      .client()
      .send<AutomationsResult>(
        { type: 'automations', op: 'run', project, name },
        REQUEST_TIMEOUT_MS,
      )
    // The server says `skipped: "no-daemon"` when it queued nothing; surfacing
    // its warnings is what turns a click on a stopped scheduler into a message
    // instead of a silent no-op.
    set({ warnings: result.warnings ?? [], failed: result.ok === false })
    await get().refresh()
  },

  setEnabled: async (project, name, enabled) => {
    await useAppStore
      .getState()
      .client()
      .send<AutomationsResult>(
        { type: 'automations', op: 'update', project, name, enabled },
        REQUEST_TIMEOUT_MS,
      )
    await get().refresh()
  },

  remove: async (project, name) => {
    await useAppStore
      .getState()
      .client()
      .send<AutomationsResult>(
        { type: 'automations', op: 'rm', project, name },
        REQUEST_TIMEOUT_MS,
      )
    await get().refresh()
  },

  add: async ({ project, name, prompt, schedule, timeout_s }) => {
    await useAppStore
      .getState()
      .client()
      .send<AutomationsResult>(
        {
          type: 'automations',
          op: 'add',
          project,
          name,
          prompt,
          schedule,
          ...(timeout_s === undefined ? {} : { timeout_s }),
        },
        REQUEST_TIMEOUT_MS,
      )
    await get().refresh()
  },
}))

/** Poll while the panel is mounted; returns the unsubscribe function. */
export function startAutomationsPolling(): () => void {
  const store = useAutomationsStore.getState()
  void store.refresh()
  const timer = setInterval(() => void useAutomationsStore.getState().refresh(), POLL_MS)
  return () => clearInterval(timer)
}
