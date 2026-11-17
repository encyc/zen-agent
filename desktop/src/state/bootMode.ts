/** The boot override a detached session window carries in its URL:
 * `?boot=session&session=<path>&project=<root>`. Parsing is pure so node
 * tests and the real webview share one decision; anything malformed (a
 * hand-typed URL, a stale link) boots the normal shell instead. */

export interface BootOverride {
  kind: 'session'
  session: string
  project: string | null
}

export function parseBootOverride(search: string): BootOverride | null {
  let params: URLSearchParams
  try {
    params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  } catch {
    return null
  }
  if (params.get('boot') !== 'session') return null
  const session = params.get('session')?.trim() ?? ''
  if (session === '') return null
  const project = params.get('project')?.trim() ?? ''
  return { kind: 'session', session, project: project === '' ? null : project }
}
