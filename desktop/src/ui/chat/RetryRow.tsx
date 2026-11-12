/** Retry + run-failure rows: a swallowed provider attempt turned visible.
 *
 * The loop retries a request that failed before its first token, so nothing
 * was ever shown for the dead attempt. Without a row the UI looks frozen for
 * the length of the backoff; with one the person sees the count, the elapsed
 * wait and — when every attempt is spent — the reason, inline where the turn
 * happened instead of as an error popup. */

import { useEffect, useState } from 'react'
import type { FailureNode, RetryNode } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import { ChevronDownIcon, ChevronRightIcon, RefreshIcon } from '../icons'
import css from './RetryRow.module.css'

/** `0.5s`, `3.5s`, `8s`: whole seconds stay bare, halves keep one decimal. */
export function formatWait(ms: number): string {
  const seconds = Math.max(0, ms) / 1000
  return Number.isInteger(seconds) ? `${seconds}s` : `${seconds.toFixed(1)}s`
}

/** The announced backoff so far, plus the slice of the current wait already
 * spent. The loop announces a retry *before* sleeping, so the last delay has
 * to be counted as it elapses rather than added up front. */
function useWaitedMs(node: RetryNode): number {
  const live = node.state === 'retrying'
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!live) return
    const timer = setInterval(() => setNow(Date.now()), 200)
    return () => clearInterval(timer)
  }, [live])
  if (!live) return node.waitedMs
  const spent = Math.min(Math.max(0, now - node.lastAt), node.lastDelayMs)
  return node.waitedMs - node.lastDelayMs + spent
}

function retryLabel(t: ReturnType<typeof useT>, state: RetryNode['state']): string {
  if (state === 'retrying') return t('chat.retrying')
  if (state === 'stopped') return t('chat.retryStopped')
  return t('chat.retried')
}

export function RetryRow({ node }: { node: RetryNode }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const waited = useWaitedMs(node)
  const live = node.state === 'retrying'
  return (
    <div className={css.root} data-retry-state={node.state} data-selectable>
      <button
        type="button"
        className={css.head}
        aria-expanded={open}
        aria-label={open ? t('chat.retryCollapse') : t('chat.retryExpand')}
        data-retry-toggle
        onClick={() => setOpen((value) => !value)}
      >
        <span className={css.glyph} aria-hidden>
          <RefreshIcon size={12} className={live ? css.spin : undefined} />
        </span>
        <span className={css.label}>{retryLabel(t, node.state)}</span>
        {node.maxRetries > 0 && (
          <span className={css.count} data-retry-count>
            ({node.attempts}/{node.maxRetries})
          </span>
        )}
        <span className={css.sep} aria-hidden>
          ·
        </span>
        <span className={css.wait} data-retry-wait>
          {formatWait(waited)}
        </span>
        <span className={css.chevron} aria-hidden>
          {open ? <ChevronDownIcon size={11} /> : <ChevronRightIcon size={11} />}
        </span>
      </button>
      {open && (
        <div className={css.details}>
          {node.errors.length === 0 ? (
            <div className={css.attempt}>
              <span className={css.attemptError}>{t('chat.retryUnknown')}</span>
            </div>
          ) : (
            node.errors.map((error, index) => (
              <div className={css.attempt} key={`${node.id}-${index}`}>
                <span className={css.attemptIndex}>
                  {t('chat.retryAttempt', { n: index + 1 })}
                </span>
                <span className={css.attemptError}>{error}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

/** The run's terminal failure, right under the retry row it belongs to. */
export function FailureRow({ node }: { node: FailureNode }) {
  const t = useT()
  return (
    <div className={css.failure} role="status" data-run-failure>
      <span className={css.dot} aria-hidden />
      <span className={css.failureLabel}>{t('chat.runFailed')}</span>
      <span className={css.failureText}>{node.text}</span>
    </div>
  )
}
