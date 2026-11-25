# Coding-Only Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the zen and swarm workspace modes end to end — orchestrators, control plane, RPC family, config tables, prompts, desktop surfaces, tests and docs — leaving coding as the only workspace mode and `plan` / `ask` / `auto` as the only permission modes.

**Architecture:** The backend loses four host request kinds (`supervisor`, `swarm`, `zen_fleet`, `workspace`), both orchestrator packages (`app/zen`, `app/swarm`), the supervisor registry / dispatch layer, and the `[zen]` / `[swarm]` config tables (retired to tombstones: accepted, ignored, warned once). The protocol version goes 3 → 4; the desktop loses the mode switcher, room/role pages, worker views and their stores, and the session `workspace` classification. `task` subagents, goals and file browsing are untouched; old on-disk data is left in place and simply no longer read.

**Tech Stack:** Python 3.11+ (pytest asyncio auto mode, uv, ruff, mypy, stdlib gates under `scripts/`), TypeScript + React desktop under `desktop/` (`tsc`, `node --test`), generated wire types via `scripts/gen_rpc_ts.py`.

**Spec:** `docs/superpowers/specs/2026-11-25-coding-only-design.md`

## Global Constraints

- Workspace modes are gone: no `coding/zen/swarm` three-state, no mode switcher, no `workspace.json` fact, no `workspace` RPC, no session `workspace` field.
- Permission vocabulary is exactly `plan` / `ask` / `auto`. `--mode zen` is an unknown mode: argparse exits **2** with `unknown mode: zen`. `--mode PLAN` still normalizes.
- `task` subagents, `/goal`, `workspace_files` / the work panel and the run ledger stay.
- `[zen]` and `[swarm]` config tables are retired, in **both** the global and project layer: parsed as absent, each warned once per process, never an error.
- Old data is never read and never deleted: `~/.zenagent/agents/`, project `swarm/` rooms, `workspace.json`, old `runs.jsonl` rows. Old `.zen.json`-marked sessions list as ordinary coding sessions.
- `RUN_KINDS` keeps `zen_router` / `zen_worker` / `swarm` as historical values (one per line, each ending in `# HISTORICAL:`), so old ledgers still filter and list.
- `zen` survives only as product name: `zenagent` package, `zen` binary, `~/.zenagent` and `<root>/.zenagent` paths. No mode, alias, room, role, fleet or supervisor meaning.
- `PROTOCOL_VERSION` is 4 in `zenagent/cli/rpc/codec.py`, `zenagent/cli/rpc/server.py` and `desktop/src/rpc/types.ts`; `desktop/src/rpc/wire.gen.ts` is regenerated, never hand-edited.
- Python tests: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest <path> -q` from the worktree root. Desktop: `cd desktop && ./node_modules/.bin/tsc --noEmit && node --test tests/*.test.ts`.
- One commit per task; message style `type(scope): lowercase imperative`.
- Work happens in a worktree created through `superpowers:using-git-worktrees`; the spec and this plan are committed there before Task 1.
- The worktree has no `desktop/node_modules`: symlink the main checkout's (`ln -s /Users/mac/zen-agent/desktop/node_modules desktop/node_modules`, already gitignored) before any desktop command.

## Review Focus

- An old `config.toml` (global or project layer) carrying `[zen]` / `[swarm]` must start, ignore their values, and warn exactly once per section per process — Task 2.
- A session directory still holding `.zen.json` must list its sessions as ordinary coding conversations, not hide them or crash — Task 2.
- An old `runs.jsonl` row with `kind="swarm"` / `"zen_worker"` must still be listed and filterable — Task 2.
- `--mode zen` must exit 2 with `unknown mode: zen` while `--mode PLAN` still works and `--yes`/`--read-only` keep mapping to auto/plan — Task 1.
- A persisted desktop pref `mode="zen"` must load as `null` (child default `ask`), and `mode="turbo"` must keep falling back to `null` — Task 1.

---

### Task 1: Remove the `zen` permission-mode alias

**Files:**
- Modify: `zenagent/app/modes.py`
- Modify: `zenagent/cli/flags.py`
- Modify: `zenagent/cli/rpc/cli.py:36-38` (comment only)
- Modify: `desktop/src/state/prefs.ts`
- Modify: `tests/test_modes.py`, `tests/test_cli.py`, `desktop/tests/prefs.test.ts`
- Modify: `docs/guide/cli.md` (`--mode` row)

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_mode("zen") is None`; `resolve_mode(explicit="zen")` raises `ValueError`; `--mode zen` raises argparse `SystemExit(2)` with `unknown mode: zen`; `loadPrefs().mode` is `null` for a stored `zen`.

- [ ] **Step 1: Update the failing tests**

In `tests/test_modes.py`, replace `test_legacy_zen_is_alias_for_auto` with:

```python
def test_zen_is_not_a_mode():
    assert normalize_mode("zen") is None
    with pytest.raises(ValueError):
        resolve_mode(explicit="zen")


def test_mode_names_stay_case_and_space_insensitive():
    assert normalize_mode(" PLAN ") == "plan"
```

and add `"zen"` to the `test_normalize_mode_rejects_unknown` parametrize list.

In `tests/test_cli.py`, add `import pytest` at module level if it is missing, delete `test_legacy_zen_mode_flag_still_reaches_auto`, and replace `test_unknown_mode_is_rejected` with:

```python
@pytest.mark.parametrize("raw", ["zen", "yolo"])
def test_unknown_mode_is_rejected(monkeypatch, capsys, raw):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    with pytest.raises(SystemExit) as exc:
        cli_main.main(["-p", "hi", "--mode", raw])
    assert exc.value.code == 2
    assert f"unknown mode: {raw}" in capsys.readouterr().err
```

In `desktop/tests/prefs.test.ts`, replace `a legacy zen mode pref loads as auto` with:

```ts
test('a retired zen mode pref is not a mode and loads as null', () => {
  withStorage((store) => {
    store.set(MODE_KEY, 'zen')
    assert.equal(loadPrefs().mode, null)
  })
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_modes.py tests/test_cli.py -q`
Expected: FAIL — `normalize_mode("zen")` returns `"auto"`; `--mode zen` reaches the gate.

Run: `cd desktop && node --test tests/prefs.test.ts`
Expected: FAIL — `zen` loads as `auto`.

- [ ] **Step 3: Implement**

`zenagent/app/modes.py`: delete `LEGACY_MODES` (and its `__all__` entry), delete the `LEGACY_MODES.get(...)` line in `normalize_mode`, and replace the docstring paragraph mentioning `zen` with: `Only ``plan`` / ``ask`` / ``auto`` are modes; the retired ``zen`` spelling is unknown.`

`zenagent/cli/flags.py`: `_permission_mode` keeps normalizing case and spaces; update its docstring to `"""argparse type: normalize case/whitespace before choices run."""` and change the `--mode` help to `"runtime permission mode: plan (read-only) | ask (confirm) | auto (fully automatic, no confirmations)"`.

`zenagent/cli/rpc/cli.py:36-38`: change `then --yes (zen)` to `then --yes (auto)`.

`desktop/src/state/prefs.ts`: delete `const LEGACY_MODES: Record<string, string> = { zen: 'auto' }` and change `const mode = stored === null ? null : (LEGACY_MODES[stored] ?? stored)` to `const mode = stored`.

`docs/guide/cli.md`: `--mode` row becomes `plan` / `ask` / `auto`（默认 ask；`--yes`≈auto，`--read-only`≈plan） — no alias note.

- [ ] **Step 4: Run the tests and the gates for this task**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_modes.py tests/test_cli.py -q`
Expected: PASS.

Run: `cd desktop && node --test tests/prefs.test.ts && ./node_modules/.bin/tsc --noEmit`
Expected: PASS, `tsc` exit 0.

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check zenagent/app/modes.py zenagent/cli/flags.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/modes.py zenagent/cli/flags.py zenagent/cli/rpc/cli.py \
  desktop/src/state/prefs.ts desktop/tests/prefs.test.ts \
  tests/test_modes.py tests/test_cli.py docs/guide/cli.md
git commit -m "feat(modes)!: the permission vocabulary has no zen alias"
```

---

### Task 2: Delete the zen/swarm backend and bump the wire to v4

**Files (delete):**
- `zenagent/app/zen/` (whole directory), `zenagent/app/swarm/` (whole directory)
- `zenagent/swarm_settings.py`, `zenagent/app/workspace_state.py`, `zenagent/app/dispatch.py`
- `zenagent/cli/rpc/ops_zen.py`, `ops_swarm.py`, `ops_supervisor.py`, `ops_workspace.py`
- `zenagent/prompts/blocks/swarm_protocol.md`
- Tests: `tests/test_swarm_{gate,manager,protocol,reclaim,roles,room,roster,router,runner,storm,transcript,workitems}.py`, `tests/test_zen_{fleet,roster,rpc,runtime,tools}.py`, `tests/test_rpc_swarm.py`, `tests/test_rpc_supervisor.py`, `tests/test_workspace_state.py`, `tests/test_dispatch.py`

**Files (modify):** the edit table in the spec §5.3, plus `tests/test_agent_profile.py`, `tests/test_runtime_role_assembly.py` (renamed), `tests/test_rpc_contract.py`, `tests/test_rpc_host.py`, `tests/test_run_ledger.py`, `tests/test_app_sessions.py`, `tests/test_config.py`, `desktop/src/rpc/types.ts` (`REQUEST_KINDS` + `PROTOCOL_VERSION`), `desktop/src/rpc/client.ts` (`SESSION_TAG_EXEMPT`), and the regenerated `desktop/src/rpc/wire.gen.ts`.

**Interfaces:**
- Consumes: Task 1's permission vocabulary.
- Produces: `_warn_retired_section(name: str) -> None` (private, `zenagent/config.py`); `RUN_KINDS` with three `# HISTORICAL:` lines; `SessionEntry` without `workspace`; `RpcHost._handle_line` answers `{"type": "error", "message": "unknown type: <kind>"}` for the four removed kinds.

- [ ] **Step 1: Write the failing regression tests**

Append to `tests/test_config.py`:

```python
def test_retired_zen_and_swarm_tables_are_ignored_with_one_warning(tmp_path, caplog):
    import logging

    import zenagent.config as config_module

    config_module._warned_sections.clear()
    path = tmp_path / "config.toml"
    path.write_text(
        'default_model = "m"\n\n[zen]\nmax_concurrent = 9\n\n[swarm]\nmax_chain_depth = 9\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="zenagent.config"):
        first = config_module.load_user_config(path=path)
        config_module.load_user_config(path=path)

    retired = [record.getMessage() for record in caplog.records if "retired" in record.getMessage()]
    assert len(retired) == 2
    assert any("[zen]" in message for message in retired)
    assert any("[swarm]" in message for message in retired)
    assert first.default_model == "m"
    # The project layer used to reject these tables as global-only; retired
    # tables are ignored there too instead of failing the load.
    config_module._load_config_file(path, project_layer=True)
```

Append to `tests/test_app_sessions.py`:

```python
async def test_an_old_zen_session_lists_as_a_normal_coding_session(session_runtime):
    import json

    runtime = session_runtime
    manager = SessionManager(runtime)
    await runtime.agent.prompt("hello")
    path = manager.current()
    (path.parent / ".zen.json").write_text(
        json.dumps({path.stem: {"role": "router", "focus": "", "set_at": "2026-01-01T00:00:00.000Z"}}),
        encoding="utf-8",
    )

    entry = next(entry for entry in manager.entries() if entry.path == path)
    assert not hasattr(entry, "workspace")
    assert entry.messages == 2
```

Append to `tests/test_run_ledger.py`:

```python
def test_historical_orchestration_kinds_still_list(tmp_path):
    ledger = _ledger(tmp_path)
    for kind in ("swarm", "zen_router", "zen_worker"):
        record = ledger.start(kind=kind, agent_id="old", session="/p/s.jsonl", label=kind)
        ledger.finish(record, status="done", termination="stop", turns=1, cost=0.0)

    assert [row.kind for row in ledger.list(kind="swarm")] == ["swarm"]
    assert len(ledger.list()) == 3
```

Append to `tests/test_rpc_host.py`:

```python
async def test_removed_orchestration_kinds_are_unknown(host_env):
    host = host_env.build()
    for kind in ("swarm", "zen_fleet", "supervisor", "workspace"):
        frame = await call(host, host_env.writes, {"type": kind, "id": kind})
        assert frame["type"] == "error"
        assert "unknown type" in frame["message"]
```

In `tests/test_rpc_contract.py`: change both `PROTOCOL_VERSION = 3` assertions to `= 4`.

- [ ] **Step 2: Run them to see them fail**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_config.py tests/test_app_sessions.py tests/test_run_ledger.py tests/test_rpc_host.py tests/test_rpc_contract.py -q`
Expected: FAIL — no `_warned_sections`; `SessionEntry.workspace` exists; four kinds are dispatched; version is 3.

- [ ] **Step 3: Delete the control plane and rewire the host**

- Delete the four `ops_*.py` files, `app/workspace_state.py`, `app/dispatch.py`, and the twenty-one feature test files listed above.
- `zenagent/cli/rpc/host.py`: drop the imports of the four ops and of `dispatch`-backed `Supervisor`; delete `_swarm`, `_supervisors`, `_zen_pre` and every `supervisor.register/unregister` call (constructor and `_adopt`); delete the `supervisor` / `workspace` / `swarm` / `zen_fleet` dispatch branches, `_is_swarm_request_id` + `_resolve_swarm_prompt` handling, `_supervisor()` helper, the `was_zen` block in `_drop`, and the `set_workspace_mode` import/call.
- `zenagent/cli/rpc/slot.py`: delete the `zen_fleet` branches in `pinned` and `close`.
- `zenagent/cli/rpc/ops_secrets.py`: delete `_drop_idle_swarm_rooms` and both calls; rewrite `_knob_value`'s docstring without fleet/room.
- `zenagent/cli/rpc/protocol/_host.py`: delete `HostSupervisor*` and `HostWorkspaceSetRequest`, `SupervisorAgentRow`, `WorkspaceStateWire`, the `supervisor`/`workspace` `OpSpec` entries and the `swarm`/`zen_fleet` entries in `HOST_PARTIAL`.
- `zenagent/cli/rpc/protocol/_views.py`: delete the `workspace` field from `SessionSummary`.
- `zenagent/cli/rpc/server.py` and `codec.py`: `PROTOCOL_VERSION = 4`.
- `zenagent/app/agent_profile.py`: keep `AgentProfile` unchanged for now; delete the `dispatch` import, `AgentHandle`, `Supervisor`, `register_swarm_agent`, `swarm_handle_key`. Keep `project_sessions` and `effective_worker_mode` until Step 4 (runtime and the fleet package still use them).
- `zenagent/app/sessions.py`: delete the `read_roster` import, the `workspace` dataclass field, and its two assignments.
- `desktop/src/rpc/types.ts`: `PROTOCOL_VERSION = 4`; remove `'supervisor'`, `'swarm'`, `'workspace'`, `'zen_fleet'` from `REQUEST_KINDS`.
- `desktop/src/rpc/client.ts`: remove the same four entries from `SESSION_TAG_EXEMPT`.
- `tests/test_rpc_host.py:144`: change `assert frame["protocol_version"] == 3` to `== 4`.
- Run `UV_CACHE_DIR="$PWD/.uv-cache" uv run python scripts/gen_rpc_ts.py` to regenerate `desktop/src/rpc/wire.gen.ts`.

- [ ] **Step 4: Delete the packages and their app/config tails**

- Delete `app/zen/`, `app/swarm/`, `swarm_settings.py`, `prompts/blocks/swarm_protocol.md`, and the `swarm_protocol` `BlockSpec` in `prompts/spec.py`.
- `app/runtime.py`: delete `zen_fleet`, `tool_providers`, `prompt_note_providers`, and the `project_sessions` branch in `_note_session_scope`; update the docstrings.
- `app/skills_recompose.py`: delete the `tool_providers` loop.
- `app/limits_apply.py`: delete the `SWARM_FIELDS` import and the `zen_*` / `*SWARM_FIELDS` names from `copy_onto`'s tuple; update its docstring.
- `zenagent/config.py`: delete `_ZEN_KEYS`, the `DEFAULT_ZEN_*` constants, `ZEN_WORKER_MODES`, `LEGACY_ZEN_WORKER_MODES`, the `zen_*` fields, the `[zen]` parse block, the `SwarmPolicy` base and `[swarm]` parse, **and the zen/swarm copies in `_merge_user_configs`** (lines ~555-561); add:

```python
_log = logging.getLogger(__name__)
#: Whole tables this release retired: accepted, ignored, warned once each.
_RETIRED_SECTIONS = frozenset({"zen", "swarm"})
_warned_sections: set[str] = set()


def _warn_retired_section(name: str) -> None:
    if name in _warned_sections:
        return
    _warned_sections.add(name)
    _log.warning(
        "[%s] is retired and ignored: the zen and swarm orchestration modes "
        "were removed; coding is the only workspace mode",
        name,
    )
```

and, in `_load_config_file` right after `data` is loaded:

```python
for name in sorted(_RETIRED_SECTIONS & set(data)):
    _warn_retired_section(name)
```

  Remove `zen` / `swarm` from the `global_only` list (they are no longer live sections there; the tombstone covers both layers).
- `zenagent/limits.py`: delete the `ZEN_KNOBS` (4 rows) and `SWARM_KNOBS` (10 rows) tuples, their `__all__` entries, both terms from `ALL_KNOBS`, the `"zen"` / `"swarm"` entries in `MANAGED_TABLES`, and the `swarm_settings` import. `GROUP_ORDER` is already free of them.
- `app/agent_profile.py`: delete `effective_worker_mode` and the `project_sessions` field (and the `mode` import if now unused).
- `app/run_wiring.py`: delete `attach_swarm_run` and its `__all__` entry; replace the zen/swarm docstring example with a `coding` + `subagent` one (the residue gate matches the words themselves).
- `app/runs.py`: split the historical kinds one per line with a trailing marker:

```python
#: What produced the run. The last three are historical: no code produces
#: them any more, but old ledgers still carry them.
RUN_KINDS = (
    "coding",
    "subagent",
    "zen_router",  # HISTORICAL:
    "zen_worker",  # HISTORICAL:
    "swarm",  # HISTORICAL:
)
```

  and change the `RUN_STATUSES` comment `(swarm)` to a neutral `an owning layer`.
- `zenagent/i18n/{zh,en}.py`: delete `swarm.locked`.
- Docstring edits, rewriting each so the word disappears (the residue gate matches words): `app/approval_router.py` ("across slots"); `agent/jsonl.py` ("the ledger"); `app/runs.py` module header; `app/run_wiring.py` examples (covered above); `config_errors.py` and `config_fields.py` examples (`[task]` / "a section parser"); `cli/rpc/ops_runs.py` ("coding runs and subagent runs"); `cli/rpc/protocol/_host.py` `RunView` doc (drop the swarm scope note; the regenerated wire comment follows).
- Tests whose orchestration cases live in kept files:
  - `tests/test_run_scopes.py` — delete the whole file (three zen/swarm scope tests).
  - `tests/test_jsonl.py` — delete `test_room_append_line_survives_a_torn_tail` (the RunLedger torn-tail test above it already covers the same path).
  - `tests/test_config.py` — delete `test_zen_fleet_behaviour_parsed`, `test_zen_fleet_behaviour_defaults`, `test_zen_worker_mode_accepts_the_legacy_zen_alias`, `test_zen_worker_mode_rejects_unknown_choice`; the new tombstone test replaces them.
  - `tests/test_app_sessions.py` — delete `test_entries_tag_zen_roster_sessions_as_zen` (the new listing test replaces it).
  - `tests/test_rpc_host_e2e.py` — delete `test_zen_gives_the_router_its_own_slot` and `test_enabling_zen_does_not_abort_a_running_turn`.
  - `tests/test_rpc_contract.py` — `SERVER_FRAME_TYPES = {"result", "error", "event"}`; `_backend_source()` returns `RPC_PATH`; delete `test_swarm_frames_carry_swarm_flag` and `test_every_swarm_frame_names_the_project_of_its_room`; `test_server_frame_union_matches_backend_literals` reads only `TS_PATH + TS_EVENT_TYPES_PATH`.
  - `tests/test_agent_profile.py` — keep only `test_profile_builds_a_run_scope` (use `kind="coding"`).
  - Rename `tests/test_runtime_role_assembly.py` → `tests/test_runtime_profile_assembly.py`; rename its `swarm_post` stub to `stub_tool`.
- Sweep for anything missed before running the suite:

```bash
grep -ln "from zenagent\.app\.swarm\|from zenagent\.app\.zen\|zen_fleet\|SwarmManager\|ZenFleet\|from zenagent\.app\.dispatch\|from zenagent\.app\.workspace_state" tests/*.py
```

Expected: no output (every hit is either deleted or edited above).

- [ ] **Step 5: Run the full Python suite and gates**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q`
Expected: PASS (roughly 3,240 minus the deleted feature tests plus the new regressions).

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check . && UV_CACHE_DIR="$PWD/.uv-cache" uv run mypy zenagent`
Expected: `All checks passed!` and `Success: no issues found`.

Run: `python3 scripts/check_architecture.py && python3 scripts/check_i18n.py && python3 scripts/gen_rpc_ts.py --check`
Expected: `architecture budget: ok`, i18n exit 0, `wire.gen.ts is fresh`.

Run: `python3 scripts/check_worktrees.py`
Expected: `worktree hygiene: ok` (the feature worktree is active, so it may report KEEP-ACTIVE — that is fine; no RETIRE line for it).

- [ ] **Step 6: Commit**

```bash
git add -A zenagent tests desktop/src/rpc scripts docs/superpowers
git commit -m "feat(agent)!: delete the zen and swarm orchestrators"
```

---

### Task 3: Desktop — one workspace, no mode switch

**Files (delete):**
- `desktop/src/ui/`: `ModeSwitcher.{tsx,module.css}`, `ProjectSwitcher.{tsx,module.css}`, `SwarmPanels.module.css`, `SwarmRoster.tsx`, `SwarmSidebar.tsx`, `SwarmTasks.tsx`, `SwarmWorks.{tsx,module.css}`, `WorkerConversation.{tsx,module.css}`, `ZenRoster.tsx`, `ZenSidebar.tsx`, `ZenWordmark.{tsx,module.css}`, `pages/{RoomPage.*,WorkspaceSettings.*,AgentSettings.*,LibraryPicker.tsx,agentEditor.tsx}`, `composer/RoomComposer.{tsx,module.css}`
- `desktop/src/state/`: `zenActions.ts`, `zenStore.ts`, `zenSidebar.ts`, `swarmStore.ts`, `swarmWiring.ts`, `swarmRun.ts`, `swarmWorks.ts`, `roomCommands.ts`, `roomPreview.ts`
- `desktop/src/rpc/types-swarm.ts`; `desktop/src/dev/{swarmMock,zenMock}.ts`; `desktop/src/i18n/messages-swarm.ts`
- `desktop/tests/`: `roomCommands.test.ts`, `roomComposerStore.test.ts`, `roomPreview.test.ts`, `swarmRun.test.ts`, `swarmStore.test.ts`, `swarmWiring.test.ts`, `swarmWorks.test.ts`, `workspaceMode.test.ts`, `zenActions.test.ts`, `zenSidebar.test.ts`, `zenStore.test.ts`
- `desktop/e2e/swarm.spec.ts`, `desktop/e2e/zen.spec.ts`

**Files (modify):** `App.tsx`, `ui/Sidebar.tsx`, `ui/SidebarCoding.tsx`, `ui/composer/{InputBar,Menus}.tsx`, `ui/pages/{SettingsPage,settingsNav}.tsx`, `state/{stores,appStore,frameRouter,sessionPanes,sessionGroups,mentions}.ts`, `rpc/{types,types-events,client}.ts`, `dev/bridge.ts`, `i18n/{messages,messages-settings}.ts`, `e2e/{home,layout,session,settings,work-panel,git-panel,plugin}.spec.ts`, `tests/{frameRouter,frames,sessionGroups,bridge,rpcClient,sessionBoot}.test.ts`

**Interfaces:**
- Consumes: Task 2's wire v4 (no `WorkspaceStateWire`, `SupervisorAgentRow`, session `workspace`; four kinds gone).
- Produces: no new exports. `useSettingsStore` no longer has `workspaceMode`; `App` renders the coding layout unconditionally; `Sidebar` always renders `SidebarCoding`.

- [ ] **Step 1: Delete the dedicated files and tests**

Delete every path in the two delete lists above. Then run:

`cd desktop && ./node_modules/.bin/tsc --noEmit`
Expected: FAIL, naming the first missing imports (`ModeSwitcher`, `swarmStore`, `types-swarm`, …).

- [ ] **Step 2: Rewire the shared files**

- `App.tsx`: remove the `useZenStore` import, the `workspaceMode` selector, the `closeWorker` effect, and the `workspaceMode === 'swarm' ? … : workspaceMode === 'zen' && … ? … : …` render branch — the coding branch becomes unconditional.
- `ui/Sidebar.tsx`: remove the mode selector and the `ZenSidebar`/`SwarmSidebar` branch; render the brand header + `SidebarCoding` + footer only.
- `ui/SidebarCoding.tsx`: delete the `sessionsForWorkspace` import and the `useMemo(() => sessionsForWorkspace(allSessions, 'coding'), [allSessions])` filter; the sidebar lists `allSessions` (old zen sessions now appear here).
- `state/stores.ts`: delete `WorkspaceMode`, `WORKSPACE_MODES`, `workspaceMode`, `loadWorkspaceMode`, `setWorkspaceMode`, and the `zen.workspaceMode` persistence.
- `state/appStore.ts`: delete the `zenActions` import, `switchWorkspaceMode`, `stopFleet`, `zenTranscript` (declarations and implementations), and the two `syncProjectState(get)` calls.
- `state/frameRouter.ts`: delete the `applyFleetFrame` / `applySwarmFrame` imports and calls.
- `state/sessionPanes.ts`: delete both `zen_fleet op=transcript` probe blocks (fall through to the existing session/disk reads); drop the zen sentence from the module docstring.
- `state/sessionGroups.ts`: delete `sessionsForWorkspace`.
- `state/mentions.ts`: change `recognizedTokens(value, commands, skills, entries)` — delete the `agents` parameter, the `roles` set, and the `@role` branch; `ui/composer/InputBar.tsx` already passes four arguments.
- `ui/composer/Menus.tsx`: delete the `swarm.modelDefault*` menu items and any room-only branches.
- `ui/pages/SettingsPage.tsx` and `settingsNav.tsx`: remove the Swarm/Agents tabs and their keyword rows.
- `rpc/types.ts`: delete the `types-swarm` import/re-export block.
- `dev/bridge.ts`: delete the `supervisor` / `swarm` / `zen_fleet` / `workspace` dev handlers and their imports.
- `i18n/messages.ts`: delete `import { swarmEn, swarmZh }` and their spread into the dictionaries; delete the `workspace.*` and `zen.*` keys and the `swarm.*` keys whose callers are gone.
- `i18n/messages-settings.ts`: delete the `settings.limit.zen.*` (4) and `settings.limit.swarm.*` (10) descriptions in both locales (28 entries).
- `e2e/*.spec.ts`: remove mode-switcher steps and workspace-mode assertions from the seven edited specs.
- `tests/rpcClient.test.ts:155` and `tests/sessionBoot.test.ts:45`: change the hardcoded `protocol_version: 3` fixtures to `4` (the handshake compares strictly after the bump).
- `tests/frameRouter.test.ts`: delete the `swarm and fleet frames are consumed before pane routing ever sees them` test.

- [ ] **Step 3: Type-check and fix until clean**

Run: `cd desktop && ./node_modules/.bin/tsc --noEmit`
Expected: PASS (exit 0). Fix remaining references (unused imports, stale props) one by one — `tsc` names each file and line.

- [ ] **Step 4: Run the desktop tests**

Run: `cd desktop && node --test tests/*.test.ts`
Expected: PASS. If `tests/i18nKeys.test.ts` reports a one-sided key, delete or add the key on **both** locales; if `tests/frames.test.ts` / `tests/sessionGroups.test.ts` still import a deleted function, update the test to the remaining API.

- [ ] **Step 5: Commit**

```bash
git add -A desktop
git commit -m "feat(desktop)!: one workspace, no mode switch"
```

---

### Task 4: Docs, changelog, architecture allowlist

**Files (delete):** `docs/guide/swarm.md`, `docs/guide/zen-mode.md`, `docs/guide/orchestration.md`, `docs/internals/supervisor.md`, `docs/internals/zen.html`

**Files (modify):** `docs/README.md`, `README.md`, `docs/architecture.md`, `docs/guide/{config,projects,sessions,rpc-sdk,cli,commands,i18n}.md`, `docs/internals/{run-ledger,agent-loop}.md`, `docs/frontend-matrix.md`, `docs/feature-inventory.md`, `docs/feature-map.md`, `docs/architecture/allowlist.json`, `CHANGELOG.md`

**Interfaces:**
- Consumes: the deleted surface from Tasks 1–3.
- Produces: no code.

- [ ] **Step 1: Delete the five pages and fix every link**

Delete the five files. Then scan for inbound links:

`grep -rnE "swarm\.md|zen-mode\.md|orchestration\.md|supervisor\.md|zen\.html" docs README.md`

Update `docs/README.md` (drop the Zen/Swarm/控制面 entries and the Supervisor step), `docs/guide/projects.md` (drop the 控制面 link and the workspace-mode sentence), and `README.md` (drop the swarm screenshot and the mode bullets).

- [ ] **Step 2: Update the reference docs**

- `docs/architecture.md`: remove zen/swarm from the topology and the run-kind list.
- `docs/guide/config.md`: delete the `[zen]` and `[swarm]` sections; state that both tables are retired (accepted, ignored, warned once) and that the settings page no longer lists them.
- `docs/guide/sessions.md`: delete the session `workspace` field mention.
- `docs/guide/rpc-sdk.md`: remove `supervisor` / `workspace` / `swarm` / `zen_fleet` from the host-op tables and the workspace-mode paragraph.
- `docs/guide/cli.md` + `commands.md`: no remaining mode-alias or orchestration references.
- `docs/frontend-matrix.md`, `docs/feature-inventory.md`, `docs/feature-map.md`: drop the zen/swarm rows.
- `docs/internals/run-ledger.md`: rewrite the producer table and the swarm/zen sections (§ writes, § scope, the `kind=swarm` example, § recovery notes) so the ledger is described as coding + subagent, with the three historical kinds called out as read-only legacy.
- `docs/internals/agent-loop.md`: drop the "swarm 的链级预算" parenthetical from the cost section.
- `docs/guide/i18n.md`: drop `-swarm` from the desktop dictionary-file list.

- [ ] **Step 3: Allowlist and changelog**

- `docs/architecture/allowlist.json`: delete the `zenagent/app/swarm/manager.py` line and its reason entry; adjust the `_readme` payback list if it names swarm.
- `CHANGELOG.md` (Unreleased, Breaking): the two workspace modes and their RPC families are gone; `--mode zen` is rejected; `[zen]`/`[swarm]` are tombstoned; old zen sessions list as coding; a desktop pref stored as `zen` falls back to `ask`; old data stays on disk and is not read.

- [ ] **Step 4: Verify no doc still teaches a deleted thing**

Run: `grep -rnE "zen-mode|orchestration|swarm\.md|supervisor\.md|--mode zen|workspaceMode|三态切换" docs/guide docs/README.md README.md`
Expected: no matches outside `CHANGELOG.md` and `docs/superpowers/`.

Run: `python3 scripts/check_i18n.py && python3 scripts/check_architecture.py`
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git add -A docs README.md CHANGELOG.md
git commit -m "docs: coding is the only mode"
```

---

### Task 5: Whole-branch verification and startup smoke

**Files:** none expected; fixes land wherever the checks point.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: the evidence for the final report.

- [ ] **Step 1: Run every gate**

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check .
UV_CACHE_DIR="$PWD/.uv-cache" uv run mypy zenagent
python3 scripts/check_architecture.py
python3 scripts/check_i18n.py
python3 scripts/gen_rpc_ts.py --check
UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q
cd desktop && ./node_modules/.bin/tsc --noEmit && node --test tests/*.test.ts
```

Expected: all green; pytest reports the reduced count with zero failures.

- [ ] **Step 2: Run the static residue gates**

```bash
grep -rnE "swarm|zen_fleet|WorkspaceMode|workspaceMode|LEGACY_MODES|attach_swarm_run" \
  --include=*.py zenagent/ | grep -v "# HISTORICAL:"
grep -rnE "swarm|zen_fleet|supervisor|workspaceMode|LEGACY_MODES" \
  desktop/src --include=*.ts --include=*.tsx
grep -rnE "zen-mode|orchestration|swarm\.md|supervisor\.md|--mode zen|三态切换" \
  docs/guide docs/internals docs/README.md README.md
```

Expected: all three emit nothing (`desktop/src/rpc/wire.gen.ts` included — the regenerated file must not carry the removed shapes).

- [ ] **Step 3: Start the real thing**

```bash
zen --version
printf '%s\n' '{"type":"status","id":"s1"}' '{"type":"shutdown","id":"x"}' | zen rpc
zen --mode zen -p hi            # expect exit 2 and "unknown mode: zen"
```

Then, with a provider key available in an isolated `HOME`:

```bash
zenagent -p "Reply with exactly: pong" --json     # ok:true, termination:"stop", detail:null
```

Then boot the TUI in tmux, capture one frame, and quit (`/quit`). Then start `zen rpc` once against a config containing `[zen]` / `[swarm]` and confirm the two warnings appear exactly once.

- [ ] **Step 4: Commit any fixes, then report**

If a check required a code change, commit it as `fix: <what the gate caught>`. Report: commit list, net diff, deleted-test count, gate output, and the smoke results.
