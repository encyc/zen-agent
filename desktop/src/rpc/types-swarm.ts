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
  /** The built role's model takes image input; the library view omits it. */
  supportsVision?: boolean
}

export interface SwarmAuthor {
  kind: 'user' | 'agent' | 'system'
  id: string
}

/** A snapshot of the message a post quotes; the source may be outside the
 * loaded window, so the card renders from this rather than a lookup. */
export interface SwarmRef {
  id: string
  author: SwarmAuthor
  text: string
}

export interface SwarmMessage {
  id: string
  ts: number
  author: SwarmAuthor
  text: string
  mentions: string[]
  taskId: string | null
  chainDepth: number
  ref?: SwarmRef | null
  images?: string[]
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
  /** The work item this round belongs to; null = ad-hoc history. */
  workItemId: string | null
  /** This round's own spend; chainCost is a cumulative snapshot. */
  runCost: number
}

export interface SwarmAgentState {
  agent: string
  name: string
  state: SwarmAgentStatus
  /** Which work item this instance is bound to; null = ad-hoc. */
  workItem: string | null
  task: string | null
  pendingApply: boolean
}

export type SwarmWorkState = 'open' | 'review' | 'closed'

/** One resumable job: a thread of rounds with its own context and score. */
export interface SwarmWork {
  id: string
  title: string
  state: SwarmWorkState
  rootMessageId: string
  openedBy: SwarmAuthor
  openedAt: number
  closedAt: number | null
  score: number | null
  scoreNote: string | null
  mergedInto: string | null
  continuedFrom: string | null
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

export interface SwarmWorkFrame extends SwarmWork {
  type: 'swarm_work'
  swarm: true
  project?: string
}

/** Every server-initiated swarm frame, for the ServerFrame union. */
export type SwarmServerFrame =
  | SwarmMessageFrame
  | SwarmTaskFrame
  | SwarmAgentFrame
  | SwarmNoticeFrame
  | SwarmWorkFrame
  | SwarmEventFrame
