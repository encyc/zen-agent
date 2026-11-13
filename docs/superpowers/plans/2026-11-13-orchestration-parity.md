# 编排器分叉修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 zen worker 继承 router 的模型与预算、给它一条 ask 通道，并让 swarm 的 run unit 走同一条 `prepare()` 装配路径。

**Architecture:** 三处都是「把已经存在的接线补齐」，不引入新抽象：`ZenFleet` 多两个构造参数并在 `_build_worker` 里转发给 `RuntimeOptions`；`SwarmManager` 把 `Runtime.bootstrap(...)` 换成 `prepare(..., views=False)`，保留自己的 confirm 与 ask 接线。

**Tech Stack:** Python 3.11+，pytest，asyncio；无新依赖。

**Spec:** `docs/superpowers/specs/2026-11-13-orchestration-parity-design.md`（含十处分叉的证据表与 D1–D4 决策）

## Global Constraints

- Python 3.11+，零新增依赖。
- 测试命令：`.venv/bin/pytest -q <path>::<test>`。
- 不改 kernel：`zenagent/agent/`、`zenagent/providers/`、`zenagent/tools/`。
- 不改 workspace mode 与权限档位的词汇：`plan` / `ask` / `auto`。
- `CHANGELOG.md` 的新条目写在现有 `## Unreleased` 段内，沿用其「加粗结论段 + `### Added`」格式。
- 注释与文档用中文，风格与 `docs/internals/supervisor.md` 一致。

## Review Focus

- **router 没有 chat 时**（headless / SDK 里 `chat=None`）：`_router_model_id()` 必须返回 `None` 而不是抛异常，worker 退回默认模型。
- **测试替身没有 `.runtime`**：`FakeChat` 不带 runtime，取值链必须全程 `getattr`，不能 `chat.runtime.agent.model` 硬取。
- **confirm 的时序**：swarm 换用 `prepare()` 后，`broker.set_confirm(...)` 必须仍在任何 run 之前调用，否则第一个写操作会走默认拒绝。
- **run recorder 幂等**：`prepare()` 内部已调 `attach_run_recorder`，`attach_swarm_run` 必须仍然幂等，不能出现两个 ledger 记录器。
- **无 UI 的 ask**：`ask=None` 时 worker 的 `ask_handler` 保持 `None`，`ask` 工具按原样返回错误——不要伪造一个永远超时的 handler。

---

### Task 1: zen worker 继承 router 的模型与预算

**Files:**
- Modify: `zenagent/app/zen/fleet.py:71-115`（`ZenFleet.__init__`）、`zenagent/app/zen/fleet.py:297-345`（`_build_worker`）
- Modify: `zenagent/cli/rpc/ops_zen.py:194-210`（构造 `ZenFleet`）
- Test: `tests/test_zen_fleet.py`

**Interfaces:**
- Consumes: `zenagent.app.runtime.RuntimeOptions`（`model_id` / `thinking` / `max_cost` / `max_turns` 字段）；`zenagent.config.UserConfig.task_max_turns` / `task_max_cost`
- Produces: `ZenFleet(..., max_cost: float | None = None, max_turns: int | None = None)`；`ZenFleet._router_model_id() -> str | None`；`ZenFleet._router_thinking() -> str | None`

- [ ] **Step 1: 让测试替身记录新增的 options 字段**

在 `tests/test_zen_fleet.py` 的 `FakeWorkerFactory.prepare` 里，把 `self.calls.append(...)` 改成记录四个字段：

```python
self.calls.append(
    {
        "root": str(options.root),
        "resume": options.resume,
        "model_id": options.model_id,
        "thinking": options.thinking,
        "max_cost": options.max_cost,
        "max_turns": options.max_turns,
    }
)
```

- [ ] **Step 2: 写失败的测试**

```python
async def test_worker_inherits_router_model_and_task_budgets(env) -> None:
    env["chat"].runtime = SimpleNamespace(
        agent=SimpleNamespace(
            model=SimpleNamespace(id="router-model"), thinking="high"
        )
    )
    fleet = _fleet_with(env, max_cost=2.5, max_turns=12)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    call = env["factory"].calls[-1]
    assert call["model_id"] == "router-model"
    assert call["thinking"] == "high"
    assert call["max_cost"] == 2.5
    assert call["max_turns"] == 12
```

- [ ] **Step 3: 跑它，确认失败**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py::test_worker_inherits_router_model_and_task_budgets`
Expected: FAIL，`assert None == 'router-model'`

- [ ] **Step 4: 实现**

`ZenFleet.__init__` 增加两个参数（放在 `worker_mode` 之后）：

```python
max_cost: float | None = None,
max_turns: int | None = None,
```

并保存 `self.max_cost = max_cost` / `self.max_turns = max_turns`。

`_build_worker` 里已经有一行 `router_runtime = getattr(self.chat, "runtime", None)`；在它旁边加两个 helper 的调用，并把四个字段传进 `RuntimeOptions(...)`：

```python
def _router_agent(self):
    return getattr(getattr(self.chat, "runtime", None), "agent", None)

def _router_model_id(self) -> str | None:
    return getattr(getattr(self._router_agent(), "model", None), "id", None)

def _router_thinking(self) -> str | None:
    return getattr(self._router_agent(), "thinking", None)
```

```python
prepared = await prepare(
    RuntimeOptions(
        root=self.root,
        resume=handle.path,
        mode=mode,
        profile=profile,
        model_id=self._router_model_id(),
        thinking=self._router_thinking(),
        max_cost=self.max_cost,
        max_turns=self.max_turns,
    ),
    views=False,
)
```

- [ ] **Step 5: 补一个没有 router 的测试**

先让 `_fleet_with` 允许覆盖 chat（现在是写死 `chat=env["chat"]`，直接传 `chat=` 会撞
`TypeError: multiple values`）：把该行改成 `chat=kwargs.pop("chat", env["chat"])`。
`chat=None` 时，取值链必须返回 `None` 而不是抛：

```python
async def test_worker_without_router_falls_back_to_the_default_model(env) -> None:
    fleet = _fleet_with(env, chat=None)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert env["factory"].calls[-1]["model_id"] is None
    assert env["factory"].calls[-1]["thinking"] is None
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py`
Expected: PASS（30 + 2 个）

- [ ] **Step 7: 把旋钮接进 RPC 的 fleet 构造**

`zenagent/cli/rpc/ops_zen.py` 的 `ZenFleet(...)` 里加两行：

```python
max_cost=config.task_max_cost,
max_turns=config.task_max_turns,
```

- [ ] **Step 8: 跑 zen 相关测试**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py tests/test_zen_rpc.py tests/test_zen_runtime.py tests/test_run_scopes.py`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add zenagent/app/zen/fleet.py zenagent/cli/rpc/ops_zen.py tests/test_zen_fleet.py
git commit -m "fix(zen): inherit the router's model and task budgets for workers"
```

---

### Task 2: zen worker 接上 ask 通道

**Files:**
- Modify: `zenagent/app/zen/fleet.py:71-115`（`ZenFleet.__init__`）、`zenagent/app/zen/fleet.py:297-345`（`_build_worker`）
- Modify: `zenagent/cli/rpc/ops_zen.py:194-210`
- Test: `tests/test_zen_fleet.py`

**Interfaces:**
- Consumes: `zenagent.app.runtime.Runtime.ask_handler`（`AskHandler | None`）；Task 1 的 `ZenFleet.__init__` 参数表
- Produces: `ZenFleet(..., ask: Callable | None = None)`；worker 的 `Runtime.ask_handler` 等于该回调

- [ ] **Step 1: 写失败的测试**

```python
async def test_worker_gets_the_fleets_ask_handler(env) -> None:
    async def ask(questions):
        return {}

    fleet = _fleet_with(env, ask=ask)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    runtime = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert runtime.ask_handler is ask


async def test_downgraded_worker_still_has_an_ask_channel(env) -> None:
    async def ask(questions):
        return {}

    fleet = _fleet_with(env, mode="auto", ask=ask)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    runtime = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert env["factory"].modes[str(env["paths"]["w1"])] == "ask"
    assert runtime.ask_handler is ask
```

- [ ] **Step 2: 跑它，确认失败**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py::test_worker_gets_the_fleets_ask_handler`
Expected: FAIL（`_fleet_with(..., ask=ask)` 抛 `TypeError: unexpected keyword argument 'ask'`）

- [ ] **Step 3: 实现**

`ZenFleet.__init__` 增加 `ask: Callable | None = None`，保存为 `self.ask = ask`。

`_build_worker` 在 `prepared = await prepare(...)` 之后、`update_run_scope(...)` 之前加：

```python
if self.ask is not None:
    # Downgraded workers (auto → ask) must be able to reach a human; without
    # a handler the ask tool is a dead end.
    prepared.runtime.ask_handler = self.ask
```

- [ ] **Step 4: 补一个没有 UI 的测试**

`ask=None` 时不要伪造 handler：

```python
async def test_worker_without_ask_channel_keeps_the_handler_empty(env) -> None:
    fleet = _fleet_with(env)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    runtime = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert runtime.ask_handler is None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py`
Expected: PASS

- [ ] **Step 6: 接进 RPC 的 fleet 构造**

`zenagent/cli/rpc/ops_zen.py` 的 `ZenFleet(...)` 里加 `ask=slot.server.ask,`（与已有的 `confirm=slot.server.confirm` 并列）。

- [ ] **Step 7: 跑 zen 相关测试**

Run: `.venv/bin/pytest -q tests/test_zen_fleet.py tests/test_zen_rpc.py tests/test_rpc_supervisor.py`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add zenagent/app/zen/fleet.py zenagent/cli/rpc/ops_zen.py tests/test_zen_fleet.py
git commit -m "fix(zen): give downgraded workers an ask channel"
```

---

### Task 3: swarm 的 run unit 走 prepare()

**Files:**
- Modify: `zenagent/app/swarm/manager.py:600-660`（`_start_agent` 的构造段）
- Test: `tests/test_swarm_manager.py`

**Interfaces:**
- Consumes: `zenagent.app.bootstrap.prepare(options, *, stream_fn=None, assume_yes=False, allow_missing_key=False, views=True) -> PreparedFrontend`（`PreparedFrontend.runtime` / `.broker`）
- Produces: 无新公开接口；`_start_agent` 的运行时现在由 `prepare(views=False)` 产出

- [ ] **Step 1: 写失败的测试**

```python
async def test_run_units_are_built_through_prepare(monkeypatch) -> None:
    _write_role("research", "You research.")
    calls: list[dict] = []
    real = manager_module.prepare

    async def spy(options, **kwargs):
        calls.append(kwargs)
        return await real(options, **kwargs)

    monkeypatch.setattr(manager_module, "prepare", spy)
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [task] = await manager.post("@research hi")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
    finally:
        await manager.close()

    assert calls, "swarm must build run units through prepare()"
    assert calls[0]["views"] is False
    assert calls[0]["allow_missing_key"] is True
```

`tests/test_swarm_manager.py` 顶部需要 `from zenagent.app.swarm import manager as manager_module`。

- [ ] **Step 2: 跑它，确认失败**

Run: `.venv/bin/pytest -q tests/test_swarm_manager.py::test_run_units_are_built_through_prepare`
Expected: FAIL，`AttributeError: module 'zenagent.app.swarm.manager' has no attribute 'prepare'`

- [ ] **Step 3: 实现**

在 `manager.py` 顶部把

```python
from zenagent.app.runtime import Runtime, RuntimeOptions
```

改为保留该行，并新增：

```python
from zenagent.app.bootstrap import prepare
```

把 `_start_agent` 里的构造段：

```python
config = load_user_config(cwd=role.root)
broker = build_broker_for(
    user_config=config,
    mode=role.mode,
    cwd=role.root,
)
key = agent_key(role.id, work_item_id)
broker.set_confirm(self._confirm_callback(key))
```

与

```python
runtime = await Runtime.bootstrap(
    options,
    stream_fn=stream_fn,
    gate=broker,
    allow_missing_key=True,
)
```

替换为：

```python
key = agent_key(role.id, work_item_id)
prepared = await prepare(
    options,
    stream_fn=stream_fn,
    allow_missing_key=True,
    views=False,
)
broker = prepared.broker
broker.set_confirm(self._confirm_callback(key))
runtime = prepared.runtime
```

`config` 变量若在下方仍被使用（`config.task_timeout_s` 传给 `TaskRunner`），保留 `config = load_user_config(cwd=role.root)`；若替换后不再被引用，则删除该行与相应 import。`build_broker_for` 的 import 同理。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/test_swarm_manager.py`
Expected: PASS

- [ ] **Step 5: 跑受影响的面**

Run: `.venv/bin/pytest -q tests/test_swarm_manager.py tests/test_rpc_swarm.py tests/test_rpc_supervisor.py tests/test_dispatch.py tests/test_run_scopes.py`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add zenagent/app/swarm/manager.py tests/test_swarm_manager.py
git commit -m "refactor(swarm): build run units through prepare()"
```

---

### Task 4: 文档与 CHANGELOG

**Files:**
- Modify: `docs/guide/orchestration.md`（新增一节，位置在「组件与事实」之后）
- Modify: `CHANGELOG.md:5`（`## Unreleased` 段首）

**Interfaces:**
- Consumes: 前三项任务的最终行为；Spec 的分叉表
- Produces: 无代码接口

- [ ] **Step 1: 在 orchestration.md 增一节**

标题 `## 两个编排器的分工`，内容三段：

1. 一句话分工：zen = 「派完就算完，结果飘回来」；swarm = 「房间长期在线，谁都能围观」。
2. 一张表：只列**保留**的五处分叉（结果回传、事件可见性、超时收尾、生命周期、并发），每行写「为什么这样」。
3. 一句指针：`zen` worker 继承 router 的模型与 `[task]` 预算、且降级到 `ask` 时通过 `slot.server.ask` 问人；swarm 的 run unit 与所有前端一样走 `prepare()`。

- [ ] **Step 2: 写 CHANGELOG**

在 `## Unreleased` 段首加一段加粗结论（与现有段落风格一致）：

```markdown
**Zen workers now run on the router's model and can ask a human.** A worker used
to fall back to the configured default model — so a router switched with `/model`
spawned workers on a different brain — and carried no cost cap. Workers now
inherit the router's model and thinking, take `[task]` budgets, and get the
slot's ask channel; `auto` still downgrades to `ask`, but no longer lands in a
tool call that can neither run nor ask. Swarm run units now assemble through
`prepare()`, the same path as every frontend.
```

- [ ] **Step 3: 验证只有文档变化**

Run: `git diff --stat`
Expected: 只有 `docs/guide/orchestration.md` 与 `CHANGELOG.md`

- [ ] **Step 4: 跑全量测试**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add docs/guide/orchestration.md CHANGELOG.md
git commit -m "docs: record the orchestration split and the worker parity fixes"
```
