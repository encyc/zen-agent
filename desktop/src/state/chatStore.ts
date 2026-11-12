/** Chat assembly: mirrors zenagent/app/views.py::TranscriptBuilder — the
 * authoritative event→view mapping the TUI uses. The snapshot (`transcript`
 * RPC result) is the baseline; live events apply on top of it. */

import { create } from 'zustand'
import { firstLoadedIndex, prependFirstIndex } from './transcriptWindow.ts'
import type {
  AgentEventFrame,
  DividerView,
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

/** One retry burst inside a turn: the provider dropped the request before it
 * showed anything, so the loop silently retried it. Rendered as a compact
 * status row — live while backing off, settled once the run moves on. */
export interface RetryNode {
  kind: 'retry'
  id: string
  /** Retries announced so far (1-based; attempts = retries + 1). */
  attempts: number
  maxRetries: number
  /** Total backoff announced so far, the `· 3.5s` the row shows. */
  waitedMs: number
  lastDelayMs: number
  /** Wall clock of the latest announcement, for the live tick. */
  lastAt: number
  /** Raw provider text of each swallowed attempt, oldest first. */
  errors: string[]
  state: 'retrying' | 'recovered' | 'failed' | 'stopped'
}

/** The run died: rendered inline instead of an error popup, right where the
 * retry row (when there was one) left off. */
export interface FailureNode {
  kind: 'failure'
  id: string
  text: string
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
  | RetryNode
  | FailureNode
  | CommandNode
  | UsageNode

/** One transcript as the switch cache retains it. */
export interface ChatPane {
  nodes: ChatNode[]
  cards: Record<string, string>
  currentAssistantId: string | null
  hasMoreBefore: boolean
  firstLoadedIndex: number
}

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
  /** The snapshot was paged: older rows still exist on the server. */
  hasMoreBefore: boolean
  /** Absolute index (from the session start) of the first loaded view; the
   * `before` cursor for an older page. A tail page starts at `total -
   * page length`, never at its own length. */
  firstLoadedIndex: number

  loadSnapshot(result: TranscriptResult): void
  /** Prepend one older wire page to the loaded transcript. */
  prependOlder(result: TranscriptResult): void
  applyEvent(event: AgentEventFrame): void
  pushCommand(name: string, argument: string, message: string | null): void
  pushUsage(usage: Usage | null, turnMs: number | null, termination: string | null): void
  /** Land the run-failure line inline (the banner is only for RPC failures). */
  pushFailure(text: string): void
  finalize(): void
  /** Snapshot the loaded transcript; switching away stores it per project. */
  pane(): ChatPane
  /** Paint a retained pane before the server snapshot is revalidated. */
  restorePane(pane: ChatPane): void
  reset(): void
}

let seq = 0
const nid = () => `n${(seq += 1)}`

/** Provider events that prove a retried attempt survived (the row settles). */
const RETRY_SETTLED_EVENTS = new Set([
  'text_start',
  'text_delta',
  'thinking_start',
  'thinking_delta',
  'toolcall_start',
  'toolcall_delta',
  'toolcall_end',
  'done',
])

/** Index of the retry burst still waiting, if the newest rows carry one. A
 * usage footer may already sit above it (a run ends before status lands). */
function retryingIndex(nodes: ChatNode[]): number {
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]
    if (node.kind === 'usage') continue
    return node.kind === 'retry' && node.state === 'retrying' ? index : -1
  }
  return -1
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

function viewToNode(
  view: MessageView | ToolCallView | DividerView,
  cards: Record<string, string>,
): ChatNode | null {
  // All wire views carry an explicit `kind` discriminator.
  if (view.kind === 'divider') {
    const divider: DividerView = view
    return {
      kind: 'notice',
      id: divider.id || nid(),
      variant: 'divider',
      text: '',
      tokensBefore: divider.tokens_before ?? null,
      reason: divider.reason ?? null,
    }
  }
  if (view.kind === 'tool') {
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

/** Snapshot views → render nodes, without touching the live session state.
 * The subagent panel reads one child's transcript through the same mapping.
 * Compaction dividers arrive in-stream, positioned at the replay cut. */
export function nodesFromViews(
  views: (MessageView | ToolCallView | DividerView)[],
): ChatNode[] {
  return buildNodes(views, {})
}

function buildNodes(
  views: (MessageView | ToolCallView | DividerView)[],
  cards: Record<string, string>,
): ChatNode[] {
  return views
    .map((view) => viewToNode(view, cards))
    .filter((node): node is ChatNode => node !== null)
}

export const useChatStore = create<ChatState>((set, get) => ({
  nodes: [],
  cards: {},
  currentAssistantId: null,
  revision: 0,
  hasMoreBefore: false,
  firstLoadedIndex: 0,

  loadSnapshot(result) {
    const cards: Record<string, string> = {}
    const nodes = buildNodes(result.transcript, cards)
    // Legacy servers without in-stream dividers: fall back to drawing one
    // divider above the transcript from the records (written oldest-first,
    // repeated compactions stack; draw exactly one, the latest).
    const hasDivider = nodes.some(
      (node) => node.kind === 'notice' && node.variant === 'divider',
    )
    if (result.compactions?.length && !hasDivider) {
      const compaction = result.compactions[result.compactions.length - 1]
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
    set({
      nodes,
      cards,
      currentAssistantId: null,
      revision: get().revision + 1,
      hasMoreBefore: result.hasMoreBefore === true,
      firstLoadedIndex: firstLoadedIndex(
        result.total,
        result.transcript.length,
      ),
    })
  },

  prependOlder(result) {
    const state = get()
    const cards = { ...state.cards }
    const older = buildNodes(result.transcript, cards)
    set({
      nodes: [...older, ...state.nodes],
      cards,
      revision: state.revision + 1,
      hasMoreBefore: result.hasMoreBefore === true,
      firstLoadedIndex: prependFirstIndex(
        state.firstLoadedIndex,
        result.transcript.length,
      ),
    })
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

    // The streaming assistant node is always the newest push, so a tail-first
    // scan finds it in O(1) on the hot text_delta path (long transcripts).
    const assistantIndexOf = (id: string) => {
      for (let i = nodes.length - 1; i >= 0; i--) {
        if (nodes[i].id === id) return i
      }
      return -1
    }

    switch (event.type) {
      case 'message_start': {
        const role = event.message?.role
        if (role === 'user') {
          if (event.message?.skill === true) {
            nodes.push({
              kind: 'skill',
              id: nid(),
              name: event.message.text,
              args: event.message.skill_args ?? null,
              body: event.message.skill_body ?? null,
            })
          } else {
            nodes.push({
              kind: 'user',
              id: nid(),
              text: event.message?.text ?? '',
              steering: event.message?.steering === true,
              timestamp: 0,
            })
          }
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
        // The provider answered after one or more swallowed attempts: the
        // retry row stops ticking where it stands and keeps its count.
        if (RETRY_SETTLED_EVENTS.has(kind)) {
          const retryIndex = retryingIndex(nodes)
          if (retryIndex >= 0) {
            nodes[retryIndex] = { ...(nodes[retryIndex] as RetryNode), state: 'recovered' }
            changed = true
          }
        }
        if (currentAssistantId === null && (kind === 'text_delta' || kind === 'text_start')) {
          if (!assistantEvent.delta && kind === 'text_start') break
          const node = newAssistantNode()
          nodes.push(node)
          currentAssistantId = node.id
          changed = true
        }
        if (currentAssistantId === null) break
        if (kind === 'thinking_start' || kind === 'thinking_delta') {
          const index = assistantIndexOf(currentAssistantId)
          if (index >= 0) {
            const node = nodes[index] as AssistantNode
            nodes[index] = {
              ...node,
              thinking: (node.thinking ?? '') + (assistantEvent.delta ?? ''),
              thinkingRunning: true,
            } as ChatNode
            changed = true
          }
        } else if (kind === 'thinking_end') {
          patchNode(currentAssistantId, { thinkingRunning: false } as Partial<ChatNode>)
        } else if (kind === 'text_delta') {
          const index = assistantIndexOf(currentAssistantId)
          if (index >= 0) {
            const node = nodes[index] as AssistantNode
            nodes[index] = {
              ...node,
              text: node.text + (assistantEvent.delta ?? ''),
              thinkingRunning: false,
            } as ChatNode
            changed = true
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
      case 'retry_attempt': {
        // A swallowed attempt, announced before its backoff. Consecutive
        // announcements grow the same row; a later burst opens a new one.
        const attempt = event.attempt ?? 0
        if (attempt <= 0) break
        const delay = event.delay_ms ?? 0
        const error = (event.error ?? '').trim()
        const maxRetries = event.max_retries ?? 0
        const tailIndex = nodes.length - 1
        const tail = nodes[tailIndex]
        if (tail !== undefined && tail.kind === 'retry' && tail.state === 'retrying') {
          nodes[tailIndex] = {
            ...tail,
            attempts: attempt,
            maxRetries,
            waitedMs: tail.waitedMs + delay,
            lastDelayMs: delay,
            lastAt: Date.now(),
            errors: error === '' ? tail.errors : [...tail.errors, error],
          }
        } else {
          nodes.push({
            kind: 'retry',
            id: nid(),
            attempts: attempt,
            maxRetries,
            waitedMs: delay,
            lastDelayMs: delay,
            lastAt: Date.now(),
            errors: error === '' ? [] : [error],
            state: 'retrying',
          })
        }
        changed = true
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
    const nodes = [...state.nodes]
    // The run closed while a retry burst was still waiting: an error exhausts
    // it (the failure line lands next), an abort stops it, anything else means
    // the answer arrived without a content event the store could see.
    const retryIndex = retryingIndex(nodes)
    if (retryIndex >= 0) {
      const settled =
        termination === 'error' ? 'failed' : termination === 'aborted' ? 'stopped' : 'recovered'
      nodes[retryIndex] = { ...(nodes[retryIndex] as RetryNode), state: settled }
    }
    const node: UsageNode = { kind: 'usage', id: nid(), usage, turnMs, termination }
    nodes.push(node)
    set({ nodes, revision: get().revision + 1 })
  },

  pushFailure(text) {
    const trimmed = text.trim()
    if (trimmed === '') return
    const state = get()
    const nodes = [...state.nodes]
    const previous = [...nodes].reverse().find((node) => node.kind === 'failure')
    if (previous !== undefined && previous.kind === 'failure' && previous.text === trimmed) {
      return
    }
    // The run is over: whatever retry burst was still waiting has failed too.
    const retryIndex = retryingIndex(nodes)
    if (retryIndex >= 0) {
      nodes[retryIndex] = { ...(nodes[retryIndex] as RetryNode), state: 'failed' }
    }
    const node: FailureNode = { kind: 'failure', id: nid(), text: trimmed }
    // A failed run closes with its usage footer; the failure belongs above it,
    // right where the retry row left off.
    if (nodes[nodes.length - 1]?.kind === 'usage') nodes.splice(nodes.length - 1, 0, node)
    else nodes.push(node)
    set({ nodes, revision: get().revision + 1 })
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
    // A retry burst a cancel/exit/switch interrupted stops ticking; the row is
    // history, not a live status any more.
    const retryIndex = retryingIndex(nodes)
    if (retryIndex >= 0) {
      nodes[retryIndex] = { ...(nodes[retryIndex] as RetryNode), state: 'stopped' }
      changed = true
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
    set({
      nodes: [],
      cards: {},
      currentAssistantId: null,
      revision: get().revision + 1,
      hasMoreBefore: false,
      firstLoadedIndex: 0,
    })
  },

  pane() {
    const state = get()
    return {
      nodes: state.nodes,
      cards: state.cards,
      currentAssistantId: state.currentAssistantId,
      hasMoreBefore: state.hasMoreBefore,
      firstLoadedIndex: state.firstLoadedIndex,
    }
  },

  restorePane(pane) {
    set({
      nodes: pane.nodes,
      cards: pane.cards,
      currentAssistantId: pane.currentAssistantId,
      hasMoreBefore: pane.hasMoreBefore,
      firstLoadedIndex: pane.firstLoadedIndex,
      revision: get().revision + 1,
    })
  },
}))
