/** Shared formatting helpers for transcript rows and the session list. */

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60000)
  return `${minutes}m${Math.round((ms % 60000) / 1000)}s`
}

/** Epoch seconds → a relative label ("3m ago", "昨天"), empty when unknown. */
export function formatAge(epochSeconds: number | undefined, locale: 'zh' | 'en'): string {
  if (!epochSeconds) return ''
  const delta = epochSeconds * 1000 - Date.now()
  const abs = Math.abs(delta)
  const tag = locale === 'zh' ? 'zh-CN' : 'en'
  const relative = new Intl.RelativeTimeFormat(tag, { numeric: 'auto' })
  if (abs < 60_000) return relative.format(Math.round(delta / 1000), 'second')
  if (abs < 3_600_000) return relative.format(Math.round(delta / 60_000), 'minute')
  if (abs < 86_400_000) return relative.format(Math.round(delta / 3_600_000), 'hour')
  if (abs < 30 * 86_400_000) return relative.format(Math.round(delta / 86_400_000), 'day')
  return new Date(epochSeconds * 1000).toLocaleDateString(tag === 'en' ? 'en' : 'zh-CN')
}
