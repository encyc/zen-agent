import type { ToolCallView } from '../rpc/types'

/** What the row's trailing cue renders, from the call's status alone. */
export type ToolCue =
  | { kind: 'running' }
  | { kind: 'ok' }
  | { kind: 'stopped'; label: 'tool.stopped' }
  | { kind: 'failed'; label: 'tool.failed' | 'tool.timed_out' }

/**
 * Map a call status to its cue.
 *
 * `is_error` is deliberately false for a command that exits non-zero or gets
 * killed (`bash`'s contract: command failure is information, not an agent
 * error), so a killed command must not fall through to the check mark.
 */
export function toolCue(status: ToolCallView['status']): ToolCue {
  switch (status) {
    case 'running':
      return { kind: 'running' }
    case 'error':
      return { kind: 'failed', label: 'tool.failed' }
    case 'timed_out':
      return { kind: 'failed', label: 'tool.timed_out' }
    case 'interrupted':
      return { kind: 'stopped', label: 'tool.stopped' }
    default:
      return { kind: 'ok' }
  }
}
