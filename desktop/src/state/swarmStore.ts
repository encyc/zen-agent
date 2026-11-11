/** The swarm room store: active-room frames in, room commands out.
 *
 * Swarm frames carry the project they belong to; `appStore.handleFrame`
 * applies them only after its project filter, so the store always shows the
 * active room. The store never imports `appStore`: that module's entry drags
 * the whole shell graph and cannot load under `node --test`, so `appStore`
 * registers the sender instead.
 */

import { create } from 'zustand'
import type {
  ServerFrame,
  SwarmAgentState,
  SwarmListResult,
  SwarmMessage,
  SwarmReloadResult,
  SwarmRole,
  SwarmTask,
  SwarmTranscriptResult,
} from '../rpc/types'
import { applyRunEvent, type RunView } from './swarmRun.ts'

/** A run's live rows are a tail: a long role run must not grow without bound. */
const RUN_ROW_CAP = 1500

export interface SwarmSender {
  send(request: Record<string, unknown>): Promise<Record<string, unknown>>
}

let sender: SwarmSender | null = null

/** Test seam, and the app's wiring point (see the end of `appStore`). */
export function _useSwarmSender(next: SwarmSender): void {
  sender = next
}

function send(request: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (sender === null) {
    return Promise.reject(new Error('swarm store: no client'))
  }
  return sender.send(request)
}

/** Notification text a frame should surface, or null when it is room traffic. */
export function swarmNoticeText(frame: ServerFrame): string | null {
  return frame.type === 'swarm_notice' && frame.text !== '' ? frame.text : null
}

function replaceBy<T>(rows: T[], row: T, key: (value: T) => string): T[] {
  const id = key(row)
  return rows.some((item) => key(item) === id)
    ? rows.map((item) => (key(item) === id ? row : item))
    : [...rows, row]
}

interface SwarmState {
  messages: SwarmMessage[]
  tasks: SwarmTask[]
  agents: SwarmAgentState[]
  roles: SwarmRole[]
  /** Role build/parse problems the host reported; shown above the room. */
  warnings: string[]
  /** True once a `list` snapshot landed. */
  loaded: boolean
  post(text: string): Promise<void>
  load(): Promise<void>
  reload(): Promise<void>
  cancel(id: string): Promise<void>
  retry(id: string): Promise<void>
  /** Drop the active room before switching projects (the room is per project). */
  reset(): void
  resetAgent(agentId: string): Promise<void>
  /** One task's run steps; null when the request failed. */
  transcript(taskId: string): Promise<SwarmTranscriptResult | null>
  /** Create/update/delete a role; the panel refreshes with `load` after. */
  saveRole(
    action: 'create' | 'update' | 'delete',
    id: string,
    manifest?: Record<string, unknown>,
  ): Promise<Record<string, unknown>>
  /** The global role library (a read-only template for this project's room). */
  libraryRoles(): Promise<SwarmRole[]>
  /** Copy a library role into this project's room, then reload it. */
  copyRole(id: string): Promise<{ ok: boolean; message?: string }>
  /** Live rows of a running task's detail (taskId → views). */
  runRows: Record<string, RunView[]>
  /** Drop a task's live rows (the settled transcript is authoritative). */
  clearRun(taskId: string): void
  applyFrame(frame: ServerFrame): void
}

export const useSwarmStore = create<SwarmState>((set, get) => ({
  messages: [],
  tasks: [],
  agents: [],
  roles: [],
  warnings: [],
  loaded: false,
  runRows: {},

  clearRun(taskId) {
    set((state) => {
      if (!(taskId in state.runRows)) return {}
      const runRows = { ...state.runRows }
      delete runRows[taskId]
      return { runRows }
    })
  },

  applyFrame(frame) {
    if (frame.type === 'swarm_message') {
      set((state) =>
        state.messages.some((item) => item.id === frame.id)
          ? {}
          : { messages: [...state.messages, frame] },
      )
      return
    }
    if (frame.type === 'swarm_task') {
      set((state) => ({
        tasks: replaceBy(state.tasks, frame, (task) => task.id),
      }))
      // Settling releases the live tail whether or not a panel is open; an
      // open panel bridges to the file transcript with its last live rows.
      if (frame.state === 'done' || frame.state === 'failed' || frame.state === 'cancelled') {
        get().clearRun(frame.id)
      }
      return
    }
    if (frame.type === 'swarm_event') {
      set((state) => {
        const next = applyRunEvent(state.runRows[frame.task] ?? [], frame.event)
        return {
          runRows: {
            ...state.runRows,
            // A long run keeps its newest rows; the panel is a live tail.
            [frame.task]: next.length > RUN_ROW_CAP ? next.slice(-RUN_ROW_CAP) : next,
          },
        }
      })
      return
    }
    if (frame.type === 'swarm_agent') {
      set((state) => ({
        agents: replaceBy(state.agents, frame, (agent) => agent.agent),
      }))
    }
  },

  async post(text) {
    await send({ type: 'swarm', op: 'post', text })
  },

  async load() {
    const answer = (await send({
      type: 'swarm',
      op: 'list',
    })) as unknown as SwarmListResult
    if (answer.ok !== true) return
    set({
      roles: answer.roles,
      agents: answer.agents,
      tasks: answer.tasks,
      messages: answer.messages,
      warnings: answer.warnings,
      loaded: true,
    })
  },

  async reload() {
    const answer = (await send({
      type: 'swarm',
      op: 'reload',
    })) as unknown as SwarmReloadResult
    if (answer.ok !== true) return
    set({ roles: answer.roles, warnings: answer.warnings })
  },

  async cancel(id) {
    await send({ type: 'swarm', op: 'cancel', taskId: id })
  },

  async retry(id) {
    await send({ type: 'swarm', op: 'retry', taskId: id })
  },

  reset() {
    set({
      messages: [],
      tasks: [],
      agents: [],
      roles: [],
      warnings: [],
      runRows: {},
      loaded: false,
    })
  },

  async resetAgent(agentId) {
    await send({ type: 'swarm', op: 'reset', agentId })
  },

  async transcript(taskId) {
    const answer = (await send({
      type: 'swarm',
      op: 'transcript',
      taskId,
    })) as unknown as SwarmTranscriptResult
    return answer.ok === true ? answer : null
  },

  async saveRole(action, id, manifest) {
    const answer = (await send({
      type: 'swarm',
      op: 'roles',
      action,
      roleId: id,
      ...(manifest === undefined ? {} : { manifest }),
    })) as unknown as { ok?: boolean; roles?: SwarmRole[]; warnings?: string[] }
    if (answer.ok === true && Array.isArray(answer.roles)) {
      set({
        roles: answer.roles,
        ...(Array.isArray(answer.warnings) ? { warnings: answer.warnings } : {}),
      })
    }
    return answer as unknown as Record<string, unknown>
  },

  async libraryRoles() {
    const answer = (await send({
      type: 'swarm',
      op: 'roles',
      action: 'library',
    })) as unknown as { ok?: boolean; roles?: SwarmRole[] }
    return answer.ok === true && Array.isArray(answer.roles) ? answer.roles : []
  },

  async copyRole(id) {
    const answer = (await send({
      type: 'swarm',
      op: 'roles',
      action: 'copy',
      roleId: id,
    })) as unknown as { ok?: boolean; message?: string }
    if (answer.ok !== true) {
      return {
        ok: false,
        message: typeof answer.message === 'string' ? answer.message : undefined,
      }
    }
    await get().load()
    return { ok: true }
  },
}))
