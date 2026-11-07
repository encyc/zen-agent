# Changelog

All notable changes to ZenAgent (`zenagent-py`). The version in this file
matches the release tag; older history is in the repository tags and commits.

## Unreleased

### Added

- **`edit_file` and `write_file` refuse to touch a file the model has not read
  in this session.** A successful `read_file` fingerprints the file (mtime +
  size); a write to an existing file whose fingerprint is missing or stale —
  modified since the read by the user, a formatter or a shell command — is
  refused with an error that says to read again, so `old_string` anchors come
  from content the model actually saw and concurrent external modifications
  fail loudly. A successful write refreshes the fingerprint (follow-up edits
  need no re-read), and creating a new file never needs a read. The gate and
  its tests live in `zenagent/tools/read_state.py`; the tool descriptions
  state the contract (prompt version 2026-11-07.1, tool budget 4800 → 5200).

### Fixed

- **Quitting the desktop shell no longer leaves an empty session behind.** The
  exit teardown (`kill_all`) and the post-update restart (`relaunch_app`)
  SIGKILLed the `zen rpc` child, so its own shutdown path — the one that drops
  the session file a launch never wrote a message to — never ran, and every
  app open added one more `(empty)` row to the listings. Both paths now use
  the same graceful stop as `rpc_stop`: a `shutdown` request first, a short
  grace period, then a hard kill for a stuck child.

## v0.9.1 — 2026-11-07

**The updater works end to end, and closing the window keeps the agent alive.**

### Fixed

- **The desktop update button could do nothing.** A banner restored from the
  cached check had no deferred download handle — the plugin's `check()` result
  lives only in the session that ran it — so clicking *Update* after a relaunch
  silently returned. `install()` now re-checks once (surfacing a real failure
  if that check fails) and then downloads.
- **macOS updates downloaded a 404.** `latest.json` pointed the two darwin
  entries at `ZenAgent.app.tar.gz` while the release assets carry
  architecture-suffixed names (`ZenAgent_aarch64.app.tar.gz`): the app
  detected the new version but the download failed. The live manifest was
  hot-fixed for v0.9.0 and the fragment generator now mirrors tauri-action's
  rename, so this cannot recur.

### Added

- **Closing the window goes to the background.** The zen runtime and its
  background tasks stay alive; the tray icon (or the macOS dock) brings the
  window back, and *Quit ZenAgent* in the tray menu is the only path that
  ends the runtime.

## v0.9.0 — 2026-11-07

**The architecture cleanup: same behavior, fewer copies, harder guards.**
Full audit in `docs/reviews/2026-11-07-architecture-review.md`, task-by-task
record in `docs/superpowers/plans/2026-11-07-architecture-cleanup.md`.

### Changed

- **One bootstrap for every frontend.** REPL, headless, the Textual TUI and
  the JSONL RPC server each carried their own broker → RuntimeOptions →
  `Runtime.bootstrap` ritual, and the copies had already drifted (only RPC
  forwarded `read_only`). `zenagent.app.bootstrap.prepare` is the single
  sequence now, pinned by tests that run all four modes.
- **Tool protocol lives in `zenagent/agent/protocol.py`** (`AgentTool`,
  `ToolContext`, `ToolResultAgent`) — tools, MCP, skills and extensions no
  longer import the whole loop for two dataclasses, and the loop's one
  deferred import is gone. `StreamOptions` moved to
  `zenagent/providers/stream_options.py` for the same reason.
- **The subagent runtime moved out of the task tool**: `tools/task.py` 854 →
  320 lines; process supervision, the watchdog and child construction now
  live in `zenagent/agent/subagents_runtime.py`.
- **Ask/subagent-observer handlers are per-Runtime slots**
  (`runtime.ask_handler`, `runtime.subagent_observer`) instead of module
  globals — two agents in one process can no longer race for the
  interactive surface.
- **Usage aggregation, slash-command completion and the root package facade**
  each collapsed to one source (`views.usage_total`, the live registry,
  `from zenagent import Agent, agent_loop`).

### Added

- **RPC protocol version handshake**: clients send `hello`, the server
  answers `protocol_version`; a mismatch reaches the desktop diagnostics
  card instead of silently mis-rendering. `tests/test_rpc_contract.py` pins
  request kinds and the hello frame across Python and TypeScript.
- **LOC budget gate** (`scripts/check_architecture.py` +
  `docs/architecture/allowlist.json`): new source files cap at 800 lines,
  legacy giants are allowlisted and may only shrink; wired into CI and the
  release check.
- **Explicit `kind` discriminator on tool views** — the desktop no longer
  guesses view types from field shapes.

### Desktop

- **Composer @ file mentions with token boxes** — type `@` to fuzzy-search
  workspace files, attached as visual tokens instead of raw paths, with
  deterministic `fuzzy`/`mentions` stores under unit test.
- **Skills join the composer slash menu**, and the desktop gains settings
  pages for MCP servers and skills (layered global/project config with
  0600-private writes, API keys stored without echo) plus project state
  relocation with collision refusal.
- **New app icon**: a geometric black-and-white taiji drawn
  programmatically (no more AI-generated artwork, no watermark), rendered
  at 4× and regenerated across every Tauri platform size.

### Fixed

- **Desktop: strict CSP** replaces `"csp": null`; the login-shell
  environment harvest now times out after 3s instead of hanging every
  connect on a broken shell profile; streaming chat updates locate the
  assistant node from the tail instead of scanning the whole transcript
  per delta.
- **Providers share one `httpx.AsyncClient`** (`get_shared_async_client`)
  — a fresh TLS handshake per streaming call was the review's measured
  throughput friction; large `read_file`s run off the event loop.
- **The crash-log excepthook is installed at CLI startup** — `crash.py`
  existed, was tested, and was never wired to anything.

## v0.8.4 — 2026-11-06

**The macOS bundle opens, and the update manifest can actually be built.**

### Fixed

- **Gatekeeper no longer calls the macOS app "damaged".** The bundle shipped
  with no signature of its own: the main executable carried only its linker
  signature, which claims a `CodeResources` seal that never existed. Tauri now
  signs the bundle ad-hoc (`signingIdentity: "-"`). It also sets
  `hardenedRuntime: false`, because a hardened runtime refuses to `dlopen` the
  `libpython` that the frozen sidecar unpacks at launch — a signed sidecar that
  cannot start is worse than the "unidentified developer" prompt it replaced.
  macOS users still have to right-click → *Open* once (or clear the quarantine
  flag); only notarization removes that, and it needs a paid certificate.
- **`latest.json` was never published.** Each leg wrote its fragment to the
  same `fragment.json`, and the merge step flattened six artifacts into one
  directory — so five platforms overwrote each other and the aggregation job
  aborted on its six-platform assertion. Each leg now writes
  `fragment-<target>.json`.

## v0.8.3 — 2026-11-06

**The desktop app became installable: six bundles (macOS, Windows, Linux ×
x86_64/arm64), each carrying its own frozen `zen` runtime, with signed in-app
updates. The right rail grew into a work panel, the model can ask structured
questions mid-run, and long transcripts stream in a bounded window.**

### Added

- **Downloadable desktop builds.** `.github/workflows/desktop-release.yml`
  freezes the CLI with PyInstaller on each platform's own architecture and
  bundles it as a Tauri sidecar (`zen-<triple>`), then publishes `.dmg`,
  NSIS `.exe`, `.AppImage` and `.deb` on the GitHub Release for a `v*` tag.
  `scripts/build-sidecar.py` smoke-tests the frozen binary by running
  `zen rpc` with no provider key: reaching `missing API key for <model>`
  proves `catalog.yaml` and the prompt blocks survived packing, which
  `--version` alone does not.
- **The shell runs its bundled runtime first.** `resolve_bin` now looks beside
  its own executable before any PATH lookup, so an installed app never
  silently borrows a different `zen`.
- **A "no runtime" screen instead of a spawn error.** `rpc_start` returns a
  structured `runtime_missing` reason and the exit screen shows the resolved
  path, the OS error and a copy-diagnostics button.
- **In-app updates** (`tauri-plugin-updater`), with the same politeness as the
  TUI notice: cached for an hour, silent on failure, one notice per version,
  switchable in Settings (and by `ZENAGENT_NO_UPDATE_CHECK`). Installing is
  blocked while a turn runs or an approval waits, since it restarts the app.
- **The model can ask questions mid-run.** A new `ask` round-trip beside
  approvals: an `ask_request` event carries the questions, the client answers
  with `{"type": "ask", "request_id", "answers"}`, and the server separates
  picked labels from free-form text into `selected`/`custom` so the tool sees
  both. The desktop renders it as a composer takeover card with per-request
  drafts, and drops the card when the run is cancelled.
- **A work panel instead of a single right rail**: rendered review diffs, a
  read-only workspace file viewer, worktree switching, and a task's subagent
  transcript. The new `zenagent/app/workspace.py` keeps every path scoped to
  the project root and degrades to a small error rather than raising into the
  RPC loop.
- **A todo checklist above the composer**: the status phases flatten into one
  progress header with the current step, expanding into the full list.
- **Conversation settings**: thinking visibility and folding, the context
  readout, and Enter-to-send.

### Fixed

- **File browsing works on Python 3.11 and 3.12 again.** The workspace listing
  used `Path.is_dir(follow_symlinks=...)`, which only exists on 3.13+; it now
  inspects `lstat` itself, so a symlinked directory still reads as a file
  without leaving the workspace.
- **Updater fragments point at the file the updater downloads.** Tauri v2
  publishes the `.app.tar.gz` on macOS but the `.AppImage` and the NSIS
  `-setup.exe` themselves on Linux and Windows; the fragment script looked for
  tar/zip wrappers and exited 3 on four of six legs.

### Changed

- **New logo.** A single yin-yang mark (`assets/logo.png`, cropped to
  `desktop/app-icon.png`) replaces the old icon set; every platform icon is
  regenerated from it with `tauri icon`.
- **Long transcripts stream smoothly.** The chat mounted every row and
  re-rendered all of them on each delta; it now mounts a trailing window (15
  rows on the first commit, 60 steady state, +40 per reveal) with viewport
  anchoring, and grows when the reader scrolls to the top. The minimap spans
  the whole loaded session.

## v0.8.1 — 2026-11-06

**Architecture review follow-through: a truncated tool call can no longer be
executed, subagents answer to the same permission gate as their parent, and the
harness got the layer it was missing — `zenagent/app/` — with tests that keep
the layering and the documentation honest.**

### Fixed

- **A killed command no longer reads as a clean run.** `bash` keeps `is_error`
  false for a non-zero exit, a timeout and an abort — that is the model's
  contract (command failure is information) — but the tool card settled as
  `ok` for all of them, so `exit code: -9 … [timed out after 120s; process
  group killed]` drew the same green dot as a clean run and the detail only
  showed up when expanded. `views.settle_tool_status()` now derives the
  *presentation* status from the result details: `timed_out` when the deadline
  fired, `interrupted` when the call was stopped mid-flight (an abort, or a
  signal from outside — both leave a negative exit code), `error` for a real
  tool error, `ok` otherwise, so `grep` exiting 1 still reads as an ordinary
  result. The live builder and history replay (via the stored `tool_end`
  record) share it, and both frontends render it: the TUI card labels and tints
  a timed-out command as failed, and the desktop row says "Timed out" instead
  of showing the check mark.
- **An unused launch leaves no session file behind.** Bootstrap writes the
  session header and the starting `mode` record eagerly, so every `zen` launch
  put an `(empty)` row in the listings even when nothing was said. `Runtime`
  now remembers which session files this process created and `close()` removes
  the ones no message ever landed in — a launch that is opened and closed, one
  that only changed the permission mode, and one whose run was interrupted
  before the first message was persisted. Abandoned files count too: starting
  fresh and then switching to another session does not orphan the first. Empty
  files are unlinked rather than moved to `.trash/` (trashing would keep the
  junk), a resumed file is never removed even when it is empty, and the TUI's
  exit summary says "session not saved" instead of printing a path that is
  about to disappear.

- **Truncated tool calls are no longer executed.** Both adapters could report a
  finished `tool_use` turn whose arguments were cut off, which made the loop's
  "never execute a truncated call" guard unreachable for them. Google now keeps
  a `MAX_TOKENS` `length` verdict even when a `functionCall` arrived, and the
  Responses adapter treats OpenAI's real `response.incomplete` event (and the
  `status: "incomplete"` some compatible servers send on `response.completed`)
  as truncation, so the model gets `is_error` results for the partial calls
  instead of the agent running them.
- **Subagents go through the permission gate.** `task` built its child `Agent`
  without a `before_tool_call`, so in zen mode a child saw every tool with no
  policy: `tool_deny` patterns, the catastrophic-command check and
  `--read-only` never applied inside a subagent. The parent's gate is now passed
  into `build_task_tool`, and `--read-only` is honoured by every frontend's gate
  (only the RPC used to forward it) — an explicit `/mode` still lifts it.
- **`zen -p` sessions keep tool durations and diffs.** `tool_end` records were
  written by the TUI's chat controller, so a headless session was missing them;
  the runtime's own listener writes them now, once per call, for every frontend.
- **`zen doctor --report` lives with the doctor checks** (`zenagent/cli/doctor.py`);
  `zenagent/crash.py` writes crash logs and no longer imports the CLI.

### Added

- **The RPC ships the whole status snapshot.** `status` carries the complete
  `RunStatus` (`status` key, nested context/pending/jobs/subagents included)
  next to the flat keys it always had; additions reach clients on the commit
  that makes them instead of waiting for someone to remember the field list.
- **Three gates that keep the architecture true**: `tests/test_layering.py`
  (imports point downward; `app/` imports neither `zenagent.cli` nor a UI
  library — it caught a real `app → cli` import), `tests/test_docs_paths.py`
  (every `zenagent/*.py` a reference doc cites must exist — it caught a stale
  `providers/retry.py`), and `tests/test_app_records.py` for the record
  vocabulary.

### Changed

- **One owner for the session record vocabulary** (`zenagent/app/records.py`:
  type constants, builders, predicates, `keep_for_rewrite`). The kernel's
  `carry_records`/`fork_at`/`rewrite` now require a `keep` predicate and know no
  record kind — only the structural rule that a record carrying a `call_id`
  follows the calls that still exist — so adding a record type no longer means
  editing the kernel.
- **`zenagent/app/` is documented as the application layer**: `architecture.md`
  draws it, `file-map.md` gains the table, `tools-internals.md` describes the
  real `task` contract (async dispatch, inherited toolset, child transcripts,
  the parent gate), and `contracts.md` drops three stale claims (the
  tool-execution `try/except`, the missing `pending` stop reason, `--resume`
  never writing).
- **Entropy reduction, no behaviour change**: tool results and truncation come
  from `zenagent/tools/_result.py` (nine `_text` and three `_clip` copies
  collapsed into one helper with one notice), the loop guard moved to
  `zenagent/agent/guard.py`, tool execution to `zenagent/agent/tool_exec.py`
  (`agent/loop.py` 815 → 538 lines), and every frontend builds its policy gate +
  broker through `zenagent/app/permissions.py::build_broker_for`.


## v0.8.0 — 2026-11-06

**Frontend alignment: RPC and the REPL stop dropping capabilities the other
frontends already had, and headless `-p` stops assembling a second-class agent.**

### Fixed

- **Clicking the transcript while it streams no longer kills the TUI.** With
  `ALLOW_SELECT` on, a MouseDown/MouseMove that landed on a Markdown block
  during a rebuild died in Textual's `Screen._forward_event`
  (`AttributeError: 'NoneType' object has no attribute 'region'`) and took the
  app down. `Markdown.update` removes every block before mounting the
  replacements inside one `batch_update`, so the compositor keeps answering hit
  tests with the removed `MarkdownParagraph` (its `parent` is already `None`)
  for one event-loop turn. `ZenAgentApp.on_event` now drops pointer events whose
  target is detached — there is nothing to click — and `test_tui_selection.py`
  pins the window.
- **Streaming answers append instead of rebuilding the document.** Every text
  delta called `Markdown.update`, which re-parsed the whole answer (in an
  executor) and re-mounted every block: O(document) per delta, and the window
  the crash above lives in. The transcript's assistant widget is now a
  `StreamingMarkdown` (`zenagent/cli/tui/widgets/markdown_stream.py`) that uses
  Textual's incremental `Markdown.append`: only the uncommitted tail is parsed
  and the last block is updated in place, so the paragraph (and an open code
  fence) the user is looking at keeps its widget. One pump renders the newest
  text, so a burst of deltas collapses into one append, and a canonical rewrite
  (retry, session switch, provider-corrected text) falls back to a full update.
  Text that arrives before the widget finished mounting waits for the mount
  instead of being dropped, `tests/test_tui_markdown_stream.py` pins the append,
  the rewrite, the coalescing, a streamed fence and the pre-mount case.
- **RPC starts without `--mode`.** `zen rpc` (the documented invocation) used to
  crash at startup with `unknown mode: None`; the mode is now resolved like the
  other frontends (`--mode`, then `--read-only`→plan, then `--yes`→zen),
  `[tools]`/`[safety]` and the guard flags reach the permission gate, and
  `--mode`/`--thinking` are argparse choices (invalid values exit 2 instead of
  tracebacking).
- **RPC reports turn failures.** `prompt` results were always `ok:true`; a
  provider failure now returns `ok:false` plus the `error` text, and `text`
  falls back to the final assistant message when a provider publishes no
  deltas.
- **REPL honours the budget flags.** `--max-turns`/`--max-cost`/`--retries` were
  accepted by argparse and dropped in the REPL branch; they are now forwarded
  to `RuntimeOptions` like the TUI's. A failed turn also prints the provider
  error and any drained `ChatController` notices instead of ending on a
  neutral status line.
- **Headless `-p` matches the Runtime fronts.** Project `memory`/`memory_write`
  tools and the `## Project memory` prompt block, `ToolRegistry`
  `hidden`/`default_inactive` filtering, and the run's `finally` now releases
  cached LSP clients and background bash jobs (previously left to process exit).
- **`-p` and `--list-sessions` no longer silently ignore each other**: the
  combination is rejected with exit 1 instead of printing the session list and
  dropping the prompt.
- **A typo'd `--image` path fails before assembly.** The one-shot run used to
  check it after the Runtime was built, so the refused run left an empty session
  file behind for `--list-sessions` to render as `(empty)`. Content and vision
  checks still need the model, so they stay where they were.
- **`zen evals` no longer ImportErrors.** `scripts/eval.py` imported
  `_api_key_for`/`session_id_for` from the CLI module that the assembly
  delegation emptied; it reads them from `zenagent.app.support` now, which is
  where the Runtime reads them too.

### Added

- **RPC event and status richness.** Events carry bounded `message`/
  `new_messages` projections, tool `details` and the assistant `error`; `status`
  carries the TUI's reducer fields (thinking, context used/window, cache hit
  ratio, todos, pending lanes, jobs, subagents, subagent spend and tokens);
  `command` results carry `target` and `/lang` applies server-side.
- **RPC images and runtime flags.** `prompt` accepts `images: [path, ...]`
  (vision-checked, same loader as `--image`); `--no-templates`,
  `--no-auto-compact` and `--extension` are plumbed into `RuntimeOptions` like
  the other frontends.
- **RPC snapshot verbs for embedders.** `transcript`, `models` and `commands`
  return what the TUI reads in-process: the reducer's view models plus the
  pending notices (the baseline a GUI applies live events on after a resume or
  a session switch), the catalog behind the model picker, and the slash-command
  catalog with descriptions. Purely additive to the v1 message shape;
  `ChatController.notices_snapshot` exposes the queue without draining it.
- **Session side-channel records.** Session files gain dsh-style auxiliary
  records between the message lines (top-level objects with a `type` and no
  `role`): a `session` header on every new file (id/createdAt/cwd/app
  version, `derivedFrom` for fork/undo/compact/shake copies), `run_start`/
  `run_end` per run (termination reason, request count, duration, terminal
  error detail — previously an aborted run just stopped mid-file), `retry`
  records for transient provider failures that message-level retry swallowed
  invisibly, and `mode` records for bootstrap mode and `/mode` switches.
  `Session.load` collects them into `records`/`header` without warnings;
  export/usage/listing consumers are untouched, and record writes are
  best-effort (`append_meta` swallows I/O errors) so observability can never
  kill a run.
- **Session files reconstruct the GUI, not just the conversation.** View snapshots
  now carry stable `v{n}` ids and the assistant's stored timestamp/model/provider/
  usage/duration; `tool_end` records keep tool durations and bounded details
  (including diffs) across resume and rewrite/fork/undo; `compaction` records
  expose a single divider through `transcript.compactions`; user messages
  delivered by steering carry `"steering": true`; and `session list` reports
  `created_at`, `updated_at`, `model` and `derived_from`.

### Changed

- **Headless `-p` is assembled by the Runtime.** `run_headless` no longer builds
  its own agent: it validates the image paths, constructs `RuntimeOptions` and
  calls `Runtime.bootstrap`, keeping only the output contract (`-p` text,
  `--json`, auto-compaction, exit code) and a guarded `runtime.close()`. The
  parallel `_build_agent` path (~330 lines) is gone, so session side-channel
  records, journal/backups, the job-manager refcount, `hidden`/
  `default_inactive` tool filtering and the guard policy now come from the same
  assembly as the TUI/REPL/RPC. The ~40 tests that pinned `_build_agent` moved
  to a pass-through `cli_main._bootstrap` seam, so they still assert against the
  real Runtime; `tests/test_headless_parity.py` pins the option set the `-p`
  front is allowed to differ in.
- **The test gate is deterministic.** `tests/test_tools_github.py`'s fake `gh`
  now uses `sys.executable` instead of a bare `python` (absent on machines with
  only `python3`), and a committed RUF059 in `tests/test_repl_logic.py` no
  longer fails `scripts/check.sh` at `HEAD`.
- **Review dispatch contract (prompt version 2026-11-05.1).** `tool.task` and
  the tool policy now tell the parent to hand reviewers the implementer's
  test results and never let a review run suites itself, and to split diffs
  over ~600 lines into per-file-group reviews whose findings the parent
  merges. Evidence: a reviewer faithfully running `cargo test` + `vitest`
  per the old template was cut by the 900s watchdog at 1.9M tokens; the same
  review without test runs finished. Verified-by-reading stays the
  reviewer's job; verified-by-running belongs to the implementer and the
  parent's join gate. Prompt goldens regenerated; no runtime behavior change.
- **`release-check.sh` gates ruff.** Style errors can no longer ride a
  release tag into CI red — the 0.7.9 CI failures were exactly this gap
  (lint errors shipped in the tagged tree while the publish gate passed).

## v0.7.9 — 2026-11-04

**Subagents go async: parallel background children with a worker contract.**
Spec: `docs/superpowers/specs/2026-11-04-async-parallel-subagents-design.md`;
plan: `docs/superpowers/plans/2026-11-04-async-parallel-subagents.md`.

### Added

- **Async dispatch.** `task` gains `async: true` (mirrors `bash async`): the
  call returns a `task_N` id at once and the child runs as a background
  coroutine; `wait` collects the full envelope later. Dispatch freezes the
  child's toolset/mode/model; the child is pinned to its own transcript
  store, so compaction/undo/fork/clear never interrupt it (process-level
  lifetime, like bash jobs). ESC only affects synchronous children.
- **Per-dispatch `model`** (`id` or `provider/id`; an ambiguous bare id
  errors listing the qualified forms; missing API key fails fast naming the
  env var). Known limitation: `files_touched` sees write/edit calls only —
  writes through bash are invisible to the manifest.
- **Worker contract.** Envelopes now carry `files_touched` (extracted from
  the child's own write/edit calls, not self-reported); the child prompt
  adds file-scope discipline, a changed-code test requirement and ~10-turn
  checkpoints. Dispatch guidance: parallel workers own disjoint files; the
  parent re-runs the full suite after collecting them (one worker's green is
  invalidated by its siblings' edits).
- **`wait` collects tasks** (`task_N` ids), forwarding `subagent_cost`/
  `subagent_tokens` so background spend lands in the HUD and telemetry.

### Fixed

- Background children showed as `interrupted` in the sidebar for their whole
  run; they now show `running` (the TaskManager's in-flight set is the
  source of truth) and their spend is counted when `wait` collects them.

### Changed

- Prompt version 2026-11-04.1: worker contract in `task_child_system`,
  async/model/join-verify guidance in `tool.task` and `tool_policy`,
  task-id collection in `tool.wait`.
- `TOOL_TOTAL_BUDGET_CHARS` 4400 → 4800 (two tools gained real semantics).

## v0.7.8 — 2026-11-04

**Subagents grow up: the child mirrors the parent's capability, and runaway
runs stop burning tokens for nothing.** Motivated by a session-log audit
(12 dispatched runs, 67% failed; 95% of all subagent tokens evaporated in
no-output runs).

### Added

- **Mode-following child toolset.** The child's tool ceiling is now the
  parent's current permission mode: in zen it sees the parent's active tools
  (live-resolved per dispatch, MCP and `read_skill` included) minus a small
  denylist (`task`, `ask`, `wait`, `todo`, `rewind`, `memory*`, `checkpoint*`);
  every other mode stays read-only. The hardcoded six-tool allowlist is gone.
- **`[task].timeout_s`** (default 900, `0` disables): a wall-clock watchdog
  aborts a runaway child child-side; the envelope reports
  `status="truncated" reason="timeout"`.
- **`[task].max_cost`**: a per-run spend cap for children, overriding the
  session's `--max-cost` when set (envelope `reason="max_cost"`).
- **Checkpoint discipline** in the child system prompt: post a short findings
  checkpoint every ~10 turns, so a budget-cut run still returns usable output;
  changed code must be tested and the results included.

### Changed

- **Partial-output rescue.** When a child is cut off before its final message,
  the envelope now carries the newest earlier assistant text (usually the
  checkpoint) instead of `(no output)`.
- **Dispatch checklist** in the parent-facing `task` description and tool
  policy: confirm referenced files exist, precompute artifacts the child
  cannot produce (write a diff to a file, pass the path), state the output
  contract. Prompt version bumped to 2026-11-03.1.
- **`read_file` pages widened**: default 400 → 1000 lines, hard cap
  2000 → 5000 — page size trades cheap context for the scarcest budget (turns).

## v0.7.7 — 2026-11-03

- Refactor: `ZenAgentApp` (TUI) split from 1,903 lines into per-concern mixins
  (`cli/tui/app_parts/`), picker flows (`cli/tui/screens/`), a `SidebarResize`
  state object, and a guarded `SCREEN_OPENERS` registry; `ChatController`
  sheds three state objects (`PendingQueue`, `Attachments`, `SubagentTracker`)
  and `Runtime` its metrics (`MetricsSession`). No behavior change; the full
  suite (2,054 tests) and every doc guard stay green. `app.py`:
  1,903 → 715 lines.
## v0.7.6 — 2026-11-03

**`/memory` becomes a manager for the project memory library.**

### Added

- **`/memory` command** (TUI panel + REPL text forms, one shared command
  layer). Bare `/memory` opens the project-memory panel in the TUI
  (`a` add · `e` edit · `x` delete with confirm · `esc` close; every action
  re-reads the store, so notes the model writes underneath stay visible).
  The REPL renders the same intents as text: bare `/memory` prints the
  index, `/memory <id>` prints one entry in full, `/memory rm <id>` deletes,
  and `/memory add [title]` / `/memory edit <id>` hand a `title:`+body draft
  to `$VISUAL`/`$EDITOR` (no editor configured → a clear message, nothing
  written). All UI writes go through `MemoryStore` with `source="ui"` and a
  journal entry — the model's `memory` tools see the same files.
- **`split_title_body`** in the memory kernel: `title: X\n\nbody` parses to
  `(X, body)`; text without a leading title line stays all body and the
  store coerces the title to `(untitled)` — never a crash.
- Slash-command completion entry (`/memory`) and `zenagent.cli.editor`
  (`run_editor`) for line-based frontends.

## v0.7.5 — 2026-11-03

**Three permission modes: `plan` / `ask` / `zen` — `full` is gone.**

### Removed

- **`full` mode is gone; `zen` replaces it.** The mode vocabulary is now
  `plan / ask / zen`: plan is read-only, ask confirms every write/execute
  (risk tiers included), and zen runs everything without asking — destructive
  commands (`git reset --hard`, `sudo …`), protected credential paths and
  writes outside the workspace included, headless runs too. The old `full`
  ("ordinary work automatic, risky actions still confirm") sat between ask and
  zen with a semantics that was hard to explain and backed by best-effort
  shell heuristics; if you want "ask but quieter", stay in `ask` and relax
  individual tiers via `--allow-*` flags or the `[safety]` config.
  `--mode full` is now an unknown-mode error; `/mode full` in a session
  reports the same.

### Changed

- **`--yes` now maps to `zen`** (it used to map to `full`). Headless scripts
  keep working, but note the widened meaning: destructive commands and
  outside/protected writes that headless `--yes` runs used to refuse now
  execute. The system prompt in zen tells the model to act instead of asking
  permission, and `/plan-review` approval switches to `zen` (was `full`).

## v0.7.4 — 2026-11-02

**17 providers out of the box: every mainstream API, no new adapter code.**

### Added

- **14 more providers in the packaged catalog** (3 → 17 providers, 8 → 229
  models), synced from `@mariozechner/pi-ai@0.73.1`: `openai`, `zai`, `kimi`,
  `moonshotai` / `moonshotai-cn`, `minimax` / `minimax-cn`, `google`, `xai`,
  `xiaomi` plus its three token-plan endpoints, and `openrouter`. Overseas and
  China endpoints are separate provider ids, so a key that only reaches one of
  them still works; `moonshotai` and `moonshotai-cn` share `MOONSHOT_API_KEY`.
- **Not one line of adapter code.** Every added provider speaks one of the four
  protocols already registered, which is why this release is catalog data only:
  `openai-responses` for `openai`, `anthropic-messages` for `kimi`, `minimax`,
  `minimax-cn` and `xiaomi` (their base URLs deliberately omit `/v1` — the
  adapter adds it), `google-generative-ai` for `google`, and
  `openai-completions` for the rest. `docs/internals/providers.md` now says how
  to tell a catalog-only addition from one that needs an adapter.
- **OpenRouter with 82 curated models** rather than all 275: the vendors we
  mirror directly, newest generation only, and no colon-suffixed variants —
  the catalog tokenizer splits keys on `:`, so an id like
  `anthropic/claude-3.7-sonnet:thinking` cannot be expressed at all.

### Fixed

- **`zen doctor` no longer fails on keys you never configured.** With 17
  providers, treating every absent key as a failure made a fresh install exit 1
  (it reported 10/10 passed before this release). A missing key now prints `--`
  and still names the env var, without failing the run; a key that is present
  but broken still fails.
- Cost values from the sync are rounded to 10 significant digits:
  pi-ai's JSON floats arrive as `0.09999999999999999`, which would otherwise
  show up as noise in the catalog and the UI. The finest real price carried
  (`0.0028`) is unaffected.

### Tests

- `tests/test_catalog_pi_ai.py` pins the expansion: per-provider adapter and
  model count, every model on a registered adapter, no colon ids, the
  hand-tuned `zai-coding-cn` compat surviving a re-sync, and anthropic-style
  base URLs free of `/v1`. Each guard was mutation-tested (inject a colon id, a
  `/v1` suffix, a clobbered compat flag, an unregistered adapter, a dropped
  model) to prove it is not vacuous.
- Two existing tests hard-coded the catalog's size and now derive it from
  `load_catalog()`, so adding providers does not break them.

## v0.7.3 — 2026-11-02

**The README became a landing page, and the project got a license.**

### Changed

- **`README.md` is a landing page now, not a manual** (377 → 109 lines): install and
  a first run, why this exists, a feature table, and contributing — nothing else.
  Everything reference-grade moved into `docs/`, which is where the manual lives:
  the 47-command table is now `docs/guide/commands.md`, the test/release flow is
  `docs/development.md`, and `docs/guide/config.md` finally documents the `[task]`
  subagent budget section. A Chinese translation ships as `README.zh-CN.md`; the two
  link to each other, and both use absolute URLs because PyPI renders `README.md`
  verbatim as the project page.
- **Docs gaps filled while moving:** `[task]` (`max_turns`, transcript retention) was
  only ever described in plan documents, and the `memory` / `memory_write` tools —
  shipped in 0.7.0 — were missing from the tool table. Both are documented now.
- **The README's Python claim is pinned to the package**: it said "Requires Python
  3.15+" while `requires-python` is `>=3.11`, and that wrong floor reached PyPI.

### Added

- **MIT license** (`LICENSE`, plus PEP 639 `license = "MIT"` and `license-files` in
  `pyproject.toml`), so the metadata and the PyPI sidebar agree.
- **Two guard tests** keep the split from collapsing again: `tests/test_readme.py`
  (≤130 lines, exactly four sections, no relative links, links resolve, Python floor
  matches `pyproject.toml`) and `tests/test_docs_commands.py` (every registered
  `_command` has a row in `docs/guide/commands.md`, and the README's command count is
  the real one).

### Fixed

- `pyproject.toml`'s `Changelog` URL pointed at the raw commit list instead of
  `CHANGELOG.md`.

## v0.7.2 — 2026-11-02

### Changed

- **The default subagent turn budget is 300, not 30**
  (`zenagent/config.py::DEFAULT_TASK_MAX_TURNS`). Long read-only
  investigations — sweeping a repo, cross-checking a dozen files — were
  running out of turns and coming back `[cut]` (`status="truncated"`,
  `reason="max_turns"`) before they could report. 300 leaves room for them while
  the parent run keeps its own 100-turn cap (`_MAX_TURNS`). `[task].max_turns`
  still overrides it, and the transcript/cost/40 KB-result budgets are
  unchanged, so a wider turn budget does not widen any other one.

## v0.7.1 — 2026-11-02

### Changed

- **Drag-to-select is back on** (`zenagent/cli/tui/app.py::ALLOW_SELECT`): dragging
  over the transcript selects text, and `ctrl+c` copies the selection instead of
  clearing the draft (no selection still clears it — the same split omp gives
  `tui.input.copy`). It had been switched off after a Textual 8.2
  `Screen._forward_event` crash on Markdown blocks; that crash does not reproduce
  on the pinned 8.2.8, and `tests/test_tui_selection.py` pins drag, the `ctrl+c`
  split and dragging during streaming so it cannot come back silently.

### Added

- **`/urls`, `/copy <n>`, `/open <n>`** (`zenagent/app/links.py`): read links out
  of the transcript verbatim. The rendered transcript clips long tokens (a URL
  inside a code block loses everything past the right edge) and markdown-it
  rewrites hrefs before opening them (`%3A%2F%2F` → `://`), so the commands
  parse the raw markdown text instead: `/urls` numbers the links of the last
  answer, `/copy <n>` puts link *n* on the clipboard byte-for-byte, `/open <n>`
  hands it to the default browser. `/copy` keeps its old meanings — no argument
  copies the last answer, a non-numeric argument copies that literal text.

## v0.7.0 — 2026-11-02

**Project memory: durable per-project notes the agent can read and write.**

### Added

- **`memory` / `memory_write` tools** (`zenagent/tools/memory.py`): the model
  can now store durable project knowledge — decisions and their WHY, durable
  conventions, recurring pitfalls — as structured notes. `memory` (read tier)
  reads one entry or searches by keyword; `memory_write` (write tier) adds,
  updates or deletes entries. Entry ids are 4-hex and validated, so a crafted
  id cannot escape `.zenagent/memory/`.
- **Storage layout** (`zenagent/agent/memory_store.py`): one entry per file
  under `<project-root>/.zenagent/memory/entries/<id>.md` (minimal
  frontmatter + free markdown, `cat`/`grep`/git-diff friendly) plus an
  append-only `journal.jsonl` recording every change (ts/op/id/title/reason/
  session). Commit the directory to share memory with the team, gitignore it
  to keep it local — zenagent does not decide for you. Hand-edited files
  degrade gracefully; non-addressable files never reach the prompt.
- **System prompt index** (new `memory_index` block): one `- <id> <title>`
  line per entry, newest first, capped at 64 with a pointer to
  `memory search` beyond the cap. Empty library → the block disappears
  entirely; entry bodies never enter the prompt, so the prompt budget stays
  constant as the library grows. `PROMPT_VERSION` bumped to `2026-11-02.1`.
- `/memory`, M2 async turn-end summarization and M3 command surfaces are
  planned follow-ups (spec:
  `docs/superpowers/specs/2026-11-02-project-memory-design.md`).

## v0.6.6 — 2026-11-02

**写/改文件工具卡默认预览 diff：折叠态显示 5 行变更，展开看完整 diff。**

### Added

- **写/改文件卡默认带 diff 预览**（`zenagent/cli/tui/widgets/tool_card.py`）：
  `write_file` / `edit_file` 卡片折叠态即渲染前 5 行变更（`+`/`-` 着色、超宽行
  按终端单元格裁剪，其余行以 `… N more diff lines` 收口），不再只显示一句
  `edited x.py: replaced 1 occurrence`；`enter` / 点击展开完整结果与完整 diff。
  新增 `MAX_DIFF_PREVIEW_LINES`，中英双语文案与 `docs/guide/tui.md` 同步。
- **补齐卡片 diff 数据链路**（`zenagent/app/views.py`）：`tool_execution_end`
  把 `result.details["diff"]` 写入 `ToolCallView.diff`。该字段此前从未被赋值，
  所以卡片即使展开也看不到 diff。
- **测试**：默认预览、5 行上限与折叠计数、展开全量 diff、仅 write/edit 预览、
  超宽行裁剪、结算后才拿到 diff，以及 view builder 的 diff 接线。历史回放没有
  `details`（diff 不落盘），历史中的写/改卡仍保持单行。

## v0.6.5 — 2026-11-02

**子代理预算可配置：`[task]` 配置段、截断原因回传、转录截断可见。**

### Added

- **`[task]` 配置段**（`zenagent/config.py`，与 `[safety]`/`[backup]` 一样仅全局层生效）：
  `max_turns`（默认 30，替换原先硬编码的 12）、`keep_transcripts`（64）、
  `max_transcript_bytes`（32 MiB）、`max_transcript_file_bytes`（8 MiB）。
  统一在 `Runtime.bootstrap` 读取并注入 `task` 工具，TUI / REPL / RPC 同时生效；
  README 补充配置示例。
- **截断 envelope 自带原因**（`zenagent/tools/task.py`）：`status="truncated"` 现在带
  `reason="max_turns|max_cost"` 与 `turns="N"`，并追加 partial-output 说明，父模型不再
  自己脑补成「上下文撑爆」；子代理 system prompt 会声明轮数预算。
- **TUI 标出被截断的转录**：`SubagentView.transcript_truncated` 来自落盘的
  `SubagentMeta.truncated`，子代理横幅显示「转录已截断」、侧栏行显示「［截］」，
  不再静默变短（中英双语）。

### Fixed

- `SubagentStore._prune` 不再可能删掉刚写完的最新转录：`max_transcript_bytes` 与
  `max_transcript_file_bytes` 做跨字段校验（总预算至少容纳一份转录），prune 永远保留
  最新一份。
- `zenagent rpc` 忽略 `[task]` 的问题随 0.6.4 的 Runtime 装配一并消失（RPC 现在经
  `Runtime.bootstrap`，与 TUI/REPL 共用同一处接线）。

## v0.6.4 — 2026-11-02

**前端对齐：REPL / RPC 与 TUI 共享同一套 app 层，RPC 协议升级到 v2。**

### Added

- **RPC 协议 v2**（`zenagent/cli/rpc.py`）：新增 `steer` / `tools` / `model` /
  `mode` / `usage` / `approval` / `session` / `command` 请求；`zen rpc` 在
  `Runtime` 上启动，因此会话持久化、MCP、skills、扩展、审批与 TUI/REPL 一致。
  `session` 支持 current/list/new/fork/resume/import/undo/delete/compact/retry/export。
- **交互式审批回传**：`approval_request` 事件 + `approval` 响应组成往返，超时按拒绝；
  不再只能 `--yes` 全放行或全拒绝。
- **事件补字段**：`args`（JSON 文本）、`result_text`、`duration_ms`、`tool_stats`
  进入 RPC 事件，编辑器可以渲染工具参数与结果；所有文本字段 4000 字符有界。
- **REPL 获得 TUI 的全部命令与工具**：REPL 改由 `Runtime` 装配、共享
  `CommandRegistry` 分发、`ChatController` 驱动运行；补上 `/context` `/export`
  `/plan-review` `/usage`，工具集补上 `checkpoint`/`rewind`/`lsp`/`github`。
- **REPL 启动 flag 生效**：`--thinking` / `--metrics` / `--image`。
- **TUI 启动 flag 生效**：`--thinking` / `--trace` / `--metrics` /
  `--no-auto-compact` / `--image`；`/trace` 现在能显示真实路径。
- `RuntimeOptions` 统一承载 `api_key` / `trace` / `metrics` / `auto_compact` /
  `tools` / `mcp_retry_interval_s`，所有前端只负责填值。

### Fixed

- REPL `/compact` 之后新消息继续写入旧会话文件；现在会切到 `*-compact.jsonl`。
- REPL `/model` 用裸 catalog：`config.toml` 里的自定义模型切不过去，跨 provider
  切换仍带旧 api_key、thinking 与 session id 不更新；现在走共享的
  `apply_model_switch`（与 TUI 的 `ModelManager` 同一实现）。
- REPL 的 `/reload` 现在通过 `Runtime.reconcile_async()` 重连 MCP 并重建工具/提示词；
  `/mcp retry` 保留原来的逐服务状态输出。
- REPL 驱动运行改走 `ChatController`：自动压缩、滞留插话续跑、`/retry` 的
  语义与 TUI 一致（插话在下一轮结束前送达，不再滞留到下一次用户输入）。

## v0.6.3 — 2026-11-02

**TUI fix: the sidebar reorders live rows without taking the app down.**

### Fixed

- **Sidebar row reorder crashed the TUI** (`zenagent/cli/tui/widgets/sidebar.py`):
  `SidebarSection._sync_order` called `Widget.move_child(row, index)`
  positionally, but Textual 8 declares
  `move_child(child, *, before=..., after=...)`. The first in-place reorder of
  mounted rows — a second subagent starting while the first row was already on
  screen — raised
  `TypeError: Widget.move_child() takes 2 positional arguments but 3 were given`
  and killed the agent. The call now passes `before=index`, and a freshly
  mounted row is positioned in the same refresh (`mount()` registers a row in
  the parent's child list before its Mount event) instead of a tick later.
- **`UsageRow.plain` read the removed `Static.renderable`**
  (`zenagent/cli/tui/widgets/rows.py`): Textual 8 moved the accessor to
  `Static.content`. Latent in this release (nothing read the property yet), so
  it would have surfaced later as an `AttributeError`.
- **Empty-context placeholder used the English heading**
  (`zenagent/cli/tui/widgets/sidebar.py`): it looked up `"Context"` instead of
  the section key, raising `AssertionError` under a zh locale when the context
  report was empty. It now matches the key, like every other section.

Tests: `test_new_subagent_inserts_above_already_mounted_rows` reproduces the
crash (TypeError before the fix);
`test_usage_row_plain_returns_the_rendered_tail` and
`test_empty_context_placeholder_resolves_under_chinese` pin the other two.

## v0.6.2 — 2026-11-01

**LSP fix: rename now returns the full cross-file workspace edit.**

### Fixed

- **`lsp` rename lost edits** (`zenagent/tools/lsp.py`), two defects found by
  exercising the tool against a real pyright during the demo:
  - **Cold-start race**: pyright answers `textDocument/rename` while a freshly
    opened document is still being analyzed, and the answer then contains only
    the open file's edits — cross-file renames were silently incomplete. Rename
    now waits (best effort, ≤8 s) for the document's first
    `publishDiagnostics`, the analysis-complete signal, before asking.
  - **Unrendered response shape**: pyright returns `documentChanges` with
    kind-less `TextDocumentEdit` entries; the formatter only knew
    `changes` and `kind: rename`, so every pyright rename rendered as
    "(empty workspace edit)". Both shapes now render, including per-edit lines.
- Tests: the fake LSP server simulates the race (diagnostics 300 ms after
  didOpen; partial rename before, full edit after) and a regression test pins
  the two-file edit; a formatter unit test covers the `TextDocumentEdit` shape.

## v0.6.1 — 2026-11-01

**Update check: revalidate the PyPI cache every hour instead of once a day.**

### Changed

- **Update check cache TTL: 24 h → 1 h** (`CACHE_TTL_S` in `zenagent/update_check.py`).
  A cached PyPI answer is revalidated every hour, so a release published after the
  last check is reported within the hour instead of the next day. The
  once-per-version `notified` dedupe, the background-only lookup and the silent
  failure handling are unchanged; `README.md` now documents "at most once an hour".

## v0.6.0 — 2026-11-01

**P1/P2 completion: tool registry and world tools (LSP, GitHub, checkpoint/rewind),
a reconciling reload path, and the remaining TUI/REPL surfaces from the omp gap study.**

Plan: `docs/superpowers/plans/2026-11-01-p1-p2-completion.md` (M1–M6). Executed on
`feat/p1-p2-completion` and merged fast-forward; `bash scripts/check.sh` is the gate.

### Added — tools

- **`checkpoint` / `rewind`**: the model marks an exploration, then replaces it
  with a report. Implemented as a request projection (`Runtime._transform_context`)
  so the transcript on disk stays append-only; session changes clear stale
  markers.
- **`lsp`**: stdio JSON-RPC client (Content-Length framing, server→client request
  answers, diagnostics wait) exposing diagnostics/symbols/references/definition
  plus a rename that returns the workspace edit without writing. Servers
  (pyright, typescript-language-server) are discovered on PATH; a missing server
  is a per-action error, not a disappearing tool.
- **`github`** (conditional): `pr_view` / `pr_diff` / `pr_checks` / `run_logs`
  through the `gh` CLI, read tier, registered only when `gh` exists.
- **`read_file` `line_numbers`**, **`glob` gitignore filtering** (root
  `.gitignore`, `!negation`, `include_ignored` opt-out) and **`sort=mtime`**;
  `write_file`/`edit_file` preserve the executable bit (regression-tested).
- **MCP call-level reconnect**: a transport failure drops the client, reconnects
  once and replays the call once; JSON-RPC errors and timeouts are never retried.
- **Tool registry with `hidden` / `default_inactive`** (`agent/registry.py`):
  the kernel declares visibility, the assembly layer decides it; hot reload
  (`/reload`, MCP late connections, extension changes) goes through
  `Runtime.reconcile()` / `reconcile_async()` and rebuilds the prompt once.
- **Tool context for hooks**: 4-arg `before_tool_call` and 3-arg
  `after_tool_call` receive a `ToolContext` (call id, resolved tool, cwd,
  approval tier, timeout); a before hook may rewrite arguments, which are
  re-validated against the schema without touching the transcript.
- **Responses adapter** now fits `max_output_tokens` to the context window like
  the other three adapters (it could 400 on a long prompt).

### Added — TUI

- **`/plan-review`**: read-only plan overlay; `a` approves and switches to
  `full`, `k`/`esc` keeps planning.
- **`/hotkeys`**, **`/usage`**, **`/mcp add`**, **`/todo`** overlays: binding
  sheet, usage dashboard (current session + recent-session aggregate), MCP add
  wizard (atomic `mcp.json` write, 0600, then reconnect), and an editable todo
  list sharing the model tool's sidecar.
- **Consecutive `read_file` cards fold** into one expandable `read_group`.
- **Per-family tool-card heads** for task/todo/ask/wait/web_search/fetch/list_dir;
  `NoticeRow` gained a warning level.
- **Status band**: real git branch + staged/modified/untracked counts (cached
  3s, degrades to hidden outside a repository), `cache_read` / `cache_write`
  segments; `n queued` now survives width degradation.
- **Keys**: `f5`/`alt+r` retry, `shift+ctrl+p` reverse model cycle,
  `alt+shift+p` plan toggle, `ctrl+g` external editor, `ctrl+v` clipboard
  (image path attaches, text inserts), `ctrl+shift+o` show/hide tool cards,
  `alt+l` display reset.
- **Esc layering** in palettes (clear search, then close) and menu re-request
  focuses the open panel instead of stacking.
- **Image lines** in user turns render as their own dim row.

### Added — CLI / app

- **REPL**: running-tool progress line; `/pin` `/new` `/settings` `/search`
  `/doctor` `/todo` `/jobs` `/copy` `/btw` `/shake` `/handoff` `/add-dir`
  `/remove-dir` `/dirs` `/fresh` `/trace`.
- **TUI**: prompt-template and extension-command submissions; `/import`;
  `ctrl+g` editor and `ctrl+v` clipboard.
- **`/jobs`** lists/kills background jobs (`JobManager.kill`); **`/shake`**
  drops tool results/images/thinking into a new session file; **`/btw`** asks a
  throwaway side question; **`/copy`** uses the system clipboard; **`/handoff`**
  writes a handoff markdown then compacts; **`/add-dir`** `/remove-dir` `/dirs`
  track extra roots; **`/fresh`** resets display state.
- RPC events carry a bounded `partial_result` tail; headless documents that it
  ignores progress updates on purpose.

### Fixed

- `RunStatus.git_branch` was dead code: the status band advertised a git
  segment that never rendered.
- The 3-arg `after_tool_call` hook no longer crashes on an unknown tool (the
  context carries `tool=None`).
- Doc drift: tool counts, REPL command lists, provider adapter count and the
  `git` segment description are corrected in README/guides/feature-map.

## v0.5.1 — 2026-11-01

**Hand-slip guardrails with `/restore`, a visible mid-run input queue with steering, and one-Enter `/model` switching.**

### Added

- **Workspace fence**: `write_file`/`edit_file` paths are classified after `~` expansion and
  symlink resolution; writes outside the project root need confirmation interactively and are
  refused headless unless `--allow-outside PATH` (repeatable, also `[safety] allow_outside`).
  The system temp directory stays allowed by default.
- **Protected paths**: writes to `~/.zenagent/**`, `~/.ssh`, `~/.aws`, gcloud/kube/docker
  credential stores and private-key filename patterns need confirmation; refused headless
  unless `--allow-protected`.
- **Destructive-command tier**: `git reset --hard`, `git clean -fdx`, `git checkout -- .`,
  `git stash drop|clear`, `git push --force`, `sudo …`, `curl … | sh`, recursive `rm` outside the
  workspace, `find -delete`, `truncate -s 0`, `shred`, `dd of=<file>` now need confirmation.
  This also fixes the long-documented but unimplemented `curl … | sh` detection.
- **Write snapshots + `/restore`**: every successful `write_file`/`edit_file` records the previous
  bytes under `~/.zenagent/projects/<key>/backups/`; `/restore`, `/restore list` and
  `/restore <n>` put a run's files back and skip files the user changed afterwards. Works in
  the TUI and the REPL; headless runs are journaled too (no restore surface).
- **TUI approval dialog**: risk badges (`outside workspace` / `protected path` /
  `destructive command`) and a bounded diff preview for write/edit calls.
- **`~` expansion** in filesystem tool paths (previously the tool wrote a literal `~/…`
  directory while the permission gate expanded it).
- `[safety]` and `[backup]` config sections (global layer only; the project layer rejects them).

### Changed

- `full` mode and `--yes` no longer auto-approve confirm-level risks: outside/protected writes
  and destructive commands get a confirmation (interactive) or a refusal naming the flag
  (non-interactive).
- Headless runs now write snapshots unless `[backup] enabled = false`.
- `/undo` still only rewinds the transcript; `/restore` is the file-level counterpart.

### Fixed

- **A provider 400 no longer poisons the whole session.** An aborted turn that produced only
  thinking (or a failed turn with empty content) was stored as an assistant message with
  `content: null` and no tool calls. Strict chat-completions endpoints reject that — DeepSeek
  answers `400 Invalid assistant message: content or tool_calls must be set` — and because the
  failed turn stayed in the transcript, every later turn replayed it and failed the same way:
  the session was permanently broken (GLM tolerated it, which made the failure look
  provider-specific). Requests now go through a replay-safe transcript view
  (`zenagent/providers/history.py`) that drops failed/aborted turns with no text, partial
  thinking, and tool calls whose results are missing; a text-less assistant turn serializes as
  `content: ""` instead of `null`.
- **Provider errors are debuggable.** `post_stream` now reads the error body and appends the
  provider's own message to the raised `HTTPStatusError` (e.g. the DeepSeek reason above),
  instead of surfacing a bare `400 Bad Request` line.

### Tests

Offline suite covers the risk matrix, journal/restore semantics, config layering, approval UI and
command wiring; `bash scripts/check.sh` is the gate. Replay-safety projection, the request view
and provider-error text are covered by `tests/test_history_replay.py` plus additions to the
adapter and transport suites.

### TUI pending queue and steering

**Mid-run input now has a visible home above the composer and can be promoted into the running turn without waiting for the run to end.**

- **Pending band**: messages typed while a run is in flight render in a dock band between
  the status line and the prompt — `Queued · n` for follow-ups that send after this run,
  `Steering · n` for input injected at the next step boundary. Rows are clipped, newlines
  fold to `↵`, more than six message rows collapse into `… n more`, and the band hides
  itself when both lanes are empty. The status-band counters and the sidebar runtime row stay.
- **Steering without interrupting**: `ctrl+s` with a draft steers the running turn; with an
  empty composer it promotes the newest queued message instead. Clicking a queued row in the
  band promotes it too; steering rows are read-only (the steering channel is text-only, so a
  queued message with images reports that it still waits for the run to end).
- **`ctrl+x` clears both lanes** (queue and steering) instead of only follow-ups.
- **No stranded steering**: a steer typed after the last step boundary now continues the run
  so the model answers it at the next request; when the run ended in error or was
  interrupted, the undelivered text becomes a visible queue entry instead of hanging
  invisibly, and it no longer gets spliced behind the next prompt.
- **Pending doesn't cross conversations**: session switch, `/new`, `/undo`, `/fork` and
  `/retry` drop pending messages with a one-line notice; compaction keeps them (same session).

### TUI `/model` switches in one Enter

**`/model` now behaves like `/mode`: a value reaches the runtime directly instead of making the user pick twice.**

- **The argument is applied**: `/model <id>` — including Enter on a value hint after `/model `
  — switches immediately. The command used to drop its argument and open the model picker, so
  completing a model in the hint list then pressing Enter asked for the same choice a second
  time. Only a bare `/model` (or `alt+p`) opens the picker.
- **One implementation for both entry points**: the picker callback and the argument path now
  share `_apply_model`, so banner healing (a switch to a keyed model dismisses the missing-key
  banner), the success toast and the status repaint cannot drift.
- **Value hints lead with the current model**, then usable models, then keyless ones — a blind
  Enter on `/model ` is a no-op rather than a switch to a model whose key is missing.

## v0.5.0 — 2026-10-31

**Two appearances and a bilingual interface: dark/light themes on one colour-token source, and en/zh UI text with live switching.**

### Added

- **Dark/light appearance (`theme = auto|dark|light`)**: one colour-token
  source (`zenagent/app/appearance.py`) now feeds the TUI (two registered
  Textual themes plus CSS variables, with the Rich palette bound at runtime),
  the REPL renderer, and `--theme`. `/theme` and `/settings → Theme…` switch
  live: CSS repaints and the transcript/status band rebuild from the same
  tokens, without interrupting a running turn. `auto` follows the terminal
  through `COLORFGBG` and falls back to dark. Legacy REPL themes
  (`default|mono|solarized|<path.json>`) keep working, REPL-only.
- **Interface language (`language = auto|en|zh`)**: a stdlib-only message
  catalog (`zenagent/i18n/`) now drives every user-facing string in the TUI,
  the REPL, slash-command descriptions and argument hints, approval/ask dialogs
  and command output. `/lang`, `/settings → Language…` and `--lang` switch it;
  `auto` follows `LANG`/`LC_ALL` (zh prefix → Chinese) and the choice persists
  in `settings.json`. Switching is live (command registry rebuild, sidebar
  headings and Rich text repaint) and never interrupts a running turn. Types
  labels (`command ·`/`template ·`/`extension ·`/`skill ·`) and CJK-aware
  clipping ship with it; model-facing prompts, tool schemas and skill bodies
  stay English by design.

## v0.4.5 — 2026-10-31

**TUI work-panel polish: resize, context composition, inline checklist, job logs.**

### Added

- **Drag-resizable sidebar**: a one-column grab handle between the transcript
  and the panel; width clamps to 20–60 columns, the main column keeps at least
  30, and rows/strip bars re-clip to the real content width.
- **Context section + breakdown overlay**: the sidebar shows window usage, a
  per-slice stacked bar (the remainder is free space) and the two biggest
  contributors; clicking opens a read-only table with exact block character
  counts and estimated token shares (system prompt, AGENTS.md, skills,
  extensions, plan note, tool schemas, session, free space).
- **Inline todo list**: the sidebar lists every task (phase header + one
  clipped line each) instead of a single summary; clicking a row opens the
  existing full-text overlay.
- **Job logs**: background-job rows are clickable and open a read-only overlay
  with the command, status, duration and captured output (truncation is called
  out); a running job's log refreshes with the 1 s poll.
- **Semantic palette** (`zenagent/cli/tui/palette.py`): widgets take colours
  from one module; sidebar sections get one quiet accent each and state glyphs
  (running/done/failed/blocked) are coloured by meaning.

### Fixed

- Status-band segments degraded for the terminal width instead of the dock
  column and were not re-rendered when the sidebar was resized; they now use
  the actual column width and repaint on toggle and drag.
- Context composition: a resumed session (no provider usage yet) fell back to
  cumulative usage and could read `ctx 312%`; the total is now the estimate.
  Slice percentages use the model window as the denominator (with a
  `free space` row) instead of the used total.

### Tests

1487 offline tests; `bash scripts/check.sh` clean and packaging verified with
`scripts/release-check.sh`.

## v0.4.4 — 2026-10-31

### Added

- **User-side skill invocation (`/skill:<name>`)**: skills now appear in the
  REPL and TUI slash completion with their descriptions, line-start and
  mid-sentence (`please /skill:review this`). Submitting reads SKILL.md and
  injects the full body as a user message (omp-style), so the model does not
  have to decide to call `read_skill`; TUI renders it as an expandable
  `✦ skill:<name> · args` chip. Unknown names are reported with the available
  list; `--no-skills` keeps them out of the menu.
- Slash completion entries carry type labels (`command ·`, `template ·`,
  `extension ·`, `skill ·`) so same-name entries are distinguishable.
- **TUI work panel**: a sidebar with todo, subagent and background-job
  sections, a background-work strip above the prompt, a read-only todo overlay
  (HUD click and palette entry), and a once-per-second job poll while work is
  live. `RunStatus` now carries job/subagent view models for the panel and
  desk chrome.
- **Subagent transcripts**: `task` children persist session-scoped transcripts
  (bound, carried and trashed with the session), stream progress live, and can
  be read in a read-only transcript view swapped into the TUI main area, with
  resume integration and a completion banner.

### Changed

- The TUI transcript is now built through an extracted `TranscriptBuilder`
  shared by the live event path and session resume/reload.

## v0.4.3 — 2026-10-31

### Changed

- **Prompt subsystem, behavior-neutral.** Every fixed prompt string now lives in
  `zenagent/prompts/blocks/` (26 blocks plus the `task` subagent's read-only
  child prompt), assembled by a single contract (`compose_system` /
  `compose_compact` / `tool_description`) shared by the TUI, headless, REPL,
  RPC and eval paths. Adds the `zen prompts list|show|estimate` command, a
  byte-parity golden suite against v0.4.1, source/budget gates, and a wheel
  packaging check. No prompt text changed; content experiments land later.


## v0.4.2 — 2026-10-31

### Fixed

- **Collapsed thinking rows could wrap to two lines on Python 3.14.** The
  one-line preview is now enforced by CSS (`height: 1; text-wrap: nowrap`) and
  expansion opts back into `height: auto; text-wrap: wrap` via an `expanded`
  class; the widget also re-renders after the first layout pass, so a stale
  pre-layout width can no longer produce an extra row. This was the py3.14 CI
  failure on both ubuntu and macos.

## v0.4.1 — 2026-10-31

**Autonomy slice: delegate, ask, keep the big artifacts.** Pulls three v0.5-plan
items forward (see `docs/research/omp-cli-study/12-v0.5-plan.md`).

### Added

- **`task` (read-only subagent)**: delegates a self-contained research job to a
  child `Agent` (same model/stream, read-only tools, own context guard, no
  session, depth 1); the parent gets a `<task-result status cost tokens>` envelope
  instead of the child transcript. The child follows live model/key/thinking
  (so `/model` affects subagents), shares the session artifact sink, and its
  spend counts toward the parent's `--max-cost` guard.
- **`ask` (structured questions)**: read-tier tool asking the user 1–5 questions
  with 2–6 options. The TUI shows framed dialogs (number keys/clicks, multi-select,
  Esc cancels), the REPL prompts in the terminal (input is serialized with
  steering), and headless/RPC runs degrade to a non-error "continue conservatively"
  note. Answers are validated against the offered options.
- **Artifact spill**: results over the kernel budget are written to
  `<session>/artifacts/<session stem>/` (atomic, 64 files / 8 MiB per session,
  0600), the truncation notice points at the path, `details.artifact` lets the UI
  surface it, and `read_file` can read it back. Spilling failures degrade to the
  old plain truncation.
- **Subagent accounting**: `RunStatus.subagent_cost/tokens` and
  `RunMetrics.subagent_cost/tokens` (per-run metrics keep subagent spend apart
  from the parent's own usage).

### Changed

- `task`/`ask` are read-tier: plan mode allows read-only delegation and user
  questions; the plan-mode prompt lists both.
- The `Runtime.session` setter now retargets both the artifact sink and the todo
  sidecar, covering retry/compaction paths that assign sessions directly.

### Fixed

- Subagents kept the bootstrap model after a `/model` switch.
- `--max-cost` did not bound subagent spend.
- The REPL ask handler could race the steering reader for stdin lines.
- Child output could spoof the enclosing `</task-result>` tag; answer labels are
  now validated; `ask` is abort-aware.

## v0.4.0 — 2026-10-31

**Tool kernel hardening, orchestration and background jobs.** This release is
the first pass of the omp tool-gap study
([`docs/research/omp-cli-study/10-tools-gap.md`](docs/research/omp-cli-study/10-tools-gap.md)):
make the existing tools trustworthy and recoverable before adding breadth.

### Added

- **Tool governance**: `AgentTool.approval` (`read`/`write`/`exec`) consumed by
  the permission gate; MCP `annotations.readOnlyHint` maps to the read tier;
  `before_tool_call` now receives the resolved tool as its third argument.
- **Target-scoped approvals**: "always" grants remember a file path or a
  `bash` command (directories cover one level); the TUI approval dialog and
  REPL prompt show exactly what the grant covers.
- **Result budget**: one kernel-level budget for every tool result (built-in,
  MCP, extension): text over 40 KB keeps head+tail, overlong lines are
  middle-truncated, images pass through.
- **File tools**: `read_file` pages with `offset`/`limit` (default 400 lines,
  cap 2000, next-page hint); `write_file` refuses writing back a truncated read
  projection; `edit_file` gains `replace_all` and both tools preserve an
  existing file's CRLF/BOM convention.
- **Deadlines and errors**: per-tool `timeout_s` + `TOOL_TIMEOUTS` table with a
  kernel `asyncio.wait_for`; `ToolResultAgent.error_kind` on every degraded
  result; cross-turn loop guard for repeated identical tool rounds (poll-like
  commands and `wait` are exempt; the corrective is model-only and never
  persisted).
- **Request fitting**: output-token caps are fitted to the context window
  (messages + tool schemas + CJK/image estimates) in all three provider
  adapters.
- **`todo` tool**: session-scoped checklist (`<session>.todo.json`, content
  addressed, nine ops, atomic batches) with a one-line TUI HUD and a compact
  tool-policy prompt.
- **Search and web**: file-paged `grep` (`skip`, 20 files/page, 20 hits/file);
  `fetch` readability extraction (`<article>`/`<main>`, page chrome removed,
  `details.method`); `glob` gains `limit` and `include_hidden`.
- **Background jobs**: `bash async: true` starts a job (`bg_N`), `wait` collects
  its result (read tier, abort-aware, unknown-id errors); `Runtime.close()`
  releases the manager when the last runtime goes away.
- **Shell abstraction**: foreground bash and background jobs share one
  `resolve_shell()`; `ZENAGENT_SHELL=posix|powershell|pwsh|cmd|<path>`.
- **Observability**: `agent_end.tool_stats` (per-tool calls/errors/ms) is
  written into `MetricsRecorder` run metrics as `tools`.
- **Docs**: the omp tool-gap study (`10a`–`10e`), `README`/guide/internals
  updates, and the v0.5 plan (`12-v0.5-plan.md`).

### Changed

- `read_file` now returns 400 lines per call by default (whole-file reads are
  still byte-exact); page numbering and next offsets are reported.
- `glob` hides dotfiles by default (`.git` always); `grep` pages by file instead
  of returning one flat 200-match list.
- `web_search`/`fetch` are read-only: allowed in plan mode and by `--read-only`
  (previously the permission gate denied them outside full mode).
- Bash "always" grants are exact commands (a two-word prefix could leak consent
  across `&&`, multi-line scripts or wrapper commands).
- File writes are serialized against parallel tool batches (`write` tier is
  exclusive); sequential tools now wait for the parallel batch instead of
  running on a second track.
- Windows: `bash`/background jobs default to `pwsh` → `powershell` → `cmd`
  (previously `create_subprocess_shell` used `cmd.exe`); set
  `ZENAGENT_SHELL=cmd` for the old behavior.

### Fixed

- Plan/non-interactive ask mode refusing network research tools.
- A tool raising `TimeoutError` without a kernel deadline aborting the run.
- Truncated reads being written back over the file (silent data loss) —
  including the boundary case where the page plus its footer is longer than the
  file.
- Approval prompts claiming a tool-wide session grant while caching a narrower
  target (and blank targets degrading to tool-wide grants).
- Compaction/REPL `/retry` losing the todo sidecar binding.
- `web_search` being killed by the kernel deadline while the provider chain was
  still falling through to keyless providers.

### Tests

`bash scripts/check.sh` (ruff + offline pytest) is the single gate; the release
check adds wheel/sdist install verification (`bash scripts/release-check.sh`).
This release's suite: 1223 tests.

## v0.3.0 — 2026-10-30

Textual TUI release: single-column dock layout, status band, tool cards with
streamed progress, session picker/rename/delete/fork, approvals, plan/ask/full
modes, image attachments, update notice, and the first omp TUI study docs.
