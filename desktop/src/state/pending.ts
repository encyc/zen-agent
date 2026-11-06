import type { PendingMessage } from '../rpc/types'

/** Steering is text-only and needs a live step boundary to inject at. */
export function canPromotePending(item: PendingMessage, running: boolean): boolean {
  return running && item.lane === 'queue' && item.images === 0
}

/** Dequeue pops the newest queued entry, so the dock must headline that same
 * entry; otherwise 取回 takes back text the user cannot see. */
export function queuedNewestFirst(items: PendingMessage[]): PendingMessage[] {
  return items.filter((item) => item.lane === 'queue').reverse()
}

/** Dequeue pops the newest queued entry; image-bearing entries would come
 * back invisibly because the GUI cannot render controller-side attachments. */
export function canDequeuePending(items: PendingMessage[]): boolean {
  const latest = queuedNewestFirst(items)[0]
  return latest !== undefined && latest.images === 0
}

export function nextDraft(previous: string, taken: string): string {
  return previous.trim() === '' ? taken : `${previous}\n${taken}`
}
