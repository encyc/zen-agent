# Subagent Budget Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `task` tool's subagent budgets configurable (`[task]` in `config.toml`), explain truncation to the parent model, and surface sidecar-truncated subagent transcripts in the TUI.

**Architecture:** Four one-layer changes. `UserConfig` gains a global-only `[task]` section whose defaults are the single source of truth. `SubagentLimits` carries the three transcript-retention caps into `SubagentStore` (raised to 64 transcripts / 32 MiB total / 8 MiB per file). `build_task_tool` renders the child prompt with its turn budget (default 30, was 12) and stamps `reason`/`turns` onto truncated envelopes. The TUI flags a sidecar transcript that was cut by the per-file cap, separately from the run status.

**Tech Stack:** Python 3.11+, `tomllib`, pytest-asyncio (auto mode), Textual 8.2 (`>=8.2,<9`), Rich.

**Spec:** No separate design doc; the binding decisions are the [Decisions](#decisions) section plus the user's 2026-11-02 request: the three transcript caps are configurable with main-agent-chosen defaults; the turn budget is no longer a hardcoded 12; truncation must not look like context overflow to the parent model.

## Decisions

- Defaults: `max_turns=30`, `keep_transcripts=64`, `max_transcript_bytes=32 MiB`, `max_transcript_file_bytes=8 MiB`.
- `[task]` is **global-only** (like `[safety]`/`[backup]`): a checked-out repository cannot raise its own budget or transcript retention. `_merge_user_configs` copies it from the global layer.
- The caps are read at bootstrap only; the parent model does not set them per `task` call. Per-call overrides are out of scope.
- Transcript-file truncation is a *persistence* flag (`SubagentMeta.truncated`). It never changes the run status; the banner/sidebar wording must say "transcript truncated", never re-label the run.
- The child prompt states its turn budget so the model can wrap up before the cut.

## Global Constraints

- `requires-python = ">=3.11"`; no new runtime dependencies.
- Config errors are `ValueError` naming the file and `section.key`; ints are strictly positive (`bool` rejected, matching `_positive_int`).
- Every new user-visible string needs both `zenagent/i18n/en.py` and `zh.py` (`tests/test_i18n.py::test_catalogs_are_key_and_placeholder_identical`).
- Prompt blocks: content stays within `BlockSpec.budget_chars` (`task_child_system` = 600); every template var is declared in `required_vars`; a content change bumps `PROMPT_VERSION` and gets a `zenagent/prompts/CHANGELOG.md` entry.
- Textual 8.2 only (keyword-only APIs).
- Gate: `uv run ruff check .` and `uv run pytest -q` (or `bash scripts/check.sh`) are green at every commit.

## Review Focus

1. A project-layer `[task]` table must be rejected, exactly like `[safety]`/`[backup]` — Task 1 test.
2. Bad values (`0`, negative, `true`, floats, unknown keys) must fail with the file and `section.key` named — Task 1 tests.
3. Limit injection must not corrupt a transcript being written: a tiny per-file cap marks and stops without losing the footer; prune never drops the newest file — Task 2 tests.
4. A child cut off mid-tool-call has no assistant text: the envelope body must still explain partial output and stay spoof-proof — Task 3 test.
5. A live or freshly finished child with no sidecar meta must never claim "transcript truncated"; only a persisted `SubagentMeta(truncated=True)` may — Task 4 test.
6. The new banner/sidebar text must render in both locales and stay within the narrow sidebar row (`fit()` clips it) — Task 4 tests.

---

### Task 1: `[task]` config section and defaults

**Files:**
- Modify: `zenagent/config.py` (defaults next to `_DEFAULT_BACKUP_*`; `_TASK_KEYS`; `_parse_task`; project-layer check; `UserConfig`; `_merge_user_configs`; `__all__`; module docstring line about global-only sections)
- Modify: `README.md` (config example block)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces module constants (exported via `__all__` and re-used by later tasks):
  `DEFAULT_TASK_MAX_TURNS = 30`, `DEFAULT_TASK_KEEP_TRANSCRIPTS = 64`,
  `DEFAULT_TASK_MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024`,
  `DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024 * 1024`.
- Produces `UserConfig` fields: `task_max_turns: int = 30`,
  `task_keep_transcripts: int = 64`, `task_max_transcript_bytes: int = 33554432`,
  `task_max_transcript_file_bytes: int = 8388608`.
- `[task]` keys, all positive ints: `max_turns`, `keep_transcripts`,
  `max_transcript_bytes`, `max_transcript_file_bytes`.
- Consumes `_section_table`, `_unknown_section_keys`, `_positive_int` (already in `config.py`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_task_section_defaults(tmp_path):
    config = load_user_config(write_config(tmp_path, ""))
    assert config.task_max_turns == 30
    assert config.task_keep_transcripts == 64
    assert config.task_max_transcript_bytes == 32 * 1024 * 1024
    assert config.task_max_transcript_file_bytes == 8 * 1024 * 1024


def test_task_section_parses_overrides_and_keeps_the_rest(tmp_path):
    config = load_user_config(
        write_config(tmp_path, "[task]\nmax_turns = 7\nkeep_transcripts = 3\n")
    )
    assert config.task_max_turns == 7
    assert config.task_keep_transcripts == 3
    assert config.task_max_transcript_bytes == 32 * 1024 * 1024


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("[task]\nmax_turns = 0\n", "task.max_turns"),
        ("[task]\nkeep_transcripts = -1\n", "task.keep_transcripts"),
        ("[task]\nmax_transcript_bytes = true\n", "task.max_transcript_bytes"),
        ("[task]\nmax_transcript_file_bytes = 1.5\n", "task.max_transcript_file_bytes"),
        ("[task]\nsurprise = 1\n", "unknown keys"),
    ],
)
def test_bad_task_values_are_rejected(tmp_path, snippet, message):
    with pytest.raises(ValueError, match=message):
        load_user_config(write_config(tmp_path, snippet))


def test_project_layer_may_not_set_task(tmp_path, monkeypatch):
    repo, _ = stacked_config(tmp_path, monkeypatch, global_text="", project_text="[task]\nmax_turns = 99\n")
    with pytest.raises(ValueError, match=r"\[task\].*global-only"):
        load_user_config(cwd=repo)


def test_project_layer_inherits_global_task_values(tmp_path, monkeypatch):
    repo, _ = stacked_config(
        tmp_path,
        monkeypatch,
        global_text="[task]\nmax_turns = 5\nkeep_transcripts = 2\n",
        project_text="",
    )
    config = load_user_config(cwd=repo)
    assert (config.task_max_turns, config.task_keep_transcripts) == (5, 2)
```

- [ ] **Step 2: Run them and confirm RED**

Run: `uv run pytest tests/test_config.py -q -k "task"` — expect `AttributeError: 'UserConfig' object has no attribute 'task_max_turns'` (and the project-layer test failing for the wrong reason is fine: it is the same missing-parser gap).

- [ ] **Step 3: Implement in `zenagent/config.py`**

Next to the backup defaults:

```python
_TASK_KEYS = frozenset(
    {"max_turns", "keep_transcripts", "max_transcript_bytes", "max_transcript_file_bytes"}
)
DEFAULT_TASK_MAX_TURNS = 30
DEFAULT_TASK_KEEP_TRANSCRIPTS = 64
DEFAULT_TASK_MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024 * 1024


def _parse_task(table: dict, target: Path) -> tuple[int, int, int, int]:
    """Parse a ``[task]`` table into ``(max_turns, keep, total_bytes, file_bytes)``."""
    _unknown_section_keys(table, _TASK_KEYS, "task", target)
    return (
        _positive_int(table, "max_turns", DEFAULT_TASK_MAX_TURNS, "task", target),
        _positive_int(table, "keep_transcripts", DEFAULT_TASK_KEEP_TRANSCRIPTS, "task", target),
        _positive_int(table, "max_transcript_bytes", DEFAULT_TASK_MAX_TRANSCRIPT_BYTES, "task", target),
        _positive_int(table, "max_transcript_file_bytes", DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES, "task", target),
    )
```

- In `_load_config_file`, extend the global-only rejection to `task`, with a message naming every offending section:
  `[safety]/[backup]/[task] are global-only` (test matches `\[task\].*global-only`, so the message must contain `[task]` and `global-only`).
- Parse it next to `_parse_backup`, pass the four values into `UserConfig(...)`.
- Add the four `UserConfig` fields with the constants as defaults (comment: `#: [task]: subagent turn budget and transcript-retention caps (global-only)`).
- In `_merge_user_configs`, copy all four from `base`, under the `# global-only sections always come from the global layer` comment.
- Add the four constants to `config.__all__`.

- [ ] **Step 4: Document the section in `README.md`** — inside the config example, after `[backup]`:

```toml
# global layer only (a project-layer [task]/[safety]/[backup] is rejected)
[task]
max_turns = 30                   # subagent model turns before it is cut off
keep_transcripts = 64            # per-session subagent transcripts retained
max_transcript_bytes = 33554432  # total bytes of retained transcripts per session
max_transcript_file_bytes = 8388608  # one transcript past this is cut and marked
```

Also update the comment above `[safety]` to say `[task]/[safety]/[backup]`.

- [ ] **Step 5: Run the tests and the file**

Run: `uv run pytest tests/test_config.py -q` — expected PASS; then `uv run ruff check zenagent/config.py tests/test_config.py`.

- [ ] **Step 6: Commit**

```bash
git add zenagent/config.py README.md tests/test_config.py
git commit -m "feat(config): [task] subagent budget and transcript caps"
```

---

### Task 2: inject `SubagentLimits` into the store and the `task` tool

**Files:**
- Modify: `zenagent/agent/subagents.py` (constants become config defaults; `SubagentLimits`; store injection)
- Modify: `zenagent/tools/task.py` (`DEFAULT_TASK_MAX_TURNS` re-exported from config; `limits` parameter; store construction)
- Modify: `zenagent/app/runtime.py`, `zenagent/cli/repl.py`, `zenagent/cli/main.py` (pass `limits=` and `max_turns=` from `user_config`)
- Test: `tests/test_subagents_store.py`, `tests/test_task.py`

**Interfaces:**
- Consumes Task 1's `DEFAULT_TASK_*` constants and `UserConfig.task_*` fields.
- Produces in `zenagent/agent/subagents.py` (added to `__all__`):

```python
@dataclass(frozen=True)
class SubagentLimits:
    keep_transcripts: int = MAX_TASK_FILES
    max_total_bytes: int = MAX_TASK_TOTAL_BYTES
    max_file_bytes: int = MAX_TASK_FILE_BYTES

    @classmethod
    def from_user_config(cls, config) -> "SubagentLimits": ...
```

  with `MAX_TASK_FILES = DEFAULT_TASK_KEEP_TRANSCRIPTS`,
  `MAX_TASK_TOTAL_BYTES = DEFAULT_TASK_MAX_TRANSCRIPT_BYTES`,
  `MAX_TASK_FILE_BYTES = DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES` (imported from
  `zenagent.config`; numbers now 64 / 32 MiB / 8 MiB).
- Produces `SubagentStore(session_path, limits: SubagentLimits | None = None)`;
  `append` uses `self._limits.max_file_bytes`, `_prune` uses
  `self._limits.keep_transcripts` / `max_total_bytes`.
- Produces `build_task_tool(..., max_turns: int = DEFAULT_TASK_MAX_TURNS, limits: SubagentLimits | None = None)`
  and `DEFAULT_TASK_MAX_TURNS` importable from `zenagent.tools.task` (re-export;
  default now 30).

- [ ] **Step 1: Write failing store tests** — append to `tests/test_subagents_store.py`
  (`from zenagent.agent.subagents import SubagentLimits, ...`):

```python
def test_limits_override_the_retention_caps(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path, SubagentLimits(keep_transcripts=2))
    for index in range(4):
        store.begin(f"c{index}", f"task{index}", "m")
        store.finish(f"c{index}", status="ok", duration_ms=1, cost=0.0, tokens=0)
        time.sleep(0.005)
    names = {meta.call_id for meta in store.list()}
    assert names == {"c2", "c3"}


def test_limits_override_the_per_file_cap(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path, SubagentLimits(max_file_bytes=300))
    store.begin("c1", "research", "m")
    store.append("c1", messages())  # two JSONL lines > 300 bytes
    [meta] = store.list()
    assert meta.truncated is True
    store.finish("c1", status="ok", duration_ms=1, cost=0.0, tokens=0)
    [meta] = store.list()
    assert meta.status == "ok", "the footer still records the run result"


def test_limits_from_user_config_maps_the_task_fields():
    from zenagent.config import UserConfig

    limits = SubagentLimits.from_user_config(
        UserConfig(task_keep_transcripts=3, task_max_transcript_bytes=400,
                   task_max_transcript_file_bytes=200)
    )
    assert (limits.keep_transcripts, limits.max_total_bytes, limits.max_file_bytes) == (3, 400, 200)
```

- [ ] **Step 2: Run RED** — `uv run pytest tests/test_subagents_store.py -q -k limits` → `TypeError`/`ImportError` for `SubagentLimits`.

- [ ] **Step 3: Implement the store side.** Add the dataclass + imports in `subagents.py`;
  `__init__(self, session_path, limits: SubagentLimits | None = None)` sets
  `self._limits = limits or SubagentLimits()`; switch the two literals in
  `append`/`_prune` to `self._limits.*`. Keep `MAX_TASK_*` module names (tests and
  docs use them). `from_user_config` reads the four `task_*` attributes
  (documented as `UserConfig`, duck-typed to keep `agent` free of a config import
  cycle — `config` must not import `agent`).

- [ ] **Step 4: Write the failing tool-wiring test** — append to `tests/test_task.py`
  (`ToolCallChildStream` already exists; `_tool(stream, **kwargs)` forwards kwargs):

```python
async def test_task_tool_applies_configured_max_turns():
    stream = ToolCallChildStream()
    tool = _tool(stream, max_turns=1)
    result = await tool.execute("t1", {"prompt": "go"})
    assert result.details["termination"] == "max_turns"
    assert "status=\"truncated\"" in result.content[0].text


async def test_task_tool_retains_transcripts_by_its_limits(tmp_path):
    from zenagent.agent.subagents import SubagentLimits, SubagentStore, task_dir

    session = tmp_path / "sessions" / "s.jsonl"
    tool = _tool(ChildStream(), session_path=session, limits=SubagentLimits(keep_transcripts=1))
    await tool.execute("t1", {"prompt": "one"})
    await tool.execute("t2", {"prompt": "two"})
    files = list((task_dir(session)).glob("*.jsonl"))
    assert [path.stem for path in files] == ["t2"]
```

- [ ] **Step 5: Run RED**, then implement in `task.py`: `DEFAULT_TASK_MAX_TURNS =
  DEFAULT_TASK_MAX_TURNS` (import from `zenagent.config`, keep it in `__all__`),
  add `limits: SubagentLimits | None = None` to `build_task_tool`, build the
  store as `SubagentStore(session_path, limits)`, and thread `max_turns` through
  (it already has the parameter — only the default constant moves).

- [ ] **Step 6: Wire the three call sites** — `zenagent/app/runtime.py` (has
  `runtime.user_config`), `zenagent/cli/repl.py` (has `user_config`),
  `zenagent/cli/main.py` (has `user_config`): in each `build_task_tool(...)` call
  add

```python
max_turns=user_config.task_max_turns,
limits=SubagentLimits.from_user_config(user_config),
```

  and the corresponding import. `zenagent/cli/rpc/` has no parsed config; it
  keeps the defaults (no change).

- [ ] **Step 7: Run the task tests + affected suites**

Run: `uv run pytest tests/test_task.py tests/test_subagents_store.py tests/test_app_runtime.py -q` → PASS.

- [ ] **Step 8: Commit**

```bash
git add zenagent/agent/subagents.py zenagent/tools/task.py zenagent/app/runtime.py \
  zenagent/cli/repl.py zenagent/cli/main.py tests/test_subagents_store.py tests/test_task.py
git commit -m "feat(task): inject subagent limits; raise the turn budget to 30"
```

---

### Task 3: explicit truncation reason + budget-aware child prompt

**Files:**
- Modify: `zenagent/tools/task.py` (`_envelope` reason/turns; child prompt render)
- Modify: `zenagent/prompts/blocks/task_child_system.md` (add the budget sentence)
- Modify: `zenagent/prompts/spec.py` (`required_vars=("max_turns",)`, `PROMPT_VERSION` bump)
- Modify: `zenagent/prompts/CHANGELOG.md` (entry for the bump)
- Test: `tests/test_task.py`, `tests/test_prompts_budget.py`

**Interfaces:**
- Consumes `render` from `zenagent.prompts` and `read_block("task_child_system")`.
- Produces envelope attributes for a truncated run:
  `<task-result name="n" status="truncated" reason="max_turns" turns="12" duration_ms=... cost=... tokens=...>`
  and a body tail
  `(partial output: the child stopped at its max_turns budget after 12 turns; narrow the task and delegate again to continue.)`.
  `details` gains `"reason"` and `"turns"`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_task.py`:

```python
async def test_truncated_envelope_names_its_reason_and_turns():
    stream = ToolCallChildStream()
    tool = _tool(stream, max_turns=1)
    result = await tool.execute("t1", {"prompt": "go"})
    text = result.content[0].text
    assert 'status="truncated" reason="max_turns" turns="1"' in text
    assert "partial output" in text
    assert result.details["reason"] == "max_turns"
    assert result.details["turns"] == 1


async def test_ok_envelope_has_no_reason_attribute():
    result = await _tool(ChildStream()).execute("t1", {"prompt": "go"})
    assert "reason=" not in result.content[0].text


async def test_child_prompt_declares_its_turn_budget():
    seen: list[str] = []

    class CapturingStream(ChildStream):
        async def __call__(self, model, messages, tools, options):
            seen.append(messages[0].content)
            async for event in super().__call__(model, messages, tools, options):
                yield event

    tool = _tool(CapturingStream(), max_turns=7)
    await tool.execute("t1", {"prompt": "go"})
    assert "at most 7 model turns" in seen[0]
```

- [ ] **Step 2: Run RED**, then implement:
  - `_envelope(..., reason: str = "", turns: int = 0)`: append the reason/turns
    attributes after `status` only when `reason` is non-empty; append the body
    tail after escaping the child body (escape first, then add the tail — the
    tail contains no `</task-result>`).
  - In `execute`: `termination = child.termination`;
    `reason = termination if status == "truncated" else ""`;
    `turns = sum(1 for message in child.messages if isinstance(message, AssistantMessage))`;
    pass both to `_envelope` and add `"reason"`/`"turns"` to `details`.
  - Child system prompt:
    `system_prompt=render(read_block("task_child_system"), {"max_turns": str(max_turns)})`.
  - Add to `zenagent/prompts/blocks/task_child_system.md` (keep total ≤ 600 chars):

```
You have at most {{max_turns}} model turns for this task. The budget cuts you
off mid-work: return your findings before the last turn instead of saving them
for the end.
```

  - `zenagent/prompts/spec.py`: add `required_vars=("max_turns",)` to the
    `task_child_system` BlockSpec, set `PROMPT_VERSION = "2026-11-02.1"`, and add
    a matching `## 2026-11-02.1` entry to `zenagent/prompts/CHANGELOG.md`.

- [ ] **Step 3: Run**

`uv run pytest tests/test_task.py tests/test_prompts_budget.py tests/test_prompts_registry.py -q` → PASS. If `test_template_budgets_hold` fails, shorten the added sentence, do not raise the budget.

- [ ] **Step 4: Commit**

```bash
git add zenagent/tools/task.py zenagent/prompts tests/test_task.py
git commit -m "fix(task): truncation reason in the envelope; budget-aware child prompt"
```

---

### Task 4: show sidecar transcript truncation in the TUI

**Files:**
- Modify: `zenagent/app/views.py` (`SubagentView.transcript_truncated: bool = False`)
- Modify: `zenagent/app/chat.py` (`_subagent_view` maps `meta.truncated`)
- Modify: `zenagent/cli/tui/widgets/subagent_banner.py` (`show(..., transcript_truncated=False)`)
- Modify: `zenagent/cli/tui/app.py` (both `SubagentBanner.show` call sites)
- Modify: `zenagent/cli/tui/widgets/sidebar.py` (row suffix)
- Modify: `zenagent/i18n/en.py`, `zenagent/i18n/zh.py` (two keys)
- Test: `tests/test_app_views.py`, `tests/test_tui_subagent_resume.py`

**Interfaces:**
- Consumes `SubagentMeta.truncated` (already parsed by `_read_meta`).
- Produces `SubagentView.transcript_truncated: bool = False`;
  `SubagentBanner.show(name, *, status=None, transcript_truncated=False)`;
  i18n keys `tui.subagent.transcript_truncated` (en `"transcript truncated"`,
  zh `"转录已截断"`) and `tui.sidebar.transcript_cut` (en `" [cut]"`, zh `"［截］"`).

- [ ] **Step 1: Write failing tests**

`tests/test_app_views.py`:

```python
def test_subagent_view_defaults_to_a_complete_transcript():
    view = SubagentView(call_id="c1", name="research", status="ok", duration_ms=1, cost=0.0, tokens=0)
    assert view.transcript_truncated is False


def test_subagent_view_carries_the_transcript_truncation_flag():
    from zenagent.agent.subagents import SubagentMeta
    from zenagent.app.chat import _subagent_view
    from zenagent.app.views import ToolCallView

    card = ToolCallView(call_id="c1", name="task", args_preview="{}",
                        args={"name": "research", "prompt": "go"})
    meta = SubagentMeta(call_id="c1", name="research", model="m",
                        started_at=0.0, status="ok", truncated=True)
    view = _subagent_view(card, meta=meta, has_transcript=True)
    assert view.transcript_truncated is True
```

`tests/test_tui_subagent_resume.py` — append a test that runs one task through
the real tool, then appends the store's truncation marker to the sidecar, resumes
a fresh app and opens the row:

```python
async def test_truncated_sidecar_is_marked_in_the_row_and_banner(runtime_factory):
    from zenagent.agent.subagents import task_dir
    from zenagent.i18n import t

    runtime = await runtime_factory(stream_fn=TaskCallingStream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("delegate this")
        await app.chat.wait_for_idle()
        session_path = runtime.session.path

    sidecar = next(task_dir(session_path).glob("*.jsonl"))
    with sidecar.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "truncated"}\n')

    resumed = await runtime_factory(stream_fn=TaskCallingStream(), resume=session_path)
    app2 = ZenAgentApp(resumed)
    async with app2.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app2._refresh_status()
        await pilot.press("ctrl+b")
        await pilot.pause()
        [entry] = app2.chat.status.subagents
        assert entry.transcript_truncated is True
        row = next(row for row in app2.query_one(Sidebar).query(SidebarRow) if row.key == entry.call_id)
        assert t("tui.sidebar.transcript_cut") in row.plain
        await pilot.click(row)
        await pilot.pause()
        banner = app2.query_one(SubagentBanner)
        assert t("tui.subagent.transcript_truncated") in banner.plain
```

- [ ] **Step 2: Run RED** → `AttributeError`/assertion failures.

- [ ] **Step 3: Implement** — add the field (after `has_transcript`); map it in
  `_subagent_view` with `transcript_truncated=bool(meta is not None and meta.truncated)`;
  banner appends `f" · {t('tui.subagent.transcript_truncated')}"` (DIM) when true;
  `app.py` passes `transcript_truncated=entry.transcript_truncated if entry is not None else False`
  at both `show()` calls; sidebar appends `t("tui.sidebar.transcript_cut")` to the
  row label when `view.transcript_truncated`; add both i18n keys.

- [ ] **Step 4: Run the affected suites**

Run: `uv run pytest tests/test_app_views.py tests/test_tui_subagent_view.py tests/test_tui_subagent_resume.py tests/test_tui_sidebar.py tests/test_i18n.py -q` → PASS.

- [ ] **Step 5: Full gate**

Run: `bash scripts/check.sh` → ruff clean, `1880+ passed`.

- [ ] **Step 6: Commit**

```bash
git add zenagent/app/views.py zenagent/app/chat.py zenagent/cli/tui/widgets/subagent_banner.py \
  zenagent/cli/tui/app.py zenagent/cli/tui/widgets/sidebar.py zenagent/i18n tests
git commit -m "feat(tui): mark sidecar-truncated subagent transcripts"
```

---

## Self-Review

- **Spec coverage:** three configurable caps → Task 1/2; retained defaults → Task 1; turn budget 30 → Task 2; explicit reason to the parent → Task 3; child prompt honesty → Task 3; truncation visible (does not look like context overflow) → Task 4; README → Task 1.
- **Types:** `SubagentLimits.from_user_config` consumes exactly the `UserConfig.task_*` fields Task 1 produces; `build_task_tool`'s `limits=` is what Task 2 wires and Task 2's tests consume; `SubagentView.transcript_truncated` is produced in Task 4's chat mapping and consumed by Task 4's banner/sidebar.
- **Deferred (out of scope):** per-call budget overrides from the model, `max_cost` config, transcript retention by session age, merging the two `SubagentMeta.truncated` meanings beyond wording.
