/** zen rpc wire types (zenagent/cli/rpc.py). Shapes verified against the
 * live server; the frontend mirrors the TUI reducer (zenagent/app/views.py)
 * when assembling them. */

import type { SwarmServerFrame } from './types-swarm.ts'
import type { EventFrame } from './types-events.ts'

export type {
  AgentEventFrame,
  AssistantEvent,
  EventFrame,
  MessageProjection,
} from './types-events.ts'

export type {
  SwarmAgentFrame,
  SwarmAgentState,
  SwarmEventFrame,
  SwarmAgentStatus,
  SwarmAuthor,
  SwarmListResult,
  SwarmMessage,
  SwarmMessageFrame,
  SwarmNoticeFrame,
  SwarmOkResult,
  SwarmPostResult,
  SwarmRef,
  SwarmReloadResult,
  SwarmRole,
  SwarmRolesResult,
  SwarmTask,
  SwarmTaskFrame,
  SwarmTaskState,
  SwarmWork,
  SwarmWorkFrame,
  SwarmWorkResult,
  SwarmWorksResult,
  SwarmWorkState,
} from './types-swarm.ts'

export type Mode = 'plan' | 'ask' | 'auto'

// ---- server frames -------------------------------------------------------

export interface ErrorFrame {
  type: 'error'
  id: string | null
  message: string
  /** The slot that emitted the frame (host tag, absent on a v2 server). */
  project?: string
  /** The session the slot was on when it emitted the frame. */
  session?: string
  /** Machine-readable code (i18n on the client); optional for old servers. */
  code?: string
  params?: Record<string, string>
}

export type ResultFrame =
  | ({ type: 'result'; id: string; ok: true } & Record<string, unknown>)
  | { type: 'result'; id: string; ok: false; reason?: string; project?: string; session?: string }
  | ErrorFrame

// ---- wire contract -------------------------------------------------------
//
// The Python dispatcher (rpc.py `_handle_line`) is the authority for request
// kinds; this list mirrors it and is pinned by tests/test_rpc_contract.py so
// a one-sided change goes red instead of failing at runtime.

export const PROTOCOL_VERSION = 3

export const REQUEST_KINDS = [
  'approval',
  'ask',
  'cancel',
  'command',
  'commands',
  'follow_up',
  'fs_index',
  'fs_list',
  'fs_read',
  'git_changes',
  'git_diff',
  'git_log',
  'git_show',
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
  'supervisor',
  'swarm',
  'runs',
  'terminal',
  'tools',
  'transcript',
  'usage',
  'workspace', 'zen_fleet',
] as const
// ---- wire registry mirrors (generated) ------------------------------------
// Shapes below come from zenagent/cli/rpc/protocol.py via
// scripts/gen_rpc_ts.py (wire.gen.ts): edit the registry, not these. The
// frontend refinements that stay handwritten (e.g. WorkspaceFile's
// discriminated union) must remain assignable to the flat wire types.
import type {
  MemoryCreateResult,
  MessageUsage,
  SessionSummary,
  Usage,
} from './wire.gen.ts'
export type {
  CommandEntry,
  CompactionRecord,
  ContextReport,
  ContextSlice,
  FsIndexResult,
  FsListResult,
  GitChangesPayload,
  GitChangesResult,
  GitCommit,
  GitDiffPayload,
  GitDiffResult,
  GitFileChange,
  GitLogResult,
  GitShowFile,
  GitShowPayload,
  GitShowResult,
  GitWorktreesResult,
  HelloResult,
  MemoryDeleteResult,
  MemoryEntry,
  MemoryCreateResult,
  GoalView,
  MemoryListResult,
  MessageUsage,
  MemoryUpdateResult,
  ModelEntry,
  PendingMessage,
  SessionSummary,
  StatusResult,
  SubagentTranscriptResult,
  SubagentView,
  ToolRow,
  TranscriptResult,
  ToolsResult,
  Usage,
  WorkspaceEntry,
  WorktreeEntry,
  WorktreeOverview,
} from './wire.gen.ts'

/** create and update return the same entry envelope; consumers knew this
 * shape by one name (used for both ops), so keep it importable as such. */
export type MemoryEntryResult = MemoryCreateResult

export type RequestKind = (typeof REQUEST_KINDS)[number]

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

// ---- models / commands catalogs ------------------------------------------

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
  kind: 'int' | 'float' | 'str'
  nullable: boolean
  min: number
  max: number
  default: number | string | null
  unit: string
  /** Allowed values when `kind` is `'str'`. */
  choices: string[]
  apply: 'immediate' | 'session'
  value: number | string | null
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

export interface SessionListResult {
  type: 'result'
  id: string
  ok: true
  scope: 'project' | 'all'
  sessions: SessionSummary[]
}

// ---- transcript snapshot ---------------------------------------------------

export interface MessageView {
  kind: 'user' | 'assistant' | 'skill'
  text: string
  /** Stable row identity inside one session (`v{n}`). */
  id?: string
  timestamp?: number
  /** Delivered through the steering channel (user rows only). */
  steering?: boolean
  /** True when this user row starts a goal continuation turn. */
  goal?: boolean
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

/** A task's run activity (`swarm transcript`), in the coding transcript's
 * own view shapes so the room replays it with the same row components.
 * `available` is false when the run is not in the assignee's current session
 * (reset or compacted). */
export interface SwarmTranscriptResult {
  type: 'result'
  id: string
  ok: true
  taskId: string
  available: boolean
  transcript: (MessageView | ToolCallView)[]
}

// ---- workspace ---------------------------------------------------------------

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

// ---- git review panel --------------------------------------------------------

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

export type ZenUiMode = 'coding' | 'zen'

/** One row of the project's zen fleet roster (zenagent/app/zen/fleet.py). */
export interface RosterItem {
  path: string
  title: string
  focus: string
  role: string
  status: string
  last_report: string
}

/** Server-initiated: the fleet roster changed (or enable/disable happened). */
export interface ZenFleetFrame {
  type: 'zen_fleet'
  enabled: boolean
  roster: RosterItem[]
  project?: string
  session?: string
}
export type ServerFrame =
  | ResultFrame
  | EventFrame
  | ZenFleetFrame
  | SwarmServerFrame

export function isEventFrame(frame: ServerFrame): frame is EventFrame {
  return frame.type === 'event'
}
