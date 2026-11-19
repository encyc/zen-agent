/** The work panel: files and git diffs, the file viewer's grammar
 * highlight, mentioned paths landing in the tree, type badges, worktree
 * rows and a subagent conversation opening from its task row. The
 * topbar terminal mounts a shell in the same panel. */

import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness.ts'

test('the work panel opens on files and the git tab renders diff hunks', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'edit_file',
          args_preview: '{"path":"/src/a.ts"}',
          args: { path: '/src/a.ts' },
          status: 'ok',
          diff: ['@@ -1,2 +1,2 @@', ' keep', '-old', '+new'].join('\n'),
        },
      ],
      fsEntries: {
        '': [
          { name: 'src', path: 'src', kind: 'dir', size: 0, hidden: false },
          { name: 'README.md', path: 'README.md', kind: 'file', size: 12, hidden: false },
        ],
        src: [
          { name: 'a.ts', path: 'src/a.ts', kind: 'file', size: 20, hidden: false },
        ],
      },
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await expect(panel.getByRole('tab', { name: 'Git' })).toHaveAttribute('aria-selected', 'true')
  // The review and worktree tabs folded into the git tab in the merge.
  await expect(panel.getByRole('tab', { name: '审核' })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: '工作树' })).toHaveCount(0)
  // The bridge's default git_changes serves one file per group; opening the
  // unstaged row renders the default git_diff hunks.
  await panel.getByRole('button', { name: /ReviewPanel/ }).click()
  await expect(panel).toContainText('@@ -1,2 +1,3 @@')
  await expect(panel.locator('[data-kind="add"]').first()).toContainText('new line')
  await expect(panel.locator('[data-kind="del"]').first()).toContainText('old line')
  await page.getByRole('button', { name: '返回变更列表' }).click()

  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '文件' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await panel.getByRole('button', { name: 'src', exact: true }).click()
  await panel.getByRole('button', { name: 'a.ts' }).click()
  await expect(panel).toContainText('export { a }')
  await expect(panel.locator('[data-line="2"]')).toBeVisible()
  await panel.getByRole('button', { name: '返回文件列表' }).click()
  await expect(panel.getByRole('button', { name: 'README.md' })).toBeVisible()

  await page.getByRole('button', { name: '关闭 文件' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: 'Git' })).toHaveAttribute('aria-selected', 'true')

  // Git is a plain tab: closing it retires the last tab and the dock with it.
  await page.getByRole('button', { name: '关闭 Git' }).click()
  await expect(panel).toHaveCount(0)

  // Reopening always lands on files, never on the surface the user just shut.
  await page.getByRole('button', { name: '打开工作面板' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel.getByRole('button', { name: 'README.md' })).toBeVisible()
  await expect(panel.getByRole('tab', { name: 'Git' })).toHaveCount(0)
})
test('a file mentioned in the reply opens in the work panel', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '改了哪里？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '我改了 `src/a.ts`，不是 `store.messages`。',
          timestamp: 2,
        },
      ],
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  // Only the real path becomes a link; the dotted identifier stays text.
  const ref = page.locator('[data-file-path="src/a.ts"]')
  await expect(ref).toHaveText('src/a.ts')
  await expect(page.locator('[data-file-path="store.messages"]')).toHaveCount(0)

  await ref.click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('export { a }')
  await expect(panel.getByRole('button', { name: '返回文件列表' })).toBeVisible()

  // Back to the tree, then the same reference reopens the viewer.
  await panel.getByRole('button', { name: '返回文件列表' }).click()
  await expect(panel.getByRole('button', { name: '返回文件列表' })).toHaveCount(0)
  await ref.click()
  await expect(panel).toContainText('export { a }')
})
test('the file viewer highlights source with the matching grammar', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '入口在哪？', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: '入口在 `src/main.py`。', timestamp: 2 },
      ],
      fsFiles: {
        'src/main.py': {
          path: 'src/main.py',
          kind: 'text',
          size: 44,
          text: 'def greet(name):\n    return f"hi {name}"\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  await page.locator('[data-file-path="src/main.py"]').click()
  const panel = page.locator('[data-work-panel]')
  const code = panel.locator('[data-lang="python"]')
  await expect(code).toBeVisible()
  await expect(code).toContainText('return f"hi {name}"')
  // The grammar loads lazily; the keyword paints with a theme color once ready.
  await expect(code.locator('[data-line="1"] span[style*="color"]', { hasText: 'def' })).toHaveCount(1)
})
test('a folder mentioned in the reply lands in the tree, never on a new page', async ({
  page,
}) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: 'rpc 在哪？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '目录是 [zenagent/cli/rpc](zenagent/cli/rpc)。',
          timestamp: 2,
        },
      ],
      fsEntries: {
        '': [{ name: 'zenagent', path: 'zenagent', kind: 'dir', size: 0, hidden: false }],
        zenagent: [{ name: 'cli', path: 'zenagent/cli', kind: 'dir', size: 0, hidden: false }],
        'zenagent/cli': [
          { name: 'rpc', path: 'zenagent/cli/rpc', kind: 'dir', size: 0, hidden: false },
        ],
        'zenagent/cli/rpc': [
          {
            name: 'host.py',
            path: 'zenagent/cli/rpc/host.py',
            kind: 'file',
            size: 6,
            hidden: false,
          },
        ],
      },
      fsFiles: {
        'zenagent/cli/rpc/host.py': {
          path: 'zenagent/cli/rpc/host.py',
          kind: 'text',
          size: 6,
          text: 'x = 1\n',
          total_lines: 1,
          truncated: false,
        },
      },
    }),
  )

  const ref = page.locator('[data-file-path="zenagent/cli/rpc"]')
  await expect(ref).toHaveText('zenagent/cli/rpc')
  await ref.click()

  // The click must never navigate the webview: the shell stays mounted.
  expect(new URL(page.url()).pathname).toBe('/')
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  // The folder opened in place: its ancestors and its contents are in the tree.
  await expect(panel.getByRole('button', { name: 'zenagent' })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'host.py' })).toBeVisible()
})
test('an absolute folder path and a slashed folder token both reveal in the tree', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work/zen'))
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: 'agent 在哪？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '目录 `zenagent/agent/`，见 [rpc](/work/zen/zenagent/agent)。',
          timestamp: 2,
        },
      ],
      fsEntries: {
        '': [{ name: 'zenagent', path: 'zenagent', kind: 'dir', size: 0, hidden: false }],
        zenagent: [
          { name: 'agent', path: 'zenagent/agent', kind: 'dir', size: 0, hidden: false },
        ],
        'zenagent/agent': [
          {
            name: 'core.py',
            path: 'zenagent/agent/core.py',
            kind: 'file',
            size: 6,
            hidden: false,
          },
        ],
      },
      fsFiles: {
        'zenagent/agent/core.py': {
          path: 'zenagent/agent/core.py',
          kind: 'text',
          size: 6,
          text: 'x = 1\n',
          total_lines: 1,
          truncated: false,
        },
      },
    }),
  )

  // A trailing slash links a folder; an absolute link is a reference too.
  await expect(page.locator('[data-file-path="zenagent/agent"]')).toHaveText(
    'zenagent/agent/',
  )
  const absolute = page.locator('[data-file-path="/work/zen/zenagent/agent"]')
  await expect(absolute).toHaveText('rpc')

  await absolute.click()
  const panel = page.locator('[data-work-panel]')
  // Folded to the project-relative key: the folder reveals in place instead
  // of failing the ancestor-by-ancestor read.
  await expect(panel.getByRole('button', { name: 'core.py' })).toBeVisible()
  await expect(panel).not.toContainText('读取失败')
})
test('the file tree paints type badges and the viewer shows a breadcrumb', async ({ page }) => {
  await boot(
    page,
    initialState({
      fsEntries: {
        '': [
          { name: 'src', path: 'src', kind: 'dir', size: 0, hidden: false },
          { name: 'README.md', path: 'README.md', kind: 'file', size: 8, hidden: false },
        ],
        src: [
          { name: 'a.ts', path: 'src/a.ts', kind: 'file', size: 20, hidden: false },
          { name: 'b.py', path: 'src/b.py', kind: 'file', size: 6, hidden: false },
        ],
      },
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'MD' })).toBeVisible()
  await panel.getByRole('button', { name: 'src', exact: true }).click()
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'TS' })).toBeVisible()
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'PY' })).toBeVisible()

  await panel.getByRole('button', { name: 'a.ts' }).click()
  const crumbs = panel.locator('[data-breadcrumb]')
  await expect(crumbs).toContainText('src')
  await expect(crumbs.locator('[data-crumb-last]')).toHaveText('a.ts')
})
test('the git tab lists worktrees and marks the current one', async ({ page }) => {
  await boot(
    page,
    initialState({
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
        status: { branch: 'main', staged: 0, dirty: 2, untracked: 1, clean: false },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: 'Git' }).click()
  await expect(panel.getByRole('tab', { name: 'Git' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('main')
  await expect(panel).toContainText('feature')
  await expect(panel).toContainText('当前')
  // Rows preview; there is no project-switch button any more.
  await expect(panel.getByRole('button', { name: '打开', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '关闭工作面板' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveCount(0)
})
test('a task row opens the subagent conversation in the work panel', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'delegate', timestamp: 1 },
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'task',
          args_preview: '{"name":"Researcher"}',
          args: { name: 'Researcher', prompt: 'find x' },
          status: 'ok',
          result_text: 'done',
          duration_ms: 1200,
        },
        { kind: 'assistant', id: 'a1', text: 'handled', timestamp: 2 },
      ],
      subagents: [
        {
          call_id: 'c1',
          name: 'Researcher',
          status: 'ok',
          duration_ms: 1200,
          cost: 0.0021,
          tokens: 1500,
          has_transcript: true,
          transcript_truncated: false,
        },
      ],
      subagentTranscripts: {
        c1: [
          { kind: 'user', text: 'find x', timestamp: 1 },
          {
            id: 'ct1',
            kind: 'tool',
            call_id: 'ct1',
            name: 'bash',
            args_preview: '{"command":"rg timeout"}',
            args: { command: 'rg timeout' },
            status: 'ok',
            result_text: 'gateway.py:42',
            duration_ms: 30,
          },
          { kind: 'assistant', text: 'Found it in gateway.py', timestamp: 2 },
        ],
      },
    }),
  )
  const task = page.locator('[data-tool="task"] button')
  await expect(task).toHaveAttribute('title', '打开子智能体过程')
  await task.click()

  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: /Researcher/ })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  const transcript = page.getByTestId('subagent-transcript')
  await expect(transcript).toContainText('find x')
  await expect(transcript).toContainText('Found it in gateway.py')
  await transcript.locator('[data-tool="bash"] button').click()
  await expect(transcript).toContainText('gateway.py:42')
  await expect(transcript).toContainText('已完成')
  await expect(transcript).toContainText('1.5k tok')
  // The parent row stays one line: the conversation lives in the panel only.
  await expect(page.locator('[data-tool="task"] pre')).toHaveCount(0)

  await page.getByRole('button', { name: '关闭 Researcher' }).click()
  await expect(panel.getByRole('tab', { name: /Researcher/ })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
})
test('the topbar terminal opens a shell in the work panel', async ({ page }) => {
  // A fixed light start makes the palette flip deterministic.
  await page.addInitScript(() => localStorage.setItem('zen.theme', JSON.stringify('light')))
  await boot(page, initialState())
  // The old new-session seat is gone; the terminal owns the topbar's right edge.
  const topbar = page.getByRole('toolbar', { name: '会话' })
  await expect(topbar.getByRole('button', { name: '新会话' })).toHaveCount(0)
  await page.getByRole('button', { name: '终端' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveAttribute('data-tab', 'terminal')

  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'terminal' && call.op === 'start'),
      ),
    )
    .toBe(true)
  // React's dev double-mount starts one shell per pass; the live pane owns the
  // last token.
  const session = await page.evaluate(() =>
    String(
      window.__ZEN_E2E__.calls
        .filter((call) => call.type === 'terminal' && call.op === 'start')
        .at(-1)?.session ?? '',
    ),
  )
  await page.evaluate(
    ({ token }) => {
      window.__ZEN_E2E_EMIT__('rpc:frame', {
        event: 'rpc:frame',
        id: 1,
        payload: {
          frame: {
            type: 'event',
            event: { type: 'terminal', session: token, kind: 'output', data: 'zen@work % ' },
          },
        },
      })
    },
    { token: session },
  )
  await expect(page.locator('[data-terminal-host]')).toContainText('zen@work %')

  // The xterm pane follows the shell palette: the footer theme cycle repaints
  // the viewport through the body attributes, without remounting the shell.
  // xterm paints the theme background on its scrollable element.
  const viewport = page.locator('[data-terminal-host] .xterm-scrollable-element')
  const viewportColor = () =>
    viewport.evaluate((node) => getComputedStyle(node).backgroundColor)
  await expect.poll(viewportColor).toBe('rgb(255, 255, 255)')
  // xterm.css still paints the legacy scroll viewport #000; it has to follow the
  // palette as well, or the sub-row remainder FitAddon leaves at the bottom
  // shows through as a black seam.
  const legacyViewport = page.locator('[data-terminal-host] .xterm-viewport')
  const legacyColor = () =>
    legacyViewport.evaluate((node) => getComputedStyle(node).backgroundColor)
  await expect.poll(legacyColor).toBe('rgb(255, 255, 255)')
  await page.getByRole('button', { name: '外观: 浅色' }).click()
  await expect(page.locator('body')).toHaveAttribute('data-ds-dark-theme', '')
  await expect.poll(viewportColor).toBe('rgb(21, 21, 23)')
  await expect.poll(legacyColor).toBe('rgb(21, 21, 23)')

  await page.locator('[data-terminal-host]').click()
  await page.keyboard.type('ls')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls
          .filter((call) => call.type === 'terminal' && call.op === 'write')
          .map((call) => String(call.data ?? ''))
          .join(''),
      ),
    )
    .toBe('ls')

  // Closing the tab tells the host to end the shell.
  await page.getByRole('button', { name: '关闭 终端' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'terminal' && call.op === 'close'),
      ),
    )
    .toBe(true)
})
