/** The refusal text a reply carries, or null when the server said ok.
 *
 * Every rpc answer can be a refusal (`ok:false` with a `reason`, or an error
 * frame with a `message`); the params are structural so both the typed result
 * frames and plain bridge records pass without a cast at the call site. */
export interface ReplyLike {
  type?: string
  ok?: unknown
  message?: unknown
  reason?: unknown
}

export function frameError(frame: ReplyLike, fallback = 'request failed'): string | null {
  if (frame.type === 'error') {
    return typeof frame.message === 'string' && frame.message !== '' ? frame.message : fallback
  }
  if (frame.type === 'result' && frame.ok === false) {
    return typeof frame.reason === 'string' && frame.reason !== '' ? frame.reason : fallback
  }
  return null
}
