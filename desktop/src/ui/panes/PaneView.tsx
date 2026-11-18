/** One session pane: header (drag handle, running dot, pop-out, stop, close),
 * the shared transcript view over the pane's own chat store, and the slim
 * composer that addresses this pane's session. Clicking a pane focuses it —
 * the resume the sidebar click does — so the topbar, sidebar and status poll
 * follow the pane the person is reading. */

import { useLayoutEffect, useRef, useState, type DragEvent, type RefObject } from 'react'
import { useAppStore } from '../../state/appStore'
import { usePanesStore, type SessionPane } from '../../state/sessionPanes'
import { detachSession, outsideDrop, windowBounds } from '../../state/detachWindow'
import { useSessionStore, useSettingsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { ChatView } from '../chat/ChatView'
import { NoticeBanner } from '../NoticeBanner'
import { InputBar } from '../composer/InputBar'
import { StopIcon, XIcon } from '../icons'
import { Tooltip } from '../primitives'
import { PaneComposer } from './PaneComposer'
import css from './panes.module.css'

export function PaneView({ pane }: { pane: SessionPane }) {
  const t = useT()
  const isCurrent = useSessionStore((state) => state.currentPath === pane.path)
  const locale = useSettingsStore((state) => state.locale)
  const [dragging, setDragging] = useState(false)
  const panes = usePanesStore.getState()
  const bodyRef = useRef<HTMLDivElement | null>(null)

  const focus = () => {
    void usePanesStore.getState().focus(pane.path)
  }
  const close = () => usePanesStore.getState().close(pane.path)
  const stop = () => usePanesStore.getState().cancel(pane.path)

  const onDragStart = (event: DragEvent<HTMLDivElement>) => {
    event.dataTransfer.setData('application/x-zen-session', pane.path)
    event.dataTransfer.effectAllowed = 'move'
    setDragging(true)
  }
  // A header drag that ends outside the shell window pops the session out
  // into its own window; the desktop shell creates it and this window drops
  // the pane. In a plain browser the creation fails and the notice explains.
  const onDragEnd = (event: DragEvent<HTMLDivElement>) => {
    setDragging(false)
    if (
      !outsideDrop(
        { screenX: event.screenX, screenY: event.screenY, dataTypes: [...event.dataTransfer.types] },
        windowBounds(),
      ) ||
      !event.dataTransfer.types.includes('application/x-zen-session')
    ) {
      return
    }
    void detachSession({
      session: pane.path,
      title: pane.title,
      project: useAppStore.getState().project,
      locale,
    }).then((opened) => {
      if (opened) close()
    })
  }
  const popOut = () => {
    void detachSession({
      session: pane.path,
      title: pane.title,
      project: useAppStore.getState().project,
      locale,
    }).then((opened) => {
      if (opened) close()
    })
  }

  return (
    <section
      className={css.pane}
      data-pane={pane.path}
      data-pane-active={isCurrent || undefined}
      data-pane-dragging={dragging || undefined}
      onPointerDown={(event) => {
        // Clicking a header action (stop/pop-out/close) must not focus first.
        if (!isCurrent && !(event.target instanceof Element && event.target.closest('button'))) {
          focus()
        }
      }}
    >
      <header
        className={css.header}
        data-pane-header
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        title={t('panes.popOut')}
      >
        <button
          type="button"
          className={css.title}
          onClick={focus}
          title={pane.title}
        >
          {pane.title}
        </button>
        {pane.busy && (
          <span className={css.running} data-pane-running title={t('session.running')} />
        )}
        {!pane.live && pane.loaded && (
          <span className={css.staticBadge} data-pane-static>
            {t('panes.staticHint')}
          </span>
        )}
        {pane.busy && (
          <Tooltip label={t('panes.stop')}>
            <button
              type="button"
              className={css.action}
              data-pane-stop
              aria-label={t('panes.stop')}
              onClick={stop}
            >
              <StopIcon size={12} />
            </button>
          </Tooltip>
        )}
        <Tooltip label={t('panes.popOut')}>
          <button
            type="button"
            className={css.action}
            data-pane-popout
            aria-label={t('panes.popOut')}
            onClick={popOut}
          >
            <PopOutGlyph />
          </button>
        </Tooltip>
        <Tooltip label={t('panes.close')}>
          <button
            type="button"
            className={css.action}
            data-pane-close
            aria-label={t('panes.close')}
            onClick={close}
          >
            <XIcon size={12} />
          </button>
        </Tooltip>
      </header>
      <div className={css.body} ref={bodyRef}>
        <ChatView
          chat={pane.chat}
          busy={pane.busy}
          onLoadOlder={() => panes.loadOlder(pane.path)}
          onRetry={() => void panes.runCommand(pane.path, 'retry', '')}
          emptyState={<div className={css.empty}>{t('panes.empty')}</div>}
        />
        {!pane.loaded && (
          <div className={css.notResident} data-pane-not-resident>
            <span>{t('panes.notResident')}</span>
            <button type="button" className={css.loadButton} data-pane-load onClick={focus}>
              {t('panes.load')}
            </button>
          </div>
        )}
        {isCurrent ? (
          // The focused pane gets the real composer — approval/ask cards,
          // images, mentions, model picker — with its measured height feeding
          // this pane's transcript reserve. Unfocused panes keep the slim
          // per-session composer and reserve nothing.
          <PaneDock bodyRef={bodyRef} />
        ) : (
          <PaneComposer pane={pane} />
        )}
      </div>
    </section>
  )
}

/** The full composer dock for the focused pane: the same NoticeBanner and
 * InputBar the single view floats, in flow at the pane's bottom. Its height
 * is published as this pane's `--composer-dock-height` (overriding the pane
 * default of 0) so the transcript reserves exactly the dock and fades out
 * behind it — the same contract ComposerDock serves for the single view. */
function PaneDock({ bodyRef }: { bodyRef: RefObject<HTMLDivElement | null> }) {
  const ref = useRef<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    const dock = ref.current
    const body = bodyRef.current
    if (dock === null || body === null) return
    const publish = () => {
      const height = Math.round(dock.getBoundingClientRect().height)
      if (height > 0) {
        body.style.setProperty('--composer-dock-height', `${height}px`)
      }
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(dock)
    return () => {
      observer.disconnect()
      body.style.removeProperty('--composer-dock-height')
    }
  }, [bodyRef])

  return (
    <div ref={ref} className={css.paneDock} data-pane-dock>
      <NoticeBanner />
      <InputBar />
    </div>
  )
}

/** The pop-out glyph: one window leaving another. Drawn inline so the icon
 * sheet stays untouched for one glyph. */
function PopOutGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
      <rect x="1" y="3" width="7" height="8" rx="1.2" stroke="currentColor" strokeWidth="1.1" />
      <path d="M4.5 3V2a1 1 0 0 1 1-1H10a1 1 0 0 1 1 1v5.5a1 1 0 0 1-1 1H9.5" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  )
}
