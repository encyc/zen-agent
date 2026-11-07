/** Visual harness for the Taiji panel: renders the real component with a
 * rich mocked snapshot and screenshots it for design critique. Keeps no
 * baseline; the PNG lands in test-results/ (git-ignored). */

import { expect, test } from '@playwright/test'

import { boot, initialState } from './harness'

const now = () => Math.floor(Date.now() / 1000)

const richTaiji = {
  graph: {
    root: '/work/p',
    is_repo: true,
    commits: [
      { sha: 'm5', short: 'm5abc12', parents: ['m4', 'e1'], subject: 'taiji: merge task/explore', author: 'justin', ts: now() - 600, lane: 0 },
      { sha: 'e1', short: 'e1abc12', parents: ['m2'], subject: 'wip: 独立 job 探索', author: 'worker', ts: now() - 900, lane: 1 },
      { sha: 'm4', short: 'm4abc12', parents: ['m3'], subject: 'perf: shared httpx client and off-loop file IO', author: 'justin', ts: now() - 3600, lane: 0 },
      { sha: 't2', short: 't2abc12', parents: ['m2'], subject: 'draft: auth e2e 用例骨架', author: 'worker', ts: now() - 240, lane: 1 },
      { sha: 'm3', short: 'm3abc12', parents: ['m2'], subject: 'chore: LOC budget gate with debt allowlist', author: 'justin', ts: now() - 7200, lane: 0 },
      { sha: 't1', short: 't1abc12', parents: ['m1'], subject: 'repro: 登录超时复现脚本', author: 'worker', ts: now() - 9000, lane: 1 },
      { sha: 'm2', short: 'm2abc12', parents: ['m1'], subject: 'refactor(rpc): explicit kind discriminator', author: 'justin', ts: now() - 12000, lane: 0 },
      { sha: 'm1', short: 'm1abc12', parents: [], subject: 'init', author: 'justin', ts: now() - 86400 * 3, lane: 0 },
    ],
    branches: [
      { name: 'main', sha: 'm5', is_head: true },
      { name: 'task/e2e-auth', sha: 't2', is_head: false },
      { name: 'task/explore', sha: 'e1', is_head: false },
    ],
    max_lane: 1,
  },
  sessions: {
    repo_root: '/work/p',
    projects: [
      {
        key: 'p-main',
        root: '/work/p',
        branch: 'main',
        worktree: false,
        sessions: [
          { name: 'router', title: 'architecture-cleanup', updated_at: now() - 60, messages: 42 },
        ],
      },
      {
        key: 'p-e2e',
        root: '/work/p-e2e',
        branch: 'task/e2e-auth',
        worktree: true,
        sessions: [
          {
            name: 'w-e2e',
            title: 'draft: auth e2e 用例骨架',
            updated_at: now() - 240,
            messages: 18,
            worker: 'e2e-auth',
            status: 'running',
            branch: 'task/e2e-auth',
          },
          {
            name: 'w-e2e-old',
            title: 'repro: 登录超时复现脚本',
            updated_at: now() - 9000,
            messages: 6,
            worker: 'e2e-old',
            status: 'error',
            branch: 'task/e2e-auth',
          },
        ],
      },
      {
        key: 'p-explore',
        root: '/work/p-explore',
        branch: 'task/explore',
        worktree: true,
        sessions: [
          {
            name: 'w-explore',
            title: 'wip: 独立 job 探索',
            updated_at: now() - 900,
            messages: 7,
            worker: 'explore',
            status: 'ok',
            branch: 'task/explore',
          },
        ],
      },
    ],
  },
}

test('taiji panel visual state', async ({ page }) => {
  await boot(page, initialState({ taiji: richTaiji, statusSession: '/home/u/projects/p-main/sessions/router.jsonl' }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '太极' }).click()
  await expect(panel.getByRole('tab', { name: '太极' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('运行中')
  await page.waitForTimeout(250)
  await panel.screenshot({ path: 'test-results/taiji-panel.png' })
})
