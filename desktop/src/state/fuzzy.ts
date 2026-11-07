/** Case-insensitive fuzzy matching for the composer autocomplete, ported from
 * pi: exact name > name prefix > name substring > path substring, plus a
 * directory bonus, with a forgiving subsequence fallback at the bottom. */

export interface FuzzyMatch {
  score: number
  /** Half-open [start, end) highlight ranges into the candidate string. */
  ranges: Array<[number, number]>
}

const SCORE_EXACT_NAME = 100
const SCORE_NAME_PREFIX = 80
const SCORE_NAME_SUBSTRING = 50
const SCORE_PATH_SUBSTRING = 30
const SCORE_SUBSEQUENCE = 10
const DIR_BONUS = 10

function mergeIndices(indices: number[]): Array<[number, number]> {
  const ranges: Array<[number, number]> = []
  for (const index of indices) {
    const last = ranges[ranges.length - 1]
    if (last !== undefined && last[1] === index) last[1] = index + 1
    else ranges.push([index, index + 1])
  }
  return ranges
}

function subsequence(query: string, text: string): number[] | null {
  const indices: number[] = []
  let at = 0
  for (const character of query) {
    const found = text.indexOf(character, at)
    if (found < 0) return null
    indices.push(found)
    at = found + 1
  }
  return indices
}

export function fuzzyMatchPath(
  query: string,
  path: string,
  kind: 'dir' | 'file' = 'file',
): FuzzyMatch | null {
  const bonus = kind === 'dir' ? DIR_BONUS : 0
  if (query === '') return { score: bonus, ranges: [] }

  const needle = query.toLowerCase()
  const candidate = path.toLowerCase()
  const nameStart = candidate.lastIndexOf('/') + 1
  const name = candidate.slice(nameStart)

  if (!needle.includes('/')) {
    if (name === needle) {
      return {
        score: SCORE_EXACT_NAME + bonus,
        ranges: [[nameStart, nameStart + needle.length]],
      }
    }
    if (name.startsWith(needle)) {
      return {
        score: SCORE_NAME_PREFIX + bonus,
        ranges: [[nameStart, nameStart + needle.length]],
      }
    }
    const inName = name.indexOf(needle)
    if (inName >= 0) {
      return {
        score: SCORE_NAME_SUBSTRING + bonus,
        ranges: [[nameStart + inName, nameStart + inName + needle.length]],
      }
    }
  }

  const inPath = candidate.indexOf(needle)
  if (inPath >= 0) {
    return {
      score: SCORE_PATH_SUBSTRING + bonus,
      ranges: [[inPath, inPath + needle.length]],
    }
  }

  const indices = subsequence(needle, candidate)
  if (indices !== null) {
    return { score: SCORE_SUBSEQUENCE + bonus, ranges: mergeIndices(indices) }
  }
  return null
}

export function fuzzyMatchCommand(query: string, text: string): FuzzyMatch | null {
  if (query === '') return { score: 0, ranges: [] }
  const needle = query.toLowerCase()
  const candidate = text.toLowerCase()
  if (candidate.startsWith(needle)) {
    return { score: SCORE_EXACT_NAME, ranges: [[0, needle.length]] }
  }
  const at = candidate.indexOf(needle)
  if (at >= 0) {
    return { score: SCORE_NAME_SUBSTRING, ranges: [[at, at + needle.length]] }
  }
  const indices = subsequence(needle, candidate)
  if (indices !== null) {
    return { score: SCORE_SUBSEQUENCE, ranges: mergeIndices(indices) }
  }
  return null
}

/** Higher score first, then shorter candidate, then lexical order. */
export function compareMatches(
  a: { score: number; text: string },
  b: { score: number; text: string },
): number {
  if (a.score !== b.score) return b.score - a.score
  if (a.text.length !== b.text.length) return a.text.length - b.text.length
  return a.text < b.text ? -1 : a.text > b.text ? 1 : 0
}

/** Keep only the `limit` best candidates, sorting that bounded set instead of
 * every match: the menu filters the whole index but shows a handful of rows. */
export function selectBestMatches<T>(
  candidates: Iterable<T>,
  limit: number,
  key: (candidate: T) => { score: number; text: string },
): T[] {
  if (limit <= 0) return []
  const kept: T[] = []
  const keys: Array<{ score: number; text: string }> = []
  for (const candidate of candidates) {
    const candidateKey = key(candidate)
    if (
      kept.length === limit &&
      compareMatches(candidateKey, keys[kept.length - 1]) >= 0
    ) {
      continue
    }
    let at = kept.length
    while (at > 0 && compareMatches(candidateKey, keys[at - 1]) < 0) at -= 1
    kept.splice(at, 0, candidate)
    keys.splice(at, 0, candidateKey)
    if (kept.length > limit) {
      kept.pop()
      keys.pop()
    }
  }
  return kept
}
