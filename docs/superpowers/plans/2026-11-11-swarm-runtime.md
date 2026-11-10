# Swarm Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-role agent runtime: declarative role manifests, a global in-app room with mention-routed tasks, a first-class task registry, and a coding/zen/swarm workspace switcher.

**Architecture:** New app-layer package `zenagent/app/swarm/` (roles, room/task store, router, runner, manager, protocol) hosted by the RPC host process; the desktop gains a room page, a swarm store fed by global frames, and a sidebar mode switcher. The kernel (`zenagent/agent/`) is untouched; role assembly goes through three thin `RuntimeOptions` hooks (tool allowlist, extra tools, extra prompt).

**Tech Stack:** Python 3.11+ (stdlib only for the new package), pytest + asyncio, Tauri/React/TypeScript desktop (node --test, Playwright), arch-gov manifest tooling.

**Spec:** `docs/superpowers/specs/2026-11-10-swarm-runtime-design.md`

## Global Constraints

- Worktree: `/Users/mac/zen-agent/.worktrees/swarm-runtime`, branch `feat/swarm-runtime`. Run all commands from there.
- No changes under `zenagent/agent/`; no new third-party dependencies; `zenagent/app/swarm/` is stdlib-only.
- Layering (`tests/test_layering.py`): app may import agent/tools/mcp/plugins/settings; **app must not import `zenagent.cli`**; no cycles.
- File budget (`scripts/check_architecture.py`): 800 lines per file; allowlisted files may only shrink. Keep `desktop/src/dev/bridge.ts`, `desktop/src/rpc/types.ts`, `runtime.py`, `server.py`, `host.py`, `App.tsx` within their allowlisted caps — put swarm logic in new files and leave thin hooks only.
- RPC contract is pinned both ways (`tests/test_rpc_contract.py`): adding request kind `swarm` requires the same string in `desktop/src/rpc/types.ts` `REQUEST_KINDS` in the same task.
- i18n parity is pinned (`desktop/tests/locale.test.ts`): every new key must exist in both `zh` and `en` dicts.
- Secrets only via `zenagent/settings.py` (`get_api_key`); manifests store `${KEY}` placeholders only; never log or echo values.
- Naming: top-level mode type is `WorkspaceMode = 'coding' | 'zen' | 'swarm'`; the existing runtime autonomy modes stay `plan/ask/zen` and are called "自治模式" in code comments.
- Default role workspace root: `~/.zenagent-workspaces/<role-id>/` (outside the `~/.zenagent` protected fence). Never add a gate exemption.
- Swarm RPC frames are global: they must carry `swarm: true` and **no** `project`/`session` tags.
- Commit after every task; conventional-commit style, e.g. `feat(swarm): ...`.

## Review Focus

1. **Unknown `@role` (typo or deleted role):** the message must still land in the room, no task is created, no error frame, no crash.
2. **Mutual agent mentions (A @ B, B @ A):** the chain-depth gate must stop the loop with a system message; no unbounded spend.
3. **Host/desktop restart mid-run:** nothing auto-resumes; every non-terminal task becomes `failed(interrupted)` and is retryable.
4. **Run hits `max_turns` / `max_cost`:** the task becomes `failed` with a reason shown in the room, and the agent is free for its next queued task.
5. **Broken role manifest or missing credentials:** that role is skipped or its server omitted with a warning; other roles and the room keep working.

---

### Task 1: Runtime assembly hooks for roles

**Files:**
- Modify: `zenagent/app/runtime.py` (RuntimeOptions + `_recompose()` + `_build_prompt()`; thin hooks only)
- Test: `tests/test_runtime_role_assembly.py`

**Interfaces:**
- Produces: `RuntimeOptions.tools_allow: list[str] | None`, `RuntimeOptions.extra_tools: list[Any] | None`, `RuntimeOptions.extra_prompt: str | None`; all later swarm tasks pass these to `prepare()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime_role_assembly.py
import pytest


@pytest.mark.asyncio
async def test_tools_allow_filters_assembled_tools(runtime_factory):
    runtime = await runtime_factory(tools_allow=["read_file"])
    assert [t.name for t in runtime.agent.tools] == ["read_file"]


@pytest.mark.asyncio
async def test_extra_tools_are_appended_after_allowlist(runtime_factory):
    tool = _stub_tool("swarm_post")  # build like zenagent/skills.py:119 read_skill
    runtime = await runtime_factory(tools_allow=["read_file"], extra_tools=[tool])
    assert [t.name for t in runtime.agent.tools] == ["read_file", "swarm_post"]


@pytest.mark.asyncio
async def test_extra_prompt_reaches_system_prompt(runtime_factory):
    runtime = await runtime_factory(extra_prompt="ROLE-PERSONA")
    assert "ROLE-PERSONA" in runtime.agent.system_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime_role_assembly.py -v`
Expected: FAIL (unexpected keyword `tools_allow`).

- [ ] **Step 3: Implement the hooks**

Add the three fields to `RuntimeOptions` (`zenagent/app/runtime.py:107`). Store them on the Runtime during `bootstrap()` (next to the other option-derived attributes). In `_recompose()` (`runtime.py:647`), after the tool list is built and before `self.tool_registry.reconcile(tools)`:

```python
if self._tools_allow is not None:
    tools = [tool for tool in tools if tool.name in self._tools_allow]
tools = [*tools, *(self._extra_tools or [])]
```

In `_build_prompt()` (`runtime.py:632-634`), append a `RenderedBlock(id="role", cache_class="session")`-style fragment carrying `self._extra_prompt` when set — or append the string to `extension_fragments` if the surrounding code builds a tuple; either way the text must reach `compose_system` as an extension fragment (same channel extensions already use).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runtime_role_assembly.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/runtime.py tests/test_runtime_role_assembly.py
git commit -m "feat(swarm): add runtime hooks for role assembly"
```

---

### Task 2: Role manifests

**Files:**
- Create: `zenagent/app/swarm/__init__.py`, `zenagent/app/swarm/roles.py`
- Test: `tests/test_swarm_roles.py`

**Interfaces:**
- Consumes: `zenagent.settings.get_api_key`, `zenagent.mcp.config.parse_server`, `zenagent.plugins.discovery.discover_plugins`, `zenagent.plugins.state.resolve/is_enabled/credentials_for`.
- Produces:
  - `@dataclass RoleManifest`: `id: str`, `name: str`, `description: str`, `model: str | None`, `thinking: str | None`, `system_prompt: str`, `skill_roots: list[Path]`, `mcp_servers: dict[str, dict]`, `tools: list[str] | None`, `root: Path`, `inherit_project: bool`, `max_turns: int | None`, `max_cost: float | None`, `mode: str`, `missing_credentials: list[str]`.
  - `agents_root() -> Path` (`~/.zenagent/agents`), `workspaces_root() -> Path` (`~/.zenagent-workspaces`), `state_root() -> Path` (`~/.zenagent/swarm`).
  - `parse_role(role_dir: Path) -> tuple[RoleManifest | None, list[str]]` (warnings, never raises).
  - `discover_roles() -> tuple[list[RoleManifest], list[str]]` (sorted by id; bad roles skipped with a warning).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_swarm_roles.py
from pathlib import Path


def _write_role(home: Path, role_id: str, manifest: dict, prompt: str = "You are a role.") -> Path:
    role_dir = home / ".zenagent" / "agents" / role_id
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (role_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return role_dir


def test_parse_role_reads_fields_and_prompt(isolated_home, monkeypatch):
    # isolated_home is the existing conftest HOME isolation; build on it or
    # monkeypatch Path.home via the same helper other tests use.
    role_dir = _write_role(...)
    role, warnings = parse_role(role_dir)
    assert role.id == "research"
    assert role.system_prompt == "You are a role."
    assert role.root == Path.home() / ".zenagent-workspaces" / "research"
    assert warnings == []


def test_bad_manifest_is_skipped_with_warning(...):
    # invalid JSON -> parse_role returns (None, [warning]); discover_roles skips it


def test_missing_credentials_drops_only_that_server(...):
    # mcpServers {"feishu": {"env": {"APP_ID": "${APP_ID}"}}} with no secret
    # -> "feishu" absent from role.mcp_servers, "APP_ID" in role.missing_credentials


def test_workspace_defaults_outside_protected_home(...):
    assert str(role.root).startswith(str(Path.home() / ".zenagent-workspaces"))


def test_role_config_beats_plugin_contribution(...):
    # plugin contributes "lark"; role mcpServers also defines "lark" -> role entry wins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_swarm_roles.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `roles.py`**

Parsing rules: `id` regex `^[a-z0-9][a-z0-9._-]*$`; `systemPromptFile` defaults to `prompt.md`; `tools` omitted → `None`; `mode` defaults `"ask"` and must be one of plan/ask/zen; `inheritProject` defaults `False`. Fill `${KEY}` placeholders from `get_api_key(KEY)` before calling `parse_server(key, entry)` for validation; drop entries with unresolved placeholders and record the keys. Merge enabled plugin contributions (`resolve()` with `credentials_for(plugin_id)`) **below** role entries. Root: explicit `root` (expanduser/resolve) or `workspaces_root()/id`, created on parse.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_swarm_roles.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/ tests/test_swarm_roles.py
git commit -m "feat(swarm): add declarative role manifests"
```

---

### Task 3: Room and task store

**Files:**
- Create: `zenagent/app/swarm/room.py`
- Test: `tests/test_swarm_room.py`

**Interfaces:**
- Produces:
  - `@dataclass RoomMessage`: `id: str`, `ts: int`, `author_kind: str` (`"user"|"agent"|"system"`), `author_id: str`, `text: str`, `mentions: list[str]`, `task_id: str | None`, `chain_depth: int`.
  - `@dataclass TaskRecord`: `id, assignee, state, source_message_id, created_by_kind, created_by_id, chain_id, chain_depth, chain_cost, created_at, updated_at, result_message_id, reason, derived_from` (types as specified in the spec §7).
  - `class SwarmStore`: `__init__(root: Path)`, `load()`, `append_message(RoomMessage)`, `append_task(TaskRecord)` (validates transitions), `messages: list[RoomMessage]`, `tasks: dict[str, TaskRecord]` (last wins), `reconcile() -> list[RoomMessage]`, `new_id(prefix: str) -> str`.
  - `parse_mentions(text: str) -> list[str]` — deduped, order-preserving.
  - Terminal/non-terminal helpers: `TERMINAL = {"done","failed","cancelled"}`, `is_terminal(state)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_swarm_room.py
def test_message_and_task_roundtrip(tmp_path):
    store = SwarmStore(tmp_path)
    store.append_message(RoomMessage(id="m1", ts=1, author_kind="user", author_id="you",
                                     text="hi", mentions=[], task_id=None, chain_depth=0))
    store2 = SwarmStore(tmp_path)
    store2.load()
    assert [m.id for m in store2.messages] == ["m1"]


def test_last_state_wins(tmp_path):
    # append queued, then running, then done -> tasks["t1"].state == "done"


def test_invalid_transition_is_rejected(tmp_path):
    # done -> running raises ValueError


def test_reconcile_fails_every_nonterminal_task(tmp_path):
    # queued + running -> both failed(interrupted) and one system message appended


def test_parse_mentions_dedupes_and_keeps_order():
    assert parse_mentions("@research 和 @coder，再 @research") == ["research", "coder"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_swarm_room.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `room.py`**

Append one compact JSON line per record (`json.dumps(..., ensure_ascii=False)`) under a `threading.Lock`; replay skips malformed lines. Transition table: `queued→{running,cancelled,failed}`, `running→{done,failed,cancelled}`; anything else raises `ValueError`. `reconcile()` appends a `failed(interrupted)` transition for every non-terminal task plus one `system` room message. Mention regex: `(?<![\w@])@([a-z0-9][a-z0-9._-]*)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_swarm_room.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/room.py tests/test_swarm_room.py
git commit -m "feat(swarm): add append-only room and task registry"
```

---

### Task 4: Router and structural gates

**Files:**
- Create: `zenagent/app/swarm/router.py`
- Test: `tests/test_swarm_router.py`

**Interfaces:**
- Consumes: `SwarmStore`, `RoomMessage`, `TaskRecord`, `parse_mentions`.
- Produces:
  - `@dataclass Admission`: `accepted: list[TaskRecord]`, `rejected: list[tuple[str, str]]` (role id, reason).
  - `class Router`: `__init__(store, role_ids: set[str], *, max_chain_depth: int = 8, max_chain_cost: float = 5.0, inbox_cap: int = 16)`; `admit(message: RoomMessage, *, inbox_sizes: dict[str, int]) -> Admission`; `new_chain_id() -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_swarm_router.py
def test_user_mention_creates_task():
    # admit(msg with mentions=["research"]) -> one task, state queued, chain_depth 1


def test_unknown_mention_creates_nothing():
    # mentions=["nobody"] -> accepted empty, no rejection notice (router only gates)


def test_chain_depth_cap_rejects():
    # message.chain_depth = 8 -> rejected with "chain_depth"


def test_chain_cost_cap_rejects():
    # source task chain_cost = 5.0 -> rejected with "chain_cost"


def test_inbox_cap_rejects():
    # inbox_sizes {"research": 16} -> rejected with "inbox_full"


def test_tasks_keep_message_order():
    # two messages admitted -> task ids in arrival order
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_swarm_router.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `router.py`**

For each known mention: `chain_depth = message.chain_depth + 1`; inherit `chain_id`/`chain_cost` from the task named by `message.task_id` (new chain id otherwise); reject when depth > cap, inherited cost >= cap, or the inbox is full. Unknown mentions are ignored silently (the message already sits in the room). Admission appends `queued` task records to the store and returns them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_swarm_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/router.py tests/test_swarm_router.py
git commit -m "feat(swarm): add mention router with chain and queue gates"
```

---

### Task 5: Protocol block and task runner

**Files:**
- Create: `zenagent/app/swarm/protocol.py`, `zenagent/app/swarm/runner.py`
- Test: `tests/test_swarm_runner.py`

**Interfaces:**
- Consumes: `Agent.prompt/abort/wait_for_idle/termination/messages`, `zenagent.app.compact.maybe_auto_compact/should_auto_compact`, `Session`, `SwarmStore`, `Router`.
- Produces:
  - `PROTOCOL_TEXT: str` — room conventions for the injected message.
  - `build_swarm_post_tool(post: Callable[[str], None]) -> AgentTool` (name `swarm_post`).
  - `injected_prompt(message: RoomMessage, task: TaskRecord) -> str` — sender + task id + chain depth + `PROTOCOL_TEXT` + body.
  - `class TaskRunner`: `__init__(*, store, router, agent, session, role, post_message: Callable[[str, str], None], request_approval: Callable[[str, dict], Awaitable[str]])`; `async run(task: TaskRecord, message: RoomMessage) -> TaskRecord` (returns the terminal record).

- [ ] **Step 1: Write the failing tests** (use `conftest.runtime_factory` + `text_stream`)

```python
# tests/test_swarm_runner.py
async def test_final_text_is_posted_and_task_done(...):
    # runtime with text_stream("结论是 Z"); runner.run -> room has an agent message
    # whose text is "结论是 Z", task state "done", result_message_id set


async def test_mentions_in_result_create_tasks(...):
    # stream text "结论是 Z。@coder 改 Y" -> router admission called with ["coder"]


async def test_max_turns_marks_failed(...):
    # runtime max_turns=1 with a stream that keeps calling a tool -> state "failed",
    # reason "max_turns", system message in room


async def test_abort_marks_cancelled(...):
    # abort() during run -> state "cancelled"


async def test_chain_cost_accumulates(...):
    # assistant message usage cost 0.4 -> returned record chain_cost includes it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_swarm_runner.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `protocol.py` and `runner.py`**

`PROTOCOL_TEXT` states: report the final result; `@role` to delegate; mention the delegator to report back; `swarm_post` for mid-run messages. Runner: append `queued→running`, `await agent.prompt(injected_prompt(...))`, `await agent.wait_for_idle()`, then map `agent.termination`: absent/`"stop"` → `done`; `"aborted"` → `cancelled`; everything else → `failed(reason=termination)`. Auto-post the last assistant text as an agent message (mentions parsed, `task_id` set, `chainDepth = task.chain_depth`), admit it via the router, and set `result_message_id`. On `done`, sum run cost into `chain_cost` by mirroring the usage access in `zenagent/agent/loop.py` `_run_cost` (lines ~440) and call `maybe_auto_compact` when `should_auto_compact(runtime.guard.last_usage, agent.model.context_window)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_swarm_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/protocol.py zenagent/app/swarm/runner.py tests/test_swarm_runner.py
git commit -m "feat(swarm): add task runner and room protocol"
```

---

### Task 6: SwarmManager

**Files:**
- Create: `zenagent/app/swarm/manager.py`
- Test: `tests/test_swarm_manager.py`

**Interfaces:**
- Consumes: `prepare(RuntimeOptions)`, `roles.discover_roles`, `SwarmStore`, `Router`, `TaskRunner`, `records.open_session`/`latest_session` (or a pre-touched file passed as `RuntimeOptions.resume`).
- Produces:
  - `@dataclass SwarmAgent`: `role: RoleManifest`, `runtime: Runtime`, `session: Session`, `agent: Agent`, `state: str`, `current_task: str | None`, `pending_apply: RoleManifest | None`.
  - `class SwarmManager`: `__init__(*, write_frame: Callable[[dict], None] | None = None, stream_fn_factory: Callable[[RoleManifest], StreamFn] | None = None, max_concurrent: int = 3)`; `async start()`, `async close()`, `roles()`, `agents_state()`, `tasks()`, `messages(since: int | None)`, `async post(text) -> list[TaskRecord]`, `async cancel(task_id) -> bool`, `async retry(task_id) -> TaskRecord | None`, `async reset(agent_id) -> bool`, `async update_role(role_id, manifest: dict)`, `async delete_role(role_id)`, `async reload()`, `resolve_approval(request_id, decision) -> bool`, `resolve_ask(request_id, answers) -> bool`.

- [ ] **Step 1: Write the failing tests** (fake `stream_fn_factory`, isolated HOME)

```python
# tests/test_swarm_manager.py
async def test_two_role_chain(...):
    # research stream: "查完了 @coder 改 Y"; coder stream: "改完了 @you 验收"
    # -> tasks: research done, coder done, room has both reports


async def test_cancel_running_task(...):
    # long-running stream, cancel(task_id) -> state "cancelled", run ends


async def test_retry_creates_derived_task(...):
    # retry(failed_id) -> new task with derived_from == failed_id and same source message


async def test_restart_marks_nonterminal_interrupted(tmp_path):
    # pre-seed store with queued+running, manager.start() -> both failed(interrupted)


async def test_role_update_applies_after_run(...):
    # update while running -> SwarmAgent.pending_apply set; after run -> runtime rebuilt


async def test_delete_role_cancels_tasks(...):
    # delete while queued+running -> both cancelled(role_removed), agent stopped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_swarm_manager.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `manager.py`**

Per role: build `RuntimeOptions(root=role.root, model_id=role.model, thinking=role.thinking, mode=role.mode, max_turns=role.max_turns, max_cost=role.max_cost, mcp=..., skills=..., context_files=role.inherit_project, tools_allow=role.tools, extra_tools=[swarm_post], extra_prompt=role.system_prompt + PROTOCOL_TEXT, resume=<agent session path>)`; `prepare(...)` with the injected `stream_fn`. Agent sessions: `state_root()/agents/<id>/sessions/` — reuse the latest file there, else create a fresh timestamped path (touch it) before bootstrap. Scheduler: one run per agent at a time, a global `asyncio.Semaphore(max_concurrent)`, an `asyncio.Event` woken on enqueue/run-end; always pick FIFO. Approval: emit `approval_request` frame with id `swarm:aN`, await a future with 120 s timeout; `"timeout"` → abort run + `failed(approval_timeout)`. Asks: answer `[None,...]` immediately and post a system note (v1 unattended semantics). Frames via `write_frame`: `{"type":"swarm_message","swarm":True,...}`, `{"type":"swarm_task","swarm":True,...}`, `{"type":"swarm_agent","swarm":True,...}`. `start()` = load store → reconcile → discover roles → build agents. `close()` aborts runs, cancels tasks as `failed(interrupted)`, closes runtimes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_swarm_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/manager.py tests/test_swarm_manager.py
git commit -m "feat(swarm): add swarm manager with scheduler and lifecycle"
```

---

### Task 7: RPC family, host wiring, approval routing

**Files:**
- Create: `zenagent/cli/rpc/ops_swarm.py`
- Modify: `zenagent/cli/rpc/host.py` (lazy manager + `swarm`/`approval`/`ask` dispatch), `zenagent/cli/rpc/server.py` only if a shared helper is truly needed
- Modify: `desktop/src/rpc/types.ts` (`REQUEST_KINDS` + result/frame types)
- Test: `tests/test_rpc_swarm.py`; update `tests/test_rpc_contract.py` only if the kind scan needs it

**Interfaces:**
- Consumes: `SwarmManager` public surface (Task 6), `host._send`.
- Produces: `class SwarmOps` on `RpcHost` with `async def _swarm(self, request, request_id) -> None`; host lazily creates `self._swarm = SwarmManager(write_frame=self._send)`; request kinds `swarm`, approval/ask ids prefixed `swarm:`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rpc_swarm.py
async def test_swarm_kind_contract(...):
    # run the existing contract test after adding the kind; REQUEST_KINDS includes 'swarm'


async def test_list_returns_roles_tasks_and_messages(...):
    # host with a fake manager; {"type":"swarm","op":"list"} -> result ok with three keys


async def test_post_creates_tasks(...):
    # {"type":"swarm","op":"post","text":"@research hi"} -> tasks list non-empty


async def test_swarm_frames_are_global(...):
    # captured frames have swarm: true and no project/session keys


async def test_swarm_approval_routes_by_prefix(...):
    # {"type":"approval","request_id":"swarm:a1","decision":"allow"} reaches manager
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rpc_swarm.py tests/test_rpc_contract.py -v`
Expected: FAIL (unknown type `swarm`; kinds mismatch).

- [ ] **Step 3: Implement the RPC surface**

`SwarmOps._swarm` dispatches ops `list/post/cancel/retry/reset/roles/reload` to the manager and replies `{"type":"result","id":...,"ok":true,...}`. In `RpcHost._handle_line` add `if kind == "swarm": await self._swarm(...)` before slot routing, and route `approval`/`ask` to the manager when `request_id` starts with `swarm:`. Create the manager lazily on first swarm request; `RpcHost.close()` closes it. Add `'swarm'` to `REQUEST_KINDS` and the result interfaces (`SwarmListResult`, `SwarmPostResult`, `SwarmOkResult`, `SwarmRolesResult`) plus frame interfaces (`SwarmMessageFrame`, `SwarmTaskFrame`, `SwarmAgentFrame`) in `desktop/src/rpc/types.ts`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rpc_swarm.py tests/test_rpc_contract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/rpc/ops_swarm.py zenagent/cli/rpc/host.py desktop/src/rpc/types.ts tests/test_rpc_swarm.py
git commit -m "feat(swarm): expose the swarm RPC family and approval routing"
```

---

### Task 8: Desktop swarm store

**Files:**
- Create: `desktop/src/state/swarmStore.ts`
- Modify: `desktop/src/state/appStore.ts` (early branch in `handleFrame`)
- Test: `desktop/tests/swarmStore.test.ts`

**Interfaces:**
- Consumes: `useAppStore.getState().client()` / `RpcClient.send`, frames from Task 7.
- Produces: `useSwarmStore` with `{messages, tasks, agents, roles, loaded, post(text), load(), cancel(id), retry(id), reset(agentId), applyFrame(frame)}` and types `SwarmMessage`, `SwarmTask`, `SwarmAgentState`, `SwarmRole`.

- [ ] **Step 1: Write the failing tests** (node --test, fake bridge like `desktop/tests/rpcClient.test.ts`)

```ts
// desktop/tests/swarmStore.test.ts
test('applyFrame appends a message', () => { /* swarm_message -> messages has 1 */ })
test('applyFrame replaces a task by id', () => { /* second swarm_task with same id wins */ })
test('post sends a swarm request', () => { /* client.send called with {type:'swarm',op:'post',text} */ })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && node --test tests/swarmStore.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the store and the frame branch**

Zustand store mirroring `useSessionStore` style. In `appStore.handleFrame`, before any project/session filtering: `if ((frame as {swarm?: boolean}).swarm) { useSwarmStore.getState().applyFrame(frame as never); return }`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && node --test tests/swarmStore.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/swarmStore.ts desktop/src/state/appStore.ts desktop/tests/swarmStore.test.ts
git commit -m "feat(desktop): add the swarm store and global frame branch"
```

---

### Task 9: Workspace mode and sidebar switcher

**Files:**
- Create: `desktop/src/ui/ModeSwitcher.tsx`, `desktop/src/ui/ModeSwitcher.module.css`, `desktop/src/ui/pages/RoomPage.tsx` (stub for this task), `desktop/src/ui/pages/ZenPlaceholder.tsx`
- Modify: `desktop/src/state/stores.ts` (`WorkspaceMode`, field + setter), `desktop/src/ui/Sidebar.tsx` (mount switcher under the brand header, after `Sidebar.tsx:348`), `desktop/src/App.tsx` (route swarm/zen branches)
- Test: `desktop/tests/workspaceMode.test.ts`

**Interfaces:**
- Produces: `export type WorkspaceMode = 'coding' | 'zen' | 'swarm'`; `useSettingsStore.workspaceMode` + `setWorkspaceMode`; persisted under localStorage key `zen.workspaceMode`; `ModeSwitcher` renders three segmented buttons.

- [ ] **Step 1: Write the failing tests**

```ts
// desktop/tests/workspaceMode.test.ts
test('workspaceMode defaults to coding', ...)
test('setWorkspaceMode persists to zen.workspaceMode', ...)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && node --test tests/workspaceMode.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement mode state, switcher, and routing**

Follow the `useSettingsStore` pattern (`stores.ts:582-648`): field, setter with `persist('zen.workspaceMode', value)`, default `'coding'`. In `App.tsx` after the settings branch (`App.tsx:244`): `zen` → `ZenPlaceholder` and `swarm` → `RoomPage`, both wrapped in the same `AppFrame` + `Sidebar` shell so the switcher stays visible; the stub `RoomPage` shows an empty state and a `data-swarm-page` attribute. Keep the switcher a self-contained component (merge boundary with the zen branch).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && node --test tests/workspaceMode.test.ts && pnpm build`
Expected: PASS + clean typecheck.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/stores.ts desktop/src/ui/ModeSwitcher.tsx desktop/src/ui/ModeSwitcher.module.css desktop/src/ui/pages/RoomPage.tsx desktop/src/ui/pages/ZenPlaceholder.tsx desktop/src/ui/Sidebar.tsx desktop/src/App.tsx desktop/tests/workspaceMode.test.ts
git commit -m "feat(desktop): add the coding/zen/swarm workspace switcher"
```

---

### Task 10: Room page UI

**Files:**
- Modify: `desktop/src/ui/pages/RoomPage.tsx` (+ `RoomPage.module.css`), `desktop/src/i18n/messages-swarm.ts` (new), `desktop/src/i18n/messages.ts` (spread), `desktop/src/dev/swarmMock.ts` (new), `desktop/src/dev/bridge.ts` (thin `case 'swarm'`)
- Test: `desktop/e2e/swarm.spec.ts`

**Interfaces:**
- Consumes: `useSwarmStore`, `detectMention` from `state/mentions.ts` (for `@` completion), `useSettingsStore.workspaceMode`, i18n `useT`.
- Produces: room feed with author/status/task pills, composer posting via `swarm post`, agent roster + task strip with cancel/retry/reset, inline approval cards resolving `swarm:` ids.

- [ ] **Step 1: Write the failing e2e test**

```ts
// desktop/e2e/swarm.spec.ts
test('switch to swarm, delegate, and see the chain', async ({ page }) => {
  await boot(page, presetState(page, swarmFixture()))
  await page.getByRole('button', { name: 'Swarm' }).click()
  await page.getByPlaceholder(/message/i).fill('@research 查 X')
  await page.keyboard.press('Enter')
  await expect(page.getByText('task #1')).toBeVisible()
  await expect(page.getByText('结论是 Z')).toBeVisible()
})
test('cancel a running task', ...)  // stop button -> state cancelled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && pnpm exec playwright test e2e/swarm.spec.ts`
Expected: FAIL (no room UI).

- [ ] **Step 3: Implement the room and the dev mock**

`RoomPage` renders the feed (`data-swarm-message`), composer (`data-swarm-composer`), roster/task strip (`data-swarm-task`), and approval cards. `@` uses `detectMention` and inserts `@<role-id>`. i18n: add `swarmZh`/`swarmEn` and spread into `messages.ts`. `dev/swarmMock.ts` returns fixtures for `list/post/cancel/retry/reset/roles` and lets `swarmFixture()` seed messages/tasks; `dev/bridge.ts` gets a one-line `case 'swarm': return handleSwarm(state, payload)` (respect its 998-line allowlist cap — move any logic into `swarmMock.ts`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && pnpm exec playwright test e2e/swarm.spec.ts && node --test tests/locale.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/ui/pages/RoomPage.tsx desktop/src/ui/pages/RoomPage.module.css desktop/src/i18n/messages-swarm.ts desktop/src/i18n/messages.ts desktop/src/dev/swarmMock.ts desktop/src/dev/bridge.ts desktop/e2e/swarm.spec.ts
git commit -m "feat(desktop): add the swarm room page"
```

---

### Task 11: Role management UI

**Files:**
- Modify: `desktop/src/ui/pages/SettingsPage.tsx` (resources group destination), `desktop/src/i18n/messages-swarm.ts`
- Create: `desktop/src/ui/pages/AgentSettings.tsx`, `desktop/src/ui/pages/AgentSettings.module.css`
- Test: `desktop/e2e/swarm.spec.ts` (new case)

**Interfaces:**
- Consumes: swarm `roles` op; `useSwarmStore.roles`.
- Produces: Agents settings destination listing roles with status (`ready` / `needs credentials` / `failed`) and a create/edit form posting `{op:'roles', op2?}` — payload is `{type:'swarm', op:'roles', action:'create'|'update'|'delete', id?, manifest?}` (follow the plugins settings panel pattern).

- [ ] **Step 1: Write the failing e2e test**

```ts
// desktop/e2e/swarm.spec.ts
test('create a role from settings', async ({ page }) => {
  // switch to swarm -> open Settings -> Agents -> fill id/name/prompt -> Save
  // -> roster shows research
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && pnpm exec playwright test e2e/swarm.spec.ts -g "create a role"`
Expected: FAIL.

- [ ] **Step 3: Implement the panel**

Follow `PluginSettings` patterns (list + drawer form + status badges). Form fields: id, name, description, model, prompt (textarea), tools (comma list), root (text), mode (select plan/ask/zen), max turns/cost. Save calls the roles op then refreshes the store. Add i18n keys to both dicts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && pnpm exec playwright test e2e/swarm.spec.ts && node --test tests/locale.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/ui/pages/SettingsPage.tsx desktop/src/ui/pages/AgentSettings.tsx desktop/src/ui/pages/AgentSettings.module.css desktop/src/i18n/messages-swarm.ts desktop/e2e/swarm.spec.ts
git commit -m "feat(desktop): add role management to settings"
```

---

### Task 12: Governance, docs, and full gates

**Files:**
- Modify: `docs/arch-gov/manifest.json` (node `app.swarm` + edges, version+1), `docs/arch-gov/deltas.md`, `docs/arch-gov/health/*` (regenerate), `CHANGELOG.md`
- Create: `docs/guide/swarm.md`
- Generated: `docs/arch-gov/brief.md`, `nodes.md`, `deps.json`, `graph.html`, `docs/architecture-full.html`

**Interfaces:**
- Consumes: the finished code from Tasks 1–11.
- Produces: a governed architecture graph and user docs.

- [ ] **Step 1: Update the manifest**

Add node `app.swarm` in the app cluster with files `zenagent/app/swarm/*` and symbols `RoleManifest`, `SwarmStore`, `Router`, `TaskRunner`, `SwarmManager`; add import edges from `cli.rpc` → `app.swarm` and `app.swarm` → `app.assembly`/`mcp`/`settings`/`plugins` as the real imports show (run `gov_diff` to confirm); bump `version`; append a `deltas.md` entry.

- [ ] **Step 2: Regenerate and verify the graph**

```bash
S=/Users/mac/.agents/skills/arch-gov/scripts
python3 $S/extract.py . -o docs/arch-gov/deps.json
python3 $S/scaffold.py render .
python3 $S/gov_diff.py docs/arch-gov
python3 $S/gov_health.py docs/arch-gov --update-baseline
```

Expected: `gov_diff` exits 0; health report records the new node/edges.

- [ ] **Step 3: Write CHANGELOG and guide**

Add a `### Architecture diagrams` section describing the new node/edges (format: `CHANGELOG.md:166-179`), plus feature bullets for roles, room, tasks, gates, and the switcher. Write `docs/guide/swarm.md`: role manifest fields, room protocol (`@` semantics, report-back rule), gates and defaults, restart behavior, and the difference from zen mode.

- [ ] **Step 4: Run every gate**

```bash
uv run pytest -q
uv run ruff check zenagent tests
python3 scripts/check_architecture.py
cd desktop && pnpm test && pnpm build && pnpm e2e
```

Expected: 0 failures; architecture gate passes; all desktop tests/e2e pass.

- [ ] **Step 5: Commit**

```bash
git add docs/arch-gov docs/architecture-full.html CHANGELOG.md docs/guide/swarm.md
git commit -m "docs(swarm): govern the swarm runtime and document it"
```

---

## Self-Review

- **Spec coverage:** roles §6 → Task 2; room/task storage §7 → Task 3; routing/gates §8 → Tasks 4–6; RPC §10 → Task 7; desktop §11 → Tasks 8–11; governance §14 → Task 12; assembly hooks §9 → Tasks 1 and 6. Non-goals (IM, daemon, per-project rooms, zen internals) have no tasks by design.
- **Interfaces:** `RoleManifest`, `SwarmStore`, `Router`, `TaskRunner`, `SwarmManager`, and the frame/RPC names are consistent across Tasks 2–8; `workspaceMode` and `useSwarmStore` names match between Tasks 8–11.
- **Review Focus tests:** unknown mention → Task 4; mutual mention loop → Task 4 depth cap + Task 6 chain test; restart mid-run → Task 6; budget exhaustion → Task 5; broken manifest/credentials → Task 2.
- **Proportion:** step bodies stay at signatures and assertions; the only near-transcript blocks are the JSONL append rule and the `_recompose` filter line, both decisions the implementer cannot infer.
