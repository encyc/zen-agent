export function frameError(frame: Record<string, unknown>): string | null {
  if (frame.type === 'error') {
    return typeof frame.message === 'string' && frame.message !== '' ? frame.message : 'request failed'
  }
  if (frame.type === 'result' && frame.ok === false) {
    return typeof frame.reason === 'string' && frame.reason !== '' ? frame.reason : 'request failed'
  }
  return null
}
