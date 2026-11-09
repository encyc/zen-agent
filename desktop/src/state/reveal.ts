/** Incremental reveal for the panels' long lists.
 *
 * The files tree and the memory list arrive in one payload; mounting every
 * row is wasted work when most of them sit below the fold. The first batch
 * mounts on open and each "load more" adds one more batch. This is purely a
 * presentation concern: the data layer is untouched.
 */

/** Directory entries mounted per batch in the files tree. */
export const FILES_REVEAL_BATCH = 200

/** Memory notes mounted per batch in the memory list. */
export const MEMORY_REVEAL_BATCH = 100

/** Rows revealed after `pages` batches; stale state falls back to one page. */
export function revealedCount(total: number, pages: number, batch: number): number {
  const length = Math.max(0, total)
  const size = Math.max(0, Math.trunc(batch))
  const pageCount = Math.max(1, Math.trunc(pages))
  return Math.min(length, pageCount * size)
}
