/** Server → client event frames: the agent's live stream and its payloads.
 *
 * Split out of `types.ts` (which re-exports them) so that module stays inside
 * its line budget; the shapes still mirror `zenagent/cli/rpc/codec.py`. */

import type { AskQuestion, MessageView, ToolCallView } from './types.ts'

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
    | 'retry_attempt'
    | 'approval_request'
    | 'ask_request'
    | 'ask_cancel'
    | 'terminal'
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
  // retry extras: the swallowed attempt, announced before its backoff
  attempt?: number
  max_retries?: number
  delay_ms?: number
  model?: string
  error?: string
  // approval_request / ask_request extras
  request_id?: string
  /** True when `args` was clipped server-side (audit P2): the approver is
   *  seeing a prefix; the broker still checks the full command. */
  argsTruncated?: boolean
  // terminal extras
  kind?: 'confirm' | 'output' | 'exit'
  data?: string
  code?: number | null
  session?: string
  risk?: string | null
  target?: string | null
  reason?: string | null
  questions?: AskQuestion[]
}

export interface EventFrame {
  type: 'event'
  /** The slot that emitted the frame (host tag, absent on a v2 server). */
  project?: string
  /** The session that slot was on when the event happened. */
  session?: string
  event: AgentEventFrame
}
