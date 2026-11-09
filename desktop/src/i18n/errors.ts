/** Server error frames cross the wire as codes + params so the UI can speak
 * the person's language; `message` (server-locale) stays the fallback for
 * codes this build does not know. */

import { translate, type Locale } from './messages.ts'

const CODE_KEYS: Record<string, string> = {
  'taiji.inactive': 'taiji.error.inactive',
  'taiji.merge_blocked': 'taiji.error.mergeBlocked',
  'taiji.worker_missing': 'taiji.error.workerMissing',
  'taiji.bad_limit': 'taiji.error.badLimit',
  'prompt_failed': 'rpc.error.promptFailed',
  'fs_error': 'rpc.error.fsFailed',
}

export function localizeError(
  frame: { message?: string; code?: string; params?: Record<string, string> },
  locale: Locale = 'en',
): string {
  const key = frame.code !== undefined ? CODE_KEYS[frame.code] : undefined
  if (key === undefined) return frame.message ?? ''
  return translate(locale, key, frame.params)
}
