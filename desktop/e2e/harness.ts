/** Shared e2e harness: boot plus the scripted Tauri bridge. Lives outside any
 * *.spec.ts so specs can import it (Playwright forbids spec-to-spec imports).
 *
 * The bridge itself lives in `src/dev/bridge.ts` because `src/main.tsx` installs
 * the same mock when the app runs in a plain browser; this file adds only the
 * Playwright-only navigation helper. */

import { expect, type Page } from '@playwright/test'
import { type BridgeState } from '../src/dev/bridge'

export { initialState } from '../src/dev/bridge'
export type { BridgeState, MemoryEntryMock } from '../src/dev/bridge'

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
