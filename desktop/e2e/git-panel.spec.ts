/** The git review rail: grouped changes with tree/list views,
 * untracked and staged diffs, binary placeholders, the lane-drawn
 * commit graph, the branch/worktree list, worktree previews,
 * fullscreen behavior and graph paging. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness.ts'

test('the git panel groups changes and serves untracked and staged diffs', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      gitChanges: {
        is_repo: true,
        branch: 'main',
        files: [
          { path: 'src/staged.ts', state: 'staged', plus: 3, minus: 1 },
          { path: 'src/dirty.ts', state: 'unstaged', plus: 1, minus: 0 },
          { path: 'src/new.ts', state: 'untracked', plus: null, minus: null },
        ],
        untracked_more: 2,
      },
      gitDiff: {
        text: '@@ -0,0 +1,2 @@\n+fresh\n+lines\n',
        binary: false,
        truncated: false,
        missing: false,
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await expect(panel.getByText('已暂存', { exact: true })).toBeVisible()
  await expect(panel.getByText('更改', { exact: true })).toBeVisible()
  await expect(panel.getByText('未跟踪', { exact: true })).toBeVisible()
  await expect(panel.getByText('还有 2 个未跟踪文件未列出')).toBeVisible()

  // The untracked row opens the all-additions diff (narrow rail: list→detail).
  await panel.getByRole('button', { name: /src\/new\.ts/ }).click()
  await expect(panel).toContainText('@@ -0,0 +1,2 @@')
  await expect(panel.locator('[data-kind="add"]').first()).toContainText('fresh')
  await page.getByRole('button', { name: '返回变更列表' }).click()

  // The staged row asks the backend for the --cached variant.
  await panel.getByRole('button', { name: /src\/staged\.ts/ }).click()
  await expect(panel).toContainText('@@ -0,0 +1,2 @@')
  const stagedAsk = await page.evaluate(
    () =>
      (window as unknown as { __ZEN_E2E__: { calls: { type: string; path?: string; staged?: boolean }[] } })
        .__ZEN_E2E__.calls.find((call) => call.type === 'git_diff' && call.path === 'src/staged.ts'),
  )
  assert.equal(stagedAsk?.staged, true)
})
test('binary files show a placeholder instead of diff bytes', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      gitDiff: { text: '', binary: true, truncated: false, missing: false },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await panel.getByRole('button', { name: /git\.py/ }).click()
  await expect(panel.getByText('二进制文件，无法显示文本差异')).toBeVisible()
})
test('fullscreen expands the rail and idle escape restores the layout', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await expect(page.locator('[data-shell-center]')).toBeVisible()
  await expect(page.locator('[data-shell-sidebar]')).toBeVisible()

  await panel.getByRole('button', { name: '全屏' }).click()
  await expect(page.locator('[data-shell-center]')).toHaveCount(0)
  // True fullscreen: the app sidebar gives its column to the rail too.
  await expect(page.locator('[data-shell-sidebar]')).toHaveCount(0)
  // Wide span: list and detail live side by side, so there is no back button.
  await panel.getByRole('button', { name: /git\.py/ }).click()
  await expect(panel).toContainText('@@ -1,2 +1,3 @@')
  await expect(panel.getByRole('button', { name: '返回变更列表' })).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(page.locator('[data-shell-center]')).toBeVisible()
  await expect(page.locator('[data-shell-sidebar]')).toBeVisible()
})
test('a busy run keeps the cancel binding over fullscreen escape', async ({ page }) => {
  await boot(page, initialState({ running: true }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await panel.getByRole('button', { name: '全屏' }).click()
  await expect(page.locator('[data-shell-center]')).toHaveCount(0)

  await page.keyboard.press('Escape')
  const cancelCount = await page.evaluate(
    () =>
      (window as unknown as { __ZEN_E2E__: { calls: { type: string }[] } }).__ZEN_E2E__.calls
        .filter((call) => call.type === 'cancel').length,
  )
  assert.equal(cancelCount, 1)
  // The cancel took the Escape; fullscreen survives.
  await expect(page.locator('[data-shell-center]')).toHaveCount(0)
})
test('the graph renders lanes, refs and opens a commit diff', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  // Default bridge git_log: merge head carrying the `main` ref badge.
  await expect(panel.getByText('Graph', { exact: true })).toBeVisible()
  await expect(panel.getByText('merge feature')).toBeVisible()
  await expect(panel.locator('[data-ref="main"]').first()).toBeVisible()
  await expect(panel.getByText('initial')).toBeVisible()

  await panel.getByRole('button', { name: /merge feature/ }).click()
  await expect(panel.getByRole('button', { name: 'src/changed.ts' })).toBeVisible()
  await expect(panel.locator('[data-kind="add"]').first()).toContainText('new')
  await expect(panel.getByText('1 个文件')).toBeVisible()
})
test('the branch list is newest-first, collapsible and height-capped', async ({ page }) => {
  const branch = (name: string, timestamp: number, current = false) => ({
    path: `/work/p-${name.replace(/\//g, '-')}`,
    head: `${name.replace(/[^a-z]/g, '')}000000`,
    branch: name,
    timestamp,
    bare: false,
    detached: false,
    current,
  })
  await boot(
    page,
    initialState({
      running: false,
      worktrees: {
        root: '/work/p',
        is_repo: true,
        is_worktree: false,
        main: '/work/p-main',
        branch: 'main',
        worktrees: [
          branch('stale/topic', 1700000000),
          branch('main', 1763400000, true),
          branch('fresh/topic', 1763500000),
          branch('mid/topic', 1750000000),
          ...Array.from({ length: 12 }, (_, index) =>
            branch(`filler/${index}`, 1710000000 + index),
          ),
        ],
        status: { branch: 'main', staged: 0, dirty: 0, untracked: 0, clean: true },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()

  const list = panel.locator('[data-worktrees]')
  await expect(list).toBeVisible()
  // The newest tip heads the list; the stale one sinks below the fillers.
  await expect(list.locator('[data-worktree]').first()).toHaveAttribute(
    'data-worktree',
    'fresh/topic',
  )
  const order = await list.locator('[data-worktree]').evaluateAll((rows) =>
    rows.map((row) => row.getAttribute('data-worktree')),
  )
  assert.equal(order[0], 'fresh/topic')
  assert.equal(order[1], 'main')
  assert.ok(order.indexOf('stale/topic') > order.indexOf('filler/11'))

  // The height cap keeps a many-worktree checkout from pushing Changes away.
  const heights = await list.evaluate((element) => ({
    client: element.clientHeight,
    scroll: element.scrollHeight,
  }))
  assert.ok(heights.client <= 170, `client height ${heights.client}`)
  assert.ok(heights.scroll > heights.client, 'the list should scroll, not grow')

  const toggle = panel.getByRole('button', { name: '折叠分支列表' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()
  await expect(panel.locator('[data-worktrees]')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '展开分支列表' })).toHaveAttribute(
    'aria-expanded',
    'false',
  )
  await panel.getByRole('button', { name: '展开分支列表' }).click()
  await expect(list).toBeVisible()
})
test('a worktree row previews its changes without switching the project', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      worktrees: {
        root: '/work/p',
        is_repo: true,
        is_worktree: false,
        main: '/work/p',
        branch: 'main',
        worktrees: [
          {
            path: '/work/p',
            head: 'abc12345',
            branch: 'main',
            timestamp: 1763200000,
            bare: false,
            detached: false,
            current: true,
          },
          {
            path: '/work/p-feature',
            head: 'def67890',
            branch: 'feature',
            timestamp: 1763000000,
            bare: false,
            detached: false,
            current: false,
          },
        ],
        status: { branch: 'main', staged: 0, dirty: 0, untracked: 0, clean: true },
      },
      gitChangesByWorktree: {
        '/work/p-feature': {
          is_repo: true,
          branch: 'feature',
          files: [{ path: 'feature-only.ts', state: 'unstaged', plus: 2, minus: 0 }],
          untracked_more: 0,
        },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()

  const feature = panel.locator('[data-worktree="feature"]')
  await expect(feature.getByRole('button', { name: '打开', exact: true })).toHaveCount(0)
  await feature.getByRole('button', { name: /feature/ }).click()
  await expect(panel.getByText('正在预览 feature 的更改')).toBeVisible()
  await expect(panel.getByText('feature-only.ts')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'zenagent/app/git.py' })).toHaveCount(0)

  // The preview is a read request against that checkout; no project switch.
  const asked = await page.evaluate(
    () =>
      (window as unknown as { __ZEN_E2E__: { calls: { type: string; worktree?: string }[] } })
        .__ZEN_E2E__.calls.filter((call) => call.type === 'git_changes' && call.worktree),
  )
  assert.deepEqual(
    asked.map((call) => call.worktree),
    ['/work/p-feature'],
  )
  const switches = await page.evaluate(
    () => (window as unknown as { __ZEN_E2E__: { projectOps?: unknown[] } }).__ZEN_E2E__.projectOps ?? [],
  )
  assert.equal(switches.length, 0)

  // The scoped diff rides the same scope, and the Files shortcut is hidden.
  await panel.getByRole('button', { name: /feature-only\.ts/ }).click()
  await expect(panel).toContainText('@@ -1,2 +1,3 @@')
  await expect(panel.getByText('在文件面板打开')).toHaveCount(0)
  const scopedDiff = await page.evaluate(
    () =>
      (window as unknown as { __ZEN_E2E__: { calls: { type: string; worktree?: string }[] } })
        .__ZEN_E2E__.calls.find((call) => call.type === 'git_diff'),
  )
  assert.equal(scopedDiff?.worktree, '/work/p-feature')

  // The narrow rail swaps the whole list for the detail; go back to reach the
  // preview banner and leave the scope.
  await panel.getByRole('button', { name: '返回变更列表' }).click()
  await panel.getByRole('button', { name: '返回当前工作树' }).click()
  await expect(panel.getByText('正在预览 feature 的更改')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'zenagent/app/git.py' })).toBeVisible()
})
test('changes switch between tree and list, with independent folder folding', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      gitChanges: {
        is_repo: true,
        branch: 'main',
        files: [
          { path: 'staged/one.ts', state: 'staged', plus: 2, minus: 1 },
          { path: 'src/ui/a.ts', state: 'unstaged', plus: 1, minus: 0 },
          { path: 'src/ui/b.ts', state: 'unstaged', plus: 3, minus: 0 },
          { path: 'src/untracked.ts', state: 'untracked', plus: null, minus: null },
          { path: 'docs/readme.md', state: 'untracked', plus: null, minus: null },
        ],
        untracked_more: 0,
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()

  const toolbar = panel.locator('[data-git-changes-toolbar]')
  const unstaged = panel.locator('[data-group="unstaged"]')
  const untracked = panel.locator('[data-group="untracked"]')
  // Tree is the default: folders are rows, files carry their basename.
  await expect(toolbar.getByRole('button', { name: '树形视图' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await expect(unstaged.locator('[data-tree-dir="src"]')).toBeVisible()
  await expect(untracked.locator('[data-tree-dir="src"]')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toBeVisible()

  // The same folder path folds independently in each group.
  await unstaged.locator('[data-tree-dir="src"]').click()
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toHaveCount(0)
  await expect(untracked.getByRole('button', { name: 'src/untracked.ts' })).toBeVisible()
  await unstaged.locator('[data-tree-dir="src"]').click()
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toBeVisible()

  // Group headers fold too: folding Changes leaves Untracked untouched.
  const changesHeader = unstaged.locator('[data-group-toggle]')
  await expect(changesHeader).toHaveAttribute('aria-expanded', 'true')
  await changesHeader.click()
  await expect(changesHeader).toHaveAttribute('aria-expanded', 'false')
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toHaveCount(0)
  await expect(untracked.getByRole('button', { name: 'src/untracked.ts' })).toBeVisible()
  await changesHeader.click()
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toBeVisible()

  await toolbar.getByRole('button', { name: '列表视图' }).click()
  await expect(panel.locator('[data-tree-dir="src"]')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'src/ui/a.ts' })).toBeVisible()
  await expect(panel.getByText('src/ui/a.ts', { exact: true })).toBeVisible()

  await toolbar.getByRole('button', { name: '树形视图' }).click()
  await expect(unstaged.locator('[data-tree-dir="src"]')).toBeVisible()
})
test('the graph toggles between lanes and a flat list', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()

  const graph = panel.locator('[data-git-graph]')
  await expect(graph).toHaveAttribute('data-view', 'graph')
  await expect(graph.locator('[data-lane-graph]').first()).toBeVisible()
  await graph.getByRole('button', { name: '列表视图' }).click()
  await expect(graph).toHaveAttribute('data-view', 'list')
  await expect(graph.locator('[data-lane-graph]')).toHaveCount(0)
  await expect(graph.getByText('merge feature')).toBeVisible()

  await graph.getByRole('button', { name: '图形视图' }).click()
  await expect(graph).toHaveAttribute('data-view', 'graph')
  await expect(graph.locator('[data-lane-graph]').first()).toBeVisible()

  // The section folds to its header, like the worktree list.
  const toggle = graph.locator('[data-graph-toggle]')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()
  await expect(graph).toHaveAttribute('data-collapsed', 'true')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(graph.locator('[data-lane-graph]').first()).toBeHidden()
  await expect(graph.getByText('merge feature')).toBeHidden()
  await toggle.click()
  await expect(graph).not.toHaveAttribute('data-collapsed', 'true')
  await expect(graph.locator('[data-lane-graph]').first()).toBeVisible()
})
test('a commit file list switches between tree and list', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      gitShow: {
        found: true,
        short: 'ccccccc',
        subject: 'merge feature',
        files: [
          { path: 'src/a.ts', additions: 1, removals: 0, diff: '@@ -0,0 +1 @@\n+a\n' },
          { path: 'src/deep/b.ts', additions: 1, removals: 0, diff: '@@ -0,0 +1 @@\n+b\n' },
          { path: 'docs/c.md', additions: 1, removals: 0, diff: '@@ -0,0 +1 @@\n+c\n' },
        ],
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await panel.getByRole('button', { name: /merge feature/ }).click()

  const toolbar = panel.locator('[data-git-commit-toolbar]')
  await expect(toolbar.getByRole('button', { name: '树形视图' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await expect(toolbar.getByText('3 个文件')).toBeVisible()
  await expect(panel.locator('[data-tree-dir="src"]')).toBeVisible()
  await expect(panel.locator('[data-tree-dir="docs"]')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'src/a.ts' })).toBeVisible()

  await toolbar.getByRole('button', { name: '列表视图' }).click()
  await expect(panel.locator('[data-tree-dir="src"]')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'src/a.ts' })).toBeVisible()
  await expect(panel.getByText('src/a.ts', { exact: true })).toBeVisible()

  await toolbar.getByRole('button', { name: '树形视图' }).click()
  await panel.locator('[data-tree-dir="src"]').click()
  await expect(panel.getByRole('button', { name: 'src/a.ts' })).toHaveCount(0)
})
test('fullscreen reserves the macOS traffic-light corner', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await panel.getByRole('button', { name: '全屏' }).click()

  const header = panel.locator('[data-work-panel-header]')
  await page.evaluate(() => {
    document.documentElement.dataset.platform = 'linux'
  })
  assert.equal(
    await header.evaluate((element) => getComputedStyle(element).paddingLeft),
    '10px',
  )
  await page.evaluate(() => {
    document.documentElement.dataset.platform = 'darwin'
  })
  assert.equal(
    await header.evaluate((element) => getComputedStyle(element).paddingLeft),
    '78px',
  )
})
test('a commit detail folds its files one at a time', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      gitShow: {
        found: true,
        short: 'ccccccc',
        subject: 'merge feature',
        files: [
          {
            path: 'src/alpha.ts',
            additions: 1,
            removals: 0,
            diff: '@@ -0,0 +1 @@\n+alpha-content\n',
          },
          {
            path: 'src/beta.ts',
            additions: 1,
            removals: 0,
            diff: '@@ -0,0 +1 @@\n+beta-content\n',
          },
          {
            path: 'src/gamma.ts',
            additions: 1,
            removals: 0,
            diff: '@@ -0,0 +1 @@\n+gamma-content\n',
          },
        ],
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await panel.getByRole('button', { name: /merge feature/ }).click()

  // The first file opens; the other two stay folded.
  await expect(panel.getByText('+alpha-content')).toBeVisible()
  await expect(panel.getByText('+beta-content')).toHaveCount(0)
  await expect(panel.getByText('+gamma-content')).toHaveCount(0)
  const alpha = panel.getByRole('button', { name: /alpha\.ts/ })
  await expect(alpha).toHaveAttribute('aria-expanded', 'true')
  await expect(panel.getByRole('button', { name: /beta\.ts/ })).toHaveAttribute(
    'aria-expanded',
    'false',
  )

  await panel.getByRole('button', { name: /beta\.ts/ }).click()
  await expect(panel.getByText('+beta-content')).toBeVisible()

  await panel.getByRole('button', { name: '展开全部' }).click()
  await expect(panel.getByText('+gamma-content')).toBeVisible()
  await panel.getByRole('button', { name: '全部折叠' }).click()
  await expect(panel.getByText('+alpha-content')).toHaveCount(0)
  await expect(panel.getByText('+beta-content')).toHaveCount(0)
  await expect(panel.getByText('+gamma-content')).toHaveCount(0)
})
test('graph paging loads the next page and marks the end', async ({ page }) => {
  // 105 commits: the first page (100) fills the list, the scrolled tail loads 5.
  const commits = Array.from({ length: 105 }, (_, index) => ({
    hash: `h${String(index).padStart(3, '0')}`,
    short: `h${String(index).padStart(3, '0')}`.slice(0, 7),
    parents: [index === 0 ? '' : `h${String(index + 1).padStart(3, '0')}`].filter(Boolean),
    subject: `commit ${index}`,
    author: 'a',
    timestamp: 1763200000 - index,
    refs: index === 104 ? ['main'] : [],
  }))
  await boot(
    page,
    initialState({
      running: false,
      gitLog: commits.slice(0, 100) as unknown as Record<string, unknown>[],
    }),
  )
  // The second page answers through the same bridge state; swap it mid-test.
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await expect(panel.getByText('commit 99')).toBeVisible()

  await page.evaluate((rest) => {
    ;(window as unknown as { __ZEN_E2E__: { gitLog: Record<string, unknown>[] } }).__ZEN_E2E__.gitLog =
      rest
  }, commits.slice(100))
  await panel.getByText('已暂存', { exact: true }).scrollIntoViewIfNeeded().catch(() => {})
  // Scroll the side column to its bottom to trigger the next page.
  await panel.evaluate((el) => {
    const side = el.querySelector('[data-git-side]')
    if (side) side.scrollTop = side.scrollHeight
  })
  await expect(panel.getByText('commit 104')).toBeVisible({ timeout: 5000 })
  await expect(panel.getByText('已到最早提交')).toBeVisible()
})
