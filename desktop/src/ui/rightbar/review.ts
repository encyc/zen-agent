/** Review aggregation: every tool diff in the transcript, grouped per touched
 * file in first-seen order, with changed-line counts and the raw diff blocks. */

import type { ChatNode } from '../../state/chatStore'

export interface FileReview {
  path: string
  additions: number
  removals: number
  diffs: string[]
}

function changedPath(node: Extract<ChatNode, { kind: 'tool' }>): string {
  const fromArgs = node.args.path
  if (typeof fromArgs === 'string' && fromArgs !== '') return fromArgs
  const fromDetails = node.details.path
  if (typeof fromDetails === 'string' && fromDetails !== '') return fromDetails
  return node.name
}

export function collectFileChanges(nodes: ChatNode[]): FileReview[] {
  const files = new Map<string, FileReview>()
  for (const node of nodes) {
    if (node.kind !== 'tool' || node.diff === null || node.diff === '') continue
    const path = changedPath(node)
    let file = files.get(path)
    if (file === undefined) {
      file = { path, additions: 0, removals: 0, diffs: [] }
      files.set(path, file)
    }
    file.diffs.push(node.diff)
    for (const line of node.diff.split('\n')) {
      if (line.startsWith('+++') || line.startsWith('---')) continue
      if (line.startsWith('+')) file.additions += 1
      else if (line.startsWith('-')) file.removals += 1
    }
  }
  return [...files.values()]
}

/** The basename keeps the row readable when a session spans long paths. */
export function fileBasename(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? path
}
