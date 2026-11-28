/** The dev bridge's `fs_exists` answer. The seeded tree is the mock's only
 * filesystem, so existence is answered from it the way the real server would
 * answer from disk: fold the reference into the served root (the chat sends
 * project-relative paths, but a workspace absolute path is a valid reference
 * too), then accept a seeded file, a seeded directory key, or a path named by
 * any seeded listing. */

import { toWorkspacePath } from '../ui/rightbar/workspacePath.ts'
import type { BridgeState } from './bridge.ts'

export function mockFsExists(state: BridgeState, path: string): boolean {
  const rel = toWorkspacePath(path, state.activeRoot ?? '')
  if (rel === '') return true
  if (state.fsFiles?.[rel] !== undefined || state.fsEntries?.[rel] !== undefined) return true
  return Object.values(state.fsEntries ?? {}).some((entries) =>
    entries.some((entry) => String(entry.path) === rel),
  )
}
