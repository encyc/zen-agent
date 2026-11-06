/** Review aggregation: every tool diff in the transcript, grouped per touched
 * file in first-seen order, with changed-line counts and parsed unified hunks. */

import type { ChatNode } from '../../state/chatStore'

export interface FileReview {
  path: string
  additions: number
  removals: number
  diffs: string[]
}

export interface ReviewDiffLine {
  kind: 'add' | 'del' | 'ctx' | 'meta'
  text: string
  oldNo: number | null
  newNo: number | null
}

export interface ReviewHunk {
  header: string
  lines: ReviewDiffLine[]
}

const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
const REVIEW_MAX_LINES = 1200

/** Parse a unified diff into hunks, numbering both sides so the panel can
 * render real old/new gutters. File headers are dropped: the section title
 * already names the file. */
export function parseDiffHunks(diff: string): ReviewHunk[] {
  const hunks: ReviewHunk[] = []
  let current: ReviewHunk | null = null
  let oldNo = 0
  let newNo = 0
  let used = 0

  for (const raw of diff.split('\n')) {
    if (used >= REVIEW_MAX_LINES) {
      current?.lines.push({ kind: 'meta', text: '…', oldNo: null, newNo: null })
      break
    }
    const header = HUNK_HEADER.exec(raw)
    if (header !== null) {
      current = { header: raw, lines: [] }
      hunks.push(current)
      oldNo = Number(header[1])
      newNo = Number(header[2])
      used += 1
      continue
    }
    if (hunks.length === 0 && (raw.startsWith('---') || raw.startsWith('+++'))) continue
    if (raw.startsWith('\\')) {
      current?.lines.push({ kind: 'meta', text: raw, oldNo: null, newNo: null })
      used += 1
      continue
    }
    const mark = raw.charAt(0)
    const body = mark === ' ' || mark === '+' || mark === '-' ? raw.slice(1) : raw
    current ??= { header: '', lines: [] }
    if (hunks.length === 0) hunks.push(current)
    const numbered = current.header !== ''
    if (mark === '+') {
      current.lines.push({
        kind: 'add',
        text: body,
        oldNo: null,
        newNo: numbered ? newNo++ : null,
      })
    } else if (mark === '-') {
      current.lines.push({
        kind: 'del',
        text: body,
        oldNo: numbered ? oldNo++ : null,
        newNo: null,
      })
    } else if (mark === '@') {
      current.lines.push({ kind: 'meta', text: raw, oldNo: null, newNo: null })
    } else {
      current.lines.push({
        kind: 'ctx',
        text: body,
        oldNo: numbered ? oldNo++ : null,
        newNo: numbered ? newNo++ : null,
      })
    }
    used += 1
  }
  return hunks
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
