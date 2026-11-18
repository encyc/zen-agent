/** Git panel pure helpers: the three-group partition of the changes list and
 * the "touched this session" match between git-relative paths and the
 * transcript's (usually absolute) tool paths. */

import type { ChatNode } from '../../state/chatStore.ts'
import type { GitFileChange } from '../../rpc/types.ts'
import { collectFileChanges } from './review.ts'
import { toWorkspacePath } from './workspacePath.ts'

export interface GroupedChanges {
  staged: GitFileChange[]
  unstaged: GitFileChange[]
  untracked: GitFileChange[]
}

export function groupChanges(files: GitFileChange[]): GroupedChanges {
  const grouped: GroupedChanges = { staged: [], unstaged: [], untracked: [] }
  for (const file of files) {
    if (file.state === 'staged') grouped.staged.push(file)
    else if (file.state === 'unstaged') grouped.unstaged.push(file)
    else grouped.untracked.push(file)
  }
  return grouped
}

/** Git-relative paths the current session's tool calls touched. Transcript
 * paths are usually absolute; `root` folds them onto the served project. */
export function sessionPaths(nodes: ChatNode[], root: string | null | undefined): Set<string> {
  const paths = new Set<string>()
  for (const change of collectFileChanges(nodes)) {
    const folded = toWorkspacePath(change.path, root)
    // A path outside the root folds to itself: it can never match a
    // git-relative entry, so it stays out of the set.
    const inside = folded !== change.path || !change.path.startsWith('/')
    if (inside && folded !== '') paths.add(folded)
  }
  return paths
}

export function isSessionFile(path: string, session: Set<string>): boolean {
  return session.has(path)
}
