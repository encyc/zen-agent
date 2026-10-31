# Changelog

All notable changes to ZenAgent (`zenagent-py`). The version in this file
matches the release tag; older history is in the repository tags and commits.

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
