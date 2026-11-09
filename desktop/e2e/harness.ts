/** Shared e2e harness: boot plus the scripted Tauri bridge. Lives outside any
 * *.spec.ts so specs can import it (Playwright forbids spec-to-spec imports).
 *
 * The bridge itself lives in `src/dev/bridge.ts` because `src/main.tsx` installs
 * the same mock when the app runs in a plain browser; this file adds only the
 * Playwright-only navigation helper. */

import { expect, type Page } from '@playwright/test'
import { installBridge, initialState, type BridgeState } from '../src/dev/bridge'

export { installBridge, initialState }
export type { BridgeState, MemoryEntryMock } from '../src/dev/bridge'

export async function boot(page: Page, state: BridgeState) {
  await page.addInitScript(installBridge, state)
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)
}
