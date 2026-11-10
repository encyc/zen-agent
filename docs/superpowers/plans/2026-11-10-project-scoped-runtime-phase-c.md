# Project-Scoped Runtime — Phase C (frontend and shell) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Switching projects stops replacing the runtime process: the shell sends a `project` op to the resident host, paints the target's cached transcript immediately, and revalidates in the background.

**Architecture:** Phase B left one `zen rpc` host process with resident per-project slots. Phase C moves the desktop shell onto it: `RpcClient` carries the active project and tags routed requests; `openProject` sends `{type:'project', op:'switch'}` instead of `rpc_stop`/`rpc_start`; a client-side LRU keeps up to three transcript panes so returning to a project paints synchronously; approvals/asks are tagged with their project and only the active project's are rendered. The Rust shell keeps its single child; a crash still goes through the existing exit screen and `rpc_start` replace path — no `rpc_restart` is added.

**Tech Stack:** React + Zustand, Node 24 `node --test` with native TS type stripping, Playwright e2e with the scripted Tauri bridge (`desktop/src/dev/bridge.ts`). No Python changes.

**Spec:** `docs/research/project-scoped-runtime-audit.md` (Phase C section)

## Global Constraints

- Work only in `.worktrees/project-scoped-runtime`; never touch or commit the main worktree's uncommitted docs.
- RPC protocol stays `2`; every wire change is additive.
- No new dependencies. Run `pnpm install --frozen-lockfile` in `desktop/` before tests (the worktree has no `node_modules`).
- Tests must never write to the real `~/.zenagent` (desktop tests only touch the scripted bridge).
- English conventional commits; one commit per task. Do not commit unrelated files.
- No hover prefetch this phase (it would open background slots and churn the 4-slot cap). The LRU is the retained-pane parity piece.
- `openProject(path, null)` keeps the caller's `newSessionIn` contract: switch (or reuse) the project, leave the session choice to the following `session new`.
- A project refusal is a normal error frame: keep the old project active and the connection ready. Only a transport failure marks the connection exited.
- No Rust `rpc_restart`: `rpc_start` already replaces the single child, and the exit screen's retry is the crash path. Adding an unused shell command is YAGNI.

---

### Task 1: Client project context and frame filter

**Files:**
- Modify: `desktop/src/rpc/client.ts`
- Modify: `desktop/src/rpc/frames.ts`
- Modify: `desktop/src/rpc/types.ts` (`EventFrame.project`)
- Test: `desktop/tests/rpcClient.test.ts`
- Test: `desktop/tests/frames.test.ts`

**Interfaces:**
- Consumes: Phase B hello result (`active`, `projects`) and per-frame `project` tags from `RpcServer._send`.
- Produces:
  - `RpcClient.setProject(root: string | null): void`
  - `RpcClient.activeProject(): string | null`
  - `RpcClient.handshake(): Promise<string>` — resolves with the host's active root (possibly `''`).
  - `frameBelongsTo(frame: { project?: unknown }, active: string | null): boolean`

- [x] **Step 1: Write the failing client tests**

Append to `desktop/tests/rpcClient.test.ts`:

```ts
test('routed requests carry the active project; host ops stay untagged', async () => {
  const recorder = record(() => ({ type: 'result', id: 'r', ok: true }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')

  await client.send({ type: 'status' })
  await client.send({ type: 'hello' })
  await client.send({ type: 'project', op: 'switch', root: '/work/q' })

  assert.deepEqual(
    recorder.invokes.map((call) => call.args),
    [
      { payload: { type: 'status', project: '/work/p', id: 'r1' }, timeoutMs: null },
      { payload: { type: 'hello', id: 'r2' }, timeoutMs: null },
      {
        payload: { type: 'project', op: 'switch', root: '/work/q', id: 'r3' },
        timeoutMs: null,
      },
    ],
  )
})

test('handshake adopts the host’s active project for later requests', async () => {
  const recorder = record(() => ({
    type: 'result',
    id: 'r1',
    ok: true,
    protocol_version: 2,
    active: '/work/q',
  }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()

  assert.equal(await client.handshake(), '/work/q')
  assert.equal(client.activeProject(), '/work/q')

  await client.send({ type: 'status' })
  assert.deepEqual(recorder.invokes.at(-1)?.args, {
    payload: { type: 'status', project: '/work/q', id: 'r2' },
    timeoutMs: null,
  })
})
```

- [x] **Step 2: Write the failing frame-filter tests**

Append to `desktop/tests/frames.test.ts` (change the import to include `frameBelongsTo`):

```ts
test('a frame belongs when untagged or tagged with the active project', () => {
  assert.equal(frameBelongsTo({}, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '' }, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '/work/p' }, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '/work/q' }, '/work/p'), false)
})

test('without an active project every frame passes', () => {
  assert.equal(frameBelongsTo({ project: '/work/q' }, null), true)
})
```

- [x] **Step 3: Run the tests to verify they fail**

Run: `cd desktop && pnpm test`
Expected: FAIL — `client.setProject is not a function`, `frameBelongsTo` has no export.

- [x] **Step 4: Implement the client changes**

In `desktop/src/rpc/client.ts`, add the field and methods, reset the project on start, tag routed requests, and return `active` from `handshake`:

```ts
export class RpcClient {
  private nextId = 0
  /** The project every routed request belongs to; null until the host names
   * its active slot (a v2 server leaves requests untagged). */
  private project: string | null = null
  private handlers = new Set<EventHandler>()
  private stderrHandlers = new Set<(line: string) => void>()
  private exitHandlers = new Set<(code: number | null, pid?: number) => void>()
  private wiring: Promise<void> | null = null

  setProject(root: string | null): void {
    this.project = root === null || root === '' ? null : root
  }

  activeProject(): string | null {
    return this.project
  }

  async start(options: StartOptions = {}): Promise<number> {
    // A replaced child comes up at a new root; the handshake re-adopts it.
    this.project = null
    if (this.wiring === null) {
      this.wiring = this.listenAll().catch((error: unknown) => {
        this.wiring = null
        throw error
      })
    }
    await this.wiring
    try {
      const report = await bridge.invoke<{ pid: number }>('rpc_start', {
        options: {
          bin: options.bin ?? null,
          cwd: options.cwd ?? null,
          args: options.args ?? [],
        },
      })
      return report.pid
    } catch (error) {
      const failure = startFailureOf(error)
      throw failure ?? error
    }
  }
```

```ts
  async handshake(): Promise<string> {
    const frame = await this.send<{ protocol_version?: unknown; active?: unknown }>({
      type: 'hello',
    })
    if (frame.protocol_version !== PROTOCOL_VERSION) {
      throw {
        code: 'protocol_mismatch',
        bin: 'zen',
        osError: `client speaks protocol ${PROTOCOL_VERSION}, server answered ${String(frame.protocol_version)}`,
      }
    }
    const active = typeof frame.active === 'string' ? frame.active : ''
    this.setProject(active)
    return active
  }
```

```ts
  send<T = Record<string, unknown>>(payload: Record<string, unknown>, timeoutMs?: number): Promise<T> {
    const id = `r${(this.nextId += 1)}`
    // `hello` and `project` are host-level ops; everything else is served by
    // a slot and must name it.
    const routed =
      payload.type !== 'hello' && payload.type !== 'project' && this.project !== null
    return bridge.invoke<T>('rpc_send', {
      payload: { ...payload, ...(routed ? { project: this.project } : {}), id },
      timeoutMs: timeoutMs ?? null,
    })
  }
```

In `desktop/src/rpc/frames.ts`, append:

```ts
/** Whether a frame belongs in the active project's view. Untagged frames
 * (host-level answers, a v2 server) always pass; a tagged frame from a
 * background slot must not leak into the active transcript. */
export function frameBelongsTo(
  frame: { project?: unknown },
  active: string | null,
): boolean {
  if (active === null || active === '') return true
  const tag = typeof frame.project === 'string' ? frame.project : ''
  return tag === '' || tag === active
}
```

In `desktop/src/rpc/types.ts`, add `project` to `EventFrame`:

```ts
export interface EventFrame {
  type: 'event'
  /** The slot that emitted the frame (host tag, absent on a v2 server). */
  project?: string
  event: AgentEventFrame
}
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `cd desktop && pnpm test`
Expected: PASS (all suites).

- [x] **Step 6: Commit**

```bash
git add desktop/src/rpc/client.ts desktop/src/rpc/frames.ts desktop/src/rpc/types.ts desktop/tests/rpcClient.test.ts desktop/tests/frames.test.ts
git commit -m "feat(desktop): tag rpc requests with the active project"
```

---

### Task 2: Transcript pane snapshot + LRU cache

**Files:**
- Modify: `desktop/src/state/chatStore.ts`
- Create: `desktop/src/state/projectPanes.ts`
- Test: `desktop/tests/chatStore.test.ts`
- Test: `desktop/tests/projectPanes.test.ts`

**Interfaces:**
- Consumes: `ChatNode`, `useChatStore` state.
- Produces:
  - `ChatPane { nodes; cards; currentAssistantId; hasMoreBefore; firstLoadedIndex }`
  - `useChatStore.getState().pane(): ChatPane`
  - `useChatStore.getState().restorePane(pane: ChatPane): void`
  - `PaneCache(limit = 3)` with `get(root)`, `set(root, entry)`, `delete(root)`, `clear()`; `CachedPane { session: string | null; pane: ChatPane }`.

- [x] **Step 1: Write the failing chat-store test**

Append to `desktop/tests/chatStore.test.ts`:

```ts
test('pane() snapshots the transcript and restorePane() brings it back', () => {
  useChatStore.getState().loadSnapshot({
    ...empty,
    transcript: [
      { kind: 'user', id: 'u1', text: 'A 项目的对话', timestamp: 1 },
      { kind: 'assistant', id: 'a1', text: '收到', timestamp: 2 },
    ],
    total: 2,
  })
  const pane = useChatStore.getState().pane()

  useChatStore.getState().reset()
  assert.equal(useChatStore.getState().nodes.length, 0)

  useChatStore.getState().restorePane(pane)
  assert.deepEqual(
    useChatStore.getState().nodes.map((node) => (node.kind === 'user' ? node.text : node.kind)),
    ['A 项目的对话', 'assistant'],
  )
  assert.equal(useChatStore.getState().firstLoadedIndex, pane.firstLoadedIndex)
})
```

- [x] **Step 2: Write the failing cache test**

Create `desktop/tests/projectPanes.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { PaneCache } from '../src/state/projectPanes.ts'
import type { ChatPane } from '../src/state/chatStore.ts'

function pane(text: string): ChatPane {
  return {
    nodes: [{ kind: 'user', id: text, text, steering: false, timestamp: 0 }],
    cards: {},
    currentAssistantId: null,
    hasMoreBefore: false,
    firstLoadedIndex: 0,
  }
}

test('the least recently used project is evicted beyond the limit', () => {
  const cache = new PaneCache(2)
  cache.set('/work/a', { session: '/sessions/a.jsonl', pane: pane('a') })
  cache.set('/work/b', { session: '/sessions/b.jsonl', pane: pane('b') })
  cache.get('/work/a')
  cache.set('/work/c', { session: '/sessions/c.jsonl', pane: pane('c') })

  assert.equal(cache.get('/work/b'), undefined)
  assert.equal(cache.get('/work/a')?.session, '/sessions/a.jsonl')
  assert.equal(cache.get('/work/c')?.session, '/sessions/c.jsonl')
})

test('setting the same project replaces its entry and refreshes its place', () => {
  const cache = new PaneCache(2)
  cache.set('/work/a', { session: '/sessions/a.jsonl', pane: pane('old') })
  cache.set('/work/b', { session: '/sessions/b.jsonl', pane: pane('b') })
  cache.set('/work/a', { session: '/sessions/a2.jsonl', pane: pane('new') })

  assert.equal(cache.get('/work/a')?.session, '/sessions/a2.jsonl')
  cache.set('/work/c', { session: '/sessions/c.jsonl', pane: pane('c') })
  assert.equal(cache.get('/work/b'), undefined)
  assert.notEqual(cache.get('/work/a'), undefined)
})
```

- [x] **Step 3: Run the tests to verify they fail**

Run: `cd desktop && pnpm test`
Expected: FAIL — `pane is not a function`, `Cannot find module '../src/state/projectPanes.ts'`.

- [x] **Step 4: Implement the chat-store snapshot/restore**

In `desktop/src/state/chatStore.ts`, add the interface and state methods:

```ts
/** One transcript as the switch cache retains it. */
export interface ChatPane {
  nodes: ChatNode[]
  cards: Record<string, string>
  currentAssistantId: string | null
  hasMoreBefore: boolean
  firstLoadedIndex: number
}
```

Add to `ChatState`:

```ts
  /** Snapshot the loaded transcript; switching away stores it per project. */
  pane(): ChatPane
  /** Paint a retained pane before the server snapshot is revalidated. */
  restorePane(pane: ChatPane): void
```

Implement inside the store:

```ts
  pane() {
    const state = get()
    return {
      nodes: state.nodes,
      cards: state.cards,
      currentAssistantId: state.currentAssistantId,
      hasMoreBefore: state.hasMoreBefore,
      firstLoadedIndex: state.firstLoadedIndex,
    }
  },

  restorePane(pane) {
    set({
      nodes: pane.nodes,
      cards: pane.cards,
      currentAssistantId: pane.currentAssistantId,
      hasMoreBefore: pane.hasMoreBefore,
      firstLoadedIndex: pane.firstLoadedIndex,
      revision: get().revision + 1,
    })
  },
```

- [x] **Step 5: Create the pane cache**

Create `desktop/src/state/projectPanes.ts`:

```ts
/** Retained transcript panes, keyed by resolved project root. Switching back
 * to a project paints its cached pane before the server snapshot arrives. */

import type { ChatPane } from './chatStore.ts'

export interface CachedPane {
  /** The session the cached pane belongs to: a different target must not
   * reuse it (the snapshot would replace it a moment later anyway). */
  session: string | null
  pane: ChatPane
}

/** Three projects cover the realistic back-and-forth; the host caps resident
 * slots at four, so a fourth cached pane would usually be stale anyway. */
export const PANE_CACHE_LIMIT = 3

export class PaneCache {
  private readonly limit: number
  private readonly entries = new Map<string, CachedPane>()

  constructor(limit = PANE_CACHE_LIMIT) {
    this.limit = Math.max(1, limit)
  }

  get(root: string): CachedPane | undefined {
    const entry = this.entries.get(root)
    if (entry === undefined) return undefined
    this.entries.delete(root)
    this.entries.set(root, entry)
    return entry
  }

  set(root: string, entry: CachedPane): void {
    this.entries.delete(root)
    this.entries.set(root, entry)
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value
      if (oldest === undefined) break
      this.entries.delete(oldest)
    }
  }

  delete(root: string): void {
    this.entries.delete(root)
  }

  clear(): void {
    this.entries.clear()
  }
}
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `cd desktop && pnpm test`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add desktop/src/state/chatStore.ts desktop/src/state/projectPanes.ts desktop/tests/chatStore.test.ts desktop/tests/projectPanes.test.ts
git commit -m "feat(desktop): retain transcript panes for warm project switches"
```

---

### Task 3: Switch projects on the resident host

**Files:**
- Modify: `desktop/src/state/appStore.ts`
- Modify: `desktop/src/dev/bridge.ts`
- Modify: `desktop/e2e/app.spec.ts`
- Test: `desktop/e2e/app.spec.ts` (migrated + two new tests)

**Interfaces:**
- Consumes: `frameBelongsTo`, `PaneCache`, `useChatStore.pane()/restorePane()`, host `project` op (`open`/`switch`, `resume`, `background`; result `root`, `key`, `session`, `active`).
- Produces:
  - `AppState.project: string | null`
  - `BridgeState.activeRoot`, `projectOps`, `transcripts`, `transcriptDelayMs`, `hangTranscriptFrom`
  - `openProject` never calls `rpc_start` once connected.

- [x] **Step 1: Extend the scripted bridge (makes the new path testable)**

In `desktop/src/dev/bridge.ts`:

1. Add fields to `BridgeState`:

```ts
  /** The project the fake host is serving; set by `rpc_start` and project ops. */
  activeRoot?: string
  /** Project ops the shell sent (the resident-host switch path). */
  projectOps?: { op: string; root: string; resume?: string; background?: boolean }[]
  /** Transcript per project root; falls back to `transcript`. */
  transcripts?: Record<string, Record<string, unknown>[]>
  /** Milliseconds transcript answers wait, so cache-then-revalidate is observable. */
  transcriptDelayMs?: number
  /** Transcript requests for this root never resolve (only the cache can paint). */
  hangTranscriptFrom?: string
```

2. Add per-slot session tracking next to `currentSessionPath`:

```ts
  /** Resolved project root → the session its resident slot is on. A warm slot
   * ignores `resume`, exactly like `RpcHost._ensure`. */
  const slotSessions = new Map<string, string>()
```

3. Make `respond` async (the project/transcript branches await delays) and update `hello`:

```ts
  const respond = async (payload: Record<string, unknown>) => {
    switch (payload.type) {
      case 'hello':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          protocol_version: 2,
          active: state.activeRoot ?? '',
        }
```

4. Replace the `transcript` branch:

```ts
      case 'transcript': {
        const root = typeof payload.project === 'string' ? payload.project : ''
        if (state.hangTranscriptFrom !== undefined && root === state.hangTranscriptFrom) {
          await new Promise<never>(() => {})
        }
        if (state.transcriptDelayMs !== undefined) {
          await new Promise((resolve) => setTimeout(resolve, state.transcriptDelayMs))
        }
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          transcript: state.transcripts?.[root] ?? state.transcript ?? [],
          notices: [],
          compactions: [],
        }
      }
```

5. Update `session resume` so the active slot tracks it:

```ts
          case 'resume':
            currentSessionPath = String(payload.path)
            if (state.activeRoot !== undefined) {
              slotSessions.set(state.activeRoot, currentSessionPath)
            }
            return { type: 'result', id: payload.id, ok: true }
```

6. Replace the `project` branch (keep `relocate` as-is, add switch/open):

```ts
      case 'project': {
        if (payload.op === 'relocate') {
          if (state.relocateError !== undefined) {
            return { type: 'error', id: payload.id, message: state.relocateError }
          }
          const from = String(payload.from ?? '')
          const to = String(payload.to ?? '')
          state.relocations = [...(state.relocations ?? []), { from, to }]
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            old_root: from,
            new_root: to,
            old_key: 'old-key',
            new_key: 'new-key',
            sessions: 2,
            moved: true,
          }
        }
        if (payload.op !== 'open' && payload.op !== 'switch') {
          return { type: 'error', id: payload.id, message: 'unknown project op' }
        }
        const root = String(payload.root ?? '')
        const resume =
          typeof payload.resume === 'string' && payload.resume !== ''
            ? payload.resume
            : undefined
        state.projectOps = [
          ...(state.projectOps ?? []),
          { op: payload.op, root, ...(resume !== undefined ? { resume } : {}) },
        ]
        if (state.switchDelayMs !== undefined) {
          await new Promise((resolve) => setTimeout(resolve, state.switchDelayMs))
        }
        const warm = slotSessions.has(root)
        if (!warm) slotSessions.set(root, resume ?? '/sessions/a.jsonl')
        if (payload.background !== true) {
          state.activeRoot = root
          currentSessionPath = slotSessions.get(root) ?? currentSessionPath
        }
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          root,
          key: root,
          session: slotSessions.get(root) ?? '',
          active: state.activeRoot ?? '',
          resident: [...slotSessions.keys()],
        }
      }
```

7. In the `rpc_start` branch, adopt the root and seed its slot session; stop using
   `switchDelayMs` there (it now belongs to the project op):

```ts
      if (cmd === 'rpc_start') {
        const options = (args.options ?? {}) as {
          cwd?: string | null
          args?: string[]
        }
        const pid = nextPid
        nextPid += 1
        const flags = options.args ?? []
        state.starts.push({ cwd: options.cwd ?? null, pid, args: flags })
        const resumeAt = flags.indexOf('--resume')
        const resumed =
          resumeAt >= 0 && flags[resumeAt + 1] !== undefined ? flags[resumeAt + 1] : ''
        const cwd = options.cwd ?? ''
        if (cwd !== '') {
          state.activeRoot = cwd
          slotSessions.set(cwd, resumed !== '' ? resumed : '/sessions/a.jsonl')
          currentSessionPath = slotSessions.get(cwd) ?? currentSessionPath
        }
        if (state.startHangs === true) {
          await new Promise<never>(() => {})
        }
        return { pid }
      }
```

- [x] **Step 2: Rewrite `bootChild` and `openProject` in the app store**

Add the imports:

```ts
import { frameBelongsTo, frameErrorNotice, type ReplyLike } from '../rpc/frames'
import { PaneCache } from './projectPanes'
```

Add the store field and initialize it:

```ts
  /** The project the resident host is serving (from hello / project ops). */
  project: string | null
```

```ts
  connection: 'idle',
  switching: null,
  project: null,
```

Add the module-level cache next to `lastUsage`:

```ts
/** Transcript panes kept across project switches (pi-desktop's retained
 * panes; three covers the usual back-and-forth). */
const paneCache = new PaneCache()
```

In `bootChild`, adopt the host's active root after the handshake:

```ts
  const pid = await rpc.start({ bin: opts.bin, cwd: opts.cwd, args: opts.args })
  set({ pid })
  const active = await rpc.handshake()
  if (active !== '') set({ project: active })
  const root = active !== '' ? active : opts.cwd ?? ''
  try {
    localStorage.setItem(LAST_CWD_KEY, root)
  } catch {
    // ignore
  }
  if (root !== '') useProjectStore.getState().addProject(root)
```

In `connect`, clear the project alongside the chat (the new handshake re-adopts it):

```ts
    set({
      connection: 'starting',
      switching: null,
      project: null,
      exitCode: null,
      stderrTail: [],
      startFailure: null,
    })
```

Replace `openProject`:

```ts
  async openProject(path, target = 'latest') {
    if (path === '') return
    const current = get().project ?? lastProjectCwd()
    if (path === current && get().connection === 'ready') return
    // Resolve what the target slot should open with. On a cold slot the host
    // resumes during prepare; on a warm one the shell follows up with a
    // `session resume` (see below).
    const resume =
      target === null
        ? null
        : target === 'latest'
          ? latestSessionForProject(useSessionStore.getState().sessions, path)
          : target
    // Nothing to preserve before the first connection: a plain start.
    if (get().connection !== 'ready') {
      await get().connect(undefined, path, spawnArgs(resume))
      return
    }
    if (get().switching !== null) return

    // Retain the pane we are leaving so switching back paints it instantly.
    const chat = useChatStore.getState()
    chat.finalize()
    const previous = get().project
    if (previous !== null && previous !== path) {
      paneCache.set(previous, {
        session: useSessionStore.getState().currentPath,
        pane: chat.pane(),
      })
    }
    const cached = paneCache.get(path)
    // A cached pane is only valid for the very session this switch targets;
    // anything else would paint the wrong conversation for a moment.
    const canPaint = resume !== null && cached !== undefined && cached.session === resume
    if (canPaint) useChatStore.getState().restorePane(cached.pane)

    set({ switching: { path }, busy: false })
    // Usage deltas are per session; a new project starts a new baseline.
    lastUsage = null

    const rpc = get().client()
    try {
      const frame = await rpc.send<Record<string, unknown>>({
        type: 'project',
        op: 'switch',
        root: path,
        ...(resume !== null ? { resume } : {}),
      })
      if (frame.type !== 'result' || frame.ok !== true) {
        // A refusal keeps the old slot active: put its pane back and say why.
        if (previous !== null) {
          const old = paneCache.get(previous)
          if (old !== undefined) useChatStore.getState().restorePane(old.pane)
        }
        set({ switching: null })
        showFrameError(frame)
        return
      }
      const root =
        typeof frame.root === 'string' && frame.root !== '' ? frame.root : path
      rpc.setProject(root)
      set({ project: root, switching: null, exitCode: null, startFailure: null })
      try {
        localStorage.setItem(LAST_CWD_KEY, root)
      } catch {
        // ignore
      }
      useProjectStore.getState().addProject(root)

      // A resident slot ignores `resume` (the host keeps what it had), so the
      // requested session is resumed explicitly when it differs.
      const serverSession =
        typeof frame.session === 'string' && frame.session !== '' ? frame.session : null
      let effective = serverSession
      if (resume !== null && serverSession !== resume) {
        const answer = await rpc.send({ type: 'session', op: 'resume', path: resume })
        if (answer.type === 'result' && answer.ok === true) effective = resume
      }
      if (effective !== null) useSessionStore.getState().setCurrent(effective)

      // The cached pane stays until the snapshot lands; a switch that could
      // not hit its target must not leave another project's rows on screen.
      if (!(canPaint && effective === resume)) useChatStore.getState().reset()
      const loaded = await get().refreshTranscript()
      await Promise.all([restorePrefs(get), get().refreshSessions()])
      if (!loaded) useChatStore.getState().reset()
      paneCache.set(root, {
        session: effective,
        pane: useChatStore.getState().pane(),
      })
    } catch (error) {
      const failure = startFailureOf(error)
      set({ connection: 'exited', switching: null, startFailure: failure, exitCode: null })
      set((state) => ({
        stderrTail: [
          ...state.stderrTail,
          failure === null ? String(error) : `failed to reach ${failure.bin} rpc: ${failure.osError}`,
        ],
      }))
    }
  },
```

In `openSession`, compare against the adopted project first:

```ts
    if (root !== '' && (get().project ?? lastProjectCwd()) !== root) {
```

Filter frames in `handleFrame`:

```ts
function handleFrame(frame: ServerFrame, handling: FrameHandling): void {
  if (frame.type === 'event') {
    const event = frame.event
    const project =
      typeof frame.project === 'string' && frame.project !== '' ? frame.project : null
    // Prompts are answered by prefix-routed ids; a background slot's question
    // must stay in the queue even while another project is on screen.
    if (event.type === 'ask_request' && event.request_id) {
      useAskStore.getState().push({
        requestId: event.request_id,
        questions: event.questions ?? [],
        project,
      })
      return
    }
    if (event.type === 'ask_cancel' && event.request_id) {
      useAskStore.getState().pop(event.request_id)
      return
    }
    if (event.type === 'approval_request' && event.request_id) {
      useApprovalStore.getState().push({
        requestId: event.request_id,
        toolName: event.tool_name ?? '?',
        args: event.args ?? '',
        risk: event.risk ?? null,
        target: event.target ?? null,
        reason: event.reason ?? null,
        project,
      })
      return
    }
    // Chat events from a background slot belong to its own pane, which is
    // refreshed from its snapshot when the person switches back.
    if (!frameBelongsTo(frame, handling.get().project)) return
    useChatStore.getState().applyEvent(event)
    if (event.type === 'agent_end' || event.type === 'turn_end') {
      void handling.get().refreshStatus()
    }
    return
  }
  if (!frameBelongsTo(frame, handling.get().project)) return
  handleResultFrame(frame as Record<string, unknown>, handling)
}
```

Remove the two approval/ask `clear()` calls from `openProject` (they are already
removed by the replacement above); `connect` keeps clearing.

- [x] **Step 3: Migrate the e2e spawn assertions to project ops**

Update `desktop/e2e/app.spec.ts`:

1. Test at ~216 (`a new project joins the rail and can be removed`):

```ts
  await expect.poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.length ?? 0)).toBe(1)
  assert.equal(
    await page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root),
    '/work/newproj',
  )
```

2. Test at ~729 (`the taiji chip switches...`): replace the `starts.at(-1)?.args`
assertion with:

```ts
  assert.deepEqual(
    await page.evaluate(() => {
      const op = window.__ZEN_E2E__.projectOps?.at(-1)
      return { root: op?.root, resume: op?.resume }
    }),
    { root: '/work/p', resume: '/sessions/taiji.jsonl' },
  )
```

3. Test at ~1323 (`opening another project’s session...`): replace both the
`starts` poll and the stale-pid block with:

```ts
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/q', resume: '/sessions/c.jsonl' })
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some(
        (call) => call.type === 'session' && call.op === 'resume',
      ),
    ),
    false,
  )
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)

  // A late exit from a pid the shell no longer tracks must not end the run;
  // the tracked pid still does.
  const live = await page.evaluate(() => window.__ZEN_E2E__.starts[0]?.pid ?? 0)
  const emitExit = (pid: number) =>
    page.evaluate(
      (value) =>
        window.__ZEN_E2E_EMIT__('rpc:exit', {
          event: 'rpc:exit',
          id: 1,
          payload: { code: 0, pid: value },
        }),
      pid,
    )
  await emitExit(live + 999)
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.getByText(/会话进程已退出/)).toHaveCount(0)

  await emitExit(live)
  await expect(page.getByText(/会话进程已退出/)).toBeVisible()
```

4. Test at ~1379 (`a project discovered from sessions can start its own session`):
replace the `starts.at(-1)?.cwd` poll with:

```ts
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return op === undefined
          ? null
          : { root: op.root ?? null, resume: op.resume ?? null }
      }),
    )
    .toEqual({ root: '/work/q', resume: null })
```

5. Test at ~1622 (`the palette opens with the keyboard...`): replace the
`starts` poll with:

```ts
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/p', resume: '/sessions/b.jsonl' })
```

6. Test at ~1988 (project settings): replace the `starts` block with:

```ts
  assert.deepEqual(
    await page.evaluate(() => {
      const op = window.__ZEN_E2E__.projectOps?.at(-1)
      return { root: op?.root, resume: op?.resume }
    }),
    { root: '/work/q', resume: '/sessions/c.jsonl' },
  )
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
```

7. Test at ~2203 (palette, another project): replace the `starts` poll with the
same `{ root: '/work/q', resume: '/sessions/c.jsonl' }` poll.

8. Test at ~2311 (switch overlay): keep the strip assertions; replace the final
`starts` poll with:

```ts
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/q')
```

- [x] **Step 4: Add the acceptance and warm-resume e2e tests**

Append after the switch-overlay test:

```ts
test('switching back to a project paints its cached transcript before revalidating', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/p')
  })
  await boot(
    page,
    initialState({
      transcripts: {
        '/work/p': [{ kind: 'user', id: 'p1', text: 'P 项目的对话', timestamp: 1 }],
        '/work/q': [{ kind: 'user', id: 'q1', text: 'Q 项目的对话', timestamp: 1 }],
      },
      transcriptDelayMs: 800,
    }),
  )
  await expect(page.getByText('P 项目的对话')).toBeVisible()

  await page.locator('[data-session-path="/sessions/c.jsonl"]').click()
  await expect(page.getByText('Q 项目的对话')).toBeVisible()

  // What the server would revalidate now carries one more row, so the cached
  // pane and the fresh snapshot differ.
  await page.evaluate(() => {
    const state = window.__ZEN_E2E__
    state.transcripts = state.transcripts ?? {}
    state.transcripts['/work/p'] = [
      { kind: 'user', id: 'p1', text: 'P 项目的对话', timestamp: 1 },
      { kind: 'user', id: 'p2', text: '后台重新校验的行', timestamp: 2 },
    ]
  })
  await page.locator('[data-session-path="/sessions/a.jsonl"]').click()

  // Cached paint lands immediately; the delayed snapshot arrives after.
  await expect(page.getByText('P 项目的对话')).toBeVisible()
  await expect(page.getByText('后台重新校验的行')).toHaveCount(0)
  await expect(page.getByText('后台重新校验的行')).toBeVisible()
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
})

test('a warm project switch still resumes the requested session', async ({ page }) => {
  await boot(page, initialState())
  // b: cold slot for /work/p, resumed by the project op.
  await page.locator('[data-session-path="/sessions/b.jsonl"]').click()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/p')
  // c: cold slot for /work/q.
  await page.locator('[data-session-path="/sessions/c.jsonl"]').click()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/q')

  // Back to p is warm: the host keeps b, so the shell follows up with a resume.
  await page.locator('[data-session-path="/sessions/a.jsonl"]').click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls
          .filter((call) => call.type === 'session' && call.op === 'resume')
          .map((call) => call.path),
      ),
    )
    .toEqual(['/sessions/a.jsonl'])
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
})
```

- [x] **Step 5: Run the e2e suite**

Run: `cd desktop && npx playwright test e2e/app.spec.ts`
Expected: PASS. If a selector cannot find a session row, check that the group
is expanded (existing tests click the group header first).

- [x] **Step 6: Build and unit tests**

Run: `cd desktop && pnpm test && pnpm run build`
Expected: PASS, no type errors.

- [x] **Step 7: Commit**

```bash
git add desktop/src/state/appStore.ts desktop/src/dev/bridge.ts desktop/e2e/app.spec.ts
git commit -m "feat(desktop): switch projects on the resident rpc host"
```

---

### Task 4: Project-scoped approvals and asks

**Files:**
- Create: `desktop/src/state/pendingProject.ts`
- Modify: `desktop/src/state/stores.ts` (`PendingApproval.project`, `PendingAsk.project`)
- Modify: `desktop/src/ui/approval/ApprovalPanel.tsx`
- Modify: `desktop/src/ui/ask/AskCard.tsx`
- Test: `desktop/tests/pendingProject.test.ts`

**Interfaces:**
- Consumes: `AppState.project` (Task 3); `project` on pushed pending entries (Task 3).
- Produces: `pendingForProject<T extends { project?: string | null }>(queue: T[], project: string | null): T[]`.

- [x] **Step 1: Write the failing filter test**

Create `desktop/tests/pendingProject.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pendingForProject } from '../src/state/pendingProject.ts'

test('only the active project and untagged entries render', () => {
  const queue = [
    { requestId: 'legacy' },
    { requestId: 'p', project: '/work/p' },
    { requestId: 'q', project: '/work/q' },
  ]

  assert.deepEqual(
    pendingForProject(queue, '/work/p').map((entry) => entry.requestId),
    ['legacy', 'p'],
  )
  assert.deepEqual(
    pendingForProject(queue, null).map((entry) => entry.requestId),
    ['legacy', 'p', 'q'],
  )
})
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd desktop && pnpm test`
Expected: FAIL — module not found.

- [x] **Step 3: Implement the helper and tag the pending types**

Create `desktop/src/state/pendingProject.ts`:

```ts
/** Approvals and asks carry the project their slot belongs to. The host routes
 * answers by the id prefix, so a background project's card stays queued and
 * reappears when that project is active again. Untagged entries (a v2 server)
 * always render. */
export function pendingForProject<T extends { project?: string | null }>(
  queue: T[],
  project: string | null,
): T[] {
  return queue.filter(
    (entry) =>
      entry.project == null ||
      entry.project === '' ||
      project === null ||
      entry.project === project,
  )
}
```

In `desktop/src/state/stores.ts`, add the optional field to both pending types:

```ts
export interface PendingApproval {
  requestId: string
  toolName: string
  args: string
  risk: string | null
  target: string | null
  reason: string | null
  /** The slot that asked (absent on a v2 server). */
  project?: string | null
}
```

```ts
export interface PendingAsk {
  requestId: string
  questions: AskQuestion[]
  /** The slot that asked (absent on a v2 server). */
  project?: string | null
}
```

- [x] **Step 4: Filter in both panels**

`desktop/src/ui/approval/ApprovalPanel.tsx`:

```tsx
import { pendingForProject } from '../../state/pendingProject'
```

```tsx
export function ApprovalPanel() {
  const queue = useApprovalStore((state) => state.queue)
  const project = useAppStore((state) => state.project)
  const approve = useAppStore((state) => state.approve)
  const t = useT()
  const approval = pendingForProject(queue, project)[0]
```

`desktop/src/ui/ask/AskCard.tsx`:

```tsx
import { pendingForProject } from '../../state/pendingProject'
```

```tsx
export function AskCard() {
  const queue = useAskStore((state) => state.queue)
  const project = useAppStore((state) => state.project)
  const request = pendingForProject(queue, project)[0]
  if (request === undefined) return null
  // Keying by requestId keeps resolving/scroll state out of the next ask.
  return <AskCardBody key={request.requestId} request={request} />
}
```

In `AskCardBody`, replace the queued count:

```tsx
  const queue = useAskStore((state) => state.queue)
  const activeProject = useAppStore((state) => state.project)
  const queued = Math.max(0, pendingForProject(queue, activeProject).length - 1)
```

- [x] **Step 5: Run the tests and build**

Run: `cd desktop && pnpm test && pnpm run build`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add desktop/src/state/pendingProject.ts desktop/src/state/stores.ts desktop/src/ui/approval/ApprovalPanel.tsx desktop/src/ui/ask/AskCard.tsx desktop/tests/pendingProject.test.ts
git commit -m "feat(desktop): scope approval and ask cards to their project"
```

---

### Task 5: Document the resident switch and run the full gate

**Files:**
- Modify: `desktop/README.md`
- Modify: `docs/superpowers/plans/2026-11-10-project-scoped-runtime-phase-c.md` (check the boxes)

**Interfaces:** none.

- [x] **Step 1: Update the README architecture mapping and RPC surface**

In the mapping table, replace the host-spawn row:

```markdown
| Host spawn (`DesktopHostProcess`) | one `rpc_start` command spawning `zen rpc --cwd <dir>`; project switches go through the host's resident slots instead of respawning |
```

Append to the RPC surface paragraph:

```markdown
`project` (op: `open`/`switch`/`close`/`relocate`) drives the resident host
added in the Phase B backend work: one child keeps up to four project slots,
every frame is tagged with its project, and switching projects sends
`{type: 'project', op: 'switch', root}` instead of restarting the process.
The shell retains up to three transcript panes so switching back paints the
previous conversation before the fresh snapshot lands.
```

- [x] **Step 2: Run the full gate**

```bash
cd desktop && pnpm install --frozen-lockfile
cd desktop && pnpm test
cd desktop && pnpm run build
cd desktop && pnpm run lint:tokens
cd desktop && npx playwright test e2e/app.spec.ts
```

Expected: all pass. If Playwright's bundled browser is unavailable, retry with
`PLAYWRIGHT_CHANNEL=chrome`.

- [x] **Step 3: Commit**

```bash
git add desktop/README.md docs/superpowers/plans/2026-11-10-project-scoped-runtime-phase-c.md
git commit -m "docs(desktop): document the resident project switch path"
```
