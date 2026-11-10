/** Which plugin an install frame added: the requested id when the catalog
 * install carried one, otherwise the first id absent from the pre-install
 * list (path installs have no requested id). */

import type { PluginsResult } from '../rpc/types'

export function newlyInstalledId(
  frame: PluginsResult,
  knownIds: readonly string[],
  requestedId?: string,
): string | null {
  const rows = frame.plugins ?? []
  if (requestedId !== undefined && rows.some((row) => row.id === requestedId)) {
    return requestedId
  }
  const known = new Set(knownIds)
  const added = rows.find((row) => !known.has(row.id))
  return added ? added.id : null
}
