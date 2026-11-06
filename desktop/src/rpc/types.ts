/** zen rpc wire types (zenagent/cli/rpc.py). Shapes verified against the
 * live server; the frontend mirrors the TUI reducer (zenagent/app/views.py)
 * when assembling them. */

export interface Usage {
  input: number
  output: number
  cache_read: number
  cache_write: number
  cost: number
}

export type Mode = 'plan' | 'ask' | 'zen'

// ---- server frames -------------------------------------------------------

export interface ErrorFrame {
  type: 'error'
  id: string | null
  message: string
}

export type ResultFrame =
  | ({ type: 'result'; id: string; ok: true } & Record<string, unknown>)
  | { type: 'result'; id: string; ok: false; reason?: string }
  | ErrorFrame

// ---- prompt --------------------------------------------------------------

export interface PromptResult {
  type: 'result'
  id: string
  ok: boolean
  text: string
  usage: Usage
  termination: string | null
  error?: string
  session?: string
}

// ---- status --------------------------------------------------------------

export interface TodoPhase {
  [key: string]: unknown
}

export interface PendingMessage {
  id: number
  lane: 'queue' | 'steer'
  text: string
  images: number
  created_at: number
}

export interface JobView {
  id: string
  command: string
  status: 'running' | 'ok' | 'failed' | 'timed_out'
  started_at: number
  duration_ms: number | null
  exit_code: number | null
}

export interface SubagentView {
  call_id: string
  name: string
  status: 'running' | 'ok' | 'error' | 'aborted' | 'truncated' | 'interrupted'
  duration_ms: number
  cost: number
  tokens: number
  has_transcript: boolean
  transcript_truncated: boolean
}

/** One component of the context (chars exact, tokens estimated). */
export interface ContextSlice {
  id: string
  label: string
  chars: number
  tokens: number
  /** Share of the model window, 0-100. */
  pct: number
}

/** Context composition from `build_context_report`; slices partition the
 * prompt, while the total is provider-measured when `measured` is true. */
export interface ContextReport {
  slices: ContextSlice[]
  total_tokens: number
  window: number
  measured: boolean
  prompt_tokens: number
  free_tokens: number
}

export interface StatusResult {
  type: 'result'
  id: string
  ok: true
  running: boolean
  messages: number
  model: string
  usage: Usage
  termination: string | null
  session?: string
  mode?: Mode
  tools?: number
  busy?: boolean
  follow_ups?: number
  steering?: number
  cost?: number
  thinking?: string | null
  context_used?: number | null
  context_window?: number | null
  context?: ContextReport | null
  cache_hit_ratio?: number | null
  turn_ms?: number | null
  todos?: TodoPhase[]
  last_error?: string
  pending?: PendingMessage[]
  jobs?: JobView[]
  subagents?: SubagentView[]
  active_subagent?: string | null
  subagent_cost?: number
  subagent_tokens?: number
}

// ---- models / commands catalogs ------------------------------------------

export interface ModelEntry {
  id: string
  provider: string
  context_window: number
  max_tokens: number
  reasoning: boolean
  supports_vision: boolean
  available: boolean
  reason: string | null
  current: boolean
}

export interface CommandEntry {
  name: string
  description: string
}

// ---- sessions -------------------------------------------------------------

export interface SessionSummary {
  path: string
  name: string
  title: string
  project: string
  /** Canonical project root recorded in the project meta; empty for legacy
   * sessions kept outside a project directory. */
  project_path?: string
  messages: number
  current: boolean
  pinned: boolean
  /** Header createdAt, falling back to mtime for legacy files. */
  created_at?: number
  /** File mtime: last activity. */
  updated_at?: number
  /** Provider model id of the last assistant message; empty when none. */
  model?: string
  /** Parent session stem after fork/compact; empty when original. */
  derived_from?: string
}

export interface SessionListResult {
  type: 'result'
  id: string
  ok: true
  scope: 'project' | 'all'
  sessions: SessionSummary[]
}

// ---- transcript snapshot ---------------------------------------------------

/** Per-message usage as dumped from the `Usage` dataclass (nested cost), as
 * opposed to the flat session totals the status/prompt results carry. */
export interface MessageUsage {
  input: number
  output: number
  cache_read: number
  cache_write: number
  reasoning?: number | null
  cost: {
    input: number
    output: number
    cache_read: number
    cache_write: number
    total: number
  }
}

export interface MessageView {
  kind: 'user' | 'assistant' | 'skill'
  text: string
  /** Stable row identity inside one session (`v{n}`). */
  id?: string
  timestamp?: number
  /** Delivered through the steering channel (user rows only). */
  steering?: boolean
  skill_args?: string | null
  skill_body?: string | null
  thinking?: string | null
  thinking_started_at?: number | null
  thinking_seconds?: number | null
  model?: string
  provider?: string
  usage?: MessageUsage | null
  duration_ms?: number | null
}

export interface ToolCallView {
  call_id: string
  /** Stable row identity inside one session (`v{n}`). */
  id?: string
  name: string
  args_preview: string
  args: Record<string, unknown>
  details: Record<string, unknown>
  /** `interrupted`: the call has no result because the session was reloaded. */
  status: 'running' | 'ok' | 'error' | 'interrupted'
  progress: string
  result_text: string
  is_error: boolean
  duration_ms: number | null
  diff?: string | null
  started_at?: number | null
}

export interface TranscriptNotice {
  kind: 'divider' | 'info'
  text: string
  /** `"compaction"` notices are excluded from the snapshot in favour of the
   * records below. */
  source?: string
}

/** One compaction side-channel record, oldest first in the file. */
export interface CompactionRecord {
  type: 'compaction'
  timestamp?: number
  tokensBefore?: number
  derivedFrom?: string
  reason?: 'auto' | 'manual' | string
}

export interface TranscriptResult {
  type: 'result'
  id: string
  ok: true
  transcript: (MessageView | ToolCallView)[]
  notices: TranscriptNotice[]
  /** Compaction records from the loaded session; the divider is drawn from
   * these, never synthesized from a notice. */
  compactions?: CompactionRecord[]
}

// ---- event frames -----------------------------------------------------------

/** assistant_event payloads inside message_update (provider deltas). */
export interface AssistantEvent {
  type:
    | 'start'
    | 'text_start'
    | 'text_delta'
    | 'text_end'
    | 'thinking_start'
    | 'thinking_delta'
    | 'thinking_end'
    | 'toolcall_start'
    | 'toolcall_delta'
    | 'toolcall_end'
    | 'done'
    | 'error'
  delta?: string
  error?: string
}

export interface MessageProjection {
  role: 'user' | 'assistant' | 'toolResult' | 'message'
  text: string
  stop_reason?: string | null
  tool_name?: string
  is_error?: boolean
  steering?: boolean
}

export interface AgentEventFrame {
  type:
    | 'agent_start'
    | 'agent_end'
    | 'turn_start'
    | 'turn_end'
    | 'message_start'
    | 'message_update'
    | 'message_end'
    | 'tool_execution_start'
    | 'tool_execution_update'
    | 'tool_execution_end'
    | 'approval_request'
  message?: MessageProjection
  assistant_event?: AssistantEvent
  new_messages?: (MessageView | ToolCallView)[]
  tool_stats?: Record<string, { calls: number; errors: number; ms: number }>
  tool_call_id?: string
  tool_name?: string
  args?: string
  partial_result?: string
  result_text?: string
  details?: string
  is_error?: boolean
  duration_ms?: number
  // approval_request extras
  request_id?: string
  kind?: 'confirm'
  risk?: string | null
  target?: string | null
  reason?: string | null
}

export interface EventFrame {
  type: 'event'
  event: AgentEventFrame
}

export type ServerFrame = ResultFrame | EventFrame

export function isEventFrame(frame: ServerFrame): frame is EventFrame {
  return frame.type === 'event'
}
