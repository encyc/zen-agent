# Multi-Session Panes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (owner
> preselected native execution: the planner implements every task in this session).
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In coding mode, show up to 3 sessions of the current project side by side
(live, per-pane composers) and detach a session into an independent Tauri window.

**Architecture:** The RPC host learns one rule — a request tagged with a `session`
path is served by the resident slot holding it (refused when none). The desktop
routes frames per session into pane stores, renders a pane strip in place of the
single chat view, and boots detached windows from the same frontend with a query
override that adopts the shared sidecar.

**Tech Stack:** Python 3.11+ host (no new deps), React 18 + zustand 5 + Tauri 2,
`node --test` for desktop units, pytest for host, Playwright for browser e2e.

**Spec:** `docs/superpowers/specs/2026-11-17-multi-session-panes-design.md`
(including its Amendments section — the four blockers are binding).

## Global Constraints

- Architecture gate: files ≤ 800 LOC; allowlisted files may not exceed their
  recorded size (`appStore.ts` 1195, `dev/bridge.ts` 961, `e2e/app.spec.ts` 2750).
- New i18n strings go in `desktop/src/i18n/messages-panes.ts` (en+zh mirrored);
  `messages.ts` only gains the import + spread.
- Request ids never contain `:` (the host reads an id prefix as a slot key).
- Auto-tag exclusion set: `hello`, `project`, `terminal`, `runs`, `supervisor`,
  `swarm`, `zen_fleet`, `workspace`, `shutdown`; caller-set `session` always wins.
- Pane cap: 3 total panes (strip includes the focused session).
- No second sidecar process, ever. No growth of `server.py`.

## Review Focus

1. Non-resident session tagged request → must refuse, never serve the active
   session (wrong-conversation paint). Test: host test in Task 1; client tag
   validation in Task 5.
2. Terminal shell tokens: `terminal` requests keep their `session` token —
   auto-tag must not overwrite. Test: Task 2.
3. Two windows sharing one pending map: ids must be salted per process. Test: Task 2.
4. Pane of a running sibling keeps streaming while another pane submits a prompt
   (no cross-paint). Test: Task 4 (frame router) + Task 1 (host parallel slots).
5. Closing a detached window closes the window, not the runtime. Test: Rust unit
   not practical — pinned by Task 8 code review + manual note in the report.

---

### Task 1: Host — session-routed slot targeting (Python)

**Files:**
- Modify: `zenagent/cli/rpc/host.py` (`_slot_for`, new `_session_holder`, `_handle_line`)
- Test: `tests/test_rpc_session_routing.py` (new)

**Interfaces:**
- Produces: routing rule — for any kind that reaches `_slot_for`, a non-empty
  `request["session"]` resolves to the resident slot holding that exact session
  path (LRU-refreshed); refusal frame `{"type":"error","code":"session_invalid",`
  `"message":"no resident slot for session"}` when no holder exists or the
  holder's resolved root mismatches a present `project` tag. Rule sits before
  the project rule; approval/ask id-prefix routing stays first.

- [ ] **Step 1: Write failing tests** — four cases: (a) `prompt` tagged with the
  non-active session of a busy sibling lands on that sibling's slot (its runtime
  keeps running); (b) tagged `status`/`transcript` return the holder's session
  tag in the reply; (c) tagged request naming a non-resident path → error frame
  code `session_invalid`, active slot untouched; (d) untagged requests and
  `session op=resume` behave exactly as before (v2 parity). Reuse the fake-host
  helpers from `tests/test_rpc_v2.py` (same fixtures pattern).
- [ ] **Step 2: Run** `pytest tests/test_rpc_session_routing.py -q` → fails
- [ ] **Step 3: Implement** in `host.py` — `_session_holder(session, project)` scans
  `self._slots` by resolved session path (project-matched when a project tag is
  present); `_slot_for` checks it after the prefer_id branch and calls
  `self._slots.move_to_end(slot.key)`; `_handle_line` sends the refusal itself
  before the fall-through when a session tag named no valid holder (never
  forward such a request to any slot).
- [ ] **Step 4: Run** the new file + `pytest tests/test_rpc_v2.py tests/test_rpc_slot.py tests/test_rpc_supervisor.py tests/test_rpc_swarm.py -q` → all pass
- [ ] **Step 5: Commit** `feat(rpc): route session-tagged requests to their resident slot`

### Task 2: Client — salted ids + session auto-tagging (TS)

**Files:**
- Modify: `desktop/src/rpc/client.ts`
- Test: `desktop/tests/clientSessionTag.test.ts` (new)

**Interfaces:**
- Produces: `RpcClient.setSessionProvider(get: () => string | null): void`;
  `send()` tags `session` (when provider returns non-empty) unless the payload
  already has one or `payload.type` is in the exclusion set; ids become
  `r<4-char base36 salt><n>`.

- [ ] **Step 1: Write failing tests** against a fake bridge (`_useBridge`) capturing
  invoke payloads: prompt gets tagged with the provider's session; a payload that
  already carries `session` is never overwritten; `terminal`/`hello`/`runs`/
  `zen_fleet` are never tagged; ids from two client instances never collide and
  contain no `:`.
- [ ] **Step 2: Run** `node --test tests/clientSessionTag.test.ts` → fails
- [ ] **Step 3: Implement** in `client.ts` (module-level salt constant, provider
  field defaulting to `() => null`).
- [ ] **Step 4: Run** the file + `node --test tests/rpcClient.test.ts` → pass
- [ ] **Step 5: Commit** `feat(desktop): per-window request ids and session auto-tagging`

### Task 3: chatStore factory (TS)

**Files:**
- Modify: `desktop/src/state/chatStore.ts`
- Test: `desktop/tests/chatStoreFactory.test.ts` (new)

**Interfaces:**
- Produces: `export function createChatStore(): ChatStoreApi` where
  `ChatStoreApi = typeof useChatStore` (the zustand `ChatState` store);
  `useChatStore = createChatStore()` stays the primary instance; `nodesFromViews`,
  `retryableNodeId`, node types unchanged; module-level `nid` sequence stays
  shared across instances.

- [ ] **Step 1: Write failing test** — two instances stream independent `applyEvent`
  state; both share the `nid` counter (no duplicate ids).
- [ ] **Step 2: Run** → fails (no `createChatStore` export)
- [ ] **Step 3: Implement** — wrap the existing `create<ChatState>((set, get) => …)`
  body in the factory; no behavior change to `useChatStore`.
- [ ] **Step 4: Run** `node --test tests/chatStore.test.ts tests/chatStoreFactory.test.ts` → pass
- [ ] **Step 5: Commit** `refactor(desktop): chatStore factory for per-session pane instances`

### Task 4: frameRouter — frame handling moves + pane routing (TS)

**Files:**
- Create: `desktop/src/state/frameRouter.ts`
- Modify: `desktop/src/state/appStore.ts` (net shrink: remove moved code)
- Test: `desktop/tests/frameRouter.test.ts` (new)

**Interfaces:**
- Produces:
  - `export interface FrameHandling { get(): { project: string | null; refreshStatus(): Promise<void>; refreshSessions(): Promise<void> }; set(partial: { busy?: boolean }): void }`
  - `export function ensureFrameWiring(rpc: RpcClient, handling: FrameHandling): void`
    (registers `rpc.onFrame(handleFrame)` once per process, idempotent; also sets
    the client's session provider to `useSessionStore.getState().currentPath`)
  - `export function handleResultFrame(frame, handling): void` (appStore's
    `submit`/`runCommand` keep calling it)
  - `export function recordUsageBaseline(session: string, usage: Usage | undefined): void`
    (called by appStore's `refreshStatus`)
  - `export function showFrameError(frame: ReplyLike): boolean` (moved from appStore;
    appStore's actions keep importing it — re-export from appStore or import at call sites)
  - `export interface PaneSink { hasPane(s: string | null): boolean; applyEvent(s, e): void;
    applyUsage(s, u, turnMs, termination): void; paneFailure(s, text): void; sessionEnded(): void }`
    + `export function setPaneSink(sink: PaneSink | null): void`
- Routing order (events branch): ask/approval pushes → project guard → **pane
  sink** (frame.session matches an open pane: applyEvent, plus turn_end/agent_end
  also `refreshSessions`) → current-session guard → `useChatStore`. Result
  branch: project guard → swarm → fleet → **pane sink** (usage rows + finalize +
  error frames land in the pane) → current-session guard.

- [ ] **Step 1: Write failing tests** with two fake pane sinks and the real stores:
  (a) an event tagged with an open pane's session mutates that pane's chat store
  only; (b) a non-pane background session's event still only refreshes sessions;
  (c) a prompt result (termination+usage) for a pane session lands its usage row
  in the pane; (d) a pane-addressed error frame becomes a pane failure row; (e)
  untagged frames behave exactly as today; (f) swarm/zen_fleet frames never reach
  pane routing.
- [ ] **Step 2: Run** → fails (module missing)
- [ ] **Step 3: Implement** `frameRouter.ts`; delete the moved code from
  `appStore.ts` (handleFrame, handleResultFrame, usageBaselines, showFrameError,
  FrameHandling) and import the router; `connect()` calls `ensureFrameWiring`
  (its own `wired` flag moves into frameRouter); `onExit` additionally calls
  `sink.sessionEnded()` via a setter appStore passes through.
- [ ] **Step 4: Run** `node --test tests/frameRouter.test.ts tests/appViews.test.ts 2>/dev/null; node --test tests/*.test.ts` full desktop suite → pass; `python3 scripts/check_architecture.py` → ok (appStore shrank)
- [ ] **Step 5: Commit** `refactor(desktop): frame router with per-session pane routing`

### Task 5: sessionPanes store (TS)

**Files:**
- Create: `desktop/src/state/sessionPanes.ts`
- Test: `desktop/tests/sessionPanes.test.ts` (new)

**Interfaces:**
- Consumes: `createChatStore` (Task 3), `setPaneSink`/`frameBelongsTo` (Task 4),
  `useAppStore.getState().client()/resumeSession()` (existing), client sends
  `transcript {session}` / `zen_fleet op=transcript {path, project}` /
  `prompt {text, session}` / `command {name, argument, session}` /
  `session op=retry {session}` / `status {session}` / `cancel {session}`.
- Produces: `usePanesStore` with
  `{ panes: SessionPane[]; open(path): Promise<void>; close(path): void;
  focus(path): Promise<void>; submit(path): Promise<void>; cancel(path): Promise<void>;
  loadOlder(path): Promise<boolean>; setDraft(path, text): void; finalizeAll(): void }`
  where `SessionPane = { path, title, chat: ChatStoreApi, busy, loaded, live, draft }`.
  Reply validation: a `transcript {session}` reply whose `session` tag ≠ path
  counts as refusal → `zen_fleet op=transcript` fallback → static pane (`live:
  false`). `submit` resumes first when `!live`. Poll every 2.5 s (`status
  {session}`) only while panes exist; a refusal marks the pane not-live.
  Registers itself as the frameRouter `PaneSink` at module init.

- [ ] **Step 1: Write failing tests** with a scripted fake bridge: open paints a
  snapshot into the pane store; fallback path (refusal → zen transcript) marks
  static; cap at 3 with a notice; close removes; submit on a static pane resumes
  first then sends the tagged prompt; focus resumes; sink events mutate the right
  pane; finalizeAll settles all panes.
- [ ] **Step 2: Run** → fails
- [ ] **Step 3: Implement** per the interfaces above.
- [ ] **Step 4: Run** the file + full desktop suite → pass
- [ ] **Step 5: Commit** `feat(desktop): session pane registry with per-pane state`

### Task 6: Pane UI + sidebar affordance + i18n

**Files:**
- Create: `desktop/src/ui/panes/SessionPanes.tsx`, `desktop/src/ui/panes/PaneView.tsx`,
  `desktop/src/ui/panes/PaneComposer.tsx`, `desktop/src/ui/panes/panes.module.css`
  (one CSS module for the strip)
- Modify: `desktop/src/ui/chat/ChatView.tsx` (optional props only:
  `chat?: ChatStoreApi`, `busy?: boolean`, `onLoadOlder?: () => Promise<boolean>`,
  `onRetry?: () => void`, `emptyState?: ReactNode`; defaults = today's behavior)
- Modify: `desktop/src/App.tsx` (coding branch: strip when `panes.length >= 2`,
  dock hidden then; pass `panes` values down)
- Modify: `desktop/src/ui/SidebarCoding.tsx` (row menu item "open in split" —
  only for the current project, hidden when cap reached or already open)
- Create: `desktop/src/i18n/messages-panes.ts`; Modify: `desktop/src/i18n/messages.ts`
  (import + spread only)
- Test: `desktop/e2e/panes.spec.ts` (new; the main spec is frozen)

**Interfaces:**
- Consumes: `usePanesStore` (Task 5), ChatView props above, `t()` locale.
- Produces: `[data-pane="<session path>"]` on each pane root, `[data-pane-strip]`
  on the strip, `[data-pane-active]` on the focused pane, `[data-pane-composer]`
  textarea, `[data-pane-send]`, `[data-pane-close]`, `[data-pane-popout]`,
  `[data-pane-stop]`, header drag handle `[data-pane-drag]`; e2e drives these.

- [ ] **Step 1: e2e failing spec** (`desktop/e2e/panes.spec.ts`, reusing
  `e2e/harness.ts` fixtures): open app → row menu → open in split → two panes
  render → focus second pane (click) → type + submit → user row appears in that
  pane → close one pane → single-view layout returns.
- [ ] **Step 2: Run** `pnpm e2e -- panes.spec.ts` → fails
- [ ] **Step 3: Implement** UI per interfaces; pane header shows title + busy dot +
  buttons; slim composer (Enter sends per `enterToSend` setting, Esc stops);
  `ChatWidthHandles` omitted in panes; empty pane placeholder text (no HomeHero).
- [ ] **Step 4: Run** e2e + `pnpm build` (tsc) + `pnpm lint:tokens` → pass
- [ ] **Step 5: Commit** `feat(desktop): split session panes in coding mode`

### Task 7: Detach — boot mode + window creation (TS)

**Files:**
- Create: `desktop/src/state/bootMode.ts` (pure query parser)
- Create: `desktop/src/state/detachWindow.ts` (seam-backed window creation)
- Test: `desktop/tests/detachWindow.test.ts` (new)

**Interfaces:**
- Produces:
  - `export interface BootOverride { kind: 'session'; session: string; project: string | null }`
    + `export function parseBootOverride(search: string): BootOverride | null`
  - `export interface DetachBridge { createWindow(opts: { label: string; url: string; title: string; width: number; height: number }): Promise<void>; emit(event: string, payload: unknown): Promise<void> }`
    + `export function setDetachBridge(b: DetachBridge | null): void`
  - `export function outsideDrop(event: { screenX: number; screenY: number; dataTypes: readonly string[] }, bounds: { x: number; y: number; width: number; height: number }): boolean`
  - `export async function detachSession(info: { session: string; title: string; project: string | null }): Promise<boolean>`
    — URL `location.pathname + '?boot=session&session=…&project=…'`, label
    `session-<Date.now()>`, 760×560; emits `zen:session-detached {session}`;
    false when creation throws (browser/dev → caller shows a notice, pane stays).

- [ ] **Step 1: Write failing tests** — parser round-trips encoded paths and
  rejects garbage; `outsideDrop` true/false cases; `detachSession` with a fake
  bridge builds the right URL/label and emits; a throwing bridge returns false.
- [ ] **Step 2: Run** → fails
- [ ] **Step 3: Implement** both modules (default bridge lazily imports
  `@tauri-apps/api/webviewWindow` + `@tauri-apps/api/event`).
- [ ] **Step 4: Run** → pass; **Step 5: Commit** `feat(desktop): session detach window seam`

### Task 8: Detached window boot + Rust shell

**Files:**
- Create: `desktop/src/state/sessionBoot.ts` (`bootSessionWindow(override)`)
- Create: `desktop/src/state/prefsReplay.ts` (move `restorePrefs` out of appStore
  verbatim; appStore imports it — keeps appStore shrinking)
- Modify: `desktop/src/App.tsx` (boot override: skip auto-connect, run
  `bootSessionWindow`, render without the sidebar; `ConversationTopbar` hides its
  expand seat via a new optional prop)
- Modify: `desktop/src/ui/panes/SessionPanes.tsx` (header pop-out button +
  dragend wiring calling `detachSession`; main window listens for
  `zen:session-detached` and closes the matching pane)
- Modify: `desktop/src-tauri/src/rpc.rs` (new `rpc_alive` command reading
  `RpcState`), `desktop/src-tauri/src/lib.rs` (register it; scope
  `CloseRequested → hide` to `label == "main"`), `desktop/src-tauri/capabilities/default.json`
  (`windows: ["main", "session-*"]`, add `core:webview:allow-create-webview-window`)
- Test: `desktop/tests/sessionBoot.test.ts` (new, fake bridge)

**Interfaces:**
- Consumes: `parseBootOverride` (Task 7), `ensureFrameWiring` (Task 4),
  `rpc_alive` (Rust), `rpc_start`/`rpc_send` (existing).
- Produces: boot sequence — `rpc_alive` → alive ? `handshake()` : `rpc.start()` →
  `setProject(override.project ?? active)` → `project open {root, resume: session,
  background: true}` → if reply `session` ≠ target: `session resume` →
  `setCurrent(session)` → `transcript {session}` → `loadSnapshot` →
  `refreshSessions` + `refreshStatus`; never writes `LAST_CWD_KEY`; starts the
  same 2 s status poll appStore uses (imported poll helper or local interval).

- [ ] **Step 1: Write failing test** — fake bridge scripting `rpc_alive` true/false:
  boot adopts without `rpc_start` when alive; pins project and current session;
  loads the session transcript; no `LAST_CWD_KEY` write (fake localStorage).
- [ ] **Step 2: Run** → fails
- [ ] **Step 3: Implement** `sessionBoot.ts` + `prefsReplay.ts` move + App wiring +
  Rust commands/capabilities.
- [ ] **Step 4: Run** desktop suite → pass; `cargo check` in `desktop/src-tauri` → ok; `pnpm build` → pass
- [ ] **Step 5: Commit** `feat(desktop): detached session windows share the runtime`

### Task 9: Gates + report

- [ ] **Step 1:** `cd desktop && pnpm test && pnpm build && pnpm lint:tokens` all green
- [ ] **Step 2:** `pnpm e2e` full suite green (existing specs unchanged)
- [ ] **Step 3:** repo root: `python3 scripts/check_architecture.py` ok;
  `/Users/mac/zen-agent/.venv/bin/python -m pytest -q` full suite green
- [ ] **Step 4:** `cargo check` under `desktop/src-tauri`; (optional, time
  permitting) `pnpm tauri build --debug` to leave a runnable app
- [ ] **Step 5:** write the morning report
  (`docs/superpowers/plans/2026-11-18-multi-session-panes-report.md`: what
  shipped, how to try it, known limitations) and commit
  `chore: multi-session panes report`
