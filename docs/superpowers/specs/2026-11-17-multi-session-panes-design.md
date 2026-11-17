# Multi-Session Panes + Detached Session Windows (Coding Mode)

Date: 2026-11-17 · Status: approved by owner delegation (owner asleep, wrote the
requirements autonomously per direct instruction; owner reviews in the morning)

## Intent and why

In the desktop app's **coding mode** the conversation surface shows exactly one
session. The owner wants to watch several sessions of the same project at once —
two or three side by side, live — and to pull one session out into its own
window ("把一个 session 拖出来，形成一个独立的 GUI"). The kernel already runs
sibling sessions of a project in parallel (the RPC host's slot registry); the
GUI simply never surfaces it. This feature closes that gap.

## Goals

1. **Split view.** In coding mode, the owner can open 1–2 additional sessions of
   the current project next to the conversation: up to 3 session panes visible
   simultaneously, each streaming live (assistant deltas, tool cards, diffs).
2. **Per-pane interaction.** Each pane has its own composer. Sending a prompt
   (or slash command) from a pane reaches that pane's session — without stealing
   what another pane is showing, and without stopping another session's run.
   Clicking a pane focuses it (it becomes the "current" session the topbar,
   sidebar and status poll follow, exactly like opening a session today).
3. **Drag out / pop out.** A pane can be dragged out of the window (or popped
   out with a header button) into an independent OS window that keeps showing —
   and can keep driving — that session. Closing the detached window never stops
   the session; it keeps running in the background and stays in the sidebar.
4. **Single runtime.** Everything rides the existing one `zen rpc` sidecar: the
   detached window adopts it, it never spawns a second runtime on its own.
5. Zero regression for the single-session flow (no panes open ⇒ today's layout,
   composer, approvals, transcript paging, project switching all unchanged).

## Non-goals (this iteration)

- Panes for sessions of a *different* project (frames are project-filtered on
  the wire; the split affordance only offers the current project's sessions).
  A *detached* window may hold any project's session (it retargets its own
  project context), but panes stay in-project.
- Re-docking a detached window back into the main window (close + reopen works).
- Edit/history features inside panes beyond what the main transcript already
  has (undo/retry stay on the focused session's own surfaces).
- TUI support. This is a desktop-GUI feature only.

## Anti-goals (would make this a failure even if it worked)

- Two sidecar processes (split runtimes) — session state would fork.
- Focusing a pane aborting another pane's run (host already prevents this;
  the UI must not work around it by stopping runs).
- Any growth of allowlisted architecture-debt files (`appStore.ts` 1195,
  `dev/bridge.ts` 961, `e2e/app.spec.ts`) — new logic goes into new modules.

## Constraints

- Architecture gate (`scripts/check_architecture.py`): 800 LOC per file;
  allowlisted files may only shrink. `messages.ts` is a catalog — new strings
  go in a new `messages-panes.ts` split (pattern: `messages-settings.ts`).
- The host routes a request to a slot by: id prefix (approvals) → project field
  → active slot. There is no per-session request routing yet.
- The desktop shell owns exactly one webview ("main") and one shared
  `RpcState`; `rpc:frame`/`rpc:stderr`/`rpc:exit` are broadcast to every window.
- Tauri capabilities gate window APIs per window label.

## Approaches considered

1. **Read-only mirror panes** (poll `zen_fleet op=transcript` per pane every
   ~1.2s, like `WorkerConversation`). Cheapest, but interaction requires
   focusing (resume), which steals the host's per-project active pointer — two
   windows would fight over it; a detached window would misroute the main
   window's prompts to its own session. Rejected.
2. **Per-session request routing on the wire** (chosen). The host learns one
   rule: a request that names a `session` path is served by the slot holding
   that session. Panes and detached windows then address sessions directly;
   prompts, steers, cancels, transcripts and statuses become per-session; the
   active pointer keeps meaning what it means today (the focused session).
   Small host change, removes the whole class of cross-window pointer races.
3. **One webview per pane inside the main window** (Tauri split webviews).
   Heaviest; duplicating the store stack per webview buys nothing over in-app
   panes. Rejected.

## Design

### Wire (Python host, `zenagent/cli/rpc/host.py`)

- `_slot_for(request)`: after the approval-id check, a `session` field naming a
  path resolves to the resident slot holding it (`_session_holder`, scanning
  all slots by resolved session path). Unmatched session ⇒ fall through to the
  existing project/active rules, so an older client is unaffected.
- Everything else is forwarded verbatim, as today: the slot server answers and
  tags frames with its session already.
- No `server.py` change.

### Frontend stores

- `chatStore.ts`: export a `createChatStore()` factory; `useChatStore` stays
  the primary instance (the focused session's transcript). Zero behavior change.
- NEW `state/frameRouter.ts`: `handleFrame`/`handleResultFrame` move out of
  `appStore.ts` (that file may only shrink) into the router, which additionally
  routes chat events, prompt results and usage rows for **pane sessions** into
  their pane stores (by the frame's `session` tag) before the existing
  current-session rules apply.
- NEW `state/sessionPanes.ts` — the pane registry:
  - `panes: SessionPane[]` (ordered, ≤ 3), each `{ path, title, chat, busy,
    loaded }` with its own chat store instance, draft and status;
  - actions: `openPane(path)` (snapshot via `transcript {session}` + live
    routing), `closePane`, `focusPane` (resume = current, as today's
    `openSession`), `submitPane(path, text)` (prompt/command tagged with
    `session`), `cancelPane`, `detachPane` (pop-out), per-pane older-page load;
  - a 2.5 s staggered poll of `status {session}` per open pane for the busy dot
    and catch-up (frames already stream the content).
- `submitPane` supports plain prompts and slash commands; a pane command
  result with `reset_transcript` refetches that pane's snapshot.

### UI

- `App.tsx` (coding mode, no panes open): unchanged — topbar + `ChatView` +
  composer dock. With panes open: a **pane strip** replaces the center column —
  N equal columns, the focused one outlined; each pane = header + transcript +
  slim composer; the floating dock hides while the strip is up.
- NEW `ui/panes/SessionPanes.tsx` + `PaneView.tsx` (+ CSS modules). Headers:
  title, running dot, focus ring on click, pop-out button, stop button while
  busy, close button. Empty session shows a placeholder (not the HomeHero).
- Sidebar row menu (coding mode): "Open in split" for any non-blocked session
  of the current project; disabled at the 3-pane cap or when already open.
- The strip lives inside the existing center column; rightbar/approvals behave
  exactly as today (approvals are shell-level queues, unchanged).

### Detached session window (Tauri)

- Pane header drag (HTML5 `dragstart` on the header) ending outside the window
  bounds, or the pop-out button, calls `detachSession(path, {title, project})`.
- NEW Tauri command `rpc_alive` (shared `RpcState` probe) and window creation
  via `WebviewWindow` (`session-<n>` label) loading the same frontend with
  `?boot=session&session=<path>&project=<root>`. Capabilities: extend
  `windows` to `["main", "session-*"]` and allow
  `core:webview:allow-create-webview-window` (+ the new window's own core set).
- Boot: `main.tsx`/`App.tsx` parse the query; a session-booted window skips the
  normal auto-connect and runs `adoptRuntime` — `rpc_alive` ? handshake :
  `rpc_start` — then pins project + current session, loads the session snapshot
  (session-routed) and renders the same coding surface without the sidebar
  (no mode switcher in a detached window).
- The main window removes the pane on detach; both windows hear the
  `zen:session-detached` broadcast. Closing a detached window only closes the
  window; the runtime and the session are untouched (main-window close still
  hides instead of quitting, as today).
- In a plain browser (dev/e2e) the detach falls back to a no-op notice —
  window creation is Tauri-only.

### Testing matrix

- Python: session-routed routing lands `prompt`/`status`/`transcript`/`cancel`
  on the holder slot; a busy sibling keeps running; unknown session falls back
  to project/active behavior (v2 parity).
- Desktop unit (`desktop/tests`, `node --test`): pane registry (open/close/cap/
  focus), frame router pane routing (events + usage + finalize), session-routed
  request tagging, chat-store factory independence, detach decision logic
  (outside-bounds check), messages-panes locale parity.
- E2E (browser): open a pane from the sidebar, two panes render, focused pane
  composer submits, close pane returns the single-view layout.
- Gates: `pnpm build` (tsc), `pnpm lint:tokens`, architecture gate, pytest.

## Acceptance criteria

1. With 2–3 panes open, each shows its session's transcript and live-updates
   while another pane's session is running.
2. A prompt typed in pane B goes to session B; session A's run is unaffected;
   session A's transcript keeps streaming.
3. Dragging a pane header outside the window (or clicking pop-out) opens an
   independent window on that session; the main window drops the pane; the
   runtime is shared (one sidecar process).
4. Closing the detached window does not stop the session.
5. With no panes open, the app behaves exactly as before (all existing tests
   pass; e2e smoke for the default flow unchanged).
6. All gates green: pytest, desktop unit tests, tsc build, token lint,
   architecture budget.

## Builder's leeway (my calls, already made)

- Pane cap = 3 (host caps resident slots at 4; the focused session's slot plus
  two panes stays inside it with room for a zen router).
- Slim composer inside panes (no image picker/mentions in v1); the full dock
  returns for the focused session whenever the strip is closed.
- Detached window shows the same coding surface minus the sidebar.

## Amendments (builder check, 2026-11-18)

The builder check returned READY-WITH-CHANGES; these amendments are part of the
design and the implementation must follow them.

1. **Non-resident sessions never fall through (blocker).** The host *refuses*
   (`error`, code `session_invalid`, `no resident slot for session`) any
   slot-level request whose `session` tag names no resident slot — instead of
   silently serving the active slot. Cross-project guard: when the request also
   carries a `project` tag, the holder's resolved root must match it, else the
   same refusal. The session rule sits before the project rule in `_slot_for`
   with the holder LRU-refreshed (`move_to_end`) like the project branch.
   Client side: pane open tries `transcript {session}` → on refusal falls back
   to `zen_fleet op=transcript` (works for any session file, fleet not
   required) and paints a **static** pane; a running session always has a slot,
   so live streaming works exactly when it matters. `submitPane` and
   `focusPane` resume first (making the session resident), then send tagged.
   A tagged reply whose `session` tag ≠ the requested path (an older runtime
   that ignored the tag) is treated as a failure and triggers the same
   fallback — graceful degradation without the new host.
2. **Auto-tagging exclusions (blocker).** `RpcClient.send` tags the current
   session only when the payload has no `session` field, and never for
   `hello`, `project`, `terminal` (its `session` is a shell token),
   `runs`, `supervisor`, `swarm`, `zen_fleet`, `workspace`, `shutdown`.
   Caller-set `session` fields always win.
3. **Per-window request ids (blocker).** Client ids get a per-process random
   salt (`r<salt><n>`, never containing `:` — the host reads an id prefix as a
   slot key), so two windows sharing one pending map cannot collide.
4. **Close handler scoping (blocker).** The builder-level
   `CloseRequested → hide` handler applies to `label == "main"` only;
   detached windows close normally and never touch the runtime.
5. **Frame routing order.** Pane routing slots in after the project guard and
   before the current-session guard, in both the event and result branches;
   swarm/fleet consumers stay ahead of it (router frames carry a session tag).
   Pane-addressed error frames land in the pane as a failure row, not only on
   the shell banner. `usageBaselines`, `showFrameError` and the `FrameHandling`
   seam move into `frameRouter.ts`; `appStore` imports from it (no reverse
   import).
6. **Slot-pressure degradation.** Panes ≤ 3 total; `project_busy` / `session_lease_busy`
   refusals surface as notices. An idle pane whose slot is LRU-evicted detects
   it via the refusal in amendment 1 and shows a "load session" affordance
   (click = resume + snapshot). Detached-window boot pins the project from the
   query (`project open {root, resume, background: true}`, then `session
   resume` only if the slot holds a different session) and does not write the
   shared `LAST_CWD_KEY`.
7. **New files for everything new.** E2E specs go in a new
   `desktop/e2e/panes.spec.ts` (the main spec is allowlisted and may only
   shrink). Browser/dev detection of "can create real windows" is a
   try/catch around `WebviewWindow` creation (the dev bridge installs
   `__TAURI_INTERNALS__`, so internals presence is not a Tauri signal);
   `rpc_alive` failing in dev falls back to the normal spawn path. The
   constraint wording "one webview" is corrected: the shell owns exactly one
   *runtime*; webviews are many.
