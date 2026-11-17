/** Detaching a session into its own OS window.
 *
 * The pane header drag (or its pop-out button) ends here: build the
 * `?boot=session` URL, create a `session-*` webview through a seam (the real
 * bridge imports the Tauri API; tests and the plain browser install a fake or
 * take the failure path), and tell the other windows so the main window
 * drops the pane it detached. Creation failing (no desktop shell) returns
 * false and the caller keeps the pane. */

import type { Locale } from '../i18n/messages.ts'
import { translate } from '../i18n/messages.ts'
import { useNoticeStore } from './stores.ts'

export interface DetachInfo {
  session: string
  title: string
  project: string | null
  locale: Locale
}

export interface DetachBridge {
  createWindow(options: {
    label: string
    url: string
    title: string
    width: number
    height: number
  }): Promise<void>
  emit(event: string, payload: unknown): Promise<void>
}

let bridge: DetachBridge | null = null

/** Test seam: install a fake window factory. Null restores the Tauri one. */
export function setDetachBridge(next: DetachBridge | null): void {
  bridge = next
}

function realBridge(): DetachBridge {
  return {
    // The plugin command is awaited directly: its rejection (a browser dev
    // bridge, a blocked capability) is the failure path — no reliance on the
    // tauri://created / tauri://error lifecycle events, which need an event
    // emitter the environment may not have.
    async createWindow(options) {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('plugin:webview|create_webview_window', {
        options: {
          label: options.label,
          url: options.url,
          title: options.title,
          width: options.width,
          height: options.height,
          minWidth: 420,
          minHeight: 320,
        },
      })
    },
    async emit(event, payload) {
      const { emit } = await import('@tauri-apps/api/event')
      await emit(event, payload)
    },
  }
}

/** Pure decision for the pane header's dragend: the pointer must have ended
 * outside the shell window and the drag must be ours. */
export function outsideDrop(
  event: { screenX: number; screenY: number; dataTypes: readonly string[] },
  bounds: { x: number; y: number; width: number; height: number },
): boolean {
  if (!event.dataTypes.includes('application/x-zen-session')) return false
  const outsideX =
    event.screenX < bounds.x || event.screenX > bounds.x + bounds.width
  const outsideY =
    event.screenY < bounds.y || event.screenY > bounds.y + bounds.height
  return outsideX || outsideY
}

/** The shell window's screen bounds, when the browser exposes them. A zero
 * size (an exotic webview) never matches "outside", so the drag then simply
 * does nothing — the pop-out button still works. */
export function windowBounds(): { x: number; y: number; width: number; height: number } {
  return {
    x: typeof window === 'undefined' ? 0 : window.screenX,
    y: typeof window === 'undefined' ? 0 : window.screenY,
    width: typeof window === 'undefined' ? 0 : window.outerWidth,
    height: typeof window === 'undefined' ? 0 : window.outerHeight,
  }
}

export function detachedWindowUrl(session: string, project: string | null): string {
  const params = new URLSearchParams({ boot: 'session', session })
  if (project !== null && project !== '') params.set('project', project)
  const base =
    typeof location === 'undefined'
      ? '/'
      : `${location.pathname.replace(/index\.html$/, '')}`
  return `${base}?${params.toString()}`
}

/** Create the detached window and broadcast the detach. False (with a notice
 * in the UI locale) when there is no desktop shell to create it in. */
export async function detachSession(info: DetachInfo): Promise<boolean> {
  const active = bridge ?? realBridge()
  try {
    await active.createWindow({
      label: `session-${Date.now()}`,
      url: detachedWindowUrl(info.session, info.project),
      title: `${info.title} · ZenAgent`,
      width: 760,
      height: 560,
    })
  } catch {
    useNoticeStore.getState().show({
      kind: 'error',
      text: translate(info.locale, 'panes.detachFailed'),
    })
    return false
  }
  try {
    await active.emit('zen:session-detached', { session: info.session })
  } catch {
    // The window exists; a lost broadcast only leaves the pane open.
  }
  return true
}
