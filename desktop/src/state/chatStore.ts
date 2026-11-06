/** Chat assembly: mirrors zenagent/app/views.py::TranscriptBuilder — the
 * authoritative event→view mapping the TUI uses. The snapshot (`transcript`
 * RPC result) is the baseline; live events apply on top of it. */

import { create } from 'zustand'
import type {
  AgentEventFrame,
  CompactionRecord,
  MessageUsage,
  MessageView,
  ToolCallView,
  TranscriptNotice,
  TranscriptResult,
  Usage,
} from '../rpc/types'

export interface UserNode {
  kind: 'user'
  id: string
  text: string
  /** Delivered through the steering channel. */
  steering: boolean
  timestamp: number
}

export interface SkillNode {
  kind: 'skill'
  id: string
  name: string
  args: string | null
  body: string | null
}

export interface AssistantNode {
  kind: 'assistant'
  id: string
  text: string
  thinking: string | null
  /** A reasoning block is open right now (shimmer + timer). */
  thinkingRunning: boolean
  /** The assistant row is still receiving deltas. */
  streaming: boolean
  /** Persisted assistant metadata (empty/0 on live rows). */
  timestamp: number
  model: string
  provider: string
  usage: MessageUsage | null
  durationMs: number | null
}

export interface ToolNode {
  kind: 'tool'
  id: string
  callId: string
  name: string
  argsPreview: string
  args: Record<string, unknown>
  /** `timed_out`/`interrupted` are stopped calls: `is_error` stays false for
   * them (command failure is information), so the cue reads `state`. */
  state: 'running' | 'ok' | 'error' | 'interrupted' | 'timed_out'
  progress: string
  resultText: string
  isError: boolean
  durationMs: number | null
  diff: string | null
  details: Record<string, unknown>
}

export interface NoticeNode {
  kind: 'notice'
  id: string
  variant: 'divider' | 'info'
  text: string
  /** Compaction dividers render their label from the record. */
  tokensBefore?: number | null
  reason?: string | null
}

export interface CommandNode {
  kind: 'command'
  id: string
  name: string
  argument: string
  message: string | null
}

export interface UsageNode {
  kind: 'usage'
  id: string
  usage: Usage | null
  turnMs: number | null
  termination: string | null
}

export type ChatNode =
  | UserNode
  | SkillNode
  | AssistantNode
  | ToolNode
  | NoticeNode
  | CommandNode
  | UsageNode

/** The last assistant turn, when it is settled and carries answer text — the
 * one turn a regenerate action may target. */
export function retryableNodeId(nodes: ChatNode[]): string | null {
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]
    if (node.kind !== 'assistant') continue
    if (node.streaming || node.text.trim() === '') return null
    return node.id
  }
  return null
}

interface ChatState {
  nodes: ChatNode[]
  /** call_id → tool node id, mirroring TranscriptBuilder.cards. */
  cards: Record<string, string>
  currentAssistantId: string | null
  /** Monotonic revision bumped on every visible change. */
  revision: number

  loadSnapshot(result: TranscriptResult): void
  applyEvent(event: AgentEventFrame): void
  pushCommand(name: string, argument: string, message: string | null): void
  pushUsage(usage: Usage | null, turnMs: number | null, termination: string | null): void
  finalize(): void
  reset(): void
}

let seq = 0
const nid = () => `n${(seq += 1)}`

/** Records are written oldest-first; the divider summarises the latest one. */
function latestCompaction(records: CompactionRecord[] | undefined): CompactionRecord | null {
  if (!records || records.length === 0) return null
  return records[records.length - 1]
}

function newAssistantNode(): AssistantNode {
  return {
    kind: 'assistant',
    id: nid(),
    text: '',
    thinking: null,
    thinkingRunning: false,
    streaming: true,
    timestamp: 0,
    model: '',
    provider: '',
    usage: null,
    durationMs: null,
  }
}

function viewToNode(view: MessageView | ToolCallView, cards: Record<string, string>): ChatNode | null {
  // ToolCallView carries no discriminator: only MessageView has `kind`.
  if ('kind' in view) {
    if (view.kind === 'user') {
      return {
        kind: 'user',
        id: view.id || nid(),
        text: view.text,
        steering: view.steering === true,
        timestamp: view.timestamp ?? 0,
      }
    }
    if (view.kind === 'skill') {
      return {
        kind: 'skill',
        id: view.id || nid(),
        name: view.text,
        args: view.skill_args ?? null,
        body: view.skill_body ?? null,
      }
    }
    return {
      kind: 'assistant',
      id: view.id || nid(),
      text: view.text,
      thinking: view.thinking ?? null,
      thinkingRunning: false,
      streaming: false,
      timestamp: view.timestamp ?? 0,
      model: view.model ?? '',
      provider: view.provider ?? '',
      usage: view.usage ?? null,
      durationMs: view.duration_ms ?? null,
    }
  }
  const tool: ToolCallView = view
  const node: ToolNode = {
    kind: 'tool',
    id: tool.id || nid(),
    callId: tool.call_id,
    name: tool.name,
    argsPreview: tool.args_preview,
    args: tool.args ?? {},
    state: tool.status,
    progress: tool.progress ?? '',
    resultText: tool.result_text ?? '',
    isError: Boolean(tool.is_error),
    durationMs: tool.duration_ms ?? null,
    diff: typeof tool.diff === 'string' && tool.diff ? tool.diff : null,
    details: tool.details ?? {},
  }
  cards[node.callId] = node.id
  return node
}

export const useChatStore = create<ChatState>((set, get) => ({
  nodes: [],
  cards: {},
  currentAssistantId: null,
  revision: 0,

  loadSnapshot(result) {
    const cards: Record<string, string> = {}
    const nodes = result.transcript
      .map((view) => viewToNode(view, cards))
      .filter((node): node is ChatNode => node !== null)
    // The file writes compaction records before the summary, so the divider
    // leads the transcript. Repeated compactions stack records; draw exactly
    // one divider (the latest compaction) instead of one per record.
    const compaction = latestCompaction(result.compactions)
    if (compaction !== null) {
      nodes.unshift({
        kind: 'notice',
        id: nid(),
        variant: 'divider',
        text: '',
        tokensBefore: compaction.tokensBefore ?? null,
        reason: compaction.reason ?? null,
      })
    }
    for (const notice of result.notices as TranscriptNotice[]) {
      nodes.push({ kind: 'notice', id: nid(), variant: notice.kind, text: notice.text })
    }
    set({ nodes, cards, currentAssistantId: null, revision: get().revision + 1 })
  },

  applyEvent(event) {
    const state = get()
    const nodes = [...state.nodes]
    const cards = { ...state.cards }
    let currentAssistantId = state.currentAssistantId
    let changed = false

    const patchNode = (id: string, patch: Partial<ChatNode>) => {
      const index = nodes.findIndex((node) => node.id === id)
      if (index >= 0) {
        nodes[index] = { ...nodes[index], ...patch } as ChatNode
        changed = true
      }
    }

    switch (event.type) {
      case 'message_start': {
        const role = event.message?.role
        if (role === 'user') {
          nodes.push({
            kind: 'user',
            id: nid(),
            text: event.message?.text ?? '',
            steering: event.message?.steering === true,
            timestamp: 0,
          })
          changed = true
        } else if (role === 'assistant') {
          const node = newAssistantNode()
          nodes.push(node)
          currentAssistantId = node.id
          changed = true
        }
        break
      }
      case 'message_update': {
        const assistantEvent = event.assistant_event
        if (!assistantEvent) break
        const kind = assistantEvent.type
        if (currentAssistantId === null && (kind === 'text_delta' || kind === 'text_start')) {
          if (!assistantEvent.delta && kind === 'text_start') break
          const node = newAssistantNode()
          nodes.push(node)
          currentAssistantId = node.id
          changed = true
        }
        if (currentAssistantId === null) break
        if (kind === 'thinking_start' || kind === 'thinking_delta') {
          const node = nodes.find((candidate) => candidate.id === currentAssistantId) as AssistantNode | undefined
          if (node) {
            patchNode(node.id, {
              thinking: (node.thinking ?? '') + (assistantEvent.delta ?? ''),
              thinkingRunning: true,
            } as Partial<ChatNode>)
          }
        } else if (kind === 'thinking_end') {
          patchNode(currentAssistantId, { thinkingRunning: false } as Partial<ChatNode>)
        } else if (kind === 'text_delta') {
          const node = nodes.find((candidate) => candidate.id === currentAssistantId) as AssistantNode | undefined
          if (node) {
            patchNode(node.id, {
              text: node.text + (assistantEvent.delta ?? ''),
              thinkingRunning: false,
            } as Partial<ChatNode>)
          }
        } else if (kind === 'error') {
          patchNode(currentAssistantId, { streaming: false } as Partial<ChatNode>)
        }
        break
      }
      case 'message_end': {
        if (currentAssistantId !== null) {
          const index = nodes.findIndex((node) => node.id === currentAssistantId)
          const node = index >= 0 ? (nodes[index] as AssistantNode) : undefined
          if (node) {
            const text = event.message?.text ?? node.text
            // A turn that only carried tool calls ends empty; the tool cards
            // are its visible content (views.py drops the row the same way).
            if (text === '' && (node.thinking ?? '').trim() === '') {
              nodes.splice(index, 1)
            } else {
              patchNode(node.id, {
                text,
                streaming: false,
                thinkingRunning: false,
              } as Partial<ChatNode>)
            }
            changed = true
          }
          currentAssistantId = null
        }
        break
      }
      case 'tool_execution_start': {
        let args: Record<string, unknown> = {}
        try {
          args = event.args ? (JSON.parse(event.args) as Record<string, unknown>) : {}
        } catch {
          args = {}
        }
        const preview = event.args && event.args.length <= 140 ? event.args : (event.args ?? '').slice(0, 137) + '...'
        const node: ToolNode = {
          kind: 'tool',
          id: nid(),
          callId: event.tool_call_id ?? '',
          name: event.tool_name ?? '?',
          argsPreview: preview,
          args,
          state: 'running',
          progress: '',
          resultText: '',
          isError: false,
          durationMs: null,
          diff: null,
          details: {},
        }
        nodes.push(node)
        cards[node.callId] = node.id
        changed = true
        break
      }
      case 'tool_execution_update': {
        const id = cards[event.tool_call_id ?? '']
        if (id) patchNode(id, { progress: event.partial_result ?? '' } as Partial<ChatNode>)
        break
      }
      case 'tool_execution_end': {
        const id = cards[event.tool_call_id ?? '']
        let details: Record<string, unknown> = {}
        if (event.details) {
          try {
            details = JSON.parse(event.details) as Record<string, unknown>
          } catch {
            details = {}
          }
        }
        const diff = typeof details.diff === 'string' && details.diff ? details.diff : null
        if (id) {
          patchNode(id, {
            state: event.is_error ? 'error' : 'ok',
            resultText: event.result_text ?? '',
            isError: Boolean(event.is_error),
            durationMs: event.duration_ms ?? null,
            details,
            diff,
          } as Partial<ChatNode>)
        }
        break
      }
      default:
        break
    }

    if (changed) set({ nodes, cards, currentAssistantId, revision: get().revision + 1 })
  },

  pushCommand(name, argument, message) {
    const state = get()
    const node: CommandNode = { kind: 'command', id: nid(), name, argument, message }
    set({ nodes: [...state.nodes, node], revision: get().revision + 1 })
  },

  pushUsage(usage, turnMs, termination) {
    const state = get()
    const node: UsageNode = { kind: 'usage', id: nid(), usage, turnMs, termination }
    set({ nodes: [...state.nodes, node], revision: get().revision + 1 })
  },

  finalize() {
    const state = get()
    const nodes = [...state.nodes]
    const hadCurrent = state.currentAssistantId !== null
    let changed = false
    if (state.currentAssistantId !== null) {
      const index = nodes.findIndex((node) => node.id === state.currentAssistantId)
      if (index >= 0) {
        const node = nodes[index] as AssistantNode
        if (node.text === '' && (node.thinking ?? '').trim() === '') {
          nodes.splice(index, 1)
        } else {
          nodes[index] = { ...node, streaming: false, thinkingRunning: false }
        }
        changed = true
      }
    }
    // Cancelled or crashed runs leave cards without a result; a snapshot
    // refresh marks these interrupted, so settle live ones the same way.
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index]
      if (node.kind === 'tool' && node.state === 'running') {
        nodes[index] = { ...node, state: 'interrupted' }
        changed = true
      }
    }
    if (!changed && !hadCurrent) return
    set({ nodes, currentAssistantId: null, revision: get().revision + 1 })
  },

  reset() {
    set({ nodes: [], cards: {}, currentAssistantId: null, revision: get().revision + 1 })
  },
}))
