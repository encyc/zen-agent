/** Shared e2e harness: boot plus the scripted Tauri bridge. Lives outside any
 * *.spec.ts so specs can import it (Playwright forbids spec-to-spec imports).
 *
 * The bridge itself lives in `src/dev/bridge.ts` because `src/main.tsx` installs
 * the same mock when the app runs in a plain browser; this file adds only the
 * Playwright-only navigation helper. */

import { expect, type Page } from '@playwright/test'
import { type BridgeState, type PendingItemMock } from '../src/dev/bridge'

export { initialState } from '../src/dev/bridge'
export type { BridgeState, MemoryEntryMock, PendingItemMock } from '../src/dev/bridge'

/** Hand the page its scripted state before the app boots; the dev bootstrap
 * in `main.tsx` then installs the module-resolved bridge itself, so the
 * bridge may keep ordinary module imports. */
export async function presetState(page: Page, state: BridgeState) {
  await page.addInitScript((preset) => {
    window.__ZEN_E2E__ = preset
  }, state)
}

export async function boot(page: Page, state: BridgeState) {
  await presetState(page, state)
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)
}

/** Push an app event through the scripted bridge, as a real backend frame
 * would; specs use it to fake turns, notices and other async flow. */
export async function emitEvent(page: Page, event: Record<string, unknown>) {
  await page.evaluate((payload) => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: { frame: { type: 'event', event: payload } },
    })
  }, event)
}

/** Replace the queued-message lane contents before boot. */
export async function setPending(page: Page, items: PendingItemMock[]) {
  await page.evaluate((pending) => {
    window.__ZEN_E2E__.pending = pending
  }, items)
}

/** Platform-true modifiers: ⌘ on macOS, Ctrl elsewhere. */
export const paletteKey = process.platform === 'darwin' ? 'Meta+k' : 'Control+k'
export const sidebarKey = process.platform === 'darwin' ? 'Meta+b' : 'Control+b'
