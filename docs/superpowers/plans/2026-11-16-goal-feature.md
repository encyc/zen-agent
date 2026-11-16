# Goal Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-scoped `/goal` loop to Zen Agent: set a verifiable objective, auto-continue after each clean turn, verify completion with a deterministic check or an independent one-shot model, and stop on budgets or blockers.

**Architecture:** `zenagent/app/goal.py` owns `GoalState`, `GoalController`, budgets, persistence and continuation decisions. `ChatController` schedules continuation after clean turns, enforces a mid-run budget watchdog, and gives user messages priority. `Runtime` binds the controller to the session. TUI and Desktop consume the shared `RunStatus.goal` and `/goal` command.

**Tech Stack:** Python 3.11+, asyncio, pytest/pytest-asyncio, Textual, Tauri/React/TypeScript, append-only JSONL session records.

**Spec:** `docs/superpowers/specs/2026-11-16-goal-feature-design.md`

## Global Constraints

- No new runtime dependencies; standard library + existing `httpx` only.
- `zenagent/app/goal.py` and `goal_verifier.py` must not import `argparse`, `textual`, `rich`, `prompt_toolkit`, or any CLI/desktop module.
- One active goal per session, ever. A new `/goal` replaces the old one.
- Goal state is persisted as a `goal` side-channel record in the session JSONL; no SQLite, no daemon.
- Goal never changes permission mode; tools still go through `PermissionBroker`.
- Goal text is untrusted data; check commands are user-authored and model-immutable.
- All user-facing strings go through `zenagent.i18n.t` with both `en.py` and `zh.py` entries.
- ruff/mypy: new modules must pass `uv run mypy zenagent` and `uv run ruff check .`.
- The only gate is `bash scripts/check.sh`; frontend also runs `cd desktop && npm run build && npm test`.
- Session compatibility: old JSONL files load with `goal=False` messages and no goal; old clients ignore the new `goal` record/field.

## Review Focus

These are the failure modes no single task's happy-path test covers; each has a dedicated test in the owning task.

1. **A user message arrives while the goal verifier/check is running.** The user turn must run first; goal continuation must never start two agent runs or overtake the queue. Test: `test_user_message_wins_over_goal_continuation` (Task 8).
2. **App restart or `/import` with a persisted `check`.** A session file can contain arbitrary shell; it must restore `paused` and require `/goal resume`, never auto-execute. Test: `test_persisted_check_restores_paused` (Task 9).
3. **Session switch preference.** Default keeps the goal active and resumes on return; `goal_pause_on_session_switch=true` pauses on leave. No background session may start a goal run. Test: `test_switch_keeps_goal_active_by_default` / `test_switch_pause_pref_pauses` (Task 9).
4. **Provider usage normalization and subagent spend.** `Usage.input` means different things on OpenAI vs Anthropic; budget must not undercount. Test: `test_budget_counts_provider_tokens_and_subagents` (Task 6).
5. **Goal survives undo/retry/compact but not fork.** Test: `test_goal_survives_rewrite_and_drops_on_fork` (Task 2).

---

## File Structure

**New Python modules**

- `zenagent/app/goal.py` — state, parser, controller, prompt renderer, token helper.
- `zenagent/app/goal_verifier.py` — `CheckVerifier`, `ModelVerifier`, `GoalVerifierError`.
- `zenagent/cli/tui/widgets/goal_hud.py` — one-line TUI HUD.
- `desktop/src/ui/chat/goalView.ts` — pure summary/label helpers.
- `desktop/src/ui/chat/GoalChip.tsx` — header/composer chip.
- `desktop/src/ui/chat/GoalPanel.tsx` — details/actions panel.

**Modified Python modules**

- `zenagent/types.py` — `UserMessage.goal`.
- `zenagent/agent/agent.py` — `prompt(..., goal=False)` plumbing.
- `zenagent/agent/loop.py` — `user_goal` parameter.
- `zenagent/app/records.py` — `GOAL` kind, builder, rewrite predicate.
- `zenagent/app/sessions.py` — fork drops goal.
- `zenagent/limits.py` — `[limits.goal]` knobs.
- `zenagent/app/views.py` — `GoalView`, `RunStatus.goal`, goal message rendering.
- `zenagent/app/runtime.py` — `Runtime.goal`, session binding, resume callback.
- `zenagent/app/bootstrap.py` — limits/verifier injection.
- `zenagent/app/chat.py` — `start_goal`, `_run(goal=...)`, watchdog, status.
- `zenagent/app/commands.py` — `/goal`.
- `zenagent/cli/rpc/server.py` — flat `goal` in status.
- `zenagent/cli/rpc/ops_config.py` (or a small mixin) — `set_pref` for GUI.
- `zenagent/cli/tui/app.py` — mount/update `GoalHud`.
- `zenagent/cli/tui/widgets/sidebar.py` — goal section.
- `zenagent/cli/tui/screens/editors.py` — settings toggle.
- `zenagent/cli/tui/screens/pickers.py` — toggle handler.
- `zenagent/i18n/en.py`, `zenagent/i18n/zh.py` — copy.
- `desktop/src/rpc/types.ts` — `GoalView`, `StatusResult.goal`.
- `desktop/src/ui/composer/InputBar.tsx` — mount chip/panel.
- `desktop/src/ui/pages/SettingsPage.tsx` — Conversation toggle.
- `desktop/src/state/stores.ts` — goal panel state if needed.
- `docs/guide/commands.md`, `docs/guide/tui.md`, `CHANGELOG.md`.

---

### Task 1: Goal state model and argument parser

**Files:**
- Create: `zenagent/app/goal.py`
- Test: `tests/test_goal.py`

**Interfaces:**
- Consumes: `zenagent.types.Usage`.
- Produces:
  - `GoalStatus = Literal["active","paused","blocked","budget_limited","complete"]`
  - `GoalLimits(max_turns=40, max_tokens=2_000_000, max_wall_seconds=14400, check_timeout_s=300, verifier_max_tokens=512)`
  - `GoalState` dataclass with the fields in the spec, `GoalState.new(objective, *, check=None, limits=None, max_turns=None)`, `to_dict()`, `from_dict(dict)`.
  - `GoalArgs(action, objective, check, max_turns)`, `parse_goal_args(argument: str) -> GoalArgs`.
  - `goal_tokens(usage: Usage, api: str) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal.py
import pytest

from zenagent.app.goal import GoalLimits, GoalState, goal_tokens, parse_goal_args
from zenagent.types import Usage


def test_parse_empty_is_status():
    assert parse_goal_args("").action == "status"
    assert parse_goal_args("   ").action == "status"


def test_parse_lifecycle_commands():
    assert parse_goal_args("pause").action == "pause"
    assert parse_goal_args("resume").action == "resume"
    assert parse_goal_args("clear").action == "clear"
    assert parse_goal_args("stop").action == "clear"


def test_parse_check_and_budget_before_objective():
    args = parse_goal_args('--check "pytest -q" --budget 7 fix flaky tests')
    assert args.action == "set"
    assert args.check == "pytest -q"
    assert args.max_turns == 7
    assert args.objective == "fix flaky tests"


def test_flags_after_objective_are_literal():
    args = parse_goal_args("fix tests --check nope")
    assert args.check is None
    assert args.objective == "fix tests --check nope"


@pytest.mark.parametrize(
    "raw", ["--budget 0 fix", "--budget x fix", "--check", "--check '' fix"]
)
def test_parse_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_goal_args(raw)


def test_state_roundtrip_and_defaults():
    state = GoalState.new("fix tests", check="pytest -q", limits=GoalLimits())
    assert state.status == "active"
    assert state.max_turns == 40
    assert GoalState.from_dict(state.to_dict()) == state


def test_goal_tokens_is_provider_aware():
    usage = Usage(input=100, output=20, cache_read=60, cache_write=10)
    assert goal_tokens(usage, "openai-completions") == 120
    assert goal_tokens(usage, "anthropic-messages") == 190
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal.py -q`
Expected: FAIL with `ModuleNotFoundError: zenagent.app.goal`.

- [ ] **Step 3: Implement the state model and parser**

Implement in `zenagent/app/goal.py`:

- `GoalLimits` as a frozen dataclass with the five defaults above.
- `GoalState` with all fields from the spec. `new()` generates `goal_id=uuid.uuid4().hex`, sets `created_at=updated_at=time.time()`, applies `max_turns` override only when positive.
- `from_dict()` validates `status` against the literal set and raises `ValueError` for unknown status; missing optional numeric fields default to 0.
- `parse_goal_args()`: strip; empty → status; exact lifecycle words (`pause`, `resume`, `clear`, `stop`, `off`, `cancel`) → action; otherwise loop over leading `--check`/`--budget` flags, consume a quoted or single-token value, then treat the remainder as objective. Reject empty objective, >4000 chars, `--budget <= 0`, and a bare `--check`.
- `goal_tokens()`: `usage.input + usage.output` for OpenAI-style APIs; add `usage.cache_read + usage.cache_write` for `anthropic-messages`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/goal.py tests/test_goal.py
git commit -m "feat(goal): add state model and argument parser"
```

---

### Task 2: Goal session records and rewrite/fork behavior

**Files:**
- Modify: `zenagent/app/records.py`
- Modify: `zenagent/app/sessions.py`
- Test: `tests/test_goal_records.py`

**Interfaces:**
- Consumes: `records.goal(state: dict | None)`.
- Produces: `records.GOAL`, `records.is_goal(record)`, `records.keep_for_rewrite(*, keep_tool_end=True, keep_goal=True)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_records.py
from zenagent.agent.session import Session
from zenagent.app import records


def _goal_record() -> dict:
    return records.goal({"goal_id": "g1", "status": "active", "objective": "fix"})


def test_goal_record_shape():
    record = _goal_record()
    assert record["type"] == records.GOAL
    assert records.is_goal(record)
    assert records.goal(None)["goal"] is None


def test_keep_for_rewrite_keeps_goal_by_default():
    assert records.keep_for_rewrite()({"type": records.GOAL}) is True


def test_keep_for_rewrite_can_drop_goal_for_fork():
    assert records.keep_for_rewrite(keep_goal=False)({"type": records.GOAL}) is False


async def test_fork_drops_goal_but_rewrite_keeps_it(tmp_path):
    from zenagent.types import UserMessage

    source = Session(tmp_path / "source.jsonl")
    source.materialize()
    await source.append(UserMessage(content="hi", timestamp=1))
    source.append_meta(_goal_record())
    rewrite = source.rewrite(
        [], keep=records.keep_for_rewrite(), root=tmp_path
    )
    assert [r for r in Session(rewrite).records if records.is_goal(r)]

    fork = source.fork_at(
        1, keep=records.keep_for_rewrite(keep_goal=False), root=tmp_path
    )
    assert not [r for r in Session(fork).records if records.is_goal(r)]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_records.py -q`
Expected: FAIL because `records.GOAL` is undefined.

- [ ] **Step 3: Implement the record kind and predicate**

In `zenagent/app/records.py`:

- Add `GOAL = "goal"` to `__all__`, `KNOWN_KINDS`, and `KINDS`.
- Add `is_goal(record)`.
- Add `goal(state: dict | None) -> dict` returning `{"type": GOAL, "goal": state}`; `append_meta` stamps the timestamp.
- Change `keep_for_rewrite(*, keep_tool_end=True, keep_goal=True)`; return `keep_goal` for `GOAL` before the unknown-kind fallback.

In `zenagent/app/sessions.py`, `SessionManager.fork` calls
`records.keep_for_rewrite(keep_goal=False)`. `rewrite`/undo/retry/shake keep the default `True`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_records.py tests/test_app_records.py tests/test_app_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/records.py zenagent/app/sessions.py tests/test_goal_records.py
git commit -m "feat(goal): persist goal records and control fork inheritance"
```

---

### Task 3: Goal budget configuration

**Files:**
- Modify: `zenagent/limits.py`
- Modify: `zenagent/app/goal.py`
- Test: `tests/test_limits_goal.py`

**Interfaces:**
- Consumes: `GoalLimits`.
- Produces: `Limits.goal_max_turns`, `goal_max_wall_minutes`, `goal_max_tokens`, `goal_check_timeout_s`, `goal_verifier_max_tokens`; `GoalLimits.from_limits(limits)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_limits_goal.py
from pathlib import Path

from zenagent.app.goal import GoalLimits
from zenagent.limits import Limits, parse_limits


def test_goal_defaults():
    limits = Limits()
    assert limits.goal_max_turns == 40
    assert limits.goal_max_wall_minutes == 240
    assert limits.goal_max_tokens == 2_000_000
    assert limits.goal_check_timeout_s == 300
    assert limits.goal_verifier_max_tokens == 512


def test_parse_goal_table():
    limits = parse_limits(
        {
            "goal": {
                "max_turns": 5,
                "max_wall_minutes": 30,
                "max_tokens": 10000,
                "check_timeout_s": 60,
                "verifier_max_tokens": 128,
            }
        },
        Path("config.toml"),
    )
    assert limits.goal_max_turns == 5
    assert limits.goal_max_wall_minutes == 30
    assert limits.goal_max_tokens == 10000
    assert limits.goal_check_timeout_s == 60
    assert limits.goal_verifier_max_tokens == 128


def test_goal_limits_adapter():
    goal = GoalLimits.from_limits(Limits(goal_max_wall_minutes=2))
    assert goal.max_wall_seconds == 120
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_limits_goal.py -q`
Expected: FAIL with missing `goal_*` attributes.

- [ ] **Step 3: Add the limits**

In `zenagent/limits.py`:

- Add the five fields to `Limits` with the defaults above.
- Add five `Limit` entries to `LIMITS` with `table="limits.goal"`, `section="goal"`, `kind="int"`, `apply="session"`, sensible `minimum`/`maximum`.
- `parse_limits`, `render_limits_block`, `describe`, and the settings writer work from `LIMITS`; no extra branches unless a test proves one is needed.

In `zenagent/app/goal.py` add:

```python
@classmethod
def from_limits(cls, limits) -> "GoalLimits":
    return cls(
        max_turns=limits.goal_max_turns,
        max_tokens=limits.goal_max_tokens,
        max_wall_seconds=limits.goal_max_wall_minutes * 60,
        check_timeout_s=limits.goal_check_timeout_s,
        verifier_max_tokens=limits.goal_verifier_max_tokens,
    )
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_limits_goal.py tests/test_limits_apply.py tests/test_limits_rpc.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/limits.py zenagent/app/goal.py tests/test_limits_goal.py
git commit -m "feat(goal): add goal budget limits"
```

---

### Task 4: GoalController lifecycle and persistence

**Files:**
- Modify: `zenagent/app/goal.py`
- Modify: `zenagent/app/views.py`
- Test: `tests/test_goal_controller.py`

**Interfaces:**
- Consumes: `GoalState`, `GoalLimits`, `records.goal`, `Session`.
- Produces:
  - `GoalView` and `RunStatus.goal: GoalView | None` in `zenagent/app/views.py`.
  - `GoalController(limits=None, verifier=None, check_verifier=None, now=time.time)`.
  - `GoalController.configure(*, limits, verifier, check_verifier=None)`.
  - `bind_session(session: Session | None)`, `state`, `snapshot()`, `set(objective, *, check=None, max_turns=None)`, `pause()`, `resume()`, `clear()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_controller.py
from zenagent.agent.session import Session
from zenagent.app.goal import GoalController, GoalLimits


def _session(tmp_path):
    session = Session(tmp_path / "s.jsonl")
    session.materialize()
    return session


def test_set_persists_and_replays(tmp_path):
    session = _session(tmp_path)
    ctl = GoalController(limits=GoalLimits(max_turns=5), now=lambda: 100.0)
    ctl.bind_session(session)
    state = ctl.set("fix tests", check="pytest -q")
    assert state.status == "active"
    assert state.max_turns == 5

    again = GoalController(limits=GoalLimits(max_turns=5))
    again.bind_session(Session(session.path))
    view = again.snapshot()
    assert view is not None
    assert view.objective == "fix tests"
    assert view.check == "pytest -q"


def test_pause_resume_clear(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    ctl.set("fix")
    assert ctl.pause().status == "paused"
    assert ctl.resume().status == "active"
    ctl.clear()
    assert ctl.snapshot() is None


def test_new_goal_replaces_old_id(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    first = ctl.set("one")
    second = ctl.set("two")
    assert first.goal_id != second.goal_id
    assert ctl.snapshot().objective == "two"


def test_terminal_goal_cannot_resume(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    ctl.set("one")
    ctl._state.status = "complete"
    assert ctl.resume() is None


def test_bind_session_loads_last_record(tmp_path):
    source = _session(tmp_path)
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(source)
    ctl.set("first")
    ctl.set("second")
    fresh = GoalController(limits=GoalLimits())
    fresh.bind_session(Session(source.path))
    assert fresh.snapshot().objective == "second"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_controller.py -q`
Expected: FAIL because `GoalController`/`GoalView` do not exist.

- [ ] **Step 3: Implement the controller**

In `zenagent/app/views.py` add the `GoalView` dataclass from the spec (snake_case fields)
and `RunStatus.goal: GoalView | None = None`.

In `zenagent/app/goal.py`:

- `GoalController.__init__` stores `limits`, `verifier`, `check_verifier`, `now`,
  `_state=None`, `_session=None`.
- `configure(*, limits, verifier, check_verifier=None)` updates the defaults used for
  future goals; it does not rewrite the current goal's per-goal budget.
- `bind_session(session)` stores the session, calls `session.ensure_loaded()` when not `None`, walks `session.records` in order, keeps the last `records.is_goal` payload, and sets `_state = GoalState.from_dict(payload)` or `None`.
- `set()` creates a new state via `GoalState.new`, persists it, returns it. It replaces an existing active/paused/blocked goal; terminal goals are replaced too.
- `pause()` only transitions `active → paused`; `resume()` only `paused|blocked → active` and resets `no_progress_turns`, `verifier_failures`, `check_failures`; `clear()` sets `None` and persists `records.goal(None)`.
- `_persist()` calls `self._session.append_meta(records.goal(self._state.to_dict() if self._state else None))` when a session is bound.
- `snapshot()` maps state to `GoalView`; `updated_at` is epoch seconds.
- Keep the controller UI-agnostic: no imports from `chat.py`, `runtime.py`, `cli/`, or desktop.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/goal.py zenagent/app/views.py tests/test_goal_controller.py
git commit -m "feat(goal): add controller lifecycle and replay"
```

---

### Task 5: Deterministic and model verifiers

**Files:**
- Create: `zenagent/app/goal_verifier.py`
- Modify: `zenagent/app/goal.py` (`GoalVerdict`, `GoalVerifier`, `GoalVerifierError`)
- Test: `tests/test_goal_verifier.py`

**Interfaces:**
- Consumes: `GoalState`, `GoalTurnContext`, `GoalVerdict`.
- Produces:
  - `GoalVerifier` protocol: `async def verify(goal, ctx) -> GoalVerdict`.
  - `GoalVerifierError`.
  - `CheckVerifier(timeout_s: int, cwd: Path)`.
  - `ModelVerifier(stream_fn, model, api_key, max_tokens: int)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_verifier.py
import sys

import pytest

from zenagent.app.goal import GoalLimits, GoalState, GoalTurnContext
from zenagent.app.goal_verifier import CheckVerifier, GoalVerifierError, ModelVerifier
from zenagent.types import Usage


def _goal(check=None):
    return GoalState.new("fix tests", check=check, limits=GoalLimits())


def _ctx():
    return GoalTurnContext(
        messages=[],
        auto=True,
        tool_calls=1,
        termination="stop",
        tokens_delta=0,
        subagent_tokens_delta=0,
        elapsed_s=1.0,
        user_queued=False,
    )


async def test_check_verifier_met_and_not_met(tmp_path):
    verifier = CheckVerifier(timeout_s=5, cwd=tmp_path)
    met = await verifier.verify(
        _goal(f'{sys.executable} -c "print(1)"'), _ctx()
    )
    assert met.verdict == "met"
    failed = await verifier.verify(
        _goal(f'{sys.executable} -c "raise SystemExit(2)"'), _ctx()
    )
    assert failed.verdict == "not_met"
    assert failed.evidence


async def test_check_verifier_timeout_is_not_met(tmp_path):
    verifier = CheckVerifier(timeout_s=0.1, cwd=tmp_path)
    verdict = await verifier.verify(
        _goal(f'{sys.executable} -c "import time; time.sleep(2)"'), _ctx()
    )
    assert verdict.verdict == "not_met"
    assert "timed out" in verdict.reason.lower()


async def test_model_verifier_parses_json(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent

        partial = AssistantMessage(
            content=[TextContent(text='{"verdict":"met","reason":"tests pass"}')],
            provider="p",
            model="m",
            usage=Usage(input=10, output=5),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = ModelVerifier(
        stream_fn=runtime.agent.stream_fn,
        model=runtime.agent.model,
        api_key="test-key",
        max_tokens=64,
    )
    verdict = await verifier.verify(_goal(), _ctx())
    assert verdict.verdict == "met"
    assert verdict.tokens > 0


async def test_model_verifier_rejects_non_json(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent

        partial = AssistantMessage(
            content=[TextContent(text="not json")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = ModelVerifier(
        stream_fn=runtime.agent.stream_fn,
        model=runtime.agent.model,
        api_key="test-key",
        max_tokens=64,
    )
    with pytest.raises(GoalVerifierError):
        await verifier.verify(_goal(), _ctx())
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_verifier.py -q`
Expected: FAIL with missing module/symbol.

- [ ] **Step 3: Implement the verifiers**

In `zenagent/app/goal.py` add `GoalVerdict(verdict, reason, evidence="", tokens=0)` and the
`GoalVerifier` protocol. Define `GoalVerifierError` in `zenagent/app/goal_verifier.py`
(the controller catches it without importing provider internals).

In `zenagent/app/goal_verifier.py`:

- `CheckVerifier.verify`: run `asyncio.create_subprocess_shell` with the constructor's
  `cwd`, `stdout=PIPE, stderr=STDOUT`, `start_new_session=True` on POSIX,
  `timeout=timeout_s`.
  On timeout kill the process (and group where supported), return `not_met` with
  `reason="check timed out after Ns"`. On exit 0 return `met`; otherwise `not_met` with
  the bounded output tail (max 2000 chars). `tokens=0`.
- `ModelVerifier.verify`: build a system prompt (`You are a completion auditor...`) and a
  user prompt containing `<objective>` (escaped) and a bounded transcript excerpt
  (last 12 messages / 12k chars; tool results truncated to 2k). Call `stream_fn` with
  `StreamOptions(api_key=..., thinking="off", max_tokens=...)`, collect text, parse strict
  JSON `{"verdict","reason"}`. Retry once on parse failure; then raise `GoalVerifierError`.
  Return `tokens=goal_tokens(usage, model.api)` from the assistant message.
- Use `safe_stream` so provider failures become `GoalVerifierError`, never an exception
  escaping the controller.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_verifier.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/goal.py zenagent/app/goal_verifier.py tests/test_goal_verifier.py
git commit -m "feat(goal): add check and model verifiers"
```

---

### Task 6: Continuation decisions, budgets and blockers

**Files:**
- Modify: `zenagent/app/goal.py`
- Test: `tests/test_goal_after_turn.py`

**Interfaces:**
- Consumes: `GoalVerifier`, `GoalState`, `GoalLimits`, `GoalTurnContext`.
- Produces:
  - `GoalTurnContext(messages, auto, tool_calls, termination, tokens_delta, subagent_tokens_delta, elapsed_s, user_queued)`
  - `GoalDecision(action, status, prompt=None, reason="", wrap_up=False)`
  - `GoalController.note_turn_start(*, auto)`, `note_budget_abort()`, `budget_exceeded()`, `after_turn(ctx)`.
  - `render_goal_prompt(state, *, wrap_up=False) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_after_turn.py
import pytest

from zenagent.agent.session import Session
from zenagent.app.goal import (
    GoalController,
    GoalLimits,
    GoalTurnContext,
    GoalVerdict,
)


class FakeVerifier:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)

    async def verify(self, goal, ctx):
        if not self.verdicts:
            return GoalVerdict("not_met", "still failing")
        return self.verdicts.pop(0)


def _ctl(tmp_path, verifier, *, check=None, **limits):
    session = Session(tmp_path / "s.jsonl")
    session.materialize()
    ctl = GoalController(
        limits=GoalLimits(**limits),
        verifier=verifier,
        check_verifier=verifier if check else None,
        now=lambda: 100.0,
    )
    ctl.bind_session(session)
    ctl.set("fix tests", check=check)
    return ctl


def _ctx(**overrides):
    data = dict(
        messages=[],
        auto=True,
        tool_calls=1,
        termination="stop",
        tokens_delta=10,
        subagent_tokens_delta=0,
        elapsed_s=1.0,
        user_queued=False,
    )
    data.update(overrides)
    return GoalTurnContext(**data)


async def test_met_completes(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("met", "green")))
    decision = await ctl.after_turn(_ctx())
    assert decision.action == "stop"
    assert decision.status == "complete"


async def test_not_met_continues_with_budget_and_reason(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "3 tests failed")))
    decision = await ctl.after_turn(_ctx())
    assert decision.action == "continue"
    assert "fix tests" in decision.prompt
    assert "3 tests failed" in decision.prompt


async def test_turn_budget_wraps_up_once(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")), max_turns=1)
    first = await ctl.after_turn(_ctx())
    assert first.action == "continue" and first.wrap_up is True
    second = await ctl.after_turn(_ctx())
    assert second.action == "stop" and second.status == "budget_limited"


async def test_token_budget_hard_stops_without_wrap_up(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "nope")),
        max_tokens=5,
    )
    decision = await ctl.after_turn(_ctx(tokens_delta=6))
    assert decision.action == "stop"
    assert decision.status == "budget_limited"
    assert decision.wrap_up is False


async def test_three_idle_turns_block(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    for _ in range(2):
        assert (await ctl.after_turn(_ctx(tool_calls=0))).status == "active"
    decision = await ctl.after_turn(_ctx(tool_calls=0))
    assert decision.status == "blocked"


async def test_user_turn_resets_no_progress(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    await ctl.after_turn(_ctx(tool_calls=0))
    await ctl.after_turn(_ctx(tool_calls=0))
    await ctl.after_turn(_ctx(auto=False, tool_calls=0))
    decision = await ctl.after_turn(_ctx(tool_calls=0))
    assert decision.status == "active"


async def test_three_identical_check_failures_block(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "exit 127: no tool")),
        check="pytest -q",
    )
    await ctl.after_turn(_ctx())
    await ctl.after_turn(_ctx())
    decision = await ctl.after_turn(_ctx())
    assert decision.status == "blocked"


async def test_budget_counts_subagents(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "nope")),
        max_tokens=5,
    )
    decision = await ctl.after_turn(_ctx(tokens_delta=1, subagent_tokens_delta=6))
    assert decision.status == "budget_limited"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_after_turn.py -q`
Expected: FAIL because `after_turn`/`GoalTurnContext` are missing.

- [ ] **Step 3: Implement `after_turn` and the prompt**

In `zenagent/app/goal.py`:

- `note_turn_start(auto)` records `_turn_auto`, `_turn_started_at = now()`.
- `budget_exceeded(tokens_delta, elapsed_s)` returns True when `tokens_used + tokens_delta >= max_tokens` or `wall_seconds_used + elapsed_s >= max_wall_seconds`; turn budget is checked separately in `after_turn`.
- `after_turn(ctx)`:
  - return `stop` when no goal or not active.
  - choose `check_verifier` when `state.check` is set, else `verifier`; if the chosen one
    is `None`, return `blocked` with `last_reason="no verifier available"`.
  - add `ctx.tokens_delta + ctx.subagent_tokens_delta` to `tokens_used`, add `ctx.elapsed_s` to `wall_seconds_used`; increment `turns_used` for clean turns.
  - map non-clean `termination`: `aborted` → paused unless `_budget_abort` → budget_limited; `error`/`max_turns`/`max_cost` → blocked.
  - token/wall exceeded → budget_limited, `wrap_up=False`, stop.
  - `turns_used >= max_turns` and no wrap-up yet → `continue` with `wrap_up=True`; next call stops as budget_limited.
  - `auto` turns with `tool_calls == 0` increment `no_progress_turns`; any user turn resets to 0. Three → blocked.
  - call the verifier; `GoalVerifierError` increments `verifier_failures`; three → blocked. `met` → complete. `impossible` → blocked. `not_met` → update `last_verdict/last_reason`; if `check` is set, compute a signature from exit code + last non-empty output line (the verifier puts it in `evidence`), increment `check_failures` when the signature repeats, otherwise reset to 1; three → blocked.
  - otherwise return `continue` with `render_goal_prompt(state)`.
- `render_goal_prompt` includes `<objective>`, budget counters, `last_verdict`, `last_reason`, the "take the next concrete step / controller runs the check" instructions, and a wrap-up variant that forbids new work.
- Persist on every state change.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_after_turn.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/goal.py tests/test_goal_after_turn.py
git commit -m "feat(goal): add continuation decisions and budgets"
```

---

### Task 7: Carry the goal provenance on user messages

**Files:**
- Modify: `zenagent/types.py`
- Modify: `zenagent/agent/agent.py`
- Modify: `zenagent/agent/loop.py`
- Test: `tests/test_agent_goal_message.py`

**Interfaces:**
- Consumes: `UserMessage`.
- Produces: `UserMessage.goal: bool = False`; `Agent.prompt(text, images=None, *, goal=False)`; `agent_loop(..., user_goal=False)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_goal_message.py
from zenagent.types import UserMessage, message_from_json


def test_goal_flag_roundtrip():
    message = UserMessage(content="continue", timestamp=1, goal=True)
    payload = message.to_json()
    assert payload["goal"] is True
    assert message_from_json(payload).goal is True


async def test_agent_prompt_marks_the_user_message(runtime_factory):
    runtime = await runtime_factory()
    await runtime.agent.prompt("continue", goal=True)
    assert runtime.agent.messages[0].goal is True
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_agent_goal_message.py -q`
Expected: FAIL with unexpected keyword `goal`.

- [ ] **Step 3: Plumb the flag**

- Add `goal: bool = False` to `UserMessage`; include `"goal": True` in `to_json` when set.
- `message_from_json` reads `goal=d.get("goal") is True`.
- `Agent.prompt(..., *, goal: bool = False)` passes it to `_drive`.
- `Agent._drive(prompt, *, goal: bool = False)` passes `user_goal=goal` to `agent_loop`.
- `agent_loop(..., user_goal: bool = False)` constructs `UserMessage(..., goal=user_goal)`.
- `Agent.continue_()` and all existing callers keep the default `False`.
- The loop must not branch on `goal`; it is provenance for the transcript/UI only.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_agent_goal_message.py tests/test_agent.py tests/test_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/types.py zenagent/agent/agent.py zenagent/agent/loop.py tests/test_agent_goal_message.py
git commit -m "feat(goal): mark goal-origin user messages"
```

---

### Task 8: ChatController continuation loop and budget watchdog

**Files:**
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_goal_chat.py`

**Interfaces:**
- Consumes: `Runtime.goal`, `GoalTurnContext`, `GoalDecision`, `goal_tokens`.
- Produces:
  - `ChatController.start_goal(objective, *, check=None, max_turns=None) -> str`.
  - `ChatController.on_frontend_ready() -> None`.
  - `ChatController.maybe_continue_goal() -> None`.
  - `_run(text, images=None, *, goal=False)`.
  - `ChatController._goal_turn: bool`, `_turn_tool_calls: int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_chat.py
import asyncio

from zenagent.app.chat import ChatController
from zenagent.app.goal import GoalController, GoalLimits, GoalVerdict


class FakeVerifier:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)

    async def verify(self, goal, ctx):
        return self.verdicts.pop(0) if self.verdicts else GoalVerdict("not_met", "again")


def _bind_goal(runtime, verifier, **limits):
    runtime.goal = GoalController(limits=GoalLimits(**limits), verifier=verifier)
    runtime.goal.bind_session(runtime.session)
    return runtime.goal


async def test_goal_continues_until_met(runtime_factory):
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime,
        FakeVerifier(GoalVerdict("not_met", "step 1"), GoalVerdict("met", "done")),
        max_turns=3,
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await chat.wait_for_idle()

    assert len(calls) == 2
    assert runtime.agent.messages[0].goal is True
    assert runtime.goal.snapshot().status == "complete"


async def test_user_message_wins_over_goal_continuation(runtime_factory):
    gate = asyncio.Event()
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if len(calls) == 1:
            await gate.wait()
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime, FakeVerifier(GoalVerdict("not_met", "more")), max_turns=5
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await asyncio.sleep(0.02)
    await chat.submit("user says hello")
    gate.set()
    await chat.wait_for_idle()

    assert "user says hello" in calls[1]
    assert "fix tests" in calls[2]


async def test_budget_watchdog_aborts(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        partial = AssistantMessage(
            content=[TextContent(text="burn")],
            provider="p", model="m",
            usage=Usage(input=100, output=100),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime, FakeVerifier(GoalVerdict("not_met", "more")), max_tokens=10
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await chat.wait_for_idle()

    assert runtime.goal.snapshot().status == "budget_limited"


async def test_start_goal_while_busy_defers(runtime_factory):
    gate = asyncio.Event()

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        partial = AssistantMessage(
            content=[TextContent(text="busy")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(runtime, FakeVerifier(GoalVerdict("met", "done")))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.02)

    message = chat.start_goal("later")
    assert "will start" in message
    assert runtime.goal.snapshot().status == "active"

    gate.set()
    await task
    await chat.wait_for_idle()
    assert runtime.goal.snapshot().status == "complete"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_chat.py -q`
Expected: FAIL because `start_goal`/goal continuation do not exist.

- [ ] **Step 3: Implement the integration**

In `zenagent/app/chat.py`:

- `start_goal()` calls `runtime.goal.set(...)`; if `self.busy`, return
  `t("cmdmsg.goal_start_deferred")`; otherwise create
  `_run(render_goal_prompt(state), goal=True)` and return `t("cmdmsg.goal_started")`.
- `maybe_continue_goal()`: if not busy, no pending queue, goal active and not
  `_goal_checking`, create a goal turn. This is called after `_run` and by
  `on_frontend_ready()`.
- `on_frontend_ready()` calls `maybe_continue_goal()` once per process/session.
- `_run(..., goal=False)` sets `_goal_turn`, resets `_turn_tool_calls`, and in `finally`:
  - current behavior stays for user queue/steering/compaction.
  - before draining the queue, if `termination == "stop"` and no pending queue and goal
    active: `decision = await runtime.goal.after_turn(ctx)`; if `continue`, start
    `_run(decision.prompt, goal=True)`; else go idle.
  - if the queue has a user message, drain it first; the next user turn's `finally`
    evaluates the goal.
- `_on_event` increments `_turn_tool_calls` on `tool_execution_end`; on `message_end`
  call `_check_goal_budget()`.
- `_check_goal_budget()` computes `tokens_delta = goal_tokens(usage_total(messages), model.api)
  - goal_tokens(usage_at_turn_start, model.api)` plus subagent delta; calls
  `runtime.goal.budget_exceeded(...)`; if true, `runtime.goal.note_budget_abort()` and
  `agent.abort()`.
- Start a 5s watchdog task for the duration of `_run` when a goal is active; cancel it in
  `finally`. The task only calls `_check_goal_budget()`.
- `ChatController.status` sets `goal=runtime.goal.snapshot()`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_chat.py tests/test_app_chat.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/chat.py tests/test_goal_chat.py
git commit -m "feat(goal): auto-continue in ChatController with budget watchdog"
```

---

### Task 9: Runtime binding, bootstrap verifier and session switch preference

**Files:**
- Modify: `zenagent/app/runtime.py`
- Modify: `zenagent/app/bootstrap.py`
- Modify: `zenagent/app/chat.py` (callback)
- Test: `tests/test_goal_runtime.py`

**Interfaces:**
- Consumes: `GoalController`, `GoalLimits`, `ModelVerifier`, `settings.get_pref`.
- Produces:
  - `Runtime.goal: GoalController`
  - `Runtime.goal_resume: Callable[[], None] | None` (set by `ChatController`)
  - `Runtime.session` setter calls `goal.on_session_leave(old)` then `goal.bind_session(new)`.
  - `bootstrap.assemble` configures limits and verifier after the Agent exists.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_runtime.py
import pytest

from zenagent import settings
from zenagent.app.goal import GoalController, GoalLimits, GoalVerdict


@pytest.fixture(autouse=True)
def _reset_goal_pref(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    settings.set_pref("goal_pause_on_session_switch", None)
    yield
    settings.set_pref("goal_pause_on_session_switch", None)
    settings.clear_cache()


class FakeVerifier:
    async def verify(self, goal, ctx):
        return GoalVerdict("not_met", "again")


async def test_switch_keeps_goal_active_by_default(runtime_factory, tmp_path):
    settings.set_pref("goal_pause_on_session_switch", False)
    runtime = await runtime_factory()
    original = runtime.session.path
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    other = tmp_path / "other.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    assert runtime.goal.snapshot() is None

    await runtime.switch_session(original)
    assert runtime.goal.snapshot().status == "active"


async def test_switch_pause_pref_pauses(runtime_factory, tmp_path):
    settings.set_pref("goal_pause_on_session_switch", True)
    runtime = await runtime_factory()
    original = runtime.session.path
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    other = tmp_path / "other.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    assert runtime.goal.snapshot() is None
    # Switch back: the old session's goal was persisted as paused.
    await runtime.switch_session(original)
    assert runtime.goal.snapshot().status == "paused"


async def test_persisted_check_restores_paused(runtime_factory):
    runtime = await runtime_factory()
    runtime.goal = GoalController(
        limits=GoalLimits(), verifier=FakeVerifier()
    )
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix", check="echo ok")
    # Simulate a fresh controller reading the same file.
    fresh = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    fresh.bind_session(runtime.session)
    assert fresh.snapshot().status == "paused"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_runtime.py -q`
Expected: FAIL because Runtime has no goal binding.

- [ ] **Step 3: Implement the wiring**

- `Runtime.__init__` adds `self.goal = GoalController()` and
  `self.goal_resume: Callable[[], None] | None = None`.
- `Runtime.session` setter: before replacing `_session`, call
  `self.goal.on_session_leave(self._session)`; after assigning, call
  `self.goal.bind_session(session)` and, if `self.goal_resume` is set, schedule it with
  `asyncio.get_running_loop().call_soon(self.goal_resume)` (guard no-loop).
- `GoalController.on_session_leave(old_session)`:
  - if `settings.get_pref("goal_pause_on_session_switch", False)` is true and the old
    state is active: set paused, persist to the old session;
  - otherwise leave the old state active.
- `GoalController.bind_session(session)`: load the last goal record; if the loaded state
  is active **and has a `check`**, set it to paused with
  `last_reason="check needs confirmation; run /goal resume"` and persist. This is the
  restart/import safety rule.
- `bootstrap.assemble`, after `runtime.agent = agent`:
  `runtime.goal.configure(limits=..., verifier=..., check_verifier=...)`.
  Build `ModelVerifier(agent.stream_fn, agent.model, agent.api_key,
  runtime.limits.goal_verifier_max_tokens)` and
  `CheckVerifier(runtime.limits.goal_check_timeout_s, cwd=runtime.cwd)`.
  `CheckVerifier` is used only for goals that carry a `check`; `ModelVerifier` handles the
  rest.
- `ChatController.__init__` sets `runtime.goal_resume = self.maybe_continue_goal`.
- Add `chat.on_frontend_ready()` call sites: TUI `App.on_mount`, REPL after the chat is
  built, and RPC on the first `hello` (once). Headless `-p` does not auto-start a goal.
  `on_frontend_ready()` is idempotent and calls `maybe_continue_goal()` once.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_runtime.py tests/test_app_runtime.py tests/test_app_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/runtime.py zenagent/app/bootstrap.py zenagent/app/chat.py tests/test_goal_runtime.py
git commit -m "feat(goal): bind goals to sessions and wire the verifier"
```

---

### Task 10: `/goal` command, status snapshot and RPC

**Files:**
- Modify: `zenagent/app/views.py` (`GoalView`, `RunStatus.goal`, `MessageView.goal`)
- Modify: `zenagent/app/chat.py` (`status.goal`)
- Modify: `zenagent/app/commands.py` (`/goal`)
- Modify: `zenagent/cli/rpc/server.py` (flat `goal` key)
- Modify: `desktop/src/rpc/types.ts` (`GoalView`, `StatusResult.goal`)
- Test: `tests/test_goal_commands.py`, `tests/test_rpc_goal.py`

**Interfaces:**
- Consumes: `parse_goal_args`, `GoalController`, `ChatController.start_goal`.
- Produces: `RunStatus.goal: GoalView | None`, `/goal` command, `status.goal` over RPC.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_goal_commands.py
from zenagent.app.commands import build_default_commands


async def test_goal_set_status_pause_resume_clear(services, monkeypatch):
    # Command tests must not start a real goal run: stub the chat starters and
    # assert the controller state directly.
    monkeypatch.setattr(services.chat, "start_goal", lambda *a, **k: "goal started")
    monkeypatch.setattr(services.chat, "maybe_continue_goal", lambda: None)
    registry = build_default_commands(services)
    set_result = await registry.dispatch("goal", "fix flaky tests")
    assert set_result.message
    services.runtime.goal.set("fix flaky tests")

    status = await registry.dispatch("goal", "")
    assert "fix flaky tests" in status.message

    paused = await registry.dispatch("goal", "pause")
    assert "paused" in paused.message
    resumed = await registry.dispatch("goal", "resume")
    assert "active" in resumed.message
    cleared = await registry.dispatch("goal", "clear")
    assert "cleared" in cleared.message


async def test_goal_check_and_budget_flags(services, monkeypatch):
    captured = {}

    def fake_start(objective, *, check=None, max_turns=None):
        captured.update(objective=objective, check=check, max_turns=max_turns)
        return "goal started"

    monkeypatch.setattr(services.chat, "start_goal", fake_start)
    registry = build_default_commands(services)
    result = await registry.dispatch(
        "goal", '--check "pytest -q" --budget 3 fix tests'
    )
    assert result.message == "goal started"
    assert captured == {
        "objective": "fix tests",
        "check": "pytest -q",
        "max_turns": 3,
    }


async def test_goal_rejects_bad_arguments(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("goal", "--budget nope fix")
    assert "usage" in result.message.lower() or "invalid" in result.message.lower()


def test_user_view_carries_goal_flag():
    from zenagent.app.views import user_view

    assert user_view("continue", goal=True).goal is True
```

```python
# tests/test_rpc_goal.py
import asyncio
import json

from zenagent.cli.rpc import RpcServer


async def test_status_includes_goal(services):
    runtime = services.runtime
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    queue = [
        json.dumps({"type": "status", "id": "s"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ]
    writes: list[dict] = []

    async def read_line():
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    def write_line(line: str) -> None:
        writes.append(json.loads(line))

    server = RpcServer(
        runtime.agent,
        read_line,
        write_line,
        runtime=runtime,
        chat=services.chat,
    )
    await server.run()
    status = next(m for m in writes if m.get("id") == "s")
    assert status["goal"]["objective"] == "fix"
    assert status["status"]["goal"]["objective"] == "fix"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_goal_commands.py tests/test_rpc_goal.py -q`
Expected: FAIL because `/goal` is not registered.

- [ ] **Step 3: Implement the command and status**

- `views.py`: add `MessageView.goal: bool = False`; `user_view(..., goal=False)`;
  `TranscriptBuilder` passes `message.goal`. (`GoalView`/`RunStatus.goal` are from Task 4.)
- `ChatController.status` already sets `goal=self.runtime.goal.snapshot()` from Task 8.
- `commands.py`: `@_command("goal")`; use `parse_goal_args`; status prints objective,
  status, check, turns/tokens/wall, last verdict/reason; set calls
  `services.chat.start_goal`; pause/resume/clear call the controller.
- `server._status_result`: add `payload["goal"] = asdict(status.goal) if status.goal else None`
  and keep the full `status.to_wire()` snapshot.
- `desktop/src/rpc/types.ts`: mirror `GoalView` and `StatusResult.goal?: GoalView | null`.
- i18n keys in Task 13 or here: `cmd.goal.description`, `cmdmsg.goal_*`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_goal_commands.py tests/test_rpc_goal.py tests/test_app_views.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/views.py zenagent/app/chat.py zenagent/app/commands.py zenagent/cli/rpc/server.py desktop/src/rpc/types.ts tests/test_goal_commands.py tests/test_rpc_goal.py
git commit -m "feat(goal): expose /goal and status to every frontend"
```

---

### Task 11: TUI goal HUD, sidebar and setting

**Files:**
- Create: `zenagent/cli/tui/widgets/goal_hud.py`
- Modify: `zenagent/cli/tui/app.py`
- Modify: `zenagent/cli/tui/widgets/sidebar.py`
- Modify: `zenagent/cli/tui/screens/editors.py`
- Modify: `zenagent/cli/tui/screens/pickers.py`
- Modify: `zenagent/i18n/en.py`, `zenagent/i18n/zh.py`
- Test: `tests/test_tui_goal.py`

**Interfaces:**
- Consumes: `RunStatus.goal`.
- Produces: `GoalHud.set_goal(view)`, sidebar Goal section, settings toggle
  `goal_pause_on_session_switch`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui_goal.py
from zenagent.app.views import GoalView, RunStatus
from zenagent.cli.tui.widgets.goal_hud import GoalHud


def test_goal_hud_hides_without_goal():
    hud = GoalHud()
    hud.set_goal(None)
    assert hud.display is False


def test_goal_hud_shows_status_and_budget():
    hud = GoalHud()
    hud.set_goal(
        GoalView(
            objective="fix tests",
            status="active",
            check="pytest -q",
            turns_used=3,
            max_turns=40,
            tokens_used=100,
            max_tokens=2000,
            wall_seconds=10.0,
            max_wall_seconds=3600,
            last_verdict="not_met",
            last_reason="2 failed",
            updated_at=1,
        )
    )
    assert hud.display is True
    assert "fix tests" in hud.render_text()
```

Add a `SettingsPicker` test in `tests/test_tui_pickers.py` asserting the new row exists
when `goal_pause_on_session_switch` is false/true.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_tui_goal.py -q`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement the TUI surface**

- `GoalHud`: one-line `Static`; `set_goal(view)` sets `display` and text; icon/status
  mapping (`◎ active`, `⏸ paused`, `⊘ blocked`, `◼ budget`, `✓ complete`); truncate the
  objective; show `turns/max`, tokens, last verdict/reason.
- `app.py`: mount `GoalHud` above the status band; in `_sync_chrome`/status update call
  `hud.set_goal(status.goal)`.
- `sidebar.py`: add a Goal section with objective, status, check, budget and last reason;
  hide when `status.goal is None`.
- `editors.py`: add a `PaletteItem` "Goal: pause when leaving session" with value
  `goal-pause`, note `on/off` from `settings.get_pref("goal_pause_on_session_switch", False)`.
- `pickers.py`: handle `goal-pause` by toggling the pref, saving it, and notifying;
  refresh the settings picker.
- i18n: `tui.goal.*`, `tui.settings.goal_pause`, `tui.settings.goal_pause_on/off`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_tui_goal.py tests/test_tui_sidebar.py tests/test_tui_screens.py -q`
Expected: PASS (adjust the screens test path to the existing TUI test file).

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/tui/widgets/goal_hud.py zenagent/cli/tui/app.py zenagent/cli/tui/widgets/sidebar.py zenagent/cli/tui/screens/editors.py zenagent/cli/tui/screens/pickers.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_tui_goal.py
git commit -m "feat(goal): show goal status in the TUI"
```

---

### Task 12: Desktop goal chip/panel and conversation setting

**Files:**
- Create: `desktop/src/ui/chat/GoalChip.tsx`
- Create: `desktop/src/ui/chat/GoalPanel.tsx`
- Modify: `desktop/src/ui/composer/InputBar.tsx`
- Modify: `desktop/src/ui/pages/SettingsPage.tsx`
- Modify: `desktop/src/rpc/types.ts` (already in Task 10)
- Modify: `zenagent/cli/rpc/ops_config.py` (or a small settings mixin) — `set_pref`
- Modify: `desktop/src/i18n/messages-settings.ts`, `desktop/src/i18n/messages.ts`
- Test: `desktop/tests/goalView.test.ts`

**Interfaces:**
- Consumes: `StatusResult.goal`, RPC `command` (goal pause/resume/clear), RPC `set_pref`.
- Produces: `GoalChip`/`GoalPanel`, Conversation toggle for
  `goal_pause_on_session_switch`.

- [ ] **Step 1: Write the failing test**

```ts
// desktop/tests/goalView.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { goalSummary } from '../src/ui/chat/goalView'

test('goalSummary renders status and budget', () => {
  const text = goalSummary({
    objective: 'fix tests',
    status: 'active',
    check: 'pytest -q',
    turns_used: 3,
    max_turns: 40,
    tokens_used: 100,
    max_tokens: 2000,
    wall_seconds: 10,
    max_wall_seconds: 3600,
    last_verdict: 'not_met',
    last_reason: '2 failed',
    updated_at: 1,
  })
  assert.match(text, /3\\/40/)
  assert.match(text, /fix tests/)
})
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd desktop && npm test -- goalView`
Expected: FAIL because `goalView` does not exist.

- [ ] **Step 3: Implement the desktop surface**

- `desktop/src/ui/chat/goalView.ts`: `goalSummary(view)` pure helper; status label map.
- `GoalChip`: status icon + objective + turns; click opens `GoalPanel`.
- `GoalPanel`: objective, check, budget, last verdict/reason; pause/resume/clear buttons.
  Buttons dispatch `command` frames with `{ name: 'goal', argument: 'pause' | 'resume' | 'clear' }`.
- `InputBar`: render `GoalChip` above the plate when `status.goal` is present; render
  `GoalPanel` as a popover/panel.
- `SettingsPage` Conversation tab: toggle for "Pause goal when leaving a session";
  reads `{"type":"prefs"}` → `{"prefs":{"goal_pause_on_session_switch": bool}}`;
  writes `{"type":"set_pref","name":"goal_pause_on_session_switch","value":bool}`.
- `ops_config.py`: handle both `prefs` and `set_pref`; validate `name` against a small
  allowlist containing `goal_pause_on_session_switch`; call `zenagent.settings.get_pref`
  / `set_pref`.
- i18n keys: `goal.chip.*`, `goal.panel.*`, `settings.goalPauseOnLeave`,
  `settings.goalPauseOnLeaveHint`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd desktop && npm test -- goalView && npm run build`
Expected: PASS + TypeScript build succeeds.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/ui/chat/GoalChip.tsx desktop/src/ui/chat/GoalPanel.tsx desktop/src/ui/chat/goalView.ts desktop/src/ui/composer/InputBar.tsx desktop/src/ui/pages/SettingsPage.tsx desktop/src/i18n/messages-settings.ts desktop/src/i18n/messages.ts desktop/tests/goalView.test.ts zenagent/cli/rpc/ops_config.py
git commit -m "feat(goal): add desktop goal chip, panel and preference"
```

---

### Task 13: Docs, i18n completeness and full gate

**Files:**
- Modify: `docs/guide/commands.md`
- Modify: `docs/guide/tui.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/feature-map.md` (if the repo tracks command rows)
- Test: `tests/test_docs_commands.py`, full `scripts/check.sh`

- [ ] **Step 1: Add docs and changelog**

- Document `/goal`, `--check`, `--budget`, pause/resume/clear, budgets, the
  `goal_pause_on_session_switch` setting, and the "app must stay open" boundary.
- Add a `CHANGELOG.md` entry under the current unreleased version.
- Add the goal row to `docs/feature-map.md` if that file lists app commands.

- [ ] **Step 2: Run the docs/command i18n tests**

Run: `uv run pytest tests/test_docs_commands.py tests/test_i18n_app.py tests/test_i18n_tui.py -q`
Expected: PASS; every `t("cmd.goal...")` key exists in both locales.

- [ ] **Step 3: Run the full gate**

Run: `bash scripts/check.sh`
Expected: sync, ruff, mypy, architecture budget, and pytest all pass.

- [ ] **Step 4: Run the desktop gate**

Run: `cd desktop && npm run build && npm test`
Expected: TypeScript build and node tests pass.

- [ ] **Step 5: Commit**

```bash
git add docs/guide/commands.md docs/guide/tui.md docs/feature-map.md CHANGELOG.md
git commit -m "docs(goal): document the goal loop and its boundaries"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** `GoalState`, `GoalController`, `GoalVerifier`, `records.goal`,
  `Runtime.goal`, `ChatController.start_goal`, `/goal`, `RunStatus.goal`, TUI HUD,
  desktop chip/panel, `goal_pause_on_session_switch` all map to a task.
- [ ] **Step scan:** Every test step names the exact test/assertions; every implementation
  step names the exact file and signature; no "handle edge cases" placeholders.
- [ ] **Type consistency:** `GoalLimits`, `GoalView`, `GoalTurnContext`, `GoalDecision`,
  `GoalVerdict`, `GoalController` names/signatures match across Tasks 1–12.
- [ ] **Review Focus:** each of the five failure modes has a test in its owning task.
- [ ] **Proportion:** the plan does not paste whole implementation bodies; interfaces and
  tests carry the decisions.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-11-16-goal-feature.md`.
Please review the plan. Which execution approach would you prefer?

- **Subagent-driven** — a fresh subagent implements each task and a fresh reviewer checks
  it before the next one starts, then a whole-branch review at the end. Most thorough;
  costs a fresh context per task and per review.
- **Native** — I implement every task myself in this session, then one fresh reviewer on
  the most capable model checks the whole branch. Cheapest and fastest; no independent
  review until the end.

For this plan I recommend **subagent-driven**, because the tasks cross Python, TUI,
RPC and desktop boundaries and a fresh reviewer per boundary catches interface drift
early. Does the plan capture what you want, and which approach should we use?
