# Changelog

All notable changes to ZenAgent (`zenagent-py`). The version in this file
matches the release tag; older history is in the repository tags and commits.

## Unreleased — update-check responsiveness

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
