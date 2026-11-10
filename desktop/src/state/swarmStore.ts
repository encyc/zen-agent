/** The swarm room store: global frames in, room commands out.
 *
 * Swarm frames carry no project or session tag, so `appStore.handleFrame`
 * diverts them here before any project/session filtering. The store never
 * imports `appStore`: that module's entry drags the whole shell graph and
 * cannot load under `node --test`, so `appStore` registers the sender instead.
 */

import { create } from 'zustand'
import type {
  ServerFrame,
  SwarmAgentState,
  SwarmListResult,
  SwarmMessage,
  SwarmRole,
  SwarmTask,
} from '../rpc/types'

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
  /** True once a `list` snapshot landed. */
  loaded: boolean
  post(text: string): Promise<void>
  load(): Promise<void>
  cancel(id: string): Promise<void>
  retry(id: string): Promise<void>
  reset(agentId: string): Promise<void>
  applyFrame(frame: ServerFrame): void
}

export const useSwarmStore = create<SwarmState>((set) => ({
  messages: [],
  tasks: [],
  agents: [],
  roles: [],
  loaded: false,

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
      loaded: true,
    })
  },

  async cancel(id) {
    await send({ type: 'swarm', op: 'cancel', taskId: id })
  },

  async retry(id) {
    await send({ type: 'swarm', op: 'retry', taskId: id })
  },

  async reset(agentId) {
    await send({ type: 'swarm', op: 'reset', agentId })
  },
}))
