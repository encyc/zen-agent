/** Live task-detail rows from one role's run events.
 *
 * A role's session file only lands when its run ends, so a running task's
 * detail cannot be read back from disk. These events are the same
 * AgentEvent stream the coding transcript consumes; this reducer folds them
 * into the view rows `nodesFromViews` already renders. It is deliberately
 * the small subset the run view needs: streaming text, settled messages and
 * tool calls. The terminal transcript is still authoritative (the panel
 * re-fetches it when the task settles). */

import type { AgentEventFrame, MessageView, ToolCallView } from '../rpc/types.ts'

export type RunView = MessageView | ToolCallView

/** An assistant row still taking deltas (mirrors coding's AssistantNode). */
type StreamingAssistant = MessageView & { streaming?: boolean }

let seq = 0
const nextId = (): string => `r${(seq += 1)}`

function streamingIndex(rows: RunView[]): number {
  const index = rows.length - 1
  const tail = rows[index] as StreamingAssistant | undefined
  return tail !== undefined && tail.kind === 'assistant' && tail.streaming === true
    ? index
    : -1
}

function patch(
  rows: RunView[],
  index: number,
  patchRow: Partial<MessageView> | Partial<ToolCallView>,
): RunView[] {
  const next = rows.slice()
  next[index] = { ...next[index], ...patchRow } as RunView
  return next
}

function toolIndex(rows: RunView[], callId: string | undefined): number {
  if (callId === undefined) return -1
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const row = rows[i]
    if (row.kind === 'tool' && row.call_id === callId) return i
  }
  return -1
}

/** Fold one run event into the accumulated rows. Lifecycle events return the
 * same array so React can skip the rebuild. */
export function applyRunEvent(rows: RunView[], event: AgentEventFrame): RunView[] {
  switch (event.type) {
    case 'message_start': {
      const role = event.message?.role
      if (role === 'user') {
        // A /skill: turn is a chip view, exactly as the wire projects it.
        const skill = event.message?.skill === true
        return [
          ...rows,
          {
            kind: skill ? 'skill' : 'user',
            id: nextId(),
            text: event.message?.text ?? '',
            steering: event.message?.steering === true,
            ...(skill
              ? {
                  skill_args: event.message?.skill_args ?? null,
                  skill_body: event.message?.skill_body ?? null,
                }
              : {}),
          } as MessageView,
        ]
      }
      if (role === 'assistant') {
        return [
          ...rows,
          {
            kind: 'assistant',
            id: nextId(),
            text: '',
            thinking: '',
            timestamp: 0,
            streaming: true,
          } as StreamingAssistant,
        ]
      }
      return rows
    }
    case 'message_update': {
      const kind = event.assistant_event?.type
      const delta = event.assistant_event?.delta ?? ''
      if (kind === 'thinking_start' || kind === 'thinking_delta') {
        const index = streamingIndex(rows)
        if (index < 0 || delta === '') return rows
        const row = rows[index] as MessageView
        return patch(rows, index, {
          thinking: (row.thinking ?? '') + delta,
        } as Partial<MessageView>)
      }
      if (kind !== 'text_start' && kind !== 'text_delta') return rows
      const index = streamingIndex(rows)
      if (index < 0) {
        // A run observed from the middle: open the row on the first delta.
        if (delta === '') return rows
        return [
          ...rows,
          {
            kind: 'assistant',
            id: nextId(),
            text: delta,
            thinking: '',
            timestamp: 0,
            streaming: true,
          } as StreamingAssistant,
        ]
      }
      const row = rows[index] as MessageView
      return patch(rows, index, { text: (row.text ?? '') + delta } as Partial<MessageView>)
    }
    case 'message_end': {
      const index = streamingIndex(rows)
      if (index < 0) return rows
      const row = rows[index] as MessageView
      const text = event.message?.text ?? row.text
      // A turn that only carried tool calls ends empty: the tool cards are
      // its visible content (views.py drops the row the same way). Thinking
      // still counts as content, exactly as coding's chatStore keeps it.
      if (text === '' && (row.thinking ?? '').trim() === '') {
        const next = rows.slice()
        next.splice(index, 1)
        return next
      }
      return patch(rows, index, { text, streaming: false } as Partial<StreamingAssistant>)
    }
    case 'tool_execution_start': {
      let args: Record<string, unknown> = {}
      try {
        args = event.args ? (JSON.parse(event.args) as Record<string, unknown>) : {}
      } catch {
        args = {}
      }
      const raw = event.args ?? ''
      return [
        ...rows,
        {
          kind: 'tool',
          id: nextId(),
          call_id: event.tool_call_id ?? nextId(),
          name: event.tool_name ?? '',
          args_preview: raw.length <= 140 ? raw : raw.slice(0, 137) + '...',
          args,
          details: {},
          status: 'running',
          progress: '',
          result_text: '',
          is_error: false,
          duration_ms: null,
        } as ToolCallView,
      ]
    }
    case 'tool_execution_update': {
      const index = toolIndex(rows, event.tool_call_id)
      if (index < 0) return rows
      return patch(rows, index, { progress: event.partial_result ?? '' })
    }
    case 'tool_execution_end': {
      const index = toolIndex(rows, event.tool_call_id)
      if (index < 0) return rows
      let details: Record<string, unknown> = {}
      if (event.details) {
        try {
          details = JSON.parse(event.details) as Record<string, unknown>
        } catch {
          details = {}
        }
      }
      const failed = event.is_error === true
      return patch(rows, index, {
        status: failed ? 'error' : 'ok',
        result_text: event.result_text ?? '',
        is_error: failed,
        duration_ms: event.duration_ms ?? null,
        details,
        diff: typeof details.diff === 'string' && details.diff ? details.diff : null,
      })
    }
    default:
      return rows
  }
}
