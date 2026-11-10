/** Scripted swarm room for the dev/e2e bridge: a mutable in-memory room that
 * mirrors the host's wire contract (list/post/cancel/retry/reset/roles) and
 * plays a small post→task→report chain, frames included. */

import type {
  ServerFrame,
  SwarmAgentState,
  SwarmAuthor,
  SwarmMessage,
  SwarmRole,
  SwarmTask,
} from '../rpc/types'

export interface SwarmMockState {
  /** Room snapshot served for `swarm list`; the fake appends to these. */
  swarmRoles?: SwarmRole[]
  swarmAgents?: SwarmAgentState[]
  swarmTasks?: SwarmTask[]
  swarmMessages?: SwarmMessage[]
  /** Role build problems to show above the room. */
  swarmWarnings?: string[]
}

export interface SwarmFixtureOptions extends Partial<SwarmMockState> {
  /** Seed one running research task, which the cancel flow needs. */
  runningTask?: boolean
}

const ROLES: SwarmRole[] = [
  {
    id: 'research',
    name: 'Research',
    description: '查资料、做对比、给结论',
    model: 'deepseek-chat',
    thinking: 'medium',
    systemPrompt: 'You research.',
    mode: 'ask',
    root: '/Users/e2e/.zenagent-workspaces/research',
    tools: null,
    inheritProject: false,
    maxTurns: 30,
    maxCost: 1.5,
    missingCredentials: [],
  },
  {
    id: 'builder',
    name: 'Builder',
    description: '实现并验证改动',
    model: 'deepseek-chat',
    thinking: 'medium',
    systemPrompt: 'You build.',
    mode: 'ask',
    root: '/Users/e2e/.zenagent-workspaces/builder',
    tools: null,
    inheritProject: false,
    maxTurns: 30,
    maxCost: 1.5,
    missingCredentials: [],
  },
]

/** What each role reports back; the @ in a report starts the next chain leg. */
const SCRIPT: Record<string, string> = {
  research: '结论是 Z。@builder 复核一下',
  builder: '复核完毕：结论成立。',
}

const TERMINAL = new Set(['done', 'failed', 'cancelled'])

type Room = Required<SwarmMockState>

/** A frame the shell would have received from the host. The event payload is
 * the Tauri envelope the client's listener unwraps (`payload.frame`). */
function emit(frame: ServerFrame): void {
  if (typeof window === 'undefined') return
  const send = (window as unknown as { __ZEN_E2E_EMIT__?: (event: string, payload: unknown) => void })
    .__ZEN_E2E_EMIT__
  if (send !== undefined) {
    send('rpc:frame', { event: 'rpc:frame', id: 1, payload: { frame } })
  }
}

function idleAgents(roles: SwarmRole[]): SwarmAgentState[] {
  return roles.map((role) => ({
    agent: role.id,
    name: role.name,
    state: 'idle',
    task: null,
    pendingApply: false,
  }))
}

export function swarmFixture(options: SwarmFixtureOptions = {}): SwarmMockState {
  const { runningTask = false, ...overrides } = options
  const roles = overrides.swarmRoles ?? ROLES
  const agents = (overrides.swarmAgents ?? idleAgents(roles)).map((agent) => ({ ...agent }))
  const tasks = [...(overrides.swarmTasks ?? [])]
  const messages = [...(overrides.swarmMessages ?? [])]
  if (runningTask) {
    const now = Date.now()
    messages.push({
      id: 'm_seed',
      ts: now,
      author: { kind: 'user', id: 'you' },
      text: '@research 跑一个长任务',
      mentions: ['research'],
      taskId: 't_seed',
      chainDepth: 0,
    })
    tasks.push({
      id: 't_seed',
      assignee: 'research',
      state: 'running',
      sourceMessageId: 'm_seed',
      createdBy: { kind: 'user', id: 'you' },
      chainId: 'c_seed',
      chainDepth: 1,
      chainCost: 0,
      createdAt: now,
      updatedAt: now,
      resultMessageId: null,
      reason: null,
      derivedFrom: null,
    })
    agents[0] = { ...agents[0], state: 'running', task: 't_seed' }
  }
  return {
    ...overrides,
    swarmRoles: roles,
    swarmAgents: agents,
    swarmTasks: tasks,
    swarmMessages: messages,
  }
}

function room(state: SwarmMockState): Room {
  state.swarmRoles ??= ROLES
  state.swarmAgents ??= idleAgents(state.swarmRoles)
  state.swarmTasks ??= []
  state.swarmMessages ??= []
  state.swarmWarnings ??= []
  return state as Room
}

function copy<T extends object>(rows: T[]): T[] {
  return rows.map((row) => ({ ...row }))
}

function roleMentions(rows: Room, text: string): string[] {
  const ids = new Set(rows.swarmRoles.map((role) => role.id))
  const found: string[] = []
  for (const match of text.matchAll(/@([A-Za-z][\w-]*)/g)) {
    if (ids.has(match[1]) && !found.includes(match[1])) found.push(match[1])
  }
  return found
}

function appendMessage(
  rows: Room,
  fields: {
    author: SwarmAuthor
    text: string
    mentions?: string[]
    taskId?: string | null
    depth?: number
  },
): SwarmMessage {
  const message: SwarmMessage = {
    id: `m_${rows.swarmMessages.length + 1}`,
    ts: Date.now(),
    author: fields.author,
    text: fields.text,
    mentions: fields.mentions ?? roleMentions(rows, fields.text),
    taskId: fields.taskId ?? null,
    chainDepth: fields.depth ?? 0,
  }
  rows.swarmMessages.push(message)
  emit({ ...message, type: 'swarm_message', swarm: true })
  return message
}

function setAgent(rows: Room, id: string, state: SwarmAgentState['state'], task: string | null) {
  const index = rows.swarmAgents.findIndex((agent) => agent.agent === id)
  const known = index >= 0 ? rows.swarmAgents[index] : undefined
  const role = rows.swarmRoles.find((entry) => entry.id === id)
  const row: SwarmAgentState = {
    agent: id,
    name: role?.name ?? known?.name ?? id,
    state,
    task,
    pendingApply: known?.pendingApply ?? false,
  }
  if (index >= 0) rows.swarmAgents[index] = row
  else rows.swarmAgents.push(row)
  emit({ ...row, type: 'swarm_agent', swarm: true })
}

/** A role view built the way the host builds one from a saved manifest. */
function roleView(rows: Room, id: string, manifest: Record<string, unknown>): SwarmRole {
  const prior = rows.swarmRoles.find((role) => role.id === id)
  const limits = (manifest.limits ?? {}) as Record<string, unknown>
  const tools = Array.isArray(manifest.tools) ? manifest.tools.map(String) : null
  return {
    id,
    name: String(manifest.name ?? prior?.name ?? id),
    description: String(manifest.description ?? ''),
    model: typeof manifest.model === 'string' ? manifest.model : (prior?.model ?? null),
    thinking: typeof manifest.thinking === 'string' ? manifest.thinking : (prior?.thinking ?? null),
    systemPrompt: typeof manifest.systemPrompt === 'string' ? manifest.systemPrompt : (prior?.systemPrompt ?? ''),
    mode: String(manifest.mode ?? 'ask'),
    root: String(manifest.root ?? `/Users/e2e/.zenagent-workspaces/${id}`),
    tools,
    inheritProject: manifest.inheritProject === true,
    maxTurns: typeof limits.maxTurns === 'number' ? limits.maxTurns : null,
    maxCost: typeof limits.maxCost === 'number' ? limits.maxCost : null,
    missingCredentials: prior?.missingCredentials ?? [],
  }
}

function upsertRole(rows: Room, role: SwarmRole) {
  const index = rows.swarmRoles.findIndex((entry) => entry.id === role.id)
  if (index >= 0) rows.swarmRoles[index] = role
  else rows.swarmRoles.push(role)
}

function addTask(
  rows: Room,
  spec: {
    assignee: string
    source: string | null
    author: SwarmAuthor
    chainId: string
    depth: number
    derivedFrom?: string | null
  },
): SwarmTask {
  const now = Date.now()
  const task: SwarmTask = {
    id: `t_${rows.swarmTasks.length + 1}`,
    assignee: spec.assignee,
    state: 'queued',
    sourceMessageId: spec.source,
    createdBy: spec.author,
    chainId: spec.chainId,
    chainDepth: spec.depth,
    chainCost: 0,
    createdAt: now,
    updatedAt: now,
    resultMessageId: null,
    reason: null,
    derivedFrom: spec.derivedFrom ?? null,
  }
  rows.swarmTasks.push(task)
  emit({ ...task, type: 'swarm_task', swarm: true })
  return task
}

function transition(task: SwarmTask, state: SwarmTask['state'], reason: string | null = null) {
  task.state = state
  task.reason = reason
  task.updatedAt = Date.now()
  emit({ ...task, type: 'swarm_task', swarm: true })
}

/** The scripted run: busy → report (its @ starts the next leg) → done. */
function run(rows: Room, task: SwarmTask, depth: number): void {
  setAgent(rows, task.assignee, 'running', task.id)
  transition(task, 'running')
  const reply = SCRIPT[task.assignee]
  let result: SwarmMessage | null = null
  if (reply !== undefined) {
    result = appendMessage(rows, {
      author: { kind: 'agent', id: task.assignee },
      text: reply,
      taskId: task.id,
      depth: depth + 1,
    })
    task.resultMessageId = result.id
  }
  transition(task, 'done')
  setAgent(rows, task.assignee, 'idle', null)
  if (result === null) return
  for (const roleId of result.mentions) {
    const child = addTask(rows, {
      assignee: roleId,
      source: result.id,
      author: result.author,
      chainId: task.chainId,
      depth: depth + 1,
    })
    run(rows, child, depth + 1)
  }
}

export function handleSwarm(
  state: SwarmMockState,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const rows = room(state)
  const ok = (fields: Record<string, unknown>) => ({
    type: 'result',
    id: payload.id,
    ok: true,
    ...fields,
  })
  const op = String(payload.op ?? '')

  if (op === 'list') {
    return ok({
      roles: copy(rows.swarmRoles),
      agents: copy(rows.swarmAgents),
      tasks: copy(rows.swarmTasks),
      messages: copy(rows.swarmMessages),
      warnings: [...rows.swarmWarnings],
    })
  }

  if (op === 'post') {
    const text = String(payload.text ?? '')
    const message = appendMessage(rows, { author: { kind: 'user', id: 'you' }, text })
    const created: SwarmTask[] = []
    for (const roleId of message.mentions) {
      const task = addTask(rows, {
        assignee: roleId,
        source: message.id,
        author: message.author,
        chainId: `c_${rows.swarmTasks.length + 1}`,
        depth: message.chainDepth + 1,
      })
      created.push(task)
      run(rows, task, message.chainDepth + 1)
    }
    return ok({ tasks: copy(created) })
  }

  if (op === 'cancel') {
    const taskId = String(payload.taskId ?? '')
    const task = rows.swarmTasks.find((row) => row.id === taskId)
    if (task === undefined || TERMINAL.has(task.state)) return ok({ taskId, cancelled: false })
    transition(task, 'cancelled', 'aborted')
    setAgent(rows, task.assignee, 'idle', null)
    return ok({ taskId, cancelled: true })
  }

  if (op === 'retry') {
    const taskId = String(payload.taskId ?? '')
    const task = rows.swarmTasks.find((row) => row.id === taskId)
    if (task === undefined || !TERMINAL.has(task.state)) {
      return ok({ taskId, retried: false, task: null })
    }
    const retried = addTask(rows, {
      assignee: task.assignee,
      source: task.sourceMessageId,
      author: { kind: task.createdBy.kind, id: task.createdBy.id },
      chainId: task.chainId,
      depth: task.chainDepth,
      derivedFrom: task.id,
    })
    run(rows, retried, task.chainDepth)
    return ok({ taskId, retried: true, task: { ...retried } })
  }

  if (op === 'reset') {
    const agentId = String(payload.agentId ?? '')
    const agent = rows.swarmAgents.find((row) => row.agent === agentId)
    const reset = agent !== undefined && agent.state !== 'running'
    if (reset) {
      appendMessage(rows, {
        author: { kind: 'system', id: 'room' },
        text: `@${agentId} 的会话已重置`,
      })
      setAgent(rows, agentId, 'idle', null)
    }
    return ok({ agentId, reset })
  }

  if (op === 'roles') {
    const action = String(payload.action ?? 'list')
    if (action !== 'list') {
      const id = String(payload.roleId ?? '')
      if (action === 'delete') {
        rows.swarmRoles = rows.swarmRoles.filter((role) => role.id !== id)
        rows.swarmAgents = rows.swarmAgents.filter((agent) => agent.agent !== id)
      } else {
        const manifest = (payload.manifest ?? {}) as Record<string, unknown>
        const role = roleView(rows, id, manifest)
        upsertRole(rows, role)
        // The host builds (create) or rebuilds (update) the agent, which
        // pushes a swarm_agent frame of its own.
        setAgent(rows, id, 'idle', null)
      }
    }
    return ok({ roles: copy(rows.swarmRoles), warnings: [...rows.swarmWarnings] })
  }

  if (op === 'reload') {
    // Every mock role is valid, so a reload re-reads them without problems.
    return ok({ roles: copy(rows.swarmRoles), notes: [], warnings: [] })
  }

  return { type: 'error', id: payload.id, ok: false, message: `unknown swarm op: ${op}` }
}
