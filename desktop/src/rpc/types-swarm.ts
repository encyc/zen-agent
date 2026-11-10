/** Swarm wire types: room messages, tasks, agents and role views. Split
 * out of types.ts so the kernel-facing contract stays one screen. */

export type SwarmTaskState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type SwarmAgentStatus = 'idle' | 'running' | 'rebuilding'

export interface SwarmRole {
  id: string
  name: string
  description: string
  model: string
  thinking: string
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
  sourceMessageId: string
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
}

export interface SwarmTaskFrame extends SwarmTask {
  type: 'swarm_task'
  swarm: true
}

export interface SwarmAgentFrame extends SwarmAgentState {
  type: 'swarm_agent'
  swarm: true
}
