/** `@` file mentions for the composer: trigger detection over draft+cursor,
 * insertion formatting, and the exact token ranges the input boxes. The
 * grammar mirrors the pi CLI editor: `@` opens file mode at start-of-input or
 * after a delimiter, and `@"..."` keeps a quoted path with spaces together. */

import type { SlashCommand, SlashSkill } from './slashMenu'

export interface MentionTrigger {
  /** Filter text after `@`, without the quotes. */
  query: string
  /** Index of the `@` in the draft. */
  tokenStart: number
  /** End of the replaced region — always the cursor position. */
  tokenEnd: number
  quoted: boolean
}

export interface IndexedPath {
  path: string
  kind: 'dir' | 'file'
}

export type TokenKind = 'command' | 'skill' | 'file' | 'dir'

export interface TokenBox {
  start: number
  end: number
  kind: TokenKind
}

const DELIMITERS = new Set([' ', '\t', '\n', '\r', '"', "'", '='])
const SKILL_PREFIX = 'skill:'

function isBoundary(value: string, index: number): boolean {
  if (index <= 0) return true
  return DELIMITERS.has(value[index - 1])
}

export function detectMention(value: string, cursor: number): MentionTrigger | null {
  if (cursor < 0 || cursor > value.length) return null

  // Quoted form first: @"query with spaces, still open at the cursor.
  for (let i = cursor - 1; i >= 0; i -= 1) {
    const character = value[i]
    if (character === '"') {
      if (i > 0 && value[i - 1] === '@' && isBoundary(value, i - 1)) {
        const query = value.slice(i + 1, cursor)
        if (!query.includes('"') && !query.includes('\n')) {
          return { query, tokenStart: i - 1, tokenEnd: cursor, quoted: true }
        }
      }
      break
    }
    if (character === '\n') break
  }

  // Plain token: scan back to the nearest delimiter.
  let start = cursor
  while (start > 0 && !DELIMITERS.has(value[start - 1])) start -= 1
  const token = value.slice(start, cursor)
  if (token.startsWith('@') && isBoundary(value, start)) {
    return { query: token.slice(1), tokenStart: start, tokenEnd: cursor, quoted: false }
  }
  return null
}

export function formatMentionInsert(path: string, kind: 'dir' | 'file'): string {
  const needsQuote = /\s/.test(path)
  if (kind === 'dir') return needsQuote ? `@"${path}/` : `@${path}/`
  return needsQuote ? `@"${path}" ` : `@${path} `
}

const SLASH_TOKEN = /(^|\s)\/([^\s]+)/g
const MENTION_TOKEN = /(^|\s)@(?:"([^"\n]+)"|([^\s]+))/g

function pathKind(
  path: string,
  index: ReadonlyMap<string, 'dir' | 'file'>,
): 'dir' | 'file' | null {
  const clean = path.endsWith('/') ? path.slice(0, -1) : path
  const kind = index.get(clean)
  if (kind === undefined) return null
  return kind
}

/**
 * Exact tokens the composer can resolve right now: a known `/command`, an
 * enabled `/skill:<name>`, or an `@path` present in the workspace index.
 * Partial or unknown tokens stay plain text, which is what lets the box mean
 * "this reference will be sent".
 */
export function recognizedTokens(
  value: string,
  commands: readonly SlashCommand[],
  skills: readonly SlashSkill[],
  entries: readonly IndexedPath[],
): TokenBox[] {
  const boxes: TokenBox[] = []
  const commandsByName = new Set(commands.map((command) => command.name))
  const enabledSkills = new Set(
    skills.filter((skill) => skill.enabled).map((skill) => skill.name),
  )
  const index = new Map(entries.map((entry) => [entry.path, entry.kind] as const))

  for (const match of value.matchAll(SLASH_TOKEN)) {
    const offset = match.index ?? 0
    const start = offset + match[1].length
    const end = offset + match[0].length
    const name = match[2]
    if (name.startsWith(SKILL_PREFIX)) {
      if (enabledSkills.has(name.slice(SKILL_PREFIX.length))) {
        boxes.push({ start, end, kind: 'skill' })
      }
      continue
    }
    if (commandsByName.has(name)) boxes.push({ start, end, kind: 'command' })
  }

  for (const match of value.matchAll(MENTION_TOKEN)) {
    const offset = match.index ?? 0
    const start = offset + match[1].length
    const end = offset + match[0].length
    const path = match[2] ?? match[3]
    const kind = pathKind(path, index)
    if (kind !== null) boxes.push({ start, end, kind })
  }

  return boxes.sort((a, b) => a.start - b.start || a.end - b.end)
}
