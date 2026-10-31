# Prompt CHANGELOG

Every content change bumps `PROMPT_VERSION` (`zenagent/prompts/spec.py`) and gets
one entry here: what changed, why, and the experiment/report that justified it.
P0 extracted the v0.4.1 text verbatim — no behavior change.

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
