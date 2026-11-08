# Prompt CHANGELOG

Every content change bumps `PROMPT_VERSION` (`zenagent/prompts/spec.py`) and gets
one entry here: what changed, why, and the experiment/report that justified it.
P0 extracted the v0.4.1 text verbatim — no behavior change.

## 2026-11-08.1 — the taiji Router directs, workers edit

- New session-gated block `taiji_router` (rendered only while the taiji
  session is active, via `PromptContext.include_taiji_router`): the Router
  dispatches every repository-changing task with `spawn_worker`, verifies
  milestones, merges — and never edits the main checkout. Justified by the
  2026-11-08 zen-agent session: the Router answered "切换项目 GUI reload 感"
  by implementing the fix itself (26 edits + direct commits to main) without
  spawning a worker — the brief's "执行都在 worker" contract existed only in
  docs, never in the model's instructions.
- `tool.spawn_worker` states the reversed dispatch contract: implementation
  goes to workers by default; direct answers are for questions, diagnosis
  and read-only inspection. The old wording ("use it whenever the user asks
  for parallel or background work") actively told the Router to do code
  changes itself when the user didn't mention parallelism.
- Enforcement rides with the wording: the taiji session's tool set now drops
  `edit_file`/`write_file` (`ROUTER_EXCLUDED_TOOLS` + `router_tool_gate` in
  `zenagent/app/taiji/tools.py`), so the contract holds even when the model
  ignores the prompt.

## 2026-11-07.1 — read-before-write gate on the file tools

- `tool.read_file`, `tool.write_file` and `tool.edit_file` state the new
  harness contract: a successful read marks the file fresh, and edit/write of
  an existing file is refused when the file was never read this session or
  changed on disk since (the enforcement itself is
  `zenagent/tools/read_state.py`; only the model-facing wording lives here).
  Purpose: old_string anchors must come from content the model actually saw,
  and concurrent external modifications (user, formatter, shell) must fail
  loudly instead of silently applying against stale context. The budget gate
  moves with it: `TOOL_TOTAL_BUDGET_CHARS` 4800 → 5200 (single-tool caps and
  every other block untouched).

## 2026-11-05.1 — review dispatch contract: no test runs, chunk large diffs

- `tool.task` and `system.tool_policy` carry the review-dispatch rules that
  real timeout evidence demanded (order-monitor 2026-11-04: a review child
  faithfully ran cargo test + vitest per the old template and was cut by the
  900s watchdog at 1.9M tokens; the re-dispatch without test runs finished):
  reviews receive the implementer's test results and never run suites
  themselves, and diffs over ~600 lines are split into per-file-group
  reviews whose findings the parent merges. Verified-by-reading stays the
  reviewer's job; verified-by-running is the implementer's and the parent's
  join gate.

## 2026-11-04.1 — async parallel subagents: worker contract and dispatch semantics

- `task_child_system` gains the worker contract: stay inside the file scope
  the prompt declares (parallel siblings share the tree), list created or
  changed files in the report, and keep the checkpoint discipline. The
  structured `files_touched` manifest in the envelope is extracted from the
  child's own write/edit calls, so the report list and the manifest
  cross-check. Justified by the 2026-11-04 async-parallel subagents spec
  (shared-tree safety = planning discipline + manifest + parent re-verify).
- `tool.task` documents the new dispatch surface: `async: true` (task id now,
  envelope via `wait`), `model` (bare id or provider/id), the disjoint-file
  rule for parallel workers, and the join-then-verify rule — the parent
  re-runs the full suite after collecting workers, because one worker's green
  is invalidated by its siblings' edits.
- `tool.wait` documents task-id collection (`task_N`).
- `system.tool_policy` carries the disjoint-file and join-verify rules into
  the parent's standing instructions.

## 2026-11-03.1 — super child: mode-following toolset, dispatch checklist, checkpoints

- `task_child_system` rewritten for the mode-following child: the fixed
  "read-only research" identity became a delegated-subagent identity whose
  capability line is dynamic (`{{child_tools}}`, rendered from the parent's
  active set filtered by the session's permission mode). Adds two behaviors
  observed missing in session audits (12 dispatched runs, 67% failed): post a
  short checkpoint every ~10 turns so a budget-cut run still returns usable
  output, and run the relevant tests when you changed code. Justified by the
  2026-11-03 subagent audit: 5 of 8 failures ended `(no output)` after burning
  ~95% of all subagent tokens.
- `tool.task` description now states the mode-following capability and a
  pre-dispatch checklist (confirm referenced files exist; precompute artifacts
  the child cannot produce — write a diff to a file and pass the path; state
  the output contract). Motivated by FM1/FM4 of the same audit: dispatch
  prompts assumed a shell the child never had, and one hallucinated file
  reference sent three reviewers looking for a document that was never written.
- `system.tool_policy` gains one delegation bullet carrying the same checklist
  into the parent's standing instructions.

## 2026-11-02.1 — subagent turn budget

- `task_child_system` now declares the child's turn budget (`{{max_turns}}`,
  rendered from `[task].max_turns`) and tells it to return findings before the
  last turn. Motivated by real runs where three subagents were cut at the old
  hardcoded 12-turn cap while still "doing a final check"; the parent model then
  invented a context-overflow explanation for the `status="truncated"` envelope.

## 2026-11-02.1 — project memory index block

New optional `memory_index` block (after `skills`, before `extensions`): one
`- <id> <title>` line per project-memory entry, newest first, capped at 64
with a pointer to `memory search` beyond the cap. Empty library → the block
disappears entirely (same condition pattern as `context_files`). Bodies never
enter the prompt. Justification: the project-memory spec
(`docs/superpowers/specs/2026-11-02-project-memory-design.md` §3, 索引式注入)
—— agent 需要常驻可见的记忆清单，但记忆全文常驻会随条目数无限吃 prompt 预算。

## 2026-10-31.1 — P0 extraction (behavior-neutral)

- Moved every fixed prompt string into `zenagent/prompts/blocks/`: system
  sub-blocks (identity / environment / tool policy / todo line / parallel line /
  closing), plan mode, project context wrapper, skills index header, compaction
  system/summary/result, the 14 tool descriptions, and the `task` subagent's
  read-only child system prompt (`task_child_system.md`).
- Added the registry (`spec.py`), single-pass renderer, `compose_system()`,
  `compose_compact()`, `tool_description()`, the golden parity suite, the
  source gate and the budget gate.
- No text changed: `scripts/prompt_goldens.py --check` reproduces the v0.4.1
  output byte-for-byte for all nine configurations.
