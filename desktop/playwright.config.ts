import { defineConfig } from '@playwright/test'

// Parallel worktrees each run their own dev server; ZEN_E2E_PORT keeps this
// run off a sibling checkout's server (reuseExistingServer would otherwise
// silently test the wrong code).
const port = Number(process.env.ZEN_E2E_PORT ?? 5173)
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    // Local runs may use an installed Chrome instead of downloading Chromium.
    channel: process.env.PLAYWRIGHT_CHANNEL,
  },
  webServer: {
    command: `pnpm dev --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
