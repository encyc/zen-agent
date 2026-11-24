# Uncapped Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every turn and spend ceiling from the kernel and its four satellites, and turn the loop guards from advisory correctives into one interrupt that never touches the transcript.

**Architecture:** The kernel's exit surface collapses to: the model stops, the provider fails, a tool asks to terminate, the output-truncation defense fires, or a `RunawayDetector` proves the same tool batch repeated byte-for-byte. The detector returns a string instead of injecting a message, so `state.messages` is never modified by the harness and three components lose their knowledge of synthetic guard messages. Deleted knobs leave deprecated tombstone keys so published `config.toml` files keep starting.

**Tech Stack:** Python 3.11+, pytest (asyncio auto mode), uv; TypeScript/React desktop under `desktop/`; stdlib-only gates under `scripts/`.

**Spec:** `docs/superpowers/specs/2026-11-24-uncapped-agent-loop-design.md`

## Global Constraints

- No turn ceiling and no spend ceiling anywhere: kernel, CLI, task tool, goal, swarm, zen fleet.
- Watchdogs stay: tool `timeout_s`, subagent `timeout_s`. They stop a hang, not a long run.
- `no_stream` and `_MAX_LENGTH_FAILURES` stay as defenses.
- Poll-shaped calls (`wait`, and `bash` matching `^\s*(sleep|wait|tail|ps|pgrep|top|jobs)\b`) never count toward a runaway streak.
- Termination strings `max_turns` and `max_cost` stay readable by consumers (old sessions, old ledger rows) but are never produced.
- Deprecated config keys are accepted, ignored, and warned about once each; never an error.
- Run tests with `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest <path> -q` from the worktree root.
- One commit per task; message style `type(scope): lowercase imperative`.

## Review Focus

- A repeated poll (`bash ps`, `wait`) must never be killed by the detector — the exemption is the only thing standing between a legitimate watcher and an interrupted run.
- A model re-reading the same file through differently-flagged calls now has no ceiling and no nudge; it must simply keep running (documented, accepted regression), not crash or trip the detector.
- An old `config.toml` carrying all four removed keys must start, warn once per key, and the limits RPC must stop listing them.
- An old session file with `termination="max_turns"` must still render as before: ledger `failed`, subagent `truncated`.
- A `loop_detected` run must read `interrupted` / `truncated` / `paused` in the three consumers — never `done` / `ok` / `blocked`.

---

### Task 1: `RunawayDetector` replaces both guards

**Files:**
- Modify: `zenagent/agent/guard.py` (rewrite: delete evidence machinery, add detector)
- Modify: `zenagent/agent/loop.py` (guard block, imports, `finally`)
- Modify: `zenagent/app/views.py:11,428` (drop prefix imports and the filter branch)
- Test: `tests/test_evidence_guard.py` (delete), `tests/test_loop.py`

**Interfaces:**
- Produces: `RunawayDetector(threshold: int = LOOP_GUARD_THRESHOLD)` with `observe(calls: list[ToolCall]) -> str | None`; module keepers `calls_signature(calls) -> tuple`, `is_poll_call(call) -> bool`, `POLL_COMMAND`, `LOOP_GUARD_THRESHOLD = 5`.
- Produces: `agent_loop` sets `termination="loop_detected"` and carries `detail` to `agent_end` (field added in Task 3; until then store it in a local and pass nothing).

- [ ] **Step 1: Write the failing test**

Replace `test_repeated_identical_tool_calls_get_one_corrective_notice` in `tests/test_loop.py` with:

```python
async def test_a_repeated_tool_batch_interrupts_the_run():
    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="again")])

    script = [[call_tool(f"t{i}", "echo", {"x": 1}), finish("tool_use")] for i in range(5)]
    script.append([say("done"), finish("stop")])
    stream = ScriptedStream(script)
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]))

    assert state.termination == "loop_detected"
    assert stream.calls == 5  # fired after the fifth batch, no sixth request
    end = next(event for event in events if event.type == "agent_end")
    assert end.detail and "echo" in end.detail
    # the harness wrote nothing into the transcript
    assert not any(
        "guard" in str(getattr(message, "content", "")) for message in state.messages
    )


async def test_a_repeated_poll_is_not_a_runaway():
    async def poll(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ok")])

    script = [[call_tool(f"p{i}", "bash", {"command": "ps aux"}), finish("tool_use")] for i in range(20)]
    script.append([say("done"), finish("stop")])
    stream = ScriptedStream(script)
    state = AgentState()
    await run("go", state, make_config(stream, tools=[make_tool("bash", poll)]))

    assert state.termination == "stop"
    assert stream.calls == 21
```

- [ ] **Step 2: Run to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_loop.py -q -k "repeated_tool_batch or repeated_poll"`
Expected: FAIL — the run ends `"stop"` after the sixth script, and a synthetic `[tool-loop guard]` user message is in `state.messages`.

- [ ] **Step 3: Implement `RunawayDetector` in `zenagent/agent/guard.py`**

Keep `POLL_COMMAND`, `is_poll_call`, `calls_signature`. Delete `is_loop_guard_message`, `is_stall_guard_message`, `LOOP_GUARD_*`, `STALL_GUARD_*`, `Probe`, `probe_for`, `_range_probe`, `_shell_probe`, `_norm_path`, `_grep_root`, `EvidenceLedger`, `_ASSUMED_READ_LINES`, `_EOF`, `_MUTATING_TOOLS`, and the six probe regexes. The detector mirrors the streak logic the loop used to run inline: a poll-only batch resets the streak to 0; a batch equal to the previous signature increments it; anything else sets the signature and streak to 1. `observe` returns `None` below threshold, else `f"{streak}× {name}({args_json})"` naming the first call of the batch.

- [ ] **Step 4: Wire it into `agent_loop` and delete the corrective injection**

`AgentConfig` loses `stall_guard_batches`; `agent_loop` loses `repeat_streak`/`last_signature`/`guard_injected`/`evidence`/`stall_*`, and the two injection blocks (`loop.py:438-485`). After the tool-result loop, call `detail = detector.observe(calls)`; on a hit set `termination = "loop_detected"` and `break`. The `finally` block keeps only `state.termination = termination` and the `agent_end` yield — the message-filtering list comprehension goes.

- [ ] **Step 5: Delete the stall-guard filter in `zenagent/app/views.py`**

Drop `LOOP_GUARD_PREFIX, STALL_GUARD_PREFIX` from the import and the `text.startswith(...)` branch at `views.py:428`.

- [ ] **Step 6: Delete `tests/test_evidence_guard.py`**

It exercises `EvidenceLedger` directly; the class is gone, so the file goes with it.

- [ ] **Step 7: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_loop.py tests/test_agent_class.py tests/test_views.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat(agent): interrupt a runaway run instead of nudging it"
```

---

### Task 2: Delete the kernel's turn and spend ceilings

**Files:**
- Modify: `zenagent/agent/loop.py` (`_MAX_TURNS`, `AgentConfig.max_turns`, `AgentConfig.max_cost`, `_run_cost`, `AgentState.subagent_spend`, two exit branches, the truncation exit)
- Modify: `zenagent/agent/agent.py:94,112,285` (drop the two kwargs)
- Modify: `zenagent/tools/wait.py:156` (stale `max_cost guard` comment)
- Test: `tests/test_loop.py`, `tests/test_agent_class.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond an unchanged `agent_loop` signature.
- Produces: `AgentConfig` without `max_turns`/`max_cost`; `AgentState` without `subagent_spend`; no `"max_turns"`/`"max_cost"` termination produced anywhere in the kernel.

- [ ] **Step 1: Write the failing test**

Replace `test_max_turns_stops_before_the_next_request`,
`test_max_cost_stops_after_the_turn_that_crosses_it` and
`test_default_guardrails_do_not_change_normal_runs` with:

```python
async def test_a_long_run_is_not_cut_at_one_hundred_turns():
    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ok")])

    script = [[call_tool(f"c{i}", "echo", {}), finish("tool_use")] for i in range(120)]
    script.append([say("done"), finish("stop")])
    stream = ScriptedStream(script)
    state = AgentState()
    await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]))

    assert state.termination == "stop"
    assert stream.calls == 121


async def test_no_spend_ceiling_exists():
    assert "max_cost" not in AgentConfig.__dataclass_fields__
    assert "max_turns" not in AgentConfig.__dataclass_fields__
```

`LoopingToolStream` (the always-asks-for-another-call stream) loses its last two users; delete it with them, or it becomes an infinite loop waiting to be written.

- [ ] **Step 2: Run to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_loop.py -q -k "long_run or spend_ceiling"`
Expected: FAIL — the first run ends `"max_turns"` at call 100; the second fails because both fields still exist.

- [ ] **Step 3: Delete the policy**

Remove `_MAX_TURNS`, both config fields, `_run_cost`, `state.subagent_spend` (and its `+=` inside the tool-result loop, keeping `details["subagent_cost"]` untouched), the `turns >= turn_limit` branch and the `run_spend > max_cost` branch. Keep `turns` — it is reporting only. `Agent.__init__` and `_config` stop forwarding both kwargs.

- [ ] **Step 4: Update the tests that asserted a ceiling**

In `tests/test_loop.py` and `tests/test_agent_class.py`, every case named for `max_turns`/`max_cost` becomes a "no ceiling" case: the run reaches its natural stop. Delete `LoopingToolStream` if nothing else uses it.

- [ ] **Step 5: Report the truncation stop honestly**

`loop.py:435` breaks on `length_failures >= _MAX_LENGTH_FAILURES` without setting `termination`, so the run lands on `"stop"` and every consumer reads it as a success. Set `termination = "length"` before the break, and extend the existing `test_repeated_truncation_stops_instead_of_reasking_forever` (`tests/test_loop.py:885`) to assert `state.termination == "length"`.

- [ ] **Step 6: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_loop.py tests/test_agent_class.py tests/test_events.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(agent)!: remove the turn and spend ceilings from the kernel"
```

---

### Task 3: Carry the reason, and classify the new termination

**Files:**
- Modify: `zenagent/events.py:70-107` (`AgentEvent.detail`)
- Modify: `zenagent/agent/loop.py` (`agent_end` yield)
- Modify: `zenagent/app/runs.py:104` (`status_for_termination`)
- Modify: `zenagent/agent/subagents_runtime.py:190` (`_status_for`)
- Modify: `zenagent/app/goal.py:491` (`loop_detected` → paused)
- Modify: `zenagent/cli/rpc/protocol/_chat.py` (event field), regenerate `desktop/src/rpc/wire.gen.ts`
- Test: `tests/test_events.py`, `tests/test_runs.py`, `tests/test_goal.py`, `tests/test_rpc_contract.py`

**Interfaces:**
- Produces: `AgentEvent.detail: str | None = None`, set only on `agent_end`.
- Produces: `status_for_termination("loop_detected") == "interrupted"`; `_status_for(child)` returns `"truncated"`; goal finishes `paused`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_detector_detail_reaches_agent_end(): ...
def test_a_loop_kill_is_interrupted_in_the_ledger():
    assert status_for_termination("loop_detected") == "interrupted"

def test_old_budget_terminations_still_map():
    assert status_for_termination("max_turns") == "failed"
    assert status_for_termination("max_cost") == "failed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_runs.py tests/test_events.py -q -k "loop_detected or detail"`
Expected: FAIL.

- [ ] **Step 3: Add the field and the three mappings**

`AgentEvent` gains a documented `detail` field; `agent_loop` passes the detector's string. `status_for_termination` gets an explicit `loop_detected → "interrupted"` branch **before** the `_DONE_TERMINATIONS` check (the fallback returns `done`, so ordering matters). `_status_for` includes `"loop_detected"` in the truncated tuple while keeping the two historical names. `goal.py` maps `loop_detected` to `_finish("paused", …)` before the generic non-`stop` → `blocked` branch.

- [ ] **Step 4: Publish the field on the wire**

Add the optional `detail` field to the `agent_end` event declaration in `zenagent/cli/rpc/protocol/_chat.py`, then run `node scripts/gen_rpc_ts.py` (or the Python entry it wraps) and confirm `desktop/src/rpc/wire.gen.ts` gained the field.

- [ ] **Step 5: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_rpc_contract.py tests/test_events.py tests/test_runs.py tests/test_goal.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(agent): report why a run was interrupted"
```

---

### Task 4: Deprecated-key mechanism

**Files:**
- Modify: `zenagent/limits.py` (`Limit.deprecated`, `parse_limits`, `write_managed`)
- Modify: `zenagent/config.py` (`_TASK_KEYS` path)
- Test: `tests/test_limits.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Limit.deprecated: bool = False`; a deprecated knob counts as known (no `UnknownConfigField`), is skipped by `parse_limits`, is skipped by `write_managed`, and emits one `_log.warning` per key per process.
- Produces: `DeprecatedKeyWarning` helper name is the implementer's choice; behavior is what is pinned.

- [ ] **Step 1: Write the failing test**

```python
def test_a_deprecated_key_is_accepted_ignored_and_warned(tmp_path, caplog):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[limits.agent]\nmax_turns = 300\n")
    limits = parse_limits(cfg.read_text(), target=str(cfg))
    assert limits.max_turns == DEPRECATED_SENTINEL_DEFAULT   # not 300
    assert "max_turns" in caplog.text
    parse_limits(cfg.read_text(), target=str(cfg))
    assert caplog.text.count("max_turns") == 1               # once per process
```

- [ ] **Step 2: Run to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_limits.py -q -k deprecated`
Expected: FAIL with `UnknownConfigField`.

- [ ] **Step 3: Implement**

Add the flag; in `parse_limits`, keep deprecated names inside `known` (so the unknown-key error does not fire) and `continue` before coercion; in `write_managed`, skip deprecated rows. In `config.py`, the `[task]` validator treats `max_turns`/`max_cost` the same way — accepted, ignored, warned once.

- [ ] **Step 4: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_limits.py tests/test_config.py tests/test_limits_rpc.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(config): retire removed knobs as deprecated keys"
```

---

### Task 5: Goal loses every ceiling and the wrap-up

**Files:**
- Modify: `zenagent/app/goal.py` (`GoalLimits`, `GoalState`, `GoalArgs`, `parse_goal_args`, the four budget branches, `render_goal_prompt`, `_budget_text`, the goal view)
- Modify: `zenagent/app/goal_chat.py:33-38`
- Test: `tests/test_goal.py`, `tests/test_goal_chat.py`, `tests/test_limits_goal.py`

**Interfaces:**
- Produces: `GoalLimits` with `check_timeout_s` and `verifier_max_tokens` only; `GoalState` keeps `turns_used`/`tokens_used`/`wall_seconds_used` for display and loses `max_turns`/`max_tokens`/`max_wall_seconds`.
- Produces: `parse_goal_args` rejects `--budget` with a clear message; `GoalDecision` loses `wrap_up`; `render_goal_prompt(state)` has no `wrap_up` parameter.
- Consumes: Task 3's `loop_detected → paused` mapping.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_goal_is_not_stopped_by_a_turn_or_token_ceiling(): ...
def test_no_wrap_up_decision_exists(): ...
def test_budget_flag_is_gone_from_goal_parsing():
    with pytest.raises(ValueError, match="--budget"):
        parse_goal_args("--budget 10 do the thing")
def test_the_view_no_longer_reports_ceilings(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_goal.py -q -k "ceiling or wrap_up or budget_flag"`
Expected: FAIL.

- [ ] **Step 3: Delete**

Remove the field trio from limits/state/args, the four branches at `goal.py:501-527`, `GoalDecision.wrap_up`, the `render_goal_prompt(wrap_up=...)` branch, and the `--budget` flag parser. Keep `no_progress_turns`, `check_failures`, `verifier_failures` — they drive the verifier, not a ceiling. The goal now stops only on a verdict, a blocker, the user, or a `loop_detected` pause.

- [ ] **Step 4: Mark the limit rows deprecated**

In `zenagent/limits.py`, delete `[limits.goal] max_turns`, `max_tokens`, `max_wall_minutes` rows and add them back flagged `deprecated=True` with `default=None`-equivalent semantics the loader ignores.

- [ ] **Step 5: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_goal.py tests/test_goal_chat.py tests/test_limits_goal.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(goal)!: stop only on a verdict, a blocker or the user"
```

---

### Task 6: Task tool and subagents lose their quotas

**Files:**
- Modify: `zenagent/tools/task.py` (`DEFAULT_TASK_MAX_TURNS`, `DEFAULT_TASK_MAX_COST`, the two params, two `construct_child` calls, the child prompt render)
- Modify: `zenagent/config.py` (`DEFAULT_TASK_*`, `task_max_turns`, `task_max_cost`, `_TASK_KEYS`, load/save/merge)
- Modify: `zenagent/agent/subagents_runtime.py:632-647` (`construct_child` signature)
- Modify: `zenagent/prompts/blocks/task_child_system.md` (drop the turn line)
- Test: `tests/test_task.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `construct_child(..., timeout_s=None?)` — no `max_turns`/`max_cost`; `build_task_tool` loses both parameters; the child prompt renders without `{{max_turns}}`.
- Consumes: Task 4's tombstone mechanism for `[task] max_turns`/`max_cost`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_subagent_is_not_truncated_by_a_turn_quota(): ...
def test_the_child_prompt_has_no_turn_line(): ...
def test_task_limits_are_gone_from_the_public_tool_schema(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_task.py -q -k "quota or turn_line"`
Expected: FAIL.

- [ ] **Step 3: Delete and rewire**

Drop both defaults and both parameters end to end; `timeout_s` stays and remains the only watchdog. `_status_for` keeps `"max_turns"`/`"max_cost"` in its historical tuple. The truncation reason `max_turns` disappears from `_envelope`.

- [ ] **Step 4: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_task.py tests/test_config.py tests/test_subagents.py -q`
Expected: PASS.

- [ ] **Step 5: Mark the limit row deprecated**

In `zenagent/limits.py`, the `[task] max_cost` row (section `subagents`, `limits.py:312-324`) stays but gains `deprecated=True`; `[task] max_turns` gets the same treatment. Both keep their name so old configs parse, and neither is written by `write_managed`.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(task)!: subagents have no turn or spend quota"
```

---

### Task 7: Swarm roles and the zen fleet

**Files:**
- Modify: `zenagent/app/swarm/roles.py:82-83,281-282`
- Modify: `zenagent/app/swarm/roster.py:152-153,173-174`
- Modify: `zenagent/app/swarm/manager.py:693-694`
- Modify: `zenagent/app/zen/fleet.py:136,168,417`
- Modify: `zenagent/app/runtime.py:123-124`
- Test: `tests/test_swarm_runner.py`, `tests/test_zen_fleet.py`, `tests/test_swarm_roles.py`

**Interfaces:**
- Produces: `Role` without `max_turns`/`max_cost`; manifests keep parsing the old keys and ignore them (a roster written before this change must still load).

- [ ] **Step 1: Write the failing test**

```python
def test_a_role_manifest_with_legacy_budgets_still_loads(tmp_path):
    write_manifest(tmp_path, {"limits": {"maxTurns": 12, "maxCost": 1.5}, "role": "worker"})
    role = load_role(tmp_path)
    assert not hasattr(role, "max_turns")
```

- [ ] **Step 2: Run to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_swarm_roles.py -q -k legacy`
Expected: FAIL.

- [ ] **Step 3: Delete the fields and their passthrough**

Remove the dataclass fields, the manifest parse, the roster serialization, and the `RuntimeOptions`/fleet arguments. Parsing tolerates the keys by ignoring them (do not add them to the serialized shape).

- [ ] **Step 4: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_swarm_runner.py tests/test_zen_fleet.py tests/test_swarm_roles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(swarm)!: roles carry no turn or spend limits"
```

---

### Task 8: CLI and limit application

**Files:**
- Modify: `zenagent/cli/flags.py:31,34,118` (both flags, both argparse entries)
- Modify: `zenagent/app/limits_apply.py:36,60-66`
- Modify: `zenagent/app/bootstrap.py:237-243,270-275`
- Test: `tests/test_cli_flags.py`, `tests/test_cli.py`, `tests/test_limits_apply.py`

**Interfaces:**
- Consumes: Task 2 (kernel kwargs gone), Task 4 (tombstones).
- Produces: `--max-turns` and `--max-cost` are unknown flags; `limits_apply` no longer prints either override.

- [ ] **Step 1: Write the failing test**

```python
def test_removed_flags_are_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--max-turns", "20"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_cli_flags.py -q -k removed`
Expected: FAIL (flag still accepted).

- [ ] **Step 3: Delete**

Remove both flag strings, both argparse arguments, both `options.*` reads in `limits_apply`/`bootstrap`, and the `task_max_*` wiring.

- [ ] **Step 4: Run**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_cli_flags.py tests/test_cli.py tests/test_limits_apply.py tests/test_limits_rpc.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cli)!: drop --max-turns and --max-cost"
```

---

### Task 9: Desktop

**Files:**
- Modify: `desktop/src/ui/chat/goalView.ts:26-28`, `desktop/src/ui/chat/GoalPanel.tsx:33-40`
- Modify: `desktop/src/ui/pages/agentEditor.tsx:65-66,78-79,103-104,343,355`
- Modify: `desktop/src/rpc/types-swarm.ts:25`, `desktop/src/dev/swarmMock.ts:56,75,296`
- Modify: `desktop/src/dev/bridge.ts:432,436,438,467,476`
- Modify: `desktop/src/i18n/messages-settings.ts:141,145,150,152` and the English block
- Modify: `desktop/src/state/chatStore.ts` (unknown-termination fallback), `desktop/src/ui/chat/AuxRows.tsx:83`
- Test: `desktop/tests/*.test.ts` (node:test)

**Interfaces:**
- Consumes: Task 3's `detail` field and `loop_detected`; Task 5's view without ceilings.
- Produces: `goalSummary` renders `objective · N`; `loop_detected` renders a sentence plus the carried detail, never `recovered`.

- [ ] **Step 1: Write the failing test**

```ts
// desktop/tests/goalView.test.ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { goalSummary } from '../src/ui/chat/goalView.ts'
import type { GoalView } from '../src/rpc/types.ts'

test('summary counts turns without a ceiling', () => {
  const view = { objective: 'ship it', turns_used: 3 } as unknown as GoalView
  assert.equal(goalSummary(view), 'ship it · 3')
})
```

And in the store test file that already covers termination mapping, assert `statusForTermination('loop_detected')` is not `'recovered'`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd desktop && node --test tests/goalView.test.ts`
Expected: FAIL — `goalSummary` still renders `ship it · 3/undefined`.

- [ ] **Step 3: Implement**

Drop the denominators and the two editor inputs; delete the four i18n keys in both languages; delete the `goal.panel.budget` keys that referenced ceilings and keep a turns/tokens-only line; map `loop_detected` to a localized sentence rendering `detail`.

- [ ] **Step 4: Run**

Run: `cd desktop && node --test tests/*.test.ts && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(desktop): show turns without ceilings, explain a runaway stop"
```

---

### Task 10: Docs and the full gate

**Files:**
- Modify: `docs/architecture.md:105`, `docs/guide/config.md:140-152,176-187`, `docs/guide/rpc-sdk.md:143`, `docs/guide/sessions.md:49`, `docs/guide/commands.md:117`, `docs/guide/orchestration.md:196`
- Modify: `CHANGELOG.md` (new Unreleased entry)
- Test: whole suite

**Interfaces:** none — prose only.

- [ ] **Step 1: Rewrite the passages**

Termination lists lose `max_turns`/`max_cost` from the *produced* set and gain `loop_detected`; the config guide's two `max_turns` sections become a short note that runs are unbounded and old keys are ignored; the commands guide's "max_turns/max_cost 结束" sentence describes the runaway stop instead.

- [ ] **Step 2: Run the whole suite**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests -q`
Expected: PASS, no skips beyond the pre-existing set.

- [ ] **Step 3: Run the repo gates**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" bash scripts/check.sh`
Expected: PASS (architecture line budget, i18n, worktree hygiene). The cache override is required: `check.sh` runs `uv sync`, and the default `~/.cache/uv` is outside this workspace.

- [ ] **Step 4: Manual smoke**

Run a headless one-shot against a small task with a deliberately repetitive prompt and confirm the run ends with `loop_detected` and a readable detail rather than a turn ceiling.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: the loop has no ceilings and says why it stopped"
```
