/** The refusal text a reply carries, or null when the server said ok.
 *
 * Every rpc answer can be a refusal (`ok:false` with a `reason`, or an error
 * frame with a `message`); the params are structural so both the typed result
 * frames and plain bridge records pass without a cast at the call site. */

import { localizeError } from '../i18n/errors.ts'
import type { Locale } from '../i18n/messages.ts'

export interface ReplyLike {
  type?: string
  ok?: unknown
  message?: unknown
  reason?: unknown
  code?: unknown
  params?: unknown
}

/** One server refusal: the fallback text plus the optional code/params a
 * newer server sends so the UI can speak the person's language. */
export interface ErrorDetail {
  message: string
  code?: string
  params?: Record<string, string>
}

function textField(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined
}

function paramsField(value: unknown): Record<string, string> | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined
  const params: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'string') params[key] = entry
  }
  return Object.keys(params).length > 0 ? params : undefined
}

/** Read a refusal frame without losing its code: `message` always carries
 * something (the server's text or the fallback), the code/params are only
 * there when the server sent them. */
export function frameErrorDetail(
  frame: ReplyLike,
  fallback = 'request failed',
): ErrorDetail | null {
  let message: string | undefined
  if (frame.type === 'error') {
    message = textField(frame.message)
  } else if (frame.type === 'result' && frame.ok === false) {
    message = textField(frame.reason)
  } else {
    return null
  }

  const detail: ErrorDetail = { message: message ?? fallback }
  const code = textField(frame.code)
  if (code !== undefined) detail.code = code
  const params = paramsField(frame.params)
  if (params !== undefined) detail.params = params
  return detail
}

export function frameError(frame: ReplyLike, fallback = 'request failed'): string | null {
  return frameErrorDetail(frame, fallback)?.message ?? null
}

/** The notice text for a refusal: a known code maps to the dictionary, an
 * unknown one keeps the server's own message. Null when the frame is not a
 * refusal at all. */
export function frameErrorNotice(frame: ReplyLike, locale: Locale = 'en'): string | null {
  const detail = frameErrorDetail(frame)
  return detail === null ? null : localizeError(detail, locale)
}

/** Whether a frame belongs in the active project's view. Untagged frames
 * (host-level answers, a v2 server) always pass; a tagged frame from a
 * background slot must not leak into the active transcript. */
export function frameBelongsTo(
  frame: { project?: unknown },
  active: string | null,
): boolean {
  if (active === null || active === '') return true
  const tag = typeof frame.project === 'string' ? frame.project : ''
  return tag === '' || tag === active
}
