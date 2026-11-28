/** The image an expanded read_image card should fetch, or null when there is
 * nothing to fetch. The folded card stays as-is (user request): bytes only
 * move when the person opens the body, so the decision lives here where it
 * can be tested without React. */

import type { ToolNode } from '../../state/chatStore'

export interface ReadImageTarget {
  path: string
}

/** A settled read_image call with a path argument names one image. A failed
 * or still-running call has nothing to show beyond its IN/OUT card, and a
 * path-less call should never have happened. */
export function readImageTarget(node: ToolNode): ReadImageTarget | null {
  if (node.name !== 'read_image') return null
  if (node.state !== 'ok' || node.isError) return null
  const path = node.args.path
  return typeof path === 'string' && path.trim() !== '' ? { path } : null
}
