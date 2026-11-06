# Desktop UI Parity — Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the shell-level half of pi-desktop parity: the parity token layer, the conversation topbar, a scoped/grouped sidebar with real session actions, and the ⌘K palette — all on the existing zen RPC.

**Architecture:** Five vertical tasks. Task 1 adds a `--ds-*` parity block and re-points the existing `--dsw-*` aliases, so component CSS keeps its vocabulary. Task 2 adds pure state helpers, the notice store and the session-action methods those surfaces call. Tasks 3–5 build the topbar, the sidebar and the palette on top of those helpers. No backend change, no `rpc/types.ts` change, CSS Modules only.

**Tech Stack:** Tauri v2, React 18, strict TypeScript, zustand, CSS Modules, Node's built-in test runner (`node --test`), Playwright 1.65 against the scripted Tauri bridge.

**Spec:** `docs/superpowers/specs/2026-11-06-desktop-ui-parity-design.md`

## Global Constraints

- Requests available today: `prompt, cancel, status, shutdown, steer, tools, models, commands, transcript, model, mode, usage, approval, session, command`. Do not add server ops.
- `rename` and `pin` act on the **current session only** (`command {name:'rename', argument}` / `command {name:'pin', argument:'on'|'off'}`).
- The session list carries a project display name but no project root, and the RPC process is bound to one `--cwd`. Cross-project sessions are discovery-only: render them, disable opening them, never send `resume` for them.
- `session op=import` opens a transcript file; it does not adopt it into the current project. Label it "open session file".
- `session op=export` takes `{path, format}` where **`path` is the source session**, and writes next to it with the chosen suffix; it returns `{path}`. The RPC accepts no destination, so the GUI offers **format choice** (`md`/`html`) and reports the returned path. No save dialog, no backend change. (This supersedes the design's "native save dialog" wording, which the current RPC cannot honor.)
- Queue ops remain `promote(pending_id)`, `dequeue` (latest queue entry) and `clear_pending` (both lanes).
- No file-list/file-read/file-diff RPC. Only per-tool `details.diff` exists.
- No Tailwind, no new dependencies, no verbatim pi-desktop source (LGPL-3.0).
- Every action maps to an existing request; no affordance may look enabled without RPC behind it.
- `cd desktop && pnpm test`, `cd desktop && pnpm build`, `cd desktop && pnpm e2e` stay green after every task.

## Review Focus

- **Cross-project activation.** A session from another project rendered in the all scope or the palette must never send `resume`; the process cwd would stay on the current project. Pinned in Task 4 (sidebar e2e) and Task 5 (palette unit + e2e).
- **Rename/pin targeting.** Acting on a non-current row would rename or pin the current session under another row's label. The menu must omit both actions on non-current rows. Pinned in Task 4 e2e.
- **Armed delete drift.** A first click on one row must not arm deletion of another row after a scope switch or a different row is clicked. Pinned by `armDelete` unit tests (Task 2) and the two-step e2e (Task 4).
- **Import/export edge paths.** A cancelled open dialog is a no-op; export sends only the current session plus the chosen format and reports the server-written path. Pinned in Task 4 e2e.
- **Palette Enter.** Enter on a disabled session and Enter on an argument-taking command must not run the wrong thing (no cross-project resume, no bare `/rename`). Pinned in Task 5 unit + e2e.
- **Accent inversion.** The dark accent is white, so every primary fill needs the inverse foreground token; a hardcoded white label disappears on it. Pinned by the Task 1 token test for the send button and the error-boundary retry button.

---

### Task 1: Parity tokens and sidebar width range

**Files:**
- Modify: `desktop/src/styles/tokens.css`
- Modify: `desktop/src/ui/columns.ts`
- Modify: `desktop/src/ui/AppFrame.tsx:7-14,48,77`
- Modify: `desktop/src/App.tsx:37`
- Modify: `desktop/src/ui/composer/InputBar.module.css:212-213`
- Modify: `desktop/src/ErrorBoundary.tsx:74`
- Test: `desktop/tests/tokens.test.ts`
- Test: `desktop/tests/columns.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: CSS `--ds-font-scale`, `--ds-font-family-sans`, `--ds-font-10-5` … `--ds-font-28`, `--ds-radius-4` … `--ds-radius-24`, `--ds-radius-full`, `--ds-chat-content-width: 760px`, `--ds-topbar-height: 40px`, `--ds-composer-radius: 20px`, `--ds-accent` (near-black light / white dark) and `--ds-accent-hover`; `--dsw-alias-state-business-primary` and `--dsw-alias-button-info-fill/hover` re-pointed at them; `SIDEBAR_MIN = 240`, `SIDEBAR_MAX = 520`, `SIDEBAR_DEFAULT = 275`, `sidebarMax(viewport): number`, and `computeColumns` clamping the sidebar to `[SIDEBAR_MIN, sidebarMax(viewport)]`.

- [ ] **Step 1: Write the failing token test**

```ts
// desktop/tests/tokens.test.ts
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')

test('the parity layer carries the pi font stack, ramp, radii and the 760px band', () => {
  assert.match(
    css,
    /--ds-font-family-sans:\s*-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;/,
  )
  assert.match(css, /--ds-font-scale:\s*1;/)
  for (const size of ['10-5', '11', '11-5', '12', '12-5', '13', '13-5', '14', '15', '16', '18', '20', '28']) {
    assert.match(css, new RegExp(`--ds-font-${size}:\\s*calc\\(`))
  }
  for (const radius of ['4', '6', '8', '10', '12', '14', '16', '18', '20', '24']) {
    assert.match(css, new RegExp(`--ds-radius-${radius}:\\s*${radius}px;`))
  }
  assert.match(css, /--ds-radius-full:\s*999px;/)
  assert.match(css, /--ds-chat-content-width:\s*760px;/)
  assert.match(css, /--ds-topbar-height:\s*40px;/)
  assert.match(css, /--ds-composer-radius:\s*20px;/)
  assert.match(css, /--dsh-chat-content-width:\s*var\(--ds-chat-content-width\);/)
  assert.match(css, /--dsh-composer-card-max-width:\s*var\(--ds-chat-content-width\);/)
  assert.match(css, /--dsw-radius-xl:\s*var\(--ds-radius-20\);/)
})

test('the existing text stack is re-pointed at the parity stack', () => {
  assert.match(css, /--dsh-font-family-text-default:\s*var\(--ds-font-family-sans\);/)
})

test('accent aliases stay monochrome and primary buttons invert safely', () => {
  assert.match(css, /--ds-accent:\s*var\(--dsw-static-neutral-bluish-1000\);/)
  assert.match(css, /--ds-accent-hover:\s*var\(--dsw-static-neutral-bluish-800\);/)
  assert.match(css, /--dsw-alias-state-business-primary:\s*var\(--ds-accent\);/)
  assert.match(css, /--dsw-alias-button-info-fill:\s*var\(--ds-accent\);/)
  assert.match(css, /--dsw-alias-button-info-hover:\s*var\(--ds-accent-hover\);/)
  const inputBar = readFileSync(new URL('../src/ui/composer/InputBar.module.css', import.meta.url), 'utf8')
  const boundary = readFileSync(new URL('../src/ErrorBoundary.tsx', import.meta.url), 'utf8')
  assert.match(inputBar, /\.primary\s*\{[^}]*color:\s*var\(--dsw-alias-label-primary-foreground\)/)
  assert.match(boundary, /color:\s*'var\(--dsw-alias-label-primary-foreground\)'/)
})
```

- [ ] **Step 2: Write the failing columns test**

```ts
// desktop/tests/columns.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  computeColumns,
  sidebarMax,
} from '../src/ui/columns.ts'

test('sidebar parity defaults, clamps and viewport cap', () => {
  assert.equal(SIDEBAR_DEFAULT, 275)
  assert.equal(SIDEBAR_MIN, 240)
  assert.equal(SIDEBAR_MAX, 520)
  assert.equal(sidebarMax(1280), 520)
  assert.equal(sidebarMax(700), 380)
  assert.equal(sidebarMax(500), 240)
  assert.equal(computeColumns(1280, 275, 0).sidebar, 275)
  assert.equal(computeColumns(1280, 100, 0).sidebar, 240)
  assert.equal(computeColumns(1280, 900, 0).sidebar, 520)
  assert.equal(computeColumns(700, 900, 0).sidebar, 380)
  assert.equal(computeColumns(500, 900, 0).sidebar, 240)
})

test('collapsed width and the rightbar solve are unchanged', () => {
  assert.equal(computeColumns(1280, 0, 0).sidebar, 56)
  const cols = computeColumns(1280, 275, 380)
  assert.equal(cols.sidebar, 275)
  assert.equal(cols.rightbar, 380)
  assert.equal(cols.center, 625)
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd desktop && pnpm test`
Expected: FAIL — `sidebarMax` is not exported and the token assertions do not match.

- [ ] **Step 4: Add the parity block and re-point the aliases**

In `desktop/src/styles/tokens.css`, add to the existing `:root` block, above the dsh values:

```css
  /* Parity layer — values measured from pi-desktop's rendered shell.
     Components keep using --dsw-*; this block is the single tuning point. */
  --ds-font-scale: 1;
  --ds-font-family-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --ds-accent: var(--dsw-static-neutral-bluish-1000);
  --ds-accent-hover: var(--dsw-static-neutral-bluish-800);
  --ds-font-10-5: calc(10.5px * var(--ds-font-scale));
  --ds-font-11: calc(11px * var(--ds-font-scale));
  --ds-font-11-5: calc(11.5px * var(--ds-font-scale));
  --ds-font-12: calc(12px * var(--ds-font-scale));
  --ds-font-12-5: calc(12.5px * var(--ds-font-scale));
  --ds-font-13: calc(13px * var(--ds-font-scale));
  --ds-font-13-5: calc(13.5px * var(--ds-font-scale));
  --ds-font-14: calc(14px * var(--ds-font-scale));
  --ds-font-15: calc(15px * var(--ds-font-scale));
  --ds-font-16: calc(16px * var(--ds-font-scale));
  --ds-font-18: calc(18px * var(--ds-font-scale));
  --ds-font-20: calc(20px * var(--ds-font-scale));
  --ds-font-28: calc(28px * var(--ds-font-scale));
  --ds-radius-4: 4px;
  --ds-radius-6: 6px;
  --ds-radius-8: 8px;
  --ds-radius-10: 10px;
  --ds-radius-12: 12px;
  --ds-radius-14: 14px;
  --ds-radius-16: 16px;
  --ds-radius-18: 18px;
  --ds-radius-20: 20px;
  --ds-radius-24: 24px;
  --ds-radius-full: 999px;
  --ds-chat-content-width: 760px;
  --ds-topbar-height: 40px;
  --ds-composer-radius: 20px;
```

Then change the existing declarations in the same block:

```css
  --dsh-font-family-text-default: var(--ds-font-family-sans);
  --dsw-radius-xs: var(--ds-radius-4);
  --dsw-radius-sm: var(--ds-radius-8);
  --dsw-radius-md: var(--ds-radius-12);
  --dsw-radius-lg: var(--ds-radius-16);
  --dsw-radius-xl: var(--ds-radius-20);
  --dsw-radius-panel: var(--ds-radius-24);
```

Then make the accent monochrome in both theme blocks. In the light `body` block, change the existing declarations to:

```css
  --dsw-alias-state-business-primary: var(--ds-accent);
  --dsw-alias-button-info-fill: var(--ds-accent);
  --dsw-alias-button-info-hover: var(--ds-accent-hover);
  --dsw-specific-sidebar-nav-item-active-accent: var(--dsw-static-neutral-bluish-100);
```

At the top of `body[data-ds-dark-theme]`, add the inverted accent, then change the same three aliases:

```css
  --ds-accent: var(--dsw-static-neutral-bluish-00);
  --ds-accent-hover: var(--dsw-static-neutral-bluish-100);
  --dsw-alias-state-business-primary: var(--ds-accent);
  --dsw-alias-button-info-fill: var(--ds-accent);
  --dsw-alias-button-info-hover: var(--ds-accent-hover);
```

Its `--dsw-specific-sidebar-nav-item-active-accent` already points at a neutral; leave it. Because dark accent becomes white, the two places that hardcode white text on a primary fill must use the inverse token instead:

- `desktop/src/ui/composer/InputBar.module.css`: `.primary { color: var(--dsw-alias-label-primary-foreground); }`
- `desktop/src/ErrorBoundary.tsx`: the retry button's `color: 'var(--dsw-alias-label-primary-foreground)'`.

`--dsw-alias-label-primary-foreground` is white in light and near-black in dark, so the label flips with the accent. Leave `--dsw-alias-link` and the light user bubble for their own stage; they need underline/tile treatment, not just a token swap.

In the `body` chat-layout block at the bottom, set:

```css
  --dsh-chat-content-width: var(--ds-chat-content-width);
  --dsh-composer-card-max-width: var(--ds-chat-content-width);
```

- [ ] **Step 5: Add the viewport cap and new constants**

`columns.ts` already declares `SIDEBAR_MIN = 264`, `SIDEBAR_MAX = 420` and `SIDEBAR_DEFAULT = 280`; replace those three existing declarations (do not add duplicates):

```ts
// desktop/src/ui/columns.ts
export const SIDEBAR_MIN = 240
export const SIDEBAR_MAX = 520
export const SIDEBAR_DEFAULT = 275

/** pi's clamp: min(520px, calc(100vw - 320px)), never below the drag floor. */
export function sidebarMax(viewport: number): number {
  return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, viewport - 320))
}
```

In `computeColumns`, replace the sidebar clamp with:

```ts
  const s = sidebar === 0 ? collapsedWidth : clampWidth(sidebar, SIDEBAR_MIN, sidebarMax(viewport))
```

In `AppFrame.tsx`, replace the `SIDEBAR_MAX` import with `SIDEBAR_DEFAULT` and `sidebarMax` (keep `SIDEBAR_MIN`), and clamp the drag delta with it:

```ts
        props.onSidebarWidth(
          Math.min(sidebarMax(viewport), Math.max(SIDEBAR_MIN, sidebarBase.current + dx)),
        )
```

Change the AppFrame fallback `|| 280` to `|| SIDEBAR_DEFAULT` (import the constant), and in `App.tsx` import `SIDEBAR_DEFAULT` from `./ui/columns` and change `loadWidth(SIDEBAR_KEY, 280)` to `loadWidth(SIDEBAR_KEY, SIDEBAR_DEFAULT)`.

- [ ] **Step 6: Verify tests, build and e2e**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`
Expected: all unit tests pass; `tsc --noEmit` and Vite build succeed; the existing e2e suite stays green.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/styles/tokens.css desktop/src/ui/columns.ts desktop/src/ui/AppFrame.tsx desktop/src/App.tsx desktop/src/ui/composer/InputBar.module.css desktop/src/ErrorBoundary.tsx desktop/tests/tokens.test.ts desktop/tests/columns.test.ts
git commit -m "feat(desktop): parity tokens and sidebar width range"
```

---

### Task 2: Session helpers, notices and RPC-backed session actions

**Files:**
- Create: `desktop/src/state/sessionGroups.ts`
- Create: `desktop/src/state/armedDelete.ts`
- Create: `desktop/src/rpc/frames.ts`
- Create: `desktop/src/ui/sessions/fileDialog.ts`
- Modify: `desktop/src/state/stores.ts`
- Modify: `desktop/src/state/appStore.ts`
- Test: `desktop/tests/sessionGroups.test.ts`
- Test: `desktop/tests/armedDelete.test.ts`
- Test: `desktop/tests/frames.test.ts`

**Interfaces:**
- Consumes: Task 1 only for CSS; no code dependency.
- Produces:
  - `type SessionScope = 'project' | 'all'`
  - `compareSessions(a: SessionSummary, b: SessionSummary): number`
  - `sessionGroups(sessions: SessionSummary[], scope: SessionScope, query: string): { pinned: SessionSummary[]; groups: { project: string; sessions: SessionSummary[] }[] }`
  - `armDelete(current: string | null, path: string): string | null`
  - `deleteArmed(current: string | null, path: string): boolean`
  - `frameError(frame: Record<string, unknown>): string | null`
  - `pickSessionFileToOpen(): Promise<string | null>`
  - `useSessionStore` gains `scope`, `setScope(scope)` (persisted as `zen.sidebar.scope`).
  - `useNoticeStore` with `{ notice: { kind: 'error' | 'info'; text: string } | null; show(notice): void; dismiss(): void }`.
  - `useAppStore` gains `type ExportFormat = 'md' | 'html'`, `renameSession(title): Promise<void>`, `togglePin(pinned: boolean): Promise<void>`, `importSession(path): Promise<boolean>`, `exportSession(format: ExportFormat): Promise<string | null>`; `refreshSessions(scope?)` now sends `{ type: 'session', op: 'list', scope }`.

- [ ] **Step 1: Write the failing helper tests**

```ts
// desktop/tests/sessionGroups.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sessionGroups } from '../src/state/sessionGroups.ts'
import type { SessionSummary } from '../src/rpc/types.ts'

const make = (path: string, updated: number, extra: Partial<SessionSummary> = {}): SessionSummary => ({
  path,
  name: path.slice(path.lastIndexOf('/') + 1, -6),
  title: path,
  project: 'alpha',
  messages: 1,
  current: false,
  pinned: false,
  updated_at: updated,
  ...extra,
})

test('query filters title, name and project case-insensitively', () => {
  const sessions = [
    make('/s/a.jsonl', 3, { title: 'Fix Login' }),
    make('/s/b.jsonl', 2, { title: 'other', name: 'b' }),
    make('/s/c.jsonl', 1, { title: 'other', project: 'Beta' }),
  ]
  const byTitle = sessionGroups(sessions, 'project', 'LOGIN')
  assert.deepEqual(
    [...byTitle.pinned, ...byTitle.groups.flatMap((group) => group.sessions)].map((s) => s.path),
    ['/s/a.jsonl'],
  )
  const byProject = sessionGroups(sessions, 'all', 'beta')
  assert.deepEqual(
    byProject.groups.flatMap((group) => group.sessions).map((s) => s.path),
    ['/s/c.jsonl'],
  )
})

test('pinned rows come first and each list sorts updated desc with a path tie-break', () => {
  const sections = sessionGroups(
    [
      make('/s/a.jsonl', 1),
      make('/s/b.jsonl', 5),
      make('/s/c.jsonl', 5),
      make('/s/d.jsonl', 2, { pinned: true }),
    ],
    'project',
    '',
  )
  assert.deepEqual(sections.pinned.map((s) => s.path), ['/s/d.jsonl'])
  assert.deepEqual(sections.groups[0].sessions.map((s) => s.path), ['/s/b.jsonl', '/s/c.jsonl', '/s/a.jsonl'])
})

test('project scope collapses to one group; all scope splits groups by newest session', () => {
  const sessions = [
    make('/s/a.jsonl', 9, { project: 'alpha' }),
    make('/s/b.jsonl', 4, { project: 'beta' }),
    make('/s/c.jsonl', 7, { project: 'beta' }),
  ]
  const project = sessionGroups(sessions, 'project', '')
  assert.deepEqual(project.groups.map((g) => g.project), ['alpha'])
  assert.deepEqual(project.groups[0].sessions.map((s) => s.path), ['/s/a.jsonl', '/s/c.jsonl', '/s/b.jsonl'])

  const all = sessionGroups(sessions, 'all', '')
  assert.deepEqual(all.groups.map((g) => g.project), ['alpha', 'beta'])
  assert.deepEqual(all.groups[1].sessions.map((s) => s.path), ['/s/c.jsonl', '/s/b.jsonl'])
})

test('sessions without a project group under an empty label and empty groups drop out', () => {
  const all = sessionGroups([make('/s/a.jsonl', 1, { project: '' })], 'all', '')
  assert.deepEqual(
    all.groups.map((group) => [group.project, group.sessions.map((session) => session.path)]),
    [['', ['/s/a.jsonl']]],
  )
  assert.deepEqual(sessionGroups([], 'all', ''), { pinned: [], groups: [] })
})
```

```ts
// desktop/tests/armedDelete.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { armDelete, deleteArmed } from '../src/state/armedDelete.ts'

test('the first click arms a row and a repeated click disarms it', () => {
  assert.equal(armDelete(null, '/a'), '/a')
  assert.equal(armDelete('/a', '/a'), null)
  assert.equal(deleteArmed('/a', '/a'), true)
  assert.equal(deleteArmed('/a', '/b'), false)
  assert.equal(deleteArmed(null, '/b'), false)
})

test('arming a different row moves the arm instead of deleting the old one', () => {
  const armed = armDelete('/a', '/b')
  assert.equal(armed, '/b')
  assert.equal(deleteArmed(armed, '/a'), false)
})
```

```ts
// desktop/tests/frames.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { frameError } from '../src/rpc/frames.ts'

test('error frames and rejected results both yield a message', () => {
  assert.equal(frameError({ type: 'error', message: 'boom' }), 'boom')
  assert.equal(frameError({ type: 'result', ok: false, reason: 'nope' }), 'nope')
  assert.equal(frameError({ type: 'result', ok: false }), 'request failed')
  assert.equal(frameError({ type: 'result', ok: true }), null)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd desktop && pnpm test`
Expected: FAIL — the new modules do not exist.

- [ ] **Step 3: Implement the pure helpers**

`sessionGroups.ts`:

```ts
import type { SessionSummary } from '../rpc/types'

export type SessionScope = 'project' | 'all'

export interface SessionGroup {
  project: string
  sessions: SessionSummary[]
}

export interface SessionSections {
  pinned: SessionSummary[]
  groups: SessionGroup[]
}

export function compareSessions(a: SessionSummary, b: SessionSummary): number {
  const byUpdated = (b.updated_at ?? 0) - (a.updated_at ?? 0)
  return byUpdated !== 0 ? byUpdated : a.path.localeCompare(b.path)
}

function matches(session: SessionSummary, query: string): boolean {
  return [session.title, session.name, session.project].some((value) =>
    (value ?? '').toLowerCase().includes(query),
  )
}

export function sessionGroups(
  sessions: SessionSummary[],
  scope: SessionScope,
  query: string,
): SessionSections {
  const needle = query.trim().toLowerCase()
  const visible = sessions.filter((session) => matches(session, needle))
  const pinned = visible.filter((session) => session.pinned).sort(compareSessions)
  const rest = visible.filter((session) => !session.pinned).sort(compareSessions)
  if (scope === 'project') {
    return { pinned, groups: rest.length === 0 ? [] : [{ project: rest[0].project ?? '', sessions: rest }] }
  }
  const byProject = new Map<string, SessionSummary[]>()
  for (const session of rest) {
    const key = session.project ?? ''
    const bucket = byProject.get(key)
    if (bucket === undefined) byProject.set(key, [session])
    else bucket.push(session)
  }
  const groups = [...byProject.entries()].map(([project, groupSessions]) => ({
    project,
    sessions: groupSessions,
  }))
  groups.sort((a, b) => {
    const byUpdated = (b.sessions[0]?.updated_at ?? 0) - (a.sessions[0]?.updated_at ?? 0)
    return byUpdated !== 0 ? byUpdated : a.project.localeCompare(b.project)
  })
  return { pinned, groups }
}
```

`armedDelete.ts`:

```ts
export function armDelete(current: string | null, path: string): string | null {
  return current === path ? null : path
}

export function deleteArmed(current: string | null, path: string): boolean {
  return current === path
}
```

`rpc/frames.ts`:

```ts
export function frameError(frame: Record<string, unknown>): string | null {
  if (frame.type === 'error') {
    return typeof frame.message === 'string' && frame.message !== '' ? frame.message : 'request failed'
  }
  if (frame.type === 'result' && frame.ok === false) {
    return typeof frame.reason === 'string' && frame.reason !== '' ? frame.reason : 'request failed'
  }
  return null
}
```

`ui/sessions/fileDialog.ts`:

```ts
import { invoke } from '@tauri-apps/api/core'

export async function pickSessionFileToOpen(): Promise<string | null> {
  try {
    const selection = await invoke<string | string[] | null>('plugin:dialog|open', {
      options: {
        multiple: false,
        directory: false,
        title: 'ZenAgent',
        filters: [{ name: 'Sessions', extensions: ['jsonl'] }],
      },
    })
    return typeof selection === 'string' ? selection : null
  } catch {
    return null
  }
}
```

- [ ] **Step 4: Run the helper tests to verify they pass**

Run: `cd desktop && pnpm test`
Expected: PASS for the four new test files.

- [ ] **Step 5: Add the scope and notice stores**

In `desktop/src/state/stores.ts`:

```ts
import type { SessionScope } from './sessionGroups'

export interface Notice {
  kind: 'error' | 'info'
  text: string
}

interface NoticeState {
  notice: Notice | null
  show(notice: Notice): void
  dismiss(): void
}

export const useNoticeStore = create<NoticeState>((set) => ({
  notice: null,
  show: (notice) => set({ notice }),
  dismiss: () => set({ notice: null }),
}))
```

Extend `SessionState` with `scope: SessionScope`, `setScope(scope: SessionScope): void`; initialize from `load('zen.sidebar.scope', 'project' as SessionScope)` and persist on set under `zen.sidebar.scope`.

- [ ] **Step 6: Wire the session actions**

In `desktop/src/state/appStore.ts`, import `frameError` from `../rpc/frames`, add `useNoticeStore` to the existing `./stores` import, and import `type SessionScope` from `./sessionGroups`. Then add above `AppState`:

```ts
export type ExportFormat = 'md' | 'html'
```

and add to `AppState`:

```ts
  renameSession(title: string): Promise<void>
  togglePin(pinned: boolean): Promise<void>
  importSession(path: string): Promise<boolean>
  exportSession(format: ExportFormat): Promise<string | null>
```

Implement them with the client directly (not `runCommand`, so a rename does not inject a command row into the transcript):

```ts
  async renameSession(title) {
    await runSessionCommand(get, 'rename', title)
    await get().refreshSessions()
  },

  async togglePin(pinned) {
    await runSessionCommand(get, 'pin', pinned ? 'off' : 'on')
    await get().refreshSessions()
  },

  async importSession(path) {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'import', path })
    } catch (error) {
      showActionError(error)
      return false
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return false
    }
    await refreshAfterSessionChange(get)
    return true
  },

  async exportSession(format) {
    const source = useSessionStore.getState().currentPath
    if (source === null) {
      showActionError('no current session to export')
      return null
    }
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'export', path: source, format })
    } catch (error) {
      showActionError(error)
      return null
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return null
    }
    return typeof frame.path === 'string' ? frame.path : null
  },
```

`path` is the source session and the backend writes beside it; the returned `path` is the file it wrote.

Module-level helpers:

```ts
async function runSessionCommand(
  get: () => AppState,
  name: string,
  argument: string,
): Promise<void> {
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({ type: 'command', name, argument })
  } catch (error) {
    showActionError(error)
    return
  }
  const error = frameError(frame)
  if (error !== null) {
    showActionError(error)
    return
  }
  if (typeof frame.message === 'string' && frame.message !== '') {
    useNoticeStore.getState().show({ kind: 'info', text: frame.message })
  }
}

function showActionError(value: unknown): void {
  useNoticeStore.getState().show({
    kind: 'error',
    text: value instanceof Error ? value.message : String(value),
  })
}
```

`refreshSessions` reads the scope and sends it:

```ts
  refreshSessions(scope) {
    const effective = scope ?? useSessionStore.getState().scope
    return get()
      .client()
      .send({ type: 'session', op: 'list', scope: effective })
      .then((frame) => { /* unchanged body */ })
      .catch(() => {})
  },
```

Update its interface to `refreshSessions(scope?: SessionScope): Promise<void>`.

Route every existing session-op action through the same error check — `newSession`, `forkSession`, `resumeSession`, `deleteSession`, `undoLast`, `compactNow`, `promotePending`, `dequeuePending`, `clearPending` — so no user-initiated failure stays a silent `catch`:

```ts
  async deleteSession(path) {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'delete', path })
    } catch (error) {
      showActionError(error)
      return
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return
    }
    await refreshAfterSessionChange(get)
  },
```

Keep each action's success body unchanged; only add the frame capture and the `frameError` branch. `promotePending`, `dequeuePending` and `clearPending` keep their `refreshStatus()` success tail. Background polling (`refreshStatus`, `refreshSessions`, `refreshCatalog`, `refreshTranscript`) keeps its silent catch: it is not user-initiated.

- [ ] **Step 7: Verify tests, build and e2e**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`
Expected: all unit tests pass; build succeeds; the existing e2e suite stays green with the scoped list request.

- [ ] **Step 8: Commit**

```bash
git add desktop/src/state/sessionGroups.ts desktop/src/state/armedDelete.ts desktop/src/rpc/frames.ts desktop/src/ui/sessions/fileDialog.ts desktop/src/state/stores.ts desktop/src/state/appStore.ts desktop/tests/sessionGroups.test.ts desktop/tests/armedDelete.test.ts desktop/tests/frames.test.ts
git commit -m "feat(desktop): session grouping, actions and error notices"
```

---

### Task 3: Conversation topbar with inline rename and session menu

**Files:**
- Create: `desktop/src/ui/ConversationTopbar.tsx`
- Create: `desktop/src/ui/ConversationTopbar.module.css`
- Modify: `desktop/src/ui/icons.tsx`
- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/i18n/locale.ts`
- Modify: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `useSessionStore` (`sessions`, `currentPath`), `renameSession`, `undoLast`, `compactNow`, `busy`, and the `onNewSession` prop from App.
- Produces: `<ConversationTopbar collapsed={boolean} onExpandSidebar={fn} onToggleInspector={fn} inspectorOpen={boolean} onNewSession={fn} />`. DOM hooks relied on by e2e: `data-topbar-title`, `data-topbar-project`, `data-topbar-menu`, title button `aria-label` = `topbar.rename`, title input `aria-label` = `topbar.title`.
- `PencilIcon`, `MoreIcon`, `PanelRightIcon` added to `icons.tsx`.

- [ ] **Step 1: Extend the e2e bridge and write the failing topbar test**

In `desktop/e2e/app.spec.ts`:

1. Add `title: string` to `BridgeState`, and `title: '修复登录超时'` to `initialState`.
2. Use `state.title` for session `a`'s `title` in `sessions()`.
3. Add a `command` case to `respond`:

```ts
      case 'command':
        if (payload.name === 'rename') state.title = String(payload.argument ?? '')
        return { type: 'result', id: payload.id, ok: true }
```

No `message` on this success frame: the real `/rename` success is silent in the GUI, and an info notice would pop the banner on every rename.

4. Change `boot`'s final assertion to `await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)`.

Add the test:

```ts
test('the topbar shows the project and renames the current session inline', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-topbar-project]')).toHaveText('p')
  await page.getByRole('button', { name: '重命名会话标题' }).click()
  const input = page.getByRole('textbox', { name: '会话标题' })
  await input.fill('新的标题')
  await input.press('Enter')
  await expect(page.locator('[data-topbar-title]')).toHaveText('新的标题')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '新的标题',
        ),
      ),
    )
    .toBe(true)
})

test('the topbar menu runs undo and compact through the session op', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.locator('[data-topbar-menu]').click()
  await page.getByRole('menuitem', { name: '撤销上一轮' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'undo'),
      ),
    )
    .toBe(true)
  await page.locator('[data-topbar-menu]').click()
  await page.getByRole('menuitem', { name: '压缩上下文' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'compact'),
      ),
    )
    .toBe(true)
})
```

- [ ] **Step 2: Run the topbar tests to verify they fail**

Run: `cd desktop && pnpm e2e --grep "topbar"`
Expected: FAIL — `[data-topbar-title]` is absent (the old strip renders).

- [ ] **Step 3: Add the icons**

Append to `desktop/src/ui/icons.tsx`:

```tsx
export function PanelRightIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="M9.5 2.5v11" />
    </svg>
  )
}

export function PencilIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m3 13 .7-2.8L10.9 3a1.4 1.4 0 0 1 2 0l.1.1a1.4 1.4 0 0 1 0 2L5.8 12.3Z" />
    </svg>
  )
}

export function MoreIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)} fill="currentColor" stroke="none">
      <circle cx="3.5" cy="8" r="1.1" />
      <circle cx="8" cy="8" r="1.1" />
      <circle cx="12.5" cy="8" r="1.1" />
    </svg>
  )
}
```

- [ ] **Step 4: Implement the topbar**

`ConversationTopbar.tsx` reads:

```ts
const sessions = useSessionStore((state) => state.sessions)
const currentPath = useSessionStore((state) => state.currentPath)
const current = sessions.find((session) => session.path === currentPath) ?? null
const renameSession = useAppStore((state) => state.renameSession)
const undoLast = useAppStore((state) => state.undoLast)
const compactNow = useAppStore((state) => state.compactNow)
const busy = useAppStore((state) => state.busy)
```

Layout requirements:
- Root `height: var(--ds-topbar-height)`, `display: flex`, `align-items: center`, `gap: 6px`, `padding: 0 12px`, `flex: none`, `z-index: 5`, `data-tauri-drag-region`, and `data-collapsed={collapsed || undefined}`.
- `html[data-platform='darwin'] .root[data-collapsed]` gets `padding-left: 78px` for the traffic lights; the buttons inside stay interactive because the drag region is per element.
- When `collapsed`, render a sidebar expand button (`PanelLeftIcon`, `aria-label` = `topbar.expandSidebar`) before the project label.
- Project label `<span data-topbar-project>` in `--ds-font-11`, `--ds-font-11-5` color tertiary; hidden when empty.
- Title area: when not editing, a `<button type="button" data-topbar-title aria-label={t('topbar.rename')}>` showing `session.title || session.name || t('topbar.untitled')`, styled `--ds-font-13` weight 500, ellipsis. Click starts editing. When editing, an `<input aria-label={t('topbar.title')} defaultValue={title}>`, `autoFocus`; Enter commits a non-empty trimmed value that differs; Escape and blur cancel. Escape calls `event.stopPropagation()` so App's cancel shortcut does not fire. Use a `settled` ref so Enter followed by blur commits once.
- Spacer with `flex: 1`.
- Actions: new-session button (`PlusIcon`, `aria-label` = `topbar.new`), topbar menu trigger (`MoreIcon`, `aria-label` = `topbar.more`, `data-topbar-menu` on the **button**), inspector toggle (`PanelRightIcon`, `aria-label` = `topbar.inspector`, `data-active` when open, existing busy dot semantics).
- The menu is an absolutely positioned panel under the trigger with `role="menu"`; items `role="menuitem"`: `undoLast` (`t('topbar.undo')`) and `compactNow` (`t('topbar.compact')`). Both are `disabled` while `busy` — the server rejects them mid-run. Close on item click, Escape (`stopPropagation`) and outside pointerdown. No search button yet — Task 5 adds it.

- [ ] **Step 5: Replace `TopStrip` in `App.tsx`**

Delete the `TopStrip` function and its CSS usage. Render:

```tsx
      <ConversationTopbar
        collapsed={collapsed}
        onExpandSidebar={() => setCollapsed(false)}
        onToggleInspector={() => setRightbarOpen((value) => !value)}
        inspectorOpen={rightbarOpen}
        onNewSession={() => void useAppStore.getState().newSession()}
      />
      <ChatView />
```

`new-session` may come from `useAppStore` already in `App`; use whichever keeps the component prop-free of a store import. Remove the now-unused `PanelLeftIcon` import from `App.tsx` (`noUnusedLocals` fails the build otherwise). Keep `App.module.css`'s top-strip rules only if still referenced; delete dead classes.

- [ ] **Step 6: Add the locale keys**

Both dictionaries in `desktop/src/i18n/locale.ts`:

| key | zh | en |
|---|---|---|
| `topbar.rename` | `重命名会话标题` | `Rename session title` |
| `topbar.title` | `会话标题` | `Session title` |
| `topbar.untitled` | `未命名会话` | `Untitled session` |
| `topbar.expandSidebar` | `展开侧栏` | `Expand sidebar` |
| `topbar.new` | `新会话` | `New session` |
| `topbar.more` | `更多操作` | `More actions` |
| `topbar.undo` | `撤销上一轮` | `Undo last turn` |
| `topbar.compact` | `压缩上下文` | `Compact context` |
| `topbar.inspector` | `切换检查器` | `Toggle inspector` |

- [ ] **Step 7: Run the topbar tests and the full gate**

Run: `cd desktop && pnpm e2e --grep "topbar" && pnpm test && pnpm build && pnpm e2e`
Expected: the two topbar tests pass, unit tests pass, build succeeds, and the full e2e suite stays green.

- [ ] **Step 8: Commit**

```bash
git add desktop/src/ui/ConversationTopbar.tsx desktop/src/ui/ConversationTopbar.module.css desktop/src/ui/icons.tsx desktop/src/App.tsx desktop/src/i18n/locale.ts desktop/e2e/app.spec.ts
git commit -m "feat(desktop): conversation topbar with inline rename"
```

---

### Task 4: Scoped, grouped sidebar with session menus and import/export

**Files:**
- Modify: `desktop/src/ui/Sidebar.tsx`
- Modify: `desktop/src/ui/Sidebar.module.css`
- Create: `desktop/src/ui/NoticeBanner.tsx`
- Create: `desktop/src/ui/NoticeBanner.module.css`
- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/i18n/locale.ts`
- Modify: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `sessionGroups`, `SessionScope`, `armDelete`, `deleteArmed`, `pickSessionFileToOpen`, `useNoticeStore`, `useSessionStore.scope/setScope`, `refreshSessions(scope)`, `renameSession`, `togglePin`, `importSession`, `exportSession`.
- Produces: sidebar rows with `data-session-path={path}`, a `data-action="menu"` trigger, `aria-disabled` on cross-project rows, group labels with `data-project-group={project}`, scope buttons `aria-label`/text from `sidebar.scope.project` and `sidebar.scope.all`, footer buttons `sidebar.import` / `sidebar.export`, and a dismissible notice banner.
- New locale keys: `sidebar.scope.project`, `sidebar.scope.all`, `sidebar.actions`, `sidebar.import`, `sidebar.export`, `sidebar.crossProject`, `session.rename`, `session.unpin`, `session.deleteArmed`, `session.exportMarkdown`, `session.exportHtml`, `session.exported`, `session.running`, `session.pending`, `notice.dismiss`.

- [ ] **Step 1: Extend the e2e bridge and write the failing sidebar tests**

Bridge changes:
- Add `import assert from 'node:assert/strict'` to `desktop/e2e/app.spec.ts` (the new tests use it).
- `sessions(id, scope)` returns `a` + `b` for `project`, and additionally `c` with `project: 'q'`, `title: '另一个项目'`, `path: '/sessions/c.jsonl'` for `all`; the `session` `list` case passes `payload.scope` through.
- Add a `session` case for `export` that mirrors the real server: source is `payload.path`, output is the same stem with the format suffix. Return `{ type: 'error', message: '导出失败' }` when `state.failExport` is true, else `{ type: 'result', id, ok: true, path: String(payload.path).replace(/\.jsonl$/, payload.format === 'html' ? '.html' : '.md') }`.
- Add `failExport?: boolean` to `BridgeState`.
- `initialState` leaves it undefined.

Tests:

```ts
test('the all scope groups projects and refuses cross-project opens', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '全部' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'list' && call.scope === 'all'),
      ),
    )
    .toBe(true)
  await expect(page.locator('[data-project-group="q"]')).toBeVisible()
  const cross = page.locator('[data-session-path="/sessions/c.jsonl"]')
  await expect(cross).toHaveAttribute('aria-disabled', 'true')
  // aria-disabled rows fail Playwright's actionability check; force the pointer.
  await cross.click({ force: true })
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'resume'),
    ),
    false,
  )
})

test('the current row menu pins and renames; other rows omit both actions', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '置顶' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'pin' && call.argument === 'on',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '重命名' }).click()
  const input = page.locator('[data-session-path="/sessions/a.jsonl"] input')
  await input.fill('行内标题')
  await input.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '行内标题',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '置顶' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: '重命名' })).toHaveCount(0)
})

test('delete needs two clicks on the same row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '删除' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'delete'),
    ),
    false,
  )
  // A scope switch drops the arm: reopen and the label is plain delete again.
  await page.getByRole('button', { name: '全部' }).click()
  await page.getByRole('button', { name: '当前项目' }).click()
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '删除？' })).toHaveCount(0)
  await page.getByRole('menuitem', { name: '删除' }).click()
  await page.getByRole('menuitem', { name: '删除？' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'delete' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)
})

test('the footer opens a session file and exports a chosen format', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: '/tmp/imported.jsonl' }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'import' && call.path === '/tmp/imported.jsonl',
        ),
      ),
    )
    .toBe(true)

  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 HTML' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) =>
            call.type === 'session' &&
            call.op === 'export' &&
            call.path === '/sessions/a.jsonl' &&
            call.format === 'html',
        ),
      ),
    )
    .toBe(true)
  await expect(page.getByRole('alert')).toHaveText('已导出到 /sessions/a.html')
})

test('a failed export surfaces in the notice banner', async ({ page }) => {
  await boot(page, initialState({ failExport: true }))
  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 Markdown' }).click()
  await expect(page.getByRole('alert')).toHaveText('导出失败')
})

test('a cancelled open dialog sends no import', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: null }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'import'),
    ),
    false,
  )
})
```

Replace the old fork test with a menu assertion:

```ts
test('fork is only offered on the current session row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(1)
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(0)
})
```

- [ ] **Step 2: Run the sidebar tests to verify they fail**

Run: `cd desktop && pnpm e2e --grep "scope|row menu|two clicks|footer|fork"`
Expected: FAIL — no scope control, row menu or footer import/export exists.

- [ ] **Step 3: Implement the notice banner**

`useNoticeStore` drives it: render nothing when `notice === null`; otherwise a `role="alert"` row with the text and a dismiss button (`aria-label` = `notice.dismiss`, `XIcon`). Place it in `App.tsx` between the topbar and `ChatView`, full width, `flex: none`, using `--ds-font-12`; `error` uses `background: var(--dsw-alias-interactive-bg-hover-danger)` with `color: var(--dsw-alias-state-error-primary)`, `info` uses `--dsw-alias-bg-layer-2`.

- [ ] **Step 4: Implement the sidebar sections and rows**

- Read `scope`, `setScope`, `refreshSessions`, `busy` (from `useAppStore`), `status` (from `useStatusStore`, for `currentPending(status)`) and the current session's project (`sessions.find((s) => s.path === currentPath)?.project ?? null`).
- Scope toggle above the search field: two buttons, `aria-pressed`, labels `sidebar.scope.project` / `sidebar.scope.all`. On select: `setScope(next); void refreshSessions(next)`.
- Compute `const { pinned, groups } = sessionGroups(sessions, scope, query)` in a `useMemo`.
- Render the pinned label only when `pinned.length > 0`. In project scope the rest renders headerless (the topbar already names the project); in all scope each group renders a label `group.project || t('sidebar.sessions')` with `data-project-group={group.project}`, then its rows.
- Row props add `currentProject: string | null`, `menuOpen`, `armed`, `editing`, and callbacks. Compute `crossProject = !current && currentProject !== null && session.project !== currentProject`.
- `SessionRow` root gets `data-session-path={path}`, `aria-disabled={crossProject || undefined}`, and click/keyboard open is a no-op when cross-project; its `title` shows `t('sidebar.crossProject')` for those rows. Subtitle keeps `messages · model · age` and adds the project label when `scope === 'all'`.
- The trailing action is one `data-action="menu"` button (`MoreIcon`, `aria-label` = `sidebar.actions`); it calls `event.stopPropagation()` so it never opens the session. Hover-reveal it with `opacity` only (never `display` or `visibility`), and reveal it on `:focus-visible` and while its row's menu is open — a `display: none` trigger is unclickable for both Playwright and keyboard users. The menu panel (`role="menu"`) is positioned with `position: fixed` from the trigger's `getBoundingClientRect()` and rendered through `createPortal(..., document.body)` — the session list scrolls, so an in-flow menu would clip. It contains: rename (current only), pin/unpin (current only), fork (current only, still gated by `canForkSession`), delete (every row, two-step).
- Rename, pin and fork close the menu on click. Delete's first click keeps the menu open and swaps the label to `session.deleteArmed`; the second click deletes and closes. Escape and outside pointerdown close the menu and clear the arm; switching scope clears the arm.
- Row rename: choosing rename replaces the title with an `<input>` (`autoFocus`); Enter commits a non-empty changed value, Escape/blur cancels. The input's click and keydown call `stopPropagation()` so it never opens the session; Escape also stops propagation past App's cancel shortcut. Use the same settled-ref guard as the topbar.
- Pin label: `session.pin` when unpinned, `session.unpin` when pinned; action calls `togglePin(session.pinned)`.
- Delete label: `session.delete` or `session.deleteArmed` when `deleteArmed(armedPath, path)`. First click sets `armedPath = armDelete(armedPath, path)`; a second click deletes and clears the arm.
- Add current-row cues: a `StateDot state="running"` with `title={t('session.running')}` when `current && busy`, and `t('session.pending', { count })` when `current && currentPending(status).length > 0`.

- [ ] **Step 5: Implement the footer import/export**

Keep the theme button and add two controls:
- `sidebar.import`: `FolderIcon` glyph button, `pickSessionFileToOpen()`; on a non-null path call `importSession(path)`. A cancelled picker returns `null` and sends nothing.
- `sidebar.export`: `FileIcon` glyph button that opens a small `role="menu"` panel above it with two items, `session.exportMarkdown` and `session.exportHtml`. Choosing one calls `exportSession('md' | 'html')`; when it returns a path, `useNoticeStore.getState().show({ kind: 'info', text: t('session.exported', { path }) })`. Close the menu on choose, Escape and outside pointerdown.

The export menu exists because `session op=export` chooses the destination itself (next to the session file) and accepts no path. Failures are already routed to the notice store by Task 2.

- [ ] **Step 6: Add the locale keys**

Add both zh/en entries for every key listed in **Interfaces**. Copy, in that key order: zh `当前项目`/`全部`/`会话操作`/`打开会话文件…`/`导出会话`/`属于其他项目，无法在这个窗口打开`/`重命名`/`取消置顶`/`删除？`/`导出为 Markdown`/`导出为 HTML`/`已导出到 {path}`/`运行中`/`{count} 条待处理`/`关闭`; en `This project`/`All`/`Session actions`/`Open session file…`/`Export session`/`Another project — cannot open here`/`Rename`/`Unpin`/`Delete?`/`Export as Markdown`/`Export as HTML`/`Exported to {path}`/`Running`/`{count} pending`/`Dismiss`.

- [ ] **Step 7: Run the sidebar tests and the suite**

Run: `cd desktop && pnpm e2e && pnpm test && pnpm build`
Expected: all e2e (including the existing queue/attach tests) and unit tests pass; build succeeds.

- [ ] **Step 8: Commit**

```bash
git add desktop/src/ui/Sidebar.tsx desktop/src/ui/Sidebar.module.css desktop/src/ui/NoticeBanner.tsx desktop/src/ui/NoticeBanner.module.css desktop/src/App.tsx desktop/src/i18n/locale.ts desktop/e2e/app.spec.ts
git commit -m "feat(desktop): scoped grouped sidebar with session menus"
```

---

### Task 5: ⌘K palette for sessions and commands

**Files:**
- Create: `desktop/src/state/palette.ts`
- Create: `desktop/src/ui/palette/CommandPalette.tsx`
- Create: `desktop/src/ui/palette/CommandPalette.module.css`
- Modify: `desktop/src/state/stores.ts`
- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/ui/ConversationTopbar.tsx`
- Modify: `desktop/src/i18n/locale.ts`
- Modify: `desktop/e2e/app.spec.ts`
- Test: `desktop/tests/palette.test.ts`

**Interfaces:**
- Consumes: `compareSessions`, `SessionSummary`, `CommandEntry`, `useSessionStore`, `useCatalogStore`, `useComposerStore`, `nextDraft`, `useAppStore` (`resumeSession`, `runCommand`), `usePaletteStore`.
- Produces:
  - `type PaletteCommandMode = 'run' | 'insert'`
  - `COMMAND_POLICY: Record<string, PaletteCommandMode>`
  - `paletteResults(sessions, commands, query, currentProject: string | null): { sessions: PaletteSessionHit[]; commands: PaletteCommandHit[] }` where session hits are `{ session, disabled }` and command hits are `{ name, description, mode }`.
  - `nextPaletteIndex(options: { disabled: boolean }[], from: number, delta: 1 | -1): number`
  - `usePaletteStore` with `{ open, query, openPalette(), closePalette(), setQuery(query) }`.
  - Palette DOM: `role="dialog"` named `palette.title`, input `role="combobox"` named `palette.placeholder`, options `role="option"` with `aria-selected`/`aria-disabled`.

- [ ] **Step 1: Write the failing palette tests**

```ts
// desktop/tests/palette.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { COMMAND_POLICY, nextPaletteIndex, paletteResults } from '../src/state/palette.ts'
import type { CommandEntry, SessionSummary } from '../src/rpc/types.ts'

const make = (path: string, extra: Partial<SessionSummary> = {}): SessionSummary => ({
  path,
  name: path,
  title: path,
  project: 'alpha',
  messages: 1,
  current: false,
  pinned: false,
  updated_at: 1,
  ...extra,
})

const commands: CommandEntry[] = [
  { name: 'compact', description: 'Compact context' },
  { name: 'rename', description: 'Rename session' },
  { name: 'quit', description: 'Quit' },
  { name: 'sessions', description: 'Session picker' },
]

test('sessions match title/name/project and are disabled off-project', () => {
  const hits = paletteResults(
    [
      make('/s/a.jsonl', { title: 'Fix login' }),
      make('/s/b.jsonl', { title: 'Other', project: 'beta' }),
    ],
    [],
    'fix',
    'alpha',
  )
  assert.deepEqual(hits.sessions.map((hit) => [hit.session.path, hit.disabled]), [['/s/a.jsonl', false]])

  const all = paletteResults(
    [make('/s/a.jsonl'), make('/s/b.jsonl', { project: 'beta', updated_at: 2 })],
    [],
    '',
    'alpha',
  )
  assert.deepEqual(all.sessions.map((hit) => [hit.session.path, hit.disabled]), [
    ['/s/b.jsonl', true],
    ['/s/a.jsonl', false],
  ])
})

test('only allowlisted commands surface, with run/insert modes', () => {
  const hits = paletteResults([], commands, '', 'alpha')
  assert.deepEqual(hits.commands.map((hit) => [hit.name, hit.mode]), [
    ['compact', 'run'],
    ['rename', 'insert'],
  ])
  assert.equal(COMMAND_POLICY.quit, undefined)
  assert.equal(COMMAND_POLICY.sessions, undefined)
})

test('command search matches name and description', () => {
  assert.deepEqual(paletteResults([], commands, 'RENAME', 'alpha').commands.map((hit) => hit.name), ['rename'])
  assert.deepEqual(paletteResults([], commands, 'compact', 'alpha').commands.map((hit) => hit.name), ['compact'])
})

test('nextPaletteIndex skips disabled rows and wraps', () => {
  const options = [{ disabled: false }, { disabled: true }, { disabled: false }]
  assert.equal(nextPaletteIndex(options, 0, 1), 2)
  assert.equal(nextPaletteIndex(options, 2, 1), 0)
  assert.equal(nextPaletteIndex(options, -1, 1), 0)
  assert.equal(nextPaletteIndex(options, -1, -1), 2)
  assert.equal(nextPaletteIndex(options, 0, -1), 2)
  assert.equal(nextPaletteIndex([{ disabled: true }], -1, 1), -1)
  assert.equal(nextPaletteIndex([], -1, 1), -1)
})
```

- [ ] **Step 2: Run the palette test to verify it fails**

Run: `cd desktop && pnpm test`
Expected: FAIL — `../src/state/palette.ts` does not exist.

- [ ] **Step 3: Implement the palette helper**

```ts
// desktop/src/state/palette.ts
import type { CommandEntry, SessionSummary } from '../rpc/types'
import { compareSessions } from './sessionGroups'

export type PaletteCommandMode = 'run' | 'insert'

/** Zero-argument safe commands run; argument commands insert their prefix. */
export const COMMAND_POLICY: Record<string, PaletteCommandMode> = {
  compact: 'run',
  undo: 'run',
  retry: 'run',
  new: 'run',
  fork: 'run',
  context: 'run',
  tools: 'run',
  doctor: 'run',
  jobs: 'run',
  dirs: 'run',
  hotkeys: 'run',
  rename: 'insert',
  pin: 'insert',
  export: 'insert',
  image: 'insert',
  search: 'insert',
  open: 'insert',
  urls: 'insert',
  copy: 'insert',
  btw: 'insert',
  'add-dir': 'insert',
  'remove-dir': 'insert',
  handoff: 'insert',
  trace: 'insert',
}

export interface PaletteSessionHit {
  session: SessionSummary
  disabled: boolean
}

export interface PaletteCommandHit {
  name: string
  description: string
  mode: PaletteCommandMode
}

export interface PaletteResults {
  sessions: PaletteSessionHit[]
  commands: PaletteCommandHit[]
}

export function paletteResults(
  sessions: SessionSummary[],
  commands: CommandEntry[],
  query: string,
  currentProject: string | null,
): PaletteResults {
  const needle = query.trim().toLowerCase()
  const sessionHits = sessions
    .filter((session) =>
      [session.title, session.name, session.project].some((value) =>
        (value ?? '').toLowerCase().includes(needle),
      ),
    )
    .sort(compareSessions)
    .map((session) => ({
      session,
      disabled: currentProject !== null && session.project !== currentProject,
    }))
  const commandHits = commands
    .filter((command) => COMMAND_POLICY[command.name] !== undefined)
    .filter(
      (command) =>
        command.name.toLowerCase().includes(needle) ||
        (command.description ?? '').toLowerCase().includes(needle),
    )
    .map((command) => ({
      name: command.name,
      description: command.description,
      mode: COMMAND_POLICY[command.name],
    }))
  return { sessions: sessionHits, commands: commandHits }
}

export function nextPaletteIndex(
  options: { disabled: boolean }[],
  from: number,
  delta: 1 | -1,
): number {
  if (options.length === 0) return -1
  let index = from < 0 ? (delta === 1 ? options.length - 1 : 0) : from
  for (let step = 0; step < options.length; step += 1) {
    index = (index + delta + options.length) % options.length
    if (!options[index].disabled) return index
  }
  return -1
}
```

- [ ] **Step 4: Run the palette test to verify it passes**

Run: `cd desktop && pnpm test`
Expected: PASS.

- [ ] **Step 5: Extend the e2e bridge and write the failing palette tests**

Bridge:
- `commands` returns `[ {name:'compact', description:'Compact context'}, {name:'rename', description:'Rename session'}, {name:'quit', description:'Quit'} ]`.
- Ensure `session` `resume` is recorded (the generic call log already records every payload).

Tests:

```ts
const paletteKey = process.platform === 'darwin' ? 'Meta+k' : 'Control+k'

test('the palette opens with the keyboard and resumes a matching session', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const dialog = page.getByRole('dialog', { name: '搜索' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('combobox').fill('另一个会话')
  await page.getByRole('option', { name: /另一个会话/ }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'resume' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)
  await expect(dialog).toBeHidden()
})

test('palette commands run bare or insert their prefix', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('compact')
  await page.keyboard.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'compact' && call.argument === '',
        ),
      ),
    )
    .toBe(true)

  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('rename')
  await page.keyboard.press('Enter')
  await expect(page.locator('textarea')).toHaveValue('/rename ')

  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('quit')
  await expect(page.getByRole('option', { name: /quit|Quit/ })).toHaveCount(0)
})

test('a cross-project palette result is disabled and Enter does nothing', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '全部' }).click()
  await page.keyboard.press(paletteKey)
  const input = page.getByRole('dialog').getByRole('combobox')
  await input.fill('另一个项目')
  const option = page.getByRole('option', { name: /另一个项目/ })
  await expect(option).toHaveAttribute('aria-disabled', 'true')
  // aria-disabled options are unclickable under Playwright's actionability check.
  await option.click({ force: true })
  await page.keyboard.press('Enter')
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'resume'),
    ),
    false,
  )
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
})
```

- [ ] **Step 6: Run the palette e2e tests to verify they fail**

Run: `cd desktop && pnpm e2e --grep "palette"`
Expected: FAIL — no dialog exists.

- [ ] **Step 7: Add the palette store and overlay**

Store in `desktop/src/state/stores.ts`:

```ts
interface PaletteState {
  open: boolean
  query: string
  openPalette(): void
  closePalette(): void
  setQuery(query: string): void
}

export const usePaletteStore = create<PaletteState>((set) => ({
  open: false,
  query: '',
  openPalette: () => set({ open: true, query: '' }),
  closePalette: () => set({ open: false, query: '' }),
  setQuery: (query) => set({ query }),
}))
```

`CommandPalette.tsx`:
- Return null when closed. Otherwise a fixed full-viewport backdrop (`background: var(--dsw-alias-bg-mask-1)`) centered panel (`width: 560px`, `max-height: 60vh`, `border-radius: var(--ds-radius-14)`, `background: var(--dsw-specific-menu)`, `box-shadow: var(--dsw-elevation-prominent)`, `backdrop-filter: var(--dsw-menu-backdrop-filter)`).
- Input at top with `SearchIcon`, placeholder `palette.placeholder`, `autoFocus`; value from the store.
- Results from `paletteResults(sessions, commands, query, currentProject)` where `currentProject = sessions.find((s) => s.path === currentPath)?.project ?? null`. The palette searches the list the sidebar already loaded, so cross-project hits appear only while the all scope is active — no extra `session op=list` per keystroke. Import `nextDraft` from `../../state/pending`.
- Flat options: sessions first (each `{ kind: 'session', hit }`), then commands. Keep a local `active` index; reset it to `nextPaletteIndex(flat, -1, 1)` whenever `query` or the result contents change. ArrowDown/ArrowUp move with `nextPaletteIndex`; Enter activates the active option — when `active` is still `-1` but results exist, resolve it with `nextPaletteIndex(flat, -1, 1)` first, so a fast Enter after typing never misses; Escape closes. Handle these on the dialog `onKeyDown`, call `event.stopPropagation()` for Escape so App's cancel shortcut does not also fire.
- Render group labels `palette.sessions` and `palette.commands` only when the group is non-empty. Each option: `role="option"`, `aria-selected`, `aria-disabled` for cross-project, `data-active` styling, title/subtitle; session rows show title + `project · model`; command rows show `/name` + description. Disabled session rows also render `palette.crossProject` as a hint.
- Activation:
  - Session enabled: `closePalette()` then `void resumeSession(path)`. Current session: just close.
  - Command `run`: `closePalette()` then `void runCommand(name, '')`.
  - Command `insert`: `useComposerStore.getState().setDraft(nextDraft(useComposerStore.getState().draft, `/${name} `))`, close, then focus the composer textarea on the next frame.
  - Disabled session: no-op that also leaves the active index untouched, so Enter after clicking a disabled row does not fall through to another option.
- Empty results: a `palette.empty` row.

- [ ] **Step 8: Wire the shortcut and topbar button**

In `App.tsx`:
- Render `<CommandPalette />` after the frame by wrapping the existing return in a fragment (`<><AppFrame …>…</AppFrame><CommandPalette /></>`).
- In the global keydown handler, before the Esc branch:

```ts
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        usePaletteStore.getState().openPalette()
      }
      if (usePaletteStore.getState().open) return
```

The early return keeps Esc owned by the overlay.

In `ConversationTopbar.tsx`, add a search button (`SearchIcon`, `aria-label` = `topbar.search`, tooltip `⌘K`) before the new-session button, calling `usePaletteStore.getState().openPalette()`.

- [ ] **Step 9: Add the locale keys**

| key | zh | en |
|---|---|---|
| `topbar.search` | `搜索会话和命令` | `Search sessions and commands` |
| `palette.title` | `搜索` | `Search` |
| `palette.placeholder` | `搜索会话和命令` | `Search sessions and commands` |
| `palette.sessions` | `会话` | `Sessions` |
| `palette.commands` | `命令` | `Commands` |
| `palette.empty` | `没有匹配项` | `No matches` |
| `palette.crossProject` | `属于其他项目，无法在当前窗口打开` | `Another project — cannot open here` |
| `palette.hint` | `↑↓ 选择 · Enter 打开 · Esc 关闭` | `↑↓ to move · Enter to open · Esc to close` |

- [ ] **Step 10: Verify the whole gate**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add desktop/src/state/palette.ts desktop/src/state/stores.ts desktop/src/ui/palette/CommandPalette.tsx desktop/src/ui/palette/CommandPalette.module.css desktop/src/App.tsx desktop/src/ui/ConversationTopbar.tsx desktop/src/i18n/locale.ts desktop/e2e/app.spec.ts desktop/tests/palette.test.ts
git commit -m "feat(desktop): command palette for sessions and commands"
```
