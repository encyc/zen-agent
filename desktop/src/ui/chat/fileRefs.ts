/** Conservative file-reference detection for assistant markdown. A bare token
 * only counts as a file when it carries a known extension (or is a known
 * project file name), so dotted identifiers in prose (`store.messages`) and
 * URLs stay plain text; a token ending in `/` is an unambiguous folder and
 * links without the extension test. Trailing `:line[:col]` / `#Lx` refs select
 * the file itself; the suffix is dropped before the backend reads it. */

const KNOWN_EXTS = new Set([
  'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'json', 'css', 'scss', 'less',
  'html', 'htm', 'md', 'mdx', 'txt', 'rs', 'py', 'go', 'rb', 'sh', 'zsh',
  'bash', 'yml', 'yaml', 'toml', 'sql', 'swift', 'kt', 'java', 'c', 'h',
  'cpp', 'hpp', 'cs', 'php', 'vue', 'svelte', 'xml', 'ini', 'cfg', 'conf',
  'env', 'lock', 'svg', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'pdf',
  'csv', 'tsv', 'log',
])

const KNOWN_BARE = new Set(['Makefile', 'Dockerfile', 'LICENSE', 'README', 'CHANGELOG'])

const LINE_SUFFIX_RE = /(?::\d+(?::\d+|-\d+)?|#L\d+(?:-L?\d+)?)$/iu

// Drive-prefixed Windows paths keep their colon; every other colon (a URL
// scheme, `key: value`) means the token is not a path.
const FILE_TOKEN_RE = /^(?:[A-Za-z]:[\\/])?[\p{L}\p{N}_@+.\\/-]+$/u
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*:/u

function isLikelyFileName(base: string): boolean {
  const dot = base.lastIndexOf('.')
  if (dot <= 0 || dot === base.length - 1) return false
  const ext = base.slice(dot + 1).toLowerCase()
  return KNOWN_EXTS.has(ext.replace(/[+@-][\p{L}\p{N}_@+-]*$/u, ''))
}

/** The workspace path a token names, or null when it is not a file. */
export function parseFileRef(text: string): string | null {
  let raw = text.trim()
  if (raw === '' || raw.length > 512) return null
  if (raw.startsWith('@')) raw = raw.slice(1)
  raw = raw.replace(LINE_SUFFIX_RE, '')
  if (raw === '' || !FILE_TOKEN_RE.test(raw)) return null

  // A trailing slash names a folder outright, so it links without the
  // extension test that keeps prose such as `and/or` plain.
  const folder = /[\\/]$/u.test(raw)
  if (folder) raw = raw.replace(/[\\/]+$/u, '')
  const base = raw.replaceAll('\\', '/').split('/').pop() ?? ''
  if (raw === '' || base === '' || base === '.' || base === '..') return null
  if (raw.includes('/') || raw.includes('\\')) {
    return folder || isLikelyFileName(base) ? raw : null
  }
  if (folder) return raw
  return KNOWN_BARE.has(base) || isLikelyFileName(base) ? raw : null
}

/**
 * The workspace path an explicit markdown link destination names, or null
 * when it is not a local path. A destination is an explicit reference, so
 * any relative path counts — extension or not, folders included; the Files
 * panel decides what the path turns out to be. URI schemes, protocol-relative
 * URLs and in-page fragments are not workspace paths.
 */
export function parseLinkRef(href: string): string | null {
  const trimmed = href.trim()
  if (trimmed === '') return null
  let raw: string
  try {
    raw = decodeURIComponent(trimmed).trim()
  } catch {
    raw = trimmed
  }
  if (raw === '' || raw.length > 512 || raw.startsWith('#') || raw.startsWith('//')) {
    return null
  }
  // A leading drive letter is not a scheme; everything else with a colon is.
  if (SCHEME_RE.test(raw) && !/^[A-Za-z]:[\\/]/u.test(raw)) return null
  raw = raw.replace(/^(?:\.\/)+/u, '').replace(LINE_SUFFIX_RE, '')
  if (raw === '' || /[\n\0]/u.test(raw)) return null
  return raw
}
