/** Hover action row under a transcript turn (copy, regenerate) — the quiet
 * icon chips pi-desktop/Codex put beneath a message. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useT } from '../../i18n/locale'
import { copyText } from '../clipboard'
import { CheckIcon, CopyIcon, RefreshIcon } from '../icons'
import { Tooltip } from '../primitives'
import css from './TurnActions.module.css'

function useCopy() {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = useCallback((text: string) => {
    void copyText(text).then((ok) => {
      if (!ok) return
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1500)
    })
  }, [])
  return { copied, copy }
}

export function CopyAction({ text }: { text: string }) {
  const t = useT()
  const { copied, copy } = useCopy()
  const label = copied ? t('chat.copied') : t('chat.copy')
  return (
    <Tooltip label={label}>
      <button
        type="button"
        className={css.action}
        aria-label={label}
        onClick={() => copy(text)}
      >
        {copied ? <CheckIcon size={12} /> : <CopyIcon size={12} />}
      </button>
    </Tooltip>
  )
}

function RetryAction({ onRetry }: { onRetry(): void }) {
  const t = useT()
  return (
    <Tooltip label={t('chat.regenerate')}>
      <button
        type="button"
        className={css.action}
        aria-label={t('chat.regenerate')}
        onClick={onRetry}
      >
        <RefreshIcon size={12} />
      </button>
    </Tooltip>
  )
}

export function TurnActions({
  text,
  canRetry = false,
  onRetry,
  align = 'start',
}: {
  text: string
  canRetry?: boolean
  onRetry?: () => void
  align?: 'start' | 'end'
}) {
  if (text === '' && !canRetry) return null
  return (
    <div className={css.actions} data-align={align}>
      {text !== '' && <CopyAction text={text} />}
      {canRetry && onRetry !== undefined && <RetryAction onRetry={onRetry} />}
    </div>
  )
}
