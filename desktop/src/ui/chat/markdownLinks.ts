/** External model-markdown links never navigate the app window (audit P2).
 *
 * A model can emit arbitrary markdown; a click that replaces the webview is
 * a phishing surface (a fake "re-login" page). http(s) opens in the system
 * browser; every other scheme is intercepted and dropped; in-page anchors
 * keep their default behavior.
 */

export type ExternalLinkAction = 'browser' | 'ignore' | null

export function externalLinkAction(href: string | null): ExternalLinkAction {
  if (href === null) return null
  const trimmed = href.trim()
  if (/^https?:\/\//i.test(trimmed)) return 'browser'
  return trimmed === '' || trimmed.startsWith('#') ? null : 'ignore'
}
