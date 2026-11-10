/** Server error frames cross the wire as codes + params so the UI can speak
 * the person's language; `message` (server-locale) stays the fallback for
 * codes this build does not know. */

import { translate, type Locale } from './messages.ts'

const CODE_KEYS: Record<string, string> = {
  'prompt_failed': 'rpc.error.promptFailed',
  'fs_error': 'rpc.error.fsFailed',
  'session_failed': 'rpc.error.sessionFailed',
  'session_import_missing': 'rpc.error.sessionImportMissing',
  'session_busy': 'rpc.error.sessionBusy',
  'session_invalid': 'rpc.error.sessionInvalid',
  'limits_failed': 'rpc.error.limitsFailed',
  'config_failed': 'rpc.error.configFailed',
  'mcp_failed': 'rpc.error.mcpFailed',
  'mode_failed': 'rpc.error.modeFailed',
  'project_failed': 'rpc.error.projectFailed',
  'project_busy': 'rpc.error.projectBusy',
  'project_invalid': 'rpc.error.projectInvalid',
  'command_failed': 'rpc.error.commandFailed',
  'image_read_failed': 'rpc.error.imageReadFailed',
}

/** An fs frame names the op and the file's basename; either may be absent
 * (a malformed path, an old server), so each case has its own fallback. */
function localizeFs(
  frame: { params?: Record<string, string> },
  locale: Locale,
): string {
  const op = frame.params?.op
  const file = frame.params?.file
  if (op === undefined) return translate(locale, 'rpc.error.fsFailedUnknown')
  if (file === undefined || file === '') {
    return translate(locale, 'rpc.error.fsFailedNoFile', { op })
  }
  return translate(locale, 'rpc.error.fsFailed', { op, file })
}

export function localizeError(
  frame: { message?: string; code?: string; params?: Record<string, string> },
  locale: Locale = 'en',
): string {
  if (frame.code === 'fs_error') return localizeFs(frame, locale)
  const key = frame.code !== undefined ? CODE_KEYS[frame.code] : undefined
  if (key === undefined) return frame.message ?? ''
  return translate(locale, key, frame.params)
}
