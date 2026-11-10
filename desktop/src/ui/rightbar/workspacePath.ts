/** Fold an absolute path inside the served project back to the project-relative
 * form the Files tree is keyed by. A chat reference may name the real absolute
 * path; the file read accepts it, but revealing a folder walks the tree with
 * relative paths, so the reference is normalized before the panel uses it. */

function stripTrailingSlashes(path: string): string {
  return path.replace(/\/+$/u, '')
}

export function toWorkspacePath(path: string, root: string | null | undefined): string {
  const target = stripTrailingSlashes(path.replaceAll('\\', '/'))
  const base = stripTrailingSlashes((root ?? '').replaceAll('\\', '/'))
  if (target === '' || base === '') return target
  if (target === base) return ''
  // Windows drive roots are case-insensitive; POSIX roots are not.
  const caseFold = /^[A-Za-z]:\//u.test(base)
  const prefix = `${base}/`
  const inside = caseFold
    ? target.toLowerCase().startsWith(prefix.toLowerCase())
    : target.startsWith(prefix)
  return inside ? target.slice(prefix.length) : target
}
