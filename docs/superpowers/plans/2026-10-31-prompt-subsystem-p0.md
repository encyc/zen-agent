# Prompt Subsystem P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract every fixed prompt string into `zenagent/prompts/blocks/`, add one `compose()` used by all five call sites, and prove byte-identical rendering against the v0.4.1 golden baseline.

**Architecture:** Markdown block files + Python metadata (`spec.py`), a ~100-line single-pass `{{var}}` renderer, a registry with lint/budget validation, and `compose_system()`/`compose_compact()`/`tool_description()`. Goldens are captured from the pre-refactor code (Task 1) and every later task proves parity against them; behavior changes are deferred to P1/P2.

**Tech Stack:** Python ≥3.11, standard library only (no new runtime dependency), pytest, existing `scripts/check.sh` gate.

**Spec:** `docs/superpowers/specs/2026-10-31-prompt-system-design.md` (baseline: v0.4.1 `dde8bf0`)

## Global Constraints

- Runtime dependencies stay exactly `httpx`; no Jinja/Handlebars/template-engine dependency.
- P0 is behavior-neutral: rendered prompts must be **byte-identical** to v0.4.1 for every config in the golden matrix.
- Templates support only `{{name}}` substitution. No conditionals/loops in files; conditions live in Python. Inserted values are **never re-rendered** (an AGENTS.md containing `{{` must pass through unchanged).
- Fixed prompt text lives only in `zenagent/prompts/blocks/`; the only exceptions are the explicit allowlist in `tests/test_prompts_sources.py` (design §6.5).
- Budgets from design §6.3 are hard lint limits: `system` ≤ 1,600; `plan_mode` ≤ 700; per-tool ≤ 900; tool total ≤ 4,400; fixed total ≤ 9,000 chars.
- `Agent.system_prompt: str`, provider layer, tool JSON schemas, permission/approval logic, and compaction algorithm stay unchanged.
- `scripts/check.sh` remains the only gate; prompt tests are offline (no network, no API key).
- `PROMPT_VERSION = "2026-10-31.1"`; every content change after P0 appends to `zenagent/prompts/CHANGELOG.md`.
- Commit after every task; never mix two tasks in one commit.

## Review Focus

1. **`{{` inside data** (AGENTS.md text, extension fragments, skill descriptions) must not be interpreted as template syntax; the renderer must be single-pass and insert values verbatim.
2. **Missing/extra variables** must fail loudly (lint at test time, `PromptRenderError` at render time), never render as empty text.
3. **Frontend order parity**: headless builds its tool roster in a different order than TUI (`ALL_TOOLS+MCP+read_skill+扩展+todo+task` vs `ALL_TOOLS+todo+task+MCP+read_skill+扩展`); compose must preserve the caller's order, and goldens must cover both.
4. **Tool-description constant drift**: `read_file`/`glob`/`grep`/`web_search` descriptions interpolate implementation constants; the block must hold placeholders and the parity test must compare against the real `AgentTool.description`.
5. **Wheel packaging**: blocks are `.md` files inside the package; a source-tree test does not prove they ship. `scripts/release-check.sh` must verify the built wheel contains every block file.

---

### Task 1: Capture the v0.4.1 golden baseline

**Files:**
- Create: `tests/prompt_fixtures.py`
- Create: `scripts/prompt_goldens.py`
- Create: `tests/golden/prompts/*.txt`, `tests/golden/prompts/tools.json`, `tests/golden/prompts/compact.json` (generated)
- Test: `tests/test_prompt_goldens_capture.py`

**Interfaces:**
- Consumes: existing `zenagent.app.support.system_prompt`, `zenagent.app.modes.mode_prompt_note`, `zenagent.context_files.system_prompt_with_context`, `zenagent.skills.skills_prompt_index`, `zenagent.app.compact.SUMMARIZE_PROMPT` / `_SUMMARIZE_SYSTEM`, `zenagent.tools.ALL_TOOLS`, `zenagent.tools.todo.build_todo_tool`, `zenagent.tools.task.build_task_tool`, `zenagent.skills.build_skill_tool`.
- Produces: `CONFIGS: dict[str, Config]` and fixture constants in `tests/prompt_fixtures.py`; `tests/golden/prompts/` files consumed by Task 3/4/5/6 tests.

- [ ] **Step 1: Write `tests/prompt_fixtures.py`**

Define `Config` as a frozen dataclass with fields `tool_names: tuple[str, ...]`, `context_text: str = ""`, `skills: tuple[Skill, ...] = ()`, `extension_fragments: tuple[str, ...] = ()`, `mode: str = "ask"`, `system_override: str | None = None`. Build `CONFIGS` with these exact keys and values:

- `default-11`: `ALL_TOOL_NAMES` (11 core names), everything else empty.
- `tui-14`: `ALL_TOOL_NAMES + ("todo", "task", "read_skill")`, `context_text=AGENTS_TEXT`, `skills=SKILLS`, `extension_fragments=("EXT-ONE: keep answers short.",)`, mode `ask`.
- `headless-14`: `ALL_TOOL_NAMES + ("read_skill", "todo", "task")`, same context/skills/extensions as `tui-14`.
- `plan`: same as `tui-14` but `mode="plan"`.
- `skills`: `ALL_TOOL_NAMES`, `skills=SKILLS`.
- `context`: `ALL_TOOL_NAMES`, `context_text=AGENTS_TEXT`.
- `extension`: `ALL_TOOL_NAMES`, `extension_fragments=("EXT-ONE: keep answers short.",)`.
- `rpc-13`: `ALL_TOOL_NAMES + ("todo", "task")`, `context_text=AGENTS_TEXT`.
- `eval-11`: `ALL_TOOL_NAMES`, `context_text=AGENTS_TEXT`, `skills=SKILLS`, `extension_fragments=("EXT-ONE: keep answers short.",)`, `system_override="You are zenagent, a coding agent. Be concise."`.
- `compact`: transcript `TRANSCRIPT` (a fixed 3-turn string), no prompt config.

Constants: `CWD = Path("/repo")` (golden files contain this literal path), `ALL_TOOL_NAMES` = `tuple(t.name for t in ALL_TOOLS)`, `AGENTS_TEXT` = `"### /repo/AGENTS.md\n- Use pytest. Do not add dependencies.\n"`, `SKILLS` = two `Skill(name="alpha", description="Do alpha things.", path=Path("/repo/.zenagent/skills/alpha/SKILL.md"), body="ALPHA BODY")`-style entries, `TRANSCRIPT` = `"## user\nfix the bug\n\n## assistant\nI will read the file."`.

- [ ] **Step 2: Write `scripts/prompt_goldens.py`**

Implement `main(argv)` with `--check` flag and `--out PATH` (default `tests/golden/prompts`). Capture mode renders each config using the **legacy functions** in this order (this is the current frontend behavior):

```python
base = system_override if cfg.system_override is not None else system_prompt(list(cfg.tool_names))
if cfg.context_text:
    base = system_prompt_with_context(base, cfg.context_text)
parts = []
if cfg.skills:
    parts.append(skills_prompt_index(list(cfg.skills)))
if cfg.extension_fragments:
    parts.append("\n\n".join(cfg.extension_fragments))
if parts:
    base = f"{base}\n\n" + "\n\n".join(parts)
if cfg.mode == "plan":
    base = f"{base}\n\n" + mode_prompt_note("plan")
```

Normalize the machine path: `text = text.replace(str(Path.cwd()), "/repo")`. Write `tests/golden/prompts/<config>.txt` (UTF-8, no trailing newline added) **and** `tests/golden/prompts/<config>.base.txt` containing only the system part (`base` before the `parts`/mode appends) — Task 3 compares the base files, Task 4 compares the full files.

Also capture `tools.json`: for each of the 14 tool names, `{"chars": len(description), "text": description}` using `ALL_TOOLS`, `build_todo_tool(None)`, `build_task_tool(model=None, base_tools=[])`, `build_skill_tool(list(SKILLS))` (take `read_skill` from the skill tool; take `task` from the task tool). Capture `compact.json`: `{"system": _SUMMARIZE_SYSTEM, "user": SUMMARIZE_PROMPT.format(transcript=TRANSCRIPT)}`.

- [ ] **Step 3: Run capture and inspect one golden**

Run: `uv run python scripts/prompt_goldens.py`
Expected: `tests/golden/prompts/` contains 9 `.txt` files plus `tools.json`/`compact.json`; `default-11.txt` starts with `You are zenagent, a coding agent working in the user's repository.` and contains `Working directory: /repo`.

- [ ] **Step 4: Write `tests/test_prompt_goldens_capture.py`**

Test 1: every `CONFIGS` key except `compact` has a non-empty golden file and a non-empty `.base.txt`; `tools.json` has exactly the 14 expected names. Test 2 (determinism): run `subprocess.run([sys.executable, "scripts/prompt_goldens.py", "--out", tmp1])` twice into two `tmp_path` dirs and assert the files are identical byte-for-byte.

- [ ] **Step 5: Run the test and commit**

Run: `uv run pytest tests/test_prompt_goldens_capture.py -v` → PASS.
Commit: `git add tests/prompt_fixtures.py scripts/prompt_goldens.py tests/golden/prompts tests/test_prompt_goldens_capture.py && git commit -m "test(prompts): capture v0.4.1 golden baseline"`

---

### Task 2: Single-pass renderer

**Files:**
- Create: `zenagent/prompts/__init__.py` (exports only; filled in later tasks), `zenagent/prompts/render.py`
- Test: `tests/test_prompts_render.py`

**Interfaces:**
- Produces: `render(template: str, values: Mapping[str, str]) -> str`, `template_vars(template: str) -> set[str]`, `PromptRenderError(ValueError)` — imported by registry/compose/tool_description in Tasks 3–6.

- [ ] **Step 1: Write the failing tests**

```python
from zenagent.prompts.render import PromptRenderError, render, template_vars

def test_render_substitutes():
    assert render("a {{x}} b", {"x": "V"}) == "a V b"

def test_render_repeated_and_adjacent():
    assert render("{{x}}{{x}}/{{y}}", {"x": "1", "y": "2"}) == "11/2"

def test_missing_value_raises():
    with pytest.raises(PromptRenderError):
        render("{{x}}", {})

def test_template_vars_finds_names():
    assert template_vars("{{a}} {{b}} {{a}}") == {"a", "b"}

def test_values_are_not_rerendered():
    assert render("{{x}}", {"x": "keep {{y}} literal"}) == "keep {{y}} literal"

def test_newlines_preserved():
    assert render("l1\n{{x}}\nl3", {"x": "l2"}) == "l1\nl2\nl3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts_render.py -v`
Expected: FAIL (module `zenagent.prompts.render` not found).

- [ ] **Step 3: Implement `render.py`**

Single-pass regex `\{\{([a-z0-9_]+)\}\}`; build the result by joining literal segments with looked-up values (`match.group(1)`); raise `PromptRenderError(f"missing value for {{{{{name}}}}}")` when a name is absent. `template_vars` returns `set(re.findall(...))`. No escaping, no control flow.

- [ ] **Step 4: Run tests to verify they pass and commit**

Run: `uv run pytest tests/test_prompts_render.py -v` → PASS.
Commit: `git add zenagent/prompts tests/test_prompts_render.py && git commit -m "feat(prompts): single-pass {{var}} renderer"`

---

### Task 3: Spec, registry, system blocks, `compose_system` (system only)

**Files:**
- Create: `zenagent/prompts/spec.py`, `zenagent/prompts/registry.py`
- Create: `zenagent/prompts/blocks/system/{identity,environment,tool_policy,tool_policy_todo,closing}.md`
- Modify: `zenagent/prompts/__init__.py`
- Modify: `zenagent/app/support.py` (`system_prompt` becomes a wrapper)
- Test: `tests/test_prompts_registry.py`, `tests/test_prompts_compose.py`, `tests/test_prompts_golden.py`

**Interfaces:**
- Produces: `BlockSpec(id, path, version, cache_class, budget_chars, required_vars, optional)`, `BLOCKS: dict[str, BlockSpec]`, `PROMPT_VERSION`, `read_block(block_id, root=None) -> str`, `validate_registry(root=None) -> list[str]`, `PromptContext`, `RenderedBlock`, `RenderedPrompt`, `compose_system(ctx) -> RenderedPrompt`.
- Consumes: `render`, `template_vars` from Task 2; golden files from Task 1.

- [ ] **Step 1: Write block files with the current text**

Copy the exact current strings (byte-for-byte, no trailing newline) from `zenagent/app/support.py`:

- `identity.md`: `You are zenagent, a coding agent working in the user's repository.`
- `environment.md`: `Working directory: {{cwd}}\nAvailable tools: {{tool_list}}`
- `tool_policy.md`: `Tool policy:` plus the Specialized / General / Exploration / Parallel lines (exactly the current strings, each on its own line, `\n`-joined).
- `tool_policy_todo.md`: the current `- Todo: ...` line (163 chars).
- `closing.md`: `Answer concisely. Explain what you did when you finish.`

- [ ] **Step 2: Write `spec.py`**

`BlockSpec` as a frozen dataclass. `BLOCKS` entries for the five system blocks with `version="2026-10-31.1"`, `cache_class="stable"` (tool_policy_todo is `"session"`), budgets from design §6.3 (`identity` 120, `environment` 240, `tool_policy` 700, `tool_policy_todo` 220, `closing` 100), `required_vars` matching each template (`environment`: `("cwd", "tool_list")`; others empty). `PROMPT_VERSION = "2026-10-31.1"`. Add `SYSTEM_BLOCK_ORDER = ("system.identity", "system.environment", "system.tool_policy", "system.tool_policy_todo", "system.closing")`.

- [ ] **Step 3: Write `registry.py`**

`read_block(block_id, root=None)` loads `<root>/blocks/...` when `root` is given, else `importlib.resources.files("zenagent.prompts").joinpath("blocks/...")`; cache with `functools.lru_cache` keyed by `(block_id, str(root))`. `validate_registry` returns problems: unknown id, missing file, `template_vars(content) != set(required_vars)`, non-positive budget.

- [ ] **Step 4: Write `__init__.py` and `compose_system`**

`PromptContext`/`RenderedBlock`/`RenderedPrompt` dataclasses as in the spec §5.2 (with `skill_entries: str = ""` and `root: Path | None = None`). `compose_system(ctx)` renders the system blocks in `SYSTEM_BLOCK_ORDER`, skipping `system.tool_policy_todo` unless `"todo" in ctx.tool_names`, joining with `"\n"`, and returns `RenderedPrompt(text=..., blocks=(...), version=PROMPT_VERSION)`. If `ctx.system_override is not None`, use it verbatim as the text and record a synthetic block id `override`. System-only for this task: ignore `include_context`/`include_skills`/`include_extensions`/`include_mode` (Task 4 adds them).

- [ ] **Step 5: Make `zenagent/app/support.py::system_prompt` a wrapper**

```python
def system_prompt(tool_names: list[str]) -> str:
    ctx = PromptContext(cwd=Path.cwd(), tool_names=tuple(tool_names))
    return compose_system(ctx).text
```

Delete the literal strings from `support.py` (keep `describe_tools`).

- [ ] **Step 6: Write registry/compose/golden tests**

`tests/test_prompts_registry.py`: `validate_registry() == []`; every block id in `BLOCKS` has a file; budgets > 0. `tests/test_prompts_compose.py`: `compose_system` for `default-11`/`tui-14`/`headless-14`/`rpc-13` system parts equals the corresponding `<config>.base.txt` golden (`ctx.cwd=Path("/repo")`, `include_*=False`), and `system_override` wins. `tests/test_prompts_golden.py`: parametrized over those four configs, compose system-only and compare to `tests/golden/prompts/<config>.base.txt`.

- [ ] **Step 7: Run the full gate and commit**

Run: `uv run pytest tests/test_prompts_registry.py tests/test_prompts_compose.py tests/test_prompts_golden.py -v` → PASS. Run: `bash scripts/check.sh` → all existing 1,303 tests still pass (the wrapper keeps `system_prompt` behavior).
Commit: `git add zenagent/prompts zenagent/app/support.py tests/test_prompts_registry.py tests/test_prompts_compose.py tests/test_prompts_golden.py && git commit -m "feat(prompts): system blocks, registry and compose_system"`

---

### Task 4: Plan / context / skills blocks and full `compose_system`

**Files:**
- Create: `zenagent/prompts/blocks/{plan_mode,context_files,skills}.md`
- Modify: `zenagent/prompts/__init__.py`, `zenagent/prompts/spec.py`
- Modify: `zenagent/app/modes.py`, `zenagent/context_files.py`, `zenagent/skills.py`
- Test: `tests/test_prompts_compose.py` (extend), `tests/test_prompts_golden.py` (extend)

**Interfaces:**
- Produces: full `compose_system` honoring `include_context`/`include_skills`/`include_extensions`/`include_mode` in order A→D→E→G→C; `mode_prompt_note`, `system_prompt_with_context`, `skills_prompt_index` remain thin wrappers.
- Consumes: Task 3 `compose_system`, Task 1 goldens.

- [ ] **Step 1: Add block files**

- `plan_mode.md`: exact current 294-char text from `zenagent/app/modes.py` (single paragraph).
- `context_files.md`: `## Project instructions\n{{context_text}}` (matches the current wrapper).
- `skills.md`: `## Skills\nLoad a skill with the read_skill tool before following its instructions:\n\n{{entries}}` (matches the current header; `entries` is pre-formatted by `skills.py`).

Register them in `spec.py` with ids `plan_mode` (budget 700, cache `session`), `context_files` (budget 160, cache `volatile`, required `("context_text",)`), `skills` (budget 120, cache `session`, required `("entries",)`).

- [ ] **Step 2: Extend `compose_system`**

After the system blocks, append in this order, each as `text = f"{text.rstrip()}\n\n{part}"`:
1. if `include_context and ctx.context_text.strip()`: `render(read_block("context_files"), {"context_text": ctx.context_text})`
2. if `include_skills and ctx.skill_entries.strip()`: `render(read_block("skills"), {"entries": ctx.skill_entries})`
3. if `include_extensions and ctx.extension_fragments`: `"\n\n".join(ctx.extension_fragments)`
4. if `include_mode and ctx.mode == "plan"`: `read_block("plan_mode")`

Record each appended block in `RenderedPrompt.blocks` with its chars and cache class.

- [ ] **Step 3: Convert the three modules to wrappers**

- `modes.py`: `mode_prompt_note(mode)` returns `read_block("plan_mode")` for `plan`, else `""` (keep `describe_modes` untouched).
- `context_files.py`: `system_prompt_with_context(base, context_text)` returns `f"{base.rstrip()}\n\n" + render(read_block("context_files"), {"context_text": context_text})`.
- `skills.py`: split `skills_prompt_index` into `format_skill_entries(skills) -> str` (existing entry/limit/omitted logic) and `skills_prompt_index(skills) -> str` = `render(read_block("skills"), {"entries": format_skill_entries(skills)})`; delete the literal header.

- [ ] **Step 4: Extend tests**

`tests/test_prompts_compose.py`: order assertions (context before skills before extensions before plan), include-flag off cases, plan-only note. `tests/test_prompts_golden.py`: add `plan`, `skills`, `context`, `extension`, `eval-11` configs — compose with `ctx.cwd=Path("/repo")`, `skill_entries=format_skill_entries(SKILLS)`, `extension_fragments=EXT`, `system_override` where set, and compare to goldens.

- [ ] **Step 5: Run tests + gate and commit**

Run: `uv run pytest tests/test_prompts_compose.py tests/test_prompts_golden.py tests/test_context_files.py tests/test_modes.py tests/test_skills.py -v` → PASS; `bash scripts/check.sh` → PASS.
Commit: `git add zenagent/prompts zenagent/app/modes.py zenagent/context_files.py zenagent/skills.py tests/test_prompts_compose.py tests/test_prompts_golden.py && git commit -m "feat(prompts): context, skills, plan blocks and full compose_system"`

---

### Task 5: Tool descriptions

**Files:**
- Create: `zenagent/prompts/blocks/tools/{read_file,write_file,edit_file,list_dir,bash,wait,ask,glob,grep,web_search,fetch,todo,task,read_skill}.md`
- Modify: `zenagent/prompts/{spec.py,registry.py,__init__.py}`
- Modify: `zenagent/tools/{fs,bash,search,ask,task,todo}.py`, `zenagent/tools/web/tools.py`, `zenagent/skills.py`
- Test: `tests/test_prompts_tools.py`

**Interfaces:**
- Produces: `tool_description(name: str, **values: str) -> str`; `TOOL_BLOCKS: dict[str, BlockSpec]`; `TOOL_DESCRIPTION_NAMES` tuple of the 14 names.
- Consumes: Task 2 renderer, Task 3 registry.

- [ ] **Step 1: Write the 14 block files**

Copy the exact current description strings, replacing implementation constants with placeholders:
`read_file`: `{{default_read_limit}}`, `{{max_read_limit}}`; `glob`: `{{max_glob_results}}`; `grep`: `{{max_grep_files_per_page}}`, `{{max_grep_hits_per_file}}`; `web_search`: `{{max_results}}`; `read_skill`: `{{skill_names}}`. The rest (`write_file`, `edit_file`, `list_dir`, `bash`, `wait`, `ask`, `todo`, `task`, `fetch`) are static. Register each as `tool.<name>` with `required_vars` equal to the placeholders, budget 900, cache `stable`.

- [ ] **Step 2: Write `tests/test_prompts_tools.py`**

Tests: every `ALL_TOOLS` name plus `("todo", "task", "read_skill")` has a block; no orphan block file; `validate_registry()` covers tool blocks; each tool's `AgentTool.description` equals the `tools.json` golden text (construct `ALL_TOOLS`, `build_todo_tool(None)`, `build_task_tool(model=None, base_tools=[])`, `build_skill_tool(list(SKILLS))`); rendering `read_file` with the fs constants equals the golden text (proves constants flow through, no second source of truth).

- [ ] **Step 3: Implement `tool_description` and wire the tool modules**

In `registry.py`: `read_tool_block(name, root=None)`; in `__init__.py`: `tool_description(name, **values)` = `render(read_block(f"tool.{name}"), values)`. Replace each literal description in the tool modules with the call, passing the module's own constants (e.g. `tool_description("read_file", default_read_limit=str(DEFAULT_READ_LIMIT), max_read_limit=str(MAX_READ_LIMIT))`). Keep `describe_tools()` untouched.

- [ ] **Step 4: Run tests + gate and commit**

Run: `uv run pytest tests/test_prompts_tools.py tests/test_todo.py tests/test_skills.py -v` → PASS; `bash scripts/check.sh` → PASS.
Commit: `git add zenagent/prompts zenagent/tools zenagent/skills.py tests/test_prompts_tools.py && git commit -m "feat(prompts): tool description blocks with injected constants"`

---

### Task 6: Compaction blocks

**Files:**
- Create: `zenagent/prompts/blocks/{compact_system,compact_summary,compact_result}.md`
- Modify: `zenagent/prompts/{spec.py,__init__.py}`, `zenagent/app/compact.py`
- Test: `tests/test_prompts_compact.py`

**Interfaces:**
- Produces: `compose_compact(transcript: str, *, root: Path | None = None) -> tuple[str, str]` (system, user) and `compact_result(summary: str, *, root: Path | None = None) -> str`.
- Consumes: Task 2 renderer, Task 3 registry.

- [ ] **Step 1: Write block files**

`compact_system.md` = current `_SUMMARIZE_SYSTEM` (125 chars). `compact_summary.md` = current `SUMMARIZE_PROMPT` with `{transcript}` replaced by `{{transcript}}` (201 chars template). `compact_result.md` = `# 此前会话摘要\n{{summary}}` (the literal currently inline in `compact.py`; verify the exact prefix from the source before writing).

- [ ] **Step 2: Write tests**

`compose_compact(TRANSCRIPT)` equals `compact.json`'s `system`/`user`; `compact_result("S")` starts with `# 此前会话摘要\n` and ends with `S`; `zenagent/app/compact.py` no longer contains the literal prompt strings (`grep`-style assertion on source text via `Path.read_text()`).

- [ ] **Step 3: Wire `compact.py`**

Replace `_SUMMARIZE_SYSTEM`/`SUMMARIZE_PROMPT` literals with `compose_compact(transcript)`; replace the inline `# 此前会话摘要\n{summary}` with `compact_result(summary)`. Keep `SUMMARIZE_PROMPT`/`_SUMMARIZE_SYSTEM` module names as deprecated aliases only if tests import them — otherwise update `tests/test_compact.py` imports.

- [ ] **Step 4: Run tests + gate and commit**

Run: `uv run pytest tests/test_prompts_compact.py tests/test_compact.py -v` → PASS; `bash scripts/check.sh` → PASS.
Commit: `git add zenagent/prompts zenagent/app/compact.py tests/test_prompts_compact.py && git commit -m "feat(prompts): compaction blocks"`

---

### Task 7: Wire the five call sites

**Files:**
- Modify: `zenagent/app/runtime.py`, `zenagent/cli/main.py`, `zenagent/cli/repl.py`, `zenagent/cli/rpc.py`, `scripts/eval.py`
- Modify: `scripts/prompt_goldens.py` (`--check` uses compose)
- Test: `tests/test_prompts_frontends.py`

**Interfaces:**
- Consumes: `compose_system`, `PromptContext` (Tasks 3–4); wrappers may remain but call sites must use compose.
- Produces: each frontend builds `PromptContext` and assigns `agent.system_prompt = compose_system(ctx).text`.

- [ ] **Step 1: Add a parity helper test**

`tests/test_prompts_frontends.py` calls the compose path per frontend config (same fixtures as goldens) and asserts equality with the golden text; it also asserts the order difference between `tui-14` and `headless-14` is preserved.

- [ ] **Step 2: Rewire runtime (TUI)**

In `Runtime._build_prompt`/`_prompt_with_mode`: build one `PromptContext(cwd=Path.cwd(), tool_names=tuple(t.name for t in tools), mode=self._mode, context_text=<loaded context>, skill_entries=format_skill_entries(self.skills), extension_fragments=(self._extension_prompt,) if self._extension_prompt else (), include_context=self._context_files, ...)`; set `self._base_prompt = compose_system(ctx).text` and drop the manual concatenation. Keep `_recompose` order and tool list unchanged.

- [ ] **Step 3: Rewire headless, REPL, RPC, eval**

- `cli/main.py::_build_agent`/`run_headless`: replace manual `system_prompt` + `system_prompt_with_context` + `extra_prompt` concatenation with `compose_system` using the frontend's own tool-name order; keep mode note placement (it is part of compose when `include_mode=True`).
- `cli/repl.py`: same, using `discover_runtime`'s skills/extensions results.
- `cli/rpc.py`: `include_skills=False`, `include_extensions=False`, `include_mode=False`.
- `scripts/eval.py`: `system_override=options.get("system_prompt_override") or "You are zenagent, a coding agent. Be concise."`, `include_mode=False`, `skill_entries=format_skill_entries(discovered)`, `include_context=bool(options.get("context_files"))`; keep `system_prompt_override` semantics.

- [ ] **Step 4: Update `scripts/prompt_goldens.py --check`**

`--check` renders every config through `compose_system` and compares with the goldens, printing a unified diff and exiting 1 on mismatch. Run: `uv run python scripts/prompt_goldens.py --check` → all 9 configs match.

- [ ] **Step 5: Run the full gate and commit**

Run: `uv run pytest tests/test_prompts_frontends.py -v` → PASS; `bash scripts/check.sh` → PASS (1,303+ tests).
Commit: `git add zenagent/app/runtime.py zenagent/cli scripts/eval.py scripts/prompt_goldens.py tests/test_prompts_frontends.py && git commit -m "refactor(prompts): all frontends compose through one contract"`

---

### Task 8: `zen prompts` CLI

**Files:**
- Create: `zenagent/cli/prompts_cmd.py`
- Modify: `zenagent/cli/main.py` (dispatch `raw[0] == "prompts"` before argparse, like `rpc`/`sessions`)
- Test: `tests/test_cli_prompts.py`

**Interfaces:**
- Consumes: `BLOCKS`, `PROMPT_VERSION`, `compose_system`, `read_block`.
- Produces: `main(argv) -> int` for `list` / `show <id>` / `estimate [--plan]`; no model calls, no network.

- [ ] **Step 1: Write failing CLI tests**

`tests/test_cli_prompts.py`: `main(["list"])` exits 0 and prints every block id + `PROMPT_VERSION`; `main(["show", "system.identity"])` prints the identity text; `main(["estimate"])` exits 0 and prints a total ≤ 9,000 chars; unknown block → exit 2 with a message.

- [ ] **Step 2: Implement `prompts_cmd.py`**

`list`: one line per block `id  version  cache_class  template_chars  budget  vars`. `show <id>`: raw template text. `estimate`: compose with `cwd=Path.cwd()`, `tool_names=ALL_TOOLS names + todo/task`, `include_context=False`, `include_skills=False`, `include_extensions=False`, `include_mode=("--plan" in argv)`; print per-block chars, total, `ceil(total/4)` estimated tokens, and budget headroom.

- [ ] **Step 3: Dispatch from `main()`**

Add before `build_parser()`: `if raw and raw[0] == "prompts": from zenagent.cli.prompts_cmd import main as prompts_main; return prompts_main(raw[1:])`.

- [ ] **Step 4: Run tests + gate and commit**

Run: `uv run pytest tests/test_cli_prompts.py -v` → PASS; `uv run zen prompts list` → prints 25 block ids; `bash scripts/check.sh` → PASS.
Commit: `git add zenagent/cli/prompts_cmd.py zenagent/cli/main.py tests/test_cli_prompts.py && git commit -m "feat(cli): zen prompts list/show/estimate"`

---

### Task 9: Gates, packaging, changelog, docs

**Files:**
- Create: `tests/test_prompts_sources.py`, `tests/test_prompts_budget.py`, `zenagent/prompts/CHANGELOG.md`
- Modify: `scripts/release-check.sh`, `docs/feature-map.md`
- Test: both new test files

**Interfaces:**
- Consumes: all blocks/specs from Tasks 3–6.
- Produces: offline enforcement of design §6.5 (source gate), §6.3 (budgets), wheel packaging check, `PROMPT_VERSION` changelog.

- [ ] **Step 1: Write the source-gate test**

`tests/test_prompts_sources.py`: walk `zenagent/**/*.py` and `scripts/*.py`; for each marker in a fixed list (`"You are zenagent"`, `"Tool policy:"`, `"## Project instructions"`, `"## Skills"`, `"PLAN MODE:"`, `"请把下面这段编码会话压缩"`, `"You compress coding-session"`, `"# 此前会话摘要"`, `"Answer concisely. Explain"`, `"## {role}"`, `"[tool_call"`, `"- ... more skills omitted"`, `"You are zenagent, a coding agent. Be concise."`), assert it appears only in `zenagent/prompts/blocks/**` or in the explicit allowlist of `(relative_file, marker)` pairs for data-formatting cases: `("zenagent/app/compact.py", "## {role}")`, `("zenagent/app/compact.py", "[tool_call")`, `("zenagent/skills.py", "- ... more skills omitted")`, `("scripts/eval.py", "You are zenagent, a coding agent. Be concise.")` (removed in P1). Fail with the file+line of any unlisted occurrence.

- [ ] **Step 2: Write the budget test**

`tests/test_prompts_budget.py`: for each golden config, compose and assert every rendered block ≤ its `budget_chars`, tool descriptions ≤ 900, tool total ≤ 4,400, fixed total ≤ 9,000; assert `PROMPT_VERSION` is a non-empty string and every block spec uses it.

- [ ] **Step 3: Add the wheel packaging check to `scripts/release-check.sh`**

After the existing build step, add a Python snippet that opens `dist/*.whl`, counts entries under `zenagent/prompts/blocks/`, exits 1 unless the count equals the number of block files on disk (25), and also asserts `zenagent/prompts/CHANGELOG.md` is present. Keep the existing checks untouched.

- [ ] **Step 4: Write `CHANGELOG.md` and update docs**

`zenagent/prompts/CHANGELOG.md`: one entry `2026-10-31.1 — P0 extraction: system/plan/context/skills/compact/tool blocks moved verbatim; no behavior change (golden parity).` Update `docs/feature-map.md` with a row: prompt subsystem → `zenagent/prompts/` → `tests/test_prompts_*.py`.

- [ ] **Step 5: Run everything and commit**

Run: `uv run pytest tests/test_prompts_sources.py tests/test_prompts_budget.py -v` → PASS; `uv run python scripts/prompt_goldens.py --check` → all configs match; `bash scripts/check.sh` → PASS; `bash scripts/release-check.sh` (or `uv build --wheel -o /tmp/prompt-wheel && python -c "..."`) → packaging check passes.
Commit: `git add tests/test_prompts_sources.py tests/test_prompts_budget.py zenagent/prompts/CHANGELOG.md scripts/release-check.sh docs/feature-map.md && git commit -m "test(prompts): source gate, budgets, packaging and changelog"`

---

## Final Acceptance (after Task 9)

- `bash scripts/check.sh` green; `uv run python scripts/prompt_goldens.py --check` green for all 9 configs.
- `grep -rn "You are zenagent" zenagent/` returns only `zenagent/prompts/blocks/system/identity.md`.
- `uv run zen prompts list` shows 25 blocks; `estimate` total ≤ 9,000 chars.
- `git log --oneline` shows one commit per task on `feat/prompt-system`.
- P1 (eval variant infra) and P2 (content experiments) get their own plans after this branch lands.
