# Desktop Parity Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four high-frequency gaps between the Tauri desktop and the zen RPC contract: correct fork affordance, actionable pending queue, native image picker, and a mocked-IPC browser e2e plus CI.

**Architecture:** The desktop stays a pure RPC client. Three items are frontend-only; the pending queue needs three small `session op` handlers that expose `ChatController` methods already used by the TUI. The e2e drives the real React app in Chromium with a scripted `window.__TAURI_INTERNALS__`, so it tests the shell without a `zen` binary.

**Tech Stack:** React 18 + zustand + Vite 6 + TypeScript strict; Tauri v2 dialog plugin; Python RPC server; Playwright; GitHub Actions.

**Spec:** No separate spec. This plan implements the follow-up list agreed after `6a101e4`, bounded by “capabilities follow the existing RPC”.

## Global Constraints

- Only expose existing backend behavior over RPC; do not invent new run/session semantics.
- `desktop/` production changes are TypeScript strict with `noUnusedLocals`/`noUnusedParameters`.
- Unit tests use `node --test tests/*.test.ts` (Node native TS strip); no test framework added for unit tests.
- Backend tests run with `uv run pytest`.
- Do not push; commits stay local.
- Every production change starts with a failing test, except Playwright config/CI scaffolding.

## Review Focus

- `session op promote/dequeue/clear_pending` with `chat is None` → returns an error frame, never crashes the server.
- `promote` on an entry with images → `blocked_images`, and the entry stays in the queue lane.
- `dequeue` with no queued entries → `{dequeued: false, text: null}`, no draft mutation.
- Native dialog cancelled (`null`) or non-image files → composer unchanged.
- Sidebar fork on a non-current row → button absent, so the RPC cannot fork the wrong session.

---

### Task 1: Fork affordance only on the current session

**Files:**
- Create: `desktop/src/state/sessionSelectors.ts`
- Test: `desktop/tests/sessionSelectors.test.ts`
- Modify: `desktop/src/ui/Sidebar.tsx:19,90,109,136,183`

**Interfaces:**
- Produces: `canForkSession(currentPath: string | null, sessionPath: string): boolean`

- [ ] **Step 1: Write the failing test**

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canForkSession } from '../src/state/sessionSelectors.ts'

test('fork is offered only for the current session', () => {
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/a.jsonl'), true)
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/b.jsonl'), false)
  assert.equal(canForkSession(null, '/sessions/a.jsonl'), false)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && node --test tests/sessionSelectors.test.ts`
Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `sessionSelectors.ts`.

- [ ] **Step 3: Implement the selector and gate the row button**

`sessionSelectors.ts`:

```ts
/** `session op fork` always copies the current session; offering the button on
 * another row would fork the wrong transcript. */
export function canForkSession(currentPath: string | null, sessionPath: string): boolean {
  return currentPath !== null && sessionPath === currentPath
}
```

In `Sidebar.tsx`, pass `onFork={canForkSession(currentPath, session.path) ? () => void forkSession() : undefined}` in both row maps. Change `SessionRow`'s prop to `onFork?: () => void` and render the fork button only when `props.onFork !== undefined`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd desktop && node --test tests/sessionSelectors.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/sessionSelectors.ts desktop/tests/sessionSelectors.test.ts desktop/src/ui/Sidebar.tsx
git commit -m "fix(desktop): only offer fork on the current session"
```

---

### Task 2: Pending queue actions over RPC and in the composer

**Files:**
- Modify: `zenagent/cli/rpc.py:630-725` (`_session_op`)
- Test: `tests/test_rpc_v2.py`
- Create: `desktop/src/state/pending.ts`
- Test: `desktop/tests/pending.test.ts`
- Modify: `desktop/src/state/appStore.ts:21-48,327-352`
- Modify: `desktop/src/ui/composer/Docks.tsx`
- Modify: `desktop/src/ui/composer/Docks.module.css`

**Interfaces:**
- Produces (RPC): `{"type":"session","op":"promote","pending_id":<int>}` → `{outcome: "ok"|"blocked_images"|"not_running"|"missing"}`; `{"op":"dequeue"}` → `{dequeued: boolean, text: string|null}`; `{"op":"clear_pending"}` → `{cleared: number}`.
- Produces (frontend): `canPromotePending(item, running)`, `canDequeuePending(items)`, `nextDraft(previous, taken)`; store actions `promotePending(id)`, `dequeuePending()`, `clearPending()`.

- [ ] **Step 1: Write the failing backend tests**

Append to `tests/test_rpc_v2.py`:

```python
async def test_session_pending_ops_dequeue_and_clear(runtime_factory):
    _runtime, server, writes, queue = await build(runtime_factory)
    server.chat.queue_follow_up("first")
    server.chat.queue_follow_up("second")
    await run_lines(
        server,
        queue,
        [
            {"type": "session", "id": "d", "op": "dequeue"},
            {"type": "session", "id": "c", "op": "clear_pending"},
        ],
    )
    results = {message["id"]: message for message in decode(writes)}
    assert results["d"]["ok"] is True
    assert results["d"]["dequeued"] is True
    assert results["d"]["text"] == "second"
    assert results["c"]["cleared"] == 1


async def test_session_promote_moves_a_queued_item_to_steering(runtime_factory):
    runtime, server, writes, _queue = await build(
        runtime_factory, stream_fn=await slow_stream()
    )
    runtime.agent.replace_messages([UserMessage(content="hello", timestamp=1)])
    await server._handle_line(json.dumps({"type": "prompt", "id": "p", "text": "hi"}))
    await asyncio.sleep(0.05)
    server.chat.queue_follow_up("promote me")
    pending_id = next(
        item.id for item in server.chat.status.pending if item.lane == "queue"
    )
    await server._handle_line(
        json.dumps({
            "type": "session",
            "id": "m",
            "op": "promote",
            "pending_id": pending_id,
        })
    )
    payload = next(message for message in decode(writes) if message.get("id") == "m")
    assert payload["ok"] is True
    assert payload["outcome"] == "ok"
    assert [item.lane for item in server.chat.status.pending] == ["steer"]
    server.agent.abort()
    await asyncio.gather(server._task, return_exceptions=True)


async def test_session_promote_requires_an_id(runtime_factory):
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(server, queue, [{"type": "session", "id": "m", "op": "promote"}])
    payload = decode(writes)[0]
    assert payload["type"] == "error"
    assert payload["message"] == "promote needs pending_id"
```

- [ ] **Step 2: Run backend tests to verify they fail**

Run: `uv run pytest tests/test_rpc_v2.py -q -k "pending_ops or promote"`
Expected: FAIL with `unknown session op`.

- [ ] **Step 3: Implement the three RPC ops**

Insert before the `raise ValueError(f"unknown session op: {op}")` in `_session_op`:

```python
        if op == "promote":
            if self.chat is None:
                raise RuntimeError("promote needs a runtime")
            pending_id = request.get("pending_id")
            if not isinstance(pending_id, int):
                raise ValueError("promote needs pending_id")
            return {"outcome": self.chat.promote(pending_id)}
        if op == "dequeue":
            if self.chat is None:
                raise RuntimeError("dequeue needs a runtime")
            text = self.chat.dequeue_follow_up()
            return {"dequeued": text is not None, "text": text}
        if op == "clear_pending":
            if self.chat is None:
                raise RuntimeError("clear_pending needs a runtime")
            return {"cleared": self.chat.clear_pending()}
```

- [ ] **Step 4: Run backend tests to verify they pass**

Run: `uv run pytest tests/test_rpc_v2.py -q -k "pending_ops or promote"`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend tests**

`desktop/tests/pending.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canDequeuePending, canPromotePending, nextDraft } from '../src/state/pending.ts'
import type { PendingMessage } from '../src/rpc/types.ts'

const queued = (id: number, images = 0): PendingMessage => ({
  id,
  lane: 'queue',
  text: `queued ${id}`,
  images,
  created_at: 1,
})

test('only a running, image-free queued item can be promoted', () => {
  assert.equal(canPromotePending(queued(1), true), true)
  assert.equal(canPromotePending(queued(1), false), false)
  assert.equal(canPromotePending(queued(1, 2), true), false)
  assert.equal(canPromotePending({ ...queued(1), lane: 'steer' }, true), false)
})

test('dequeue is offered for the latest queued item only when it has no images', () => {
  assert.equal(canDequeuePending([queued(1)]), true)
  assert.equal(canDequeuePending([queued(1), queued(2, 1)]), false)
  assert.equal(canDequeuePending([{ ...queued(1), lane: 'steer' }]), false)
  assert.equal(canDequeuePending([]), false)
})

test('a taken-back message appends to the draft instead of replacing it', () => {
  assert.equal(nextDraft('', 'taken'), 'taken')
  assert.equal(nextDraft('draft', 'taken'), 'draft\ntaken')
})
```

- [ ] **Step 6: Run frontend tests to verify they fail**

Run: `cd desktop && node --test tests/pending.test.ts`
Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 7: Implement the helpers and store actions**

`desktop/src/state/pending.ts`:

```ts
import type { PendingMessage } from '../rpc/types'

export function canPromotePending(item: PendingMessage, running: boolean): boolean {
  return running && item.lane === 'queue' && item.images === 0
}

export function canDequeuePending(items: PendingMessage[]): boolean {
  const latest = [...items].reverse().find((item) => item.lane === 'queue')
  return latest !== undefined && latest.images === 0
}

export function nextDraft(previous: string, taken: string): string {
  return previous.trim() === '' ? taken : `${previous}\n${taken}`
}
```

In `appStore.ts`, add the three signatures after `deleteSession` and implement near `deleteSession`:

```ts
  async promotePending(id) {
    try {
      await get().client().send({ type: 'session', op: 'promote', pending_id: id })
    } catch {
      return
    }
    await get().refreshStatus()
  },

  async dequeuePending() {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'dequeue' })
    } catch {
      return
    }
    if (
      frame.type === 'result' &&
      frame.ok &&
      frame.dequeued === true &&
      typeof frame.text === 'string'
    ) {
      const composer = useComposerStore.getState()
      composer.setDraft(nextDraft(composer.draft, frame.text))
    }
    await get().refreshStatus()
  },

  async clearPending() {
    try {
      await get().client().send({ type: 'session', op: 'clear_pending' })
    } catch {
      return
    }
    await get().refreshStatus()
  },
```

- [ ] **Step 8: Wire the dock actions**

In `Docks.tsx`, import `useAppStore` and the helpers. `PendingDock` reads `running = useStatusStore((state) => state.status?.running ?? false)` and the three actions. Pass to each `PendingGroup`:

```tsx
<PendingGroup
  lane="steer"
  label={t('composer.steering')}
  items={steering}
  onClear={() => void clearPending()}
/>
<PendingGroup
  lane="queue"
  label={t('composer.queue')}
  items={queued}
  onPromote={
    queued[0] !== undefined && canPromotePending(queued[0], running)
      ? () => void promotePending(queued[0].id)
      : undefined
  }
  onDequeue={canDequeuePending(pending) ? () => void dequeuePending() : undefined}
  onClear={() => void clearPending()}
/>
```

`PendingGroup` renders optional text buttons after `.pendingText`:

```tsx
{props.onPromote !== undefined && (
  <button type="button" className={css.pendingAction} onClick={props.onPromote}>
    {t('queue.promote')}
  </button>
)}
{props.onDequeue !== undefined && (
  <button type="button" className={css.pendingAction} onClick={props.onDequeue}>
    {t('queue.dequeue')}
  </button>
)}
{props.onClear !== undefined && (
  <button type="button" className={css.pendingAction} onClick={props.onClear}>
    {t('queue.clear')}
  </button>
)}
```

In `Docks.module.css`, add:

```css
.pendingAction {
  flex: none;
  padding: 0;
  border: none;
  background: none;
  color: var(--dsw-alias-label-tertiary);
  font-size: 11px;
  cursor: pointer;
}

.pendingAction:hover {
  color: var(--dsw-alias-label-primary);
}
```

- [ ] **Step 9: Run all checks for the task**

Run: `cd desktop && node --test tests/pending.test.ts && pnpm test && pnpm build`
Expected: PASS and build succeeds.

Run: `uv run pytest tests/test_rpc_v2.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add zenagent/cli/rpc.py tests/test_rpc_v2.py desktop/src/state/pending.ts desktop/tests/pending.test.ts desktop/src/state/appStore.ts desktop/src/ui/composer/Docks.tsx desktop/src/ui/composer/Docks.module.css
git commit -m "feat(desktop): pending queue actions over rpc"
```

---

### Task 3: Composer image picker through the native dialog

**Files:**
- Create: `desktop/src/ui/composer/pickImages.ts`
- Test: `desktop/tests/pickImages.test.ts`
- Modify: `desktop/src/ui/composer/InputBar.tsx:47,207-229`

**Interfaces:**
- Produces: `IMAGE_EXTENSIONS: string[]`, `imagePathsFromSelection(selection: string | string[] | null): string[]`, `pickImagePaths(): Promise<string[]>`.

- [ ] **Step 1: Write the failing test**

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { imagePathsFromSelection } from '../src/ui/composer/pickImages.ts'

test('keeps image paths and drops everything else', () => {
  assert.deepEqual(
    imagePathsFromSelection(['/tmp/a.png', '/tmp/b.txt', '/tmp/c.JPEG']),
    ['/tmp/a.png', '/tmp/c.JPEG'],
  )
})

test('a single selection and a cancelled dialog are both handled', () => {
  assert.deepEqual(imagePathsFromSelection('/tmp/a.webp'), ['/tmp/a.webp'])
  assert.deepEqual(imagePathsFromSelection(null), [])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && node --test tests/pickImages.test.ts`
Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement the picker**

`pickImages.ts`:

```ts
import { invoke } from '@tauri-apps/api/core'

export const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']

const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i

export function imagePathsFromSelection(selection: string | string[] | null): string[] {
  if (selection === null) return []
  const paths = Array.isArray(selection) ? selection : [selection]
  return paths.filter((path) => IMAGE_RE.test(path))
}

export async function pickImagePaths(): Promise<string[]> {
  const selection = await invoke<string | string[] | null>('plugin:dialog|open', {
    options: {
      multiple: true,
      directory: false,
      title: 'ZenAgent',
      filters: [{ name: 'Images', extensions: IMAGE_EXTENSIONS }],
    },
  })
  return imagePathsFromSelection(selection)
}
```

- [ ] **Step 4: Replace the inert file input**

In `InputBar.tsx`, delete `fileInputRef` and the hidden `<input type="file" ...>`. The attach button becomes:

```tsx
onClick={() => {
  void pickImagePaths()
    .then((paths) => {
      if (paths.length > 0) addImages(paths)
    })
    .catch(() => {})
}}
```

- [ ] **Step 5: Run tests and build**

Run: `cd desktop && node --test tests/pickImages.test.ts && pnpm test && pnpm build`
Expected: PASS; no unused refs or imports.

- [ ] **Step 6: Commit**

```bash
git add desktop/src/ui/composer/pickImages.ts desktop/tests/pickImages.test.ts desktop/src/ui/composer/InputBar.tsx
git commit -m "feat(desktop): pick composer images with the native dialog"
```

---

### Task 4: Mocked-IPC browser e2e and desktop CI

**Files:**
- Modify: `desktop/package.json` (add Playwright dev dependency and `e2e` script)
- Create: `desktop/playwright.config.ts`
- Create: `desktop/e2e/app.spec.ts`
- Modify: `.github/workflows/ci.yml`
- Modify: `desktop/.gitignore`

**Interfaces:**
- Consumes: Task 1 fork gating, Task 2 pending actions, Task 3 dialog picker.
- Produces: `pnpm e2e` runs the app in Chromium with a scripted Tauri bridge.

- [ ] **Step 1: Install Playwright**

Run: `cd desktop && pnpm add -D @playwright/test && pnpm exec playwright install chromium`
Expected: `package.json`, `pnpm-lock.yaml` updated; Chromium available.

- [ ] **Step 2: Add the Playwright config**

`desktop/playwright.config.ts`:

```ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'pnpm dev --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
```

Add `"e2e": "playwright test"` to `package.json` scripts.

- [ ] **Step 3: Write the mock bridge and the specs**

`desktop/e2e/app.spec.ts` defines the bridge in a self-contained init function (Playwright serializes it) with this contract:

- `plugin:event|listen` → store the handler id and return an event id; `plugin:event|unlisten` → null.
- `rpc_start` → `{pid: 4242}`; `rpc_stop` → null.
- `rpc_send` → answer by `payload.type`/`payload.op`:
  - `status`: `{running, pending}` from the mutable mock state.
  - `session list`: two sessions, `/s/a.jsonl` titled `修复登录超时` (`current: true`) and `/s/b.jsonl` titled `另一个会话`.
  - `session promote`: remove the pending id, return `{outcome:'ok'}`.
  - `session dequeue`: pop the latest queue item, return `{dequeued:true, text}`.
  - `session clear_pending`: clear pending, return `{cleared:n}`.
  - `transcript`: empty; `models`/`commands`: empty.
- `plugin:dialog|open` → the state's `dialogSelection`.
- Every `rpc_send` payload is appended to `state.calls`.
- Expose the state as `window.__ZEN_E2E__` and stub `window.__TAURI_EVENT_PLUGIN_INTERNALS__.unregisterListener`.
- Also stub `window.__TAURI_INTERNALS__.transformCallback`/`unregisterCallback`.

Specs (each boots via the Welcome screen button `用当前目录启动`):

1. **Fork visibility**: `getByRole('button', { name: '从此分叉' })` has count 1 while only `/s/a.jsonl` is current.
2. **Promote**: set `pending = [{id:7, lane:'queue', text:'把测试补上', images:0}]`; wait for the text (status polls every 2s); click `升为插话`; assert `state.calls` contains `{type:'session', op:'promote', id:7}`; the dock disappears.
3. **Dequeue**: set `pending = [{id:8, lane:'queue', text:'取回我', images:0}]`; click `取回`; assert the textarea value is `取回我` and the dequeue call was sent.
4. **Clear**: set two pending items; click `清空`; assert the dock disappears and `{op:'clear_pending'}` was sent.
5. **Attach**: set `dialogSelection = ['/tmp/a.png', '/tmp/b.txt']`; click `添加图片`; assert chip `a.png` is visible and `b.txt` is absent.

- [ ] **Step 4: Run e2e to verify green**

Run: `cd desktop && pnpm e2e`
Expected: all specs PASS against the Vite app.

- [ ] **Step 5: Ignore Playwright artifacts**

Append to `desktop/.gitignore`:

```
test-results/
playwright-report/
blob-report/
playwright/.cache/
```

- [ ] **Step 6: Add the desktop CI jobs**

Append to `.github/workflows/ci.yml`:

```yaml
  desktop:
    name: desktop
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: desktop
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: pnpm
          cache-dependency-path: desktop/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
      - run: pnpm test
      - run: pnpm build
      - run: pnpm exec playwright install --with-deps chromium
      - run: pnpm e2e

  desktop-rust:
    name: desktop-rust
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Install Tauri Linux dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf
      - run: cargo check --manifest-path desktop/src-tauri/Cargo.toml
```

- [ ] **Step 7: Run the full gate**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`

Run: `bash scripts/check.sh`
Expected: both green.

- [ ] **Step 8: Commit**

```bash
git add desktop/package.json desktop/pnpm-lock.yaml desktop/playwright.config.ts desktop/e2e/app.spec.ts desktop/.gitignore .github/workflows/ci.yml
git commit -m "test(desktop): browser e2e over a mocked tauri bridge and ci"
```

---

## Verification

- [ ] `cd desktop && pnpm test`
- [ ] `cd desktop && pnpm build`
- [ ] `cd desktop && pnpm e2e`
- [ ] `uv run pytest tests/test_rpc_v2.py -q`
- [ ] `bash scripts/check.sh`
- [ ] `git status --short` shows only the two pre-existing untracked contract docs
