/** Swarm wire types: room messages, tasks, agents and role views. Split
 * out of types.ts so the kernel-facing contract stays one screen. */

import type { AgentEventFrame } from './types.ts'

export type SwarmTaskState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type SwarmAgentStatus = 'idle' | 'running' | 'rebuilding'

export interface SwarmRole {
  id: string
  name: string
  description: string
  model: string | null
  thinking: string | null
  systemPrompt: string
  skills: string[]
  plugins: string[]
  /** Raw manifest entries: `${KEY}` placeholders, never resolved values. */
  mcpServers: Record<string, unknown>
  mode: string
  root: string
  tools: string[] | null
  inheritProject: boolean
  maxTurns: number | null
  maxCost: number | null
  missingCredentials: string[]
}

export interface SwarmAuthor {
  kind: 'user' | 'agent' | 'system'
  id: string
}

export interface SwarmMessage {
  id: string
  ts: number
  author: SwarmAuthor
  text: string
  mentions: string[]
  taskId: string | null
  chainDepth: number
}

export interface SwarmTask {
  id: string
  assignee: string
  state: SwarmTaskState
  sourceMessageId: string | null
  createdBy: SwarmAuthor
  chainId: string
  chainDepth: number
  chainCost: number
  createdAt: number
  updatedAt: number
  resultMessageId: string | null
  reason: string | null
  derivedFrom: string | null
}

export interface SwarmAgentState {
  agent: string
  name: string
  state: SwarmAgentStatus
  task: string | null
  pendingApply: boolean
}

export interface SwarmListResult {
  type: 'result'
  id: string
  ok: true
  roles: SwarmRole[]
  agents: SwarmAgentState[]
  tasks: SwarmTask[]
  messages: SwarmMessage[]
  warnings: string[]
}

export interface SwarmPostResult {
  type: 'result'
  id: string
  ok: true
  tasks: SwarmTask[]
}

export interface SwarmRolesResult {
  type: 'result'
  id: string
  ok: true
  roles: SwarmRole[]
  notes?: string[]
  warnings: string[]
}

export interface SwarmReloadResult {
  type: 'result'
  id: string
  ok: true
  roles: SwarmRole[]
  notes: string[]
  warnings: string[]
}

export interface SwarmOkResult {
  type: 'result'
  id: string
  ok: true
  taskId?: string
  agentId?: string
  cancelled?: boolean
  reset?: boolean
  retried?: boolean
  task?: SwarmTask | null
}

export interface SwarmMessageFrame extends SwarmMessage {
  type: 'swarm_message'
  swarm: true
  /** The room this frame belongs to; a v2 server sends none. */
  project?: string
}

export interface SwarmTaskFrame extends SwarmTask {
  type: 'swarm_task'
  swarm: true
  project?: string
}

export interface SwarmAgentFrame extends SwarmAgentState {
  type: 'swarm_agent'
  swarm: true
  project?: string
}

/** An agent mentioned @you: surface it as a notification, not in the room. */
export interface SwarmNoticeFrame {
  type: 'swarm_notice'
  swarm: true
  project?: string
  agent: string
  text: string
}

/** One role-run AgentEvent stamped with its task: the task detail streams
 * from these (the role's session file only lands when the run ends). */
export interface SwarmEventFrame {
  type: 'swarm_event'
  swarm: true
  project?: string
  agent: string
  task: string
  event: AgentEventFrame
}
