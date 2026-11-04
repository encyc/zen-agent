# Prompt CHANGELOG

Every content change bumps `PROMPT_VERSION` (`zenagent/prompts/spec.py`) and gets
one entry here: what changed, why, and the experiment/report that justified it.
P0 extracted the v0.4.1 text verbatim — no behavior change.

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
