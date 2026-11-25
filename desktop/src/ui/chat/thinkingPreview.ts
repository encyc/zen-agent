/** The collapsed reasoning row's one-line summary.
 *
 * The open body renders the model's markdown; the summary must not echo the
 * syntax — ``**Step 1**`` at 11px reads as noise, not as emphasis. So the first
 * line's inline markers are stripped and only its text is kept.
 */

const LEADING = /^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/
const LINK = /\[([^\]]*)\]\([^)]*\)/g
const PAIRED = /\*\*|__|~~|`/g
const OPEN_EMPHASIS = /(^|[\s(])[*_](?=\S)/g
const CLOSE_EMPHASIS = /([\S])[*_](?=[\s).,;:!?]|$)/g

export function plainLine(line: string): string {
  return line
    .replace(LEADING, '')
    .replace(LINK, '$1')
    .replace(PAIRED, '')
    .replace(OPEN_EMPHASIS, '$1')
    .replace(CLOSE_EMPHASIS, '$1')
    .trim()
}

export function thinkingPreview(text: string, limit = 96): string {
  const plain = plainLine(text.split('\n', 1)[0] ?? '')
  return plain.length > limit ? `${plain.slice(0, limit - 3)}…` : plain
}
