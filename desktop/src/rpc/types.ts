/** zen rpc wire types (zenagent/cli/rpc.py). Shapes verified against the
 * live server; the frontend mirrors the TUI reducer (zenagent/app/views.py)
 * when assembling them. */

import type { EventFrame } from './types-events.ts'

export type {
  AgentEventFrame,
  AssistantEvent,
  EventFrame,
  MessageProjection,
} from './types-events.ts'


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

export const PROTOCOL_VERSION = 4

export const REQUEST_KINDS = [
  'approval',
  'ask',
  'automations',
  'cancel',
  'command',
  'commands',
  'follow_up',
  'fs_exists',
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
  'runs',
  'terminal',
  'tools',
  'transcript',
  'usage',
] as const
// ---- wire registry mirrors (generated) ------------------------------------
// Shapes below come from zenagent/cli/rpc/protocol.py via
// scripts/gen_rpc_ts.py (wire.gen.ts): edit the registry, not these. The
// frontend refinements that stay handwritten (e.g. WorkspaceFile's
// discriminated union) must remain assignable to the flat wire types.
import type {
  LimitsGetResult,
  MemoryCreateResult,
  MessageUsage,
  PluginsListResult,
  SecretsListResult,
  SecretsTestResult,
  SessionSummary,
  SkillsListResult,
  SkillsReadResult,
  Usage,
} from './wire.gen.ts'
export type {
  CommandEntry,
  CompactionRecord,
  LimitKnob,
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
  BuiltinMcpRow,
  HelloResult,
  McpBuiltinResult,
  McpListResult,
  McpServerRow,
  McpTestResult,
  MemoryDeleteResult,
  MemoryEntry,
  MemoryCreateResult,
  GoalView,
  MemoryListResult,
  MessageUsage,
  MemoryUpdateResult,
  ModelEntry,
  PendingMessage,
  PluginCatalogRow,
  PluginCredentialRow,
  PluginRow,
  PluginServerRow,
  SecretProviderRow,
  SessionSummary,
  SkillRow,
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

/** Sub-op envelopes are generated per-op (SecretsListResult, ...); the
 * frontend knew these unified names, so keep them importable as such. */
export type SecretsResult = SecretsListResult
export type SecretTestResult = SecretsTestResult
export type SkillsResult = SkillsListResult
export type SkillReadResult = SkillsReadResult
export type LimitsResult = LimitsGetResult
export type PluginsResult = PluginsListResult

export type RequestKind = (typeof REQUEST_KINDS)[number]

// ---- prompt --------------------------------------------------------------

export interface PromptResult {
  type: 'result'
  id: string
  ok: boolean
  text: string
  usage: Usage
  termination: string | null
  /** Why the run closed, when the termination has a reason (a guard kill
   *  names the batch the model kept repeating). */
  detail?: string
  error?: string
  session?: string
}

// ---- status --------------------------------------------------------------

export interface TodoPhase {
  [key: string]: unknown
}

// ---- models / commands catalogs ------------------------------------------

// ---- settings secrets ------------------------------------------------------

// ---- skills --------------------------------------------------------------

// ---- plugins -------------------------------------------------------------

// ---- mcp -----------------------------------------------------------------

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

export interface FsExistsResult {
  type: 'result'
  id: string
  ok: true
  path: string
  exists: boolean
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

export type ServerFrame = ResultFrame | EventFrame

export function isEventFrame(frame: ServerFrame): frame is EventFrame {
  return frame.type === 'event'
}
