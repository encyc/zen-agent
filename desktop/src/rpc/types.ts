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
  /** Machine-readable code (i18n on the client); optional for old servers. */
  code?: string
  params?: Record<string, string>
}

export type ResultFrame =
  | ({ type: 'result'; id: string; ok: true } & Record<string, unknown>)
  | { type: 'result'; id: string; ok: false; reason?: string }
  | ErrorFrame

// ---- wire contract -------------------------------------------------------
//
// The Python dispatcher (rpc.py `_handle_line`) is the authority for request
// kinds; this list mirrors it and is pinned by tests/test_rpc_contract.py so
// a one-sided change goes red instead of failing at runtime.

export const PROTOCOL_VERSION = 2

export const REQUEST_KINDS = [
  'approval',
  'ask',
  'cancel',
  'command',
  'commands',
  'fs_index',
  'fs_list',
  'fs_read',
  'git_worktrees',
  'hello',
  'limits',
  'mcp',
  'memory',
  'mode',
  'model',
  'models',
  'plugins',
  'project',
  'prompt',
  'secrets',
  'session',
  'shutdown',
  'skills',
  'status',
  'steer',
  'subagent_transcript',
  'taiji',
  'tools',
  'transcript',
  'usage',
] as const

export type RequestKind = (typeof REQUEST_KINDS)[number]

export interface HelloResult {
  type: 'result'
  id: string
  ok: true
  protocol_version: number
}

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

// ---- settings secrets ------------------------------------------------------

export interface SecretProviderRow {
  id: string
  name: string
  env: string
  /** Catalog model count; absent for search providers. */
  models?: number
  configured: boolean
  source: 'stored' | 'env' | null
  current?: boolean
}

export interface SecretsResult {
  type: 'result'
  id: string
  ok: true
  model_providers: SecretProviderRow[]
  search_providers: SecretProviderRow[]
}

export interface SecretTestResult {
  type: 'result'
  id: string
  ok: true
  connected: boolean
  message: string
}

// ---- skills --------------------------------------------------------------

export interface SkillRow {
  name: string
  description: string
  path: string
  source: 'project' | 'global'
  enabled: boolean
}

export interface LimitKnob {
  key: string
  section: string
  table: string
  name: string
  kind: 'int' | 'float'
  nullable: boolean
  min: number
  max: number
  default: number | null
  unit: string
  apply: 'immediate' | 'session'
  value: number | null
  explicit: boolean
  override: string | null
}

export interface LimitsResult {
  type: 'result'
  id: string
  /** A refusal still carries the snapshot (ok:false) so the page can render
   * the values it could not change. */
  ok: boolean
  path: string
  project_path: string
  groups: string[]
  knobs: LimitKnob[]
  error?: string
}

export interface SkillsResult {
  type: 'result'
  id: string
  ok: true
  skills: SkillRow[]
  warnings: string[]
  notes: string[]
}

export interface SkillReadResult {
  type: 'result'
  id: string
  ok: true
  name: string
  path: string
  body: string
  truncated: boolean
}

/** One tool the connected agent exposes (Tools page, read-only). */
export interface ToolRow {
  name: string
  description: string
  approval: string
}

export interface ToolsResult {
  type: 'result'
  id: string
  ok: true
  tools: ToolRow[]
}

// ---- plugins -------------------------------------------------------------

export interface PluginCredentialRow {
  key: string
  label: string
  secret: boolean
  help: string | null
  url: string | null
  set: boolean
}

export interface PluginServerRow {
  name: string
  state: 'connected' | 'failed' | 'off'
  tools: string[]
  error: string | null
}

export interface PluginRow {
  id: string
  name: string
  version: string
  description: string
  enabled: boolean
  status: 'disabled' | 'needs_credentials' | 'failed' | 'connected' | 'ready'
  credentials: PluginCredentialRow[]
  servers: PluginServerRow[]
}

export interface PluginCatalogRow {
  id: string
  name: string
  description: string
  requires_runtime: string | null
  installed: boolean
}

export interface PluginsResult {
  type: 'result'
  id: string
  ok: true
  plugins: PluginRow[]
  catalog: PluginCatalogRow[]
  problem: string | null
  apply_pending: boolean
}

// ---- mcp -----------------------------------------------------------------

export interface McpServerRow {
  name: string
  transport: 'stdio' | 'http'
  command: string | null
  url: string | null
  env_keys: string[]
  header_keys: string[]
  source: 'global' | 'project'
  overrides_global: boolean
  state: 'connected' | 'failed' | 'off'
  tools: string[]
  error: string | null
}

export interface McpListResult {
  type: 'result'
  id: string
  ok: true
  servers: McpServerRow[]
  problem: string | null
}

export interface McpTestResult {
  type: 'result'
  id: string
  ok: true
  connected: boolean
  tools: string[]
  message: string
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
  /** Parent session stem after fork; empty when original. */
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
  /** Wire discriminator: MessageView carries user/assistant/skill. */
  kind: 'tool'
  call_id: string
  /** Stable row identity inside one session (`v{n}`). */
  id?: string
  name: string
  args_preview: string
  args: Record<string, unknown>
  details: Record<string, unknown>
  /** `interrupted`: stopped mid-flight or missing a result (reloaded session);
   * `timed_out`: the tool's deadline killed it. Both keep `is_error` false. */
  status: 'running' | 'ok' | 'error' | 'interrupted' | 'timed_out'
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
   * in-stream divider views. */
  source?: string
}

export interface DividerView {
  /** Wire discriminator: the compaction divider sits mid-stream, where the
   * model's live context was cut. */
  kind: 'divider'
  /** Stable row identity inside one session (`v{n}`). */
  id?: string
  /** Prompt tokens the cut released (from the latest compaction record). */
  tokens_before?: number | null
  reason?: string | null
}

/** One compaction side-channel record, oldest first in the file. */
export interface CompactionRecord {
  type: 'compaction'
  timestamp?: number
  tokensBefore?: number
  /** Legacy field: compaction no longer forks the session file. */
  derivedFrom?: string
  reason?: 'auto' | 'manual' | string
}

export interface TranscriptResult {
  type: 'result'
  id: string
  ok: true
  transcript: (MessageView | ToolCallView | DividerView)[]
  notices: TranscriptNotice[]
  /** Compaction records from the loaded session; dividers arrive in-stream
   * (an older server without them is still tolerated). */
  compactions?: CompactionRecord[]
  /** The snapshot was paged (`limit`/`before`): older rows exist on the
   * server. Absent on an old server, which sends the whole transcript. */
  hasMoreBefore?: boolean
  /** Full view count; the loaded page starts at `total - length`. Absent on
   * an old server, where the page is the whole transcript. */
  total?: number
}

/** One task child's conversation (`subagent_transcript`). `available` is false
 * when neither the live buffer nor a persisted sidecar exists. With `after`,
 * `transcript` holds only the new rows and `total` the full count. */
export interface SubagentTranscriptResult {
  type: 'result'
  id: string
  ok: true
  available: boolean
  transcript: (MessageView | ToolCallView)[]
  total?: number
}

// ---- workspace ---------------------------------------------------------------

export interface WorkspaceEntry {
  name: string
  /** Slash path relative to the project root. */
  path: string
  kind: 'dir' | 'file'
  size: number
  hidden: boolean
}

export interface FsListResult {
  type: 'result'
  id: string
  ok: true
  path: string
  entries: WorkspaceEntry[]
  truncated: boolean
}

export type WorkspaceFile =
  | { path: string; kind: 'text'; size: number; text: string; total_lines: number; truncated: boolean }
  | { path: string; kind: 'binary'; size: number }
  | { path: string; kind: 'image'; mime: string; size: number; data_url: string }
  | { path: string; kind: 'tooLarge'; size: number }

export interface FsReadResult {
  type: 'result'
  id: string
  ok: true
  file: WorkspaceFile
}

export interface FsIndexEntry {
  path: string
  kind: 'dir' | 'file'
}

export interface FsIndexResult {
  type: 'result'
  id: string
  ok: true
  entries: FsIndexEntry[]
  truncated: boolean
}

export interface MemoryEntry {
  id: string
  title: string
  body: string
  created: string
  updated: string
  source: string
}

export interface MemoryListResult {
  type: 'result'
  id: string
  ok: true
  entries: MemoryEntry[]
}

export interface MemoryEntryResult {
  type: 'result'
  id: string
  ok: true
  entry: MemoryEntry
}

export interface MemoryDeleteResult {
  type: 'result'
  id: string
  ok: true
  deleted: boolean
}

export interface WorktreeEntry {
  path: string
  head: string
  branch: string
  bare: boolean
  detached: boolean
  current: boolean
}

export interface WorktreeOverview {
  root: string
  is_repo: boolean
  is_worktree: boolean
  main: string
  branch: string
  worktrees: WorktreeEntry[]
  status: { branch: string; staged: number; dirty: number; untracked: number; clean: boolean } | null
}

export interface GitWorktreesResult {
  type: 'result'
  id: string
  ok: true
  worktree: WorktreeOverview
}

// ---- taiji -----------------------------------------------------------------

/** One worker question waiting on a person (from an `ask_request` event). */
export interface TaijiAttention {
  question: string
  /** Up to four option labels the panel renders as quick answers. */
  options: string[]
}

export interface TaijiDiffFile {
  path: string
  adds: number
  dels: number
}

/** One row of the dispatch board: the registry record, board-shaped. */
export interface TaijiWorker {
  name: string
  branch: string
  base: string
  status: string
  summary: string
  session: string
  created_at: number
  updated_at: number
  attention: TaijiAttention | null
  /** Present only while the worker sits at acceptance (status ok). */
  /** The task text the worker was spawned with. */
  prompt?: string
  diffstat: TaijiDiffFile[] | null
}

export interface TaijiSlots {
  busy: number
  max: number
  queued: number
}

/** The board: the fleet sorted on the client by how much a person must do. */
export interface TaijiBoard {
  /** False when this host never started taiji (worker subprocess, linked worktree). */
  active: boolean
  /** The host Router conversation is mid-turn. */
  router_busy: boolean
  slots: TaijiSlots | null
  workers: TaijiWorker[]
}

export interface TaijiBoardResult {
  type: 'result'
  id: string
  ok: true
  board: TaijiBoard
}

export interface TaijiMessageResult {
  type: 'result'
  id: string
  ok: true
  /** True when the text went to the inbox and rides the current turn's tail. */
  queued: boolean
  status: string
}

export interface TaijiPeekResult {
  type: 'result'
  id: string
  ok: true
  items: { role: string; text: string }[]
}

// ---- ask -------------------------------------------------------------------

export interface AskOption {
  label: string
  description?: string
}

/** One `ask` tool question as validated by zenagent/tools/ask.py. */
export interface AskQuestion {
  id: string
  question: string
  header?: string
  options: AskOption[]
  multi: boolean
  recommended?: string
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
  /** Set when the user turn is an injected `/skill:<name>` invocation. */
  skill?: boolean
  skill_args?: string
  skill_body?: string
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
    | 'ask_request'
    | 'ask_cancel'
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
  // approval_request / ask_request extras
  request_id?: string
  kind?: 'confirm'
  risk?: string | null
  target?: string | null
  reason?: string | null
  questions?: AskQuestion[]
}

export interface EventFrame {
  type: 'event'
  event: AgentEventFrame
}

export type ServerFrame = ResultFrame | EventFrame

export function isEventFrame(frame: ServerFrame): frame is EventFrame {
  return frame.type === 'event'
}
