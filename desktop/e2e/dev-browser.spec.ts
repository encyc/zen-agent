/** The plain-browser dev path: no Tauri shell and no spec-installed bridge, so
 * `main.tsx` must load the shared mock itself before rendering. */

import { expect, test } from '@playwright/test'

test('the plain browser boots against the dev bridge shim', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveText('修复登录超时')
})
