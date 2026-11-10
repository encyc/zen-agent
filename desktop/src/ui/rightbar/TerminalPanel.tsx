/** One interactive shell in the work panel. The PTY lives in the rpc host:
 * this component forwards keystrokes and resizes, and paints the `terminal`
 * wire events (output, exit). Closing the tab closes the shell. */

import { useEffect, useRef, useState } from 'react'
import { Terminal, type ITheme } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useAppStore } from '../../state/appStore'
import { frameError } from '../../rpc/frames'
import { useT } from '../../i18n/locale'
import css from './TerminalPanel.module.css'

type Status = 'connecting' | 'ready' | 'exited' | 'failed'

function readTheme(): ITheme {
  const style = getComputedStyle(document.body)
  const read = (name: string, fallback: string) => {
    const value = style.getPropertyValue(name).trim()
    return value === '' ? fallback : value
  }
  return {
    background: read('--dsw-alias-bg-base', '#1f1f1f'),
    foreground: read('--dsw-alias-label-primary', '#cccccc'),
    cursor: read('--ds-accent', '#0078d4'),
    selectionBackground: read('--dsw-alias-bg-mask-2', 'rgba(255,255,255,0.2)'),
  }
}

function readFontFamily(): string {
  const value = getComputedStyle(document.body).getPropertyValue('--ds-font-family-code').trim()
  return value === '' ? 'Menlo, monospace' : value
}

export function TerminalPanel(props: { active: boolean }) {
  const t = useT()
  const root = useAppStore((state) => state.project) ?? ''
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [error, setError] = useState('')
  const [exitCode, setExitCode] = useState<number | null>(null)
  const [epoch, setEpoch] = useState(0)

  useEffect(() => {
    const host = hostRef.current
    if (host === null) return
    const client = useAppStore.getState().client()
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: readFontFamily(),
      fontSize: 12.5,
      lineHeight: 1.25,
      scrollback: 5000,
      theme: readTheme(),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    termRef.current = term
    fitRef.current = fit
    setStatus('connecting')
    setError('')

    // A replaced shell's late frames carry the old token; only this mount's
    // output may reach this pane.
    const session = `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
    let live = true
    const off = client.onFrame((frame) => {
      if (frame.type !== 'event' || frame.event?.type !== 'terminal') return
      if (frame.event.session !== session) return
      const kind = frame.event.kind
      if (kind === 'output' && typeof frame.event.data === 'string') {
        term.write(frame.event.data)
        return
      }
      if (kind === 'exit') {
        setExitCode(typeof frame.event.code === 'number' ? frame.event.code : null)
        setStatus('exited')
      }
    })

    const opened = term.onResize(({ cols, rows }) => {
      if (!live) return
      void client
        .send({ type: 'terminal', op: 'resize', session, cols, rows })
        .catch(() => {})
    })
    const typed = term.onData((data) => {
      if (!live) return
      void client.send({ type: 'terminal', op: 'write', session, data }).catch(() => {})
    })
    // Hiding the pane for another tab gives the host a zero box; fitting
    // there would divide by zero, so only visible geometry may resize.
    const fitNow = () => {
      if (host.clientWidth === 0 || host.clientHeight === 0) return
      fit.fit()
    }
    const observer = new ResizeObserver(fitNow)
    observer.observe(host)

    requestAnimationFrame(() => {
      if (!live) return
      fitNow()
      void client
        .send<Record<string, unknown>>({
          type: 'terminal',
          op: 'start',
          session,
          cwd: root === '' ? null : root,
          cols: term.cols,
          rows: term.rows,
        })
        .then((frame) => {
          if (!live) return
          const failure = frameError(frame)
          if (failure !== null) {
            setError(failure)
            setStatus('failed')
            return
          }
          setStatus('ready')
        })
        .catch((reason: unknown) => {
          if (!live) return
          setError(String(reason))
          setStatus('failed')
        })
    })

    return () => {
      live = false
      observer.disconnect()
      opened.dispose()
      typed.dispose()
      off()
      void client.send({ type: 'terminal', op: 'close', session }).catch(() => {})
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [root, epoch])

  // The palette lives in body attributes written by App's theme presenter,
  // after this component's effects run: observe the attributes instead of the
  // settings store, so mode, palette and system flips all repaint alike.
  useEffect(() => {
    const apply = () => {
      if (termRef.current !== null) termRef.current.options.theme = readTheme()
    }
    apply()
    const observer = new MutationObserver(apply)
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-ds-dark-theme', 'data-ds-theme'],
    })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!props.active) return
    const frame = requestAnimationFrame(() => {
      const host = hostRef.current
      if (host !== null && host.clientWidth > 0 && host.clientHeight > 0) {
        fitRef.current?.fit()
      }
      termRef.current?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [props.active])

  return (
    <div className={css.pane} data-terminal-pane>
      <div ref={hostRef} className={css.host} data-terminal-host />
      {status === 'exited' || status === 'failed' ? (
        <div className={css.overlay} data-terminal-overlay={status}>
          <p className={css.message}>
            {status === 'failed'
              ? error
              : t('terminal.exited', { code: exitCode === null ? '?' : String(exitCode) })}
          </p>
          <button
            type="button"
            className={css.restart}
            onClick={() => setEpoch((value) => value + 1)}
          >
            {t('terminal.restart')}
          </button>
        </div>
      ) : null}
    </div>
  )
}
