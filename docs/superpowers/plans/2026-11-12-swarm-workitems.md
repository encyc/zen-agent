# Swarm 工单内核 Implementation Plan（Phase 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一次协作成为一个可续跑的工单：同工单多轮共享一份上下文，不同工单互相隔离，人和 Agent 都能开单，并给出一个可看的工单面板。

**Architecture:** 工单是房间线程 + 一张 append-only 薄表（`work_items.jsonl`，与 `tasks.jsonl` 同一套重放器）。`TaskRecord` 增加 `workItemId` / `runCost`，任务降级为工单里的一"轮"。运行单元从 `role` 改为 `(role, workItem)`：`SwarmManager.agents` / `_running` 换键，session 按 `(role, workItem)` 落盘，链在工单边界重基。角色名册改由 `discover_roles` 供源，实例懒建 + 空闲回收。

**Tech Stack:** Python 3.11+（app 层纯 stdlib：asyncio + JSONL）、pytest、TypeScript/React 桌面（node --test + Playwright）。

**Spec:** `docs/superpowers/specs/2026-11-12-swarm-agent-identity-workitems-design.md`

**范围**：本计划只做 Phase 1。KPI/打分/档案页是 Phase 2，自升级门禁 + fence + knowbase 是 Phase 3，各自单独出计划。

## Global Constraints

- Worktree：`/Users/mac/zen-agent/.worktrees/swarm-workitems`，分支 `feat/swarm-workitems`。所有命令在该目录执行。
- `zenagent/app/swarm/` 纯 stdlib；不改 `zenagent/agent/`；无新三方依赖。
- 分层（`tests/test_layering.py`）：app 不得 import `zenagent.cli`；无循环 import。
- 文件预算（`scripts/check_architecture.py`）：单文件 ≤800 行，allowlist **只许降**。`zenagent/app/swarm/manager.py` 已顶格 851 行——**不得增长**，新逻辑进新文件，可移出的代码移出。
- RPC 契约双向钉住（`tests/test_rpc_contract.py`）：新帧必须同时出现在 `desktop/src/rpc/types.ts` + `desktop/src/rpc/types-swarm.ts`。
- 每个 swarm 帧带 `swarm: true` 与 `project`（由 `ops_swarm._swarm_manager` 统一打）。
- append-only：新表同 `room.jsonl` / `tasks.jsonl` 的约定，同一 id 最后一条胜出，损坏行跳过并记 warning，绝不抛。
- 记录形状只增字段：旧 `tasks.jsonl` 无 `workItemId` / `runCost` 必须能重放（按 `None` / `0.0`）。
- i18n 双语对齐（`desktop/tests/locale.test.ts`）。
- 每完成一个 task 提交一次，`feat(swarm): ...`。

## Review Focus

1. **旧房间数据升级后首次启动**：`tasks.jsonl` 无 `workItemId`，必须全部落进 ad-hoc 分组，面板可看，房间不报错、不丢任务。
2. **两个工单同时 @ 同一个 Agent**：两个实例并行跑，各自 session 互不可见，A 工单的对话不出现在 B 工单的上下文里。
3. **Agent 在一条已烧到 4.5 美元的链上开新工单**：新工单第一轮必须拿到新链（depth 1、cost 0），不被旧链预算拒掉。
4. **引用一条已关闭工单里的消息再 @**：开新工单并记 `continuedFrom`，旧工单与分数不动。
5. **启动时 role 懒建**：Settings 页、@ 补全、Router 的 `role_ids` 必须依然列出全部角色；不得出现"`@role` 被静默丢弃"。
6. **工单整体归档后详情面板**：靠磁盘解析器还原轮次，不依赖活的 runtime。

---

### Task 1: WorkItem 记录与 store

**Files:**
- Create: `zenagent/app/swarm/workitems.py`
- Create: `tests/test_swarm_workitems.py`

**Interfaces:**
- Produces:
  - `@dataclass WorkItem: id: str; root_message_id: str; title: str; opened_by_kind: str; opened_by_id: str; opened_at: int; state: str = "open"; score: int | None = None; score_note: str | None = None; closed_at: int | None = None; merged_into: str | None = None; opened_from: str | None = None; continued_from: str | None = None; updated_at: int = 0`
  - `WORK_STATES: tuple[str, str, str] = ("open", "review", "closed")`
  - `class WorkItemStore(store: SwarmStore)`，复用 `store.root` / `store.new_id("w")` / `store._append_line` / `store._replay`
  - `WorkItemStore.load() -> None`、`items: dict[str, WorkItem]`、`warnings: list[str]`
  - `WorkItemStore.open(root_message_id, title, *, opened_by_kind, opened_by_id, opened_from=None, continued_from=None) -> WorkItem`
  - `WorkItemStore.update(work_item_id, **fields) -> WorkItem`（校验状态转换：`open → review`、`review → open|closed`、`closed` 终态）
  - `WorkItemStore.score(work_item_id, score: int | None, note: str | None) -> WorkItem`（`score` ∈ 1..5 或 `None`；允许 `closed` 之后补分）
  - `WorkItemStore.merge(source_id, target_id) -> WorkItem`（只写 `merged_into`，不搬 session）
  - `_work_item_record(item: WorkItem) -> dict` / `_apply_work_item(raw) -> WorkItem | None`

- [x] **Step 1: 写失败测试**

```python
# tests/test_swarm_workitems.py
import json

import pytest

from zenagent.app.swarm.room import SwarmStore
from zenagent.app.swarm.workitems import WorkItemStore


def _store(tmp_path):
    room = SwarmStore(tmp_path)
    items = WorkItemStore(room)
    items.load()
    return room, items


def test_open_creates_open_item(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "修好上传超时", opened_by_kind="user", opened_by_id="you")
    assert item.state == "open"
    assert item.root_message_id == "m1"
    assert items.items[item.id] == item


def test_review_then_closed_transition(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    item = items.update(item.id, state="review")
    assert item.state == "review"
    item = items.update(item.id, state="closed", closed_at=123)
    assert item.state == "closed"


def test_closed_is_terminal(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="closed")
    with pytest.raises(ValueError):
        items.update(item.id, state="open")


def test_score_allowed_after_close_and_nullable(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="closed")
    item = items.score(item.id, 4, "还行")
    assert (item.score, item.score_note) == (4, "还行")
    with pytest.raises(ValueError):
        items.score(item.id, 9, None)


def test_load_replays_and_last_wins(tmp_path):
    room, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="review")
    again = WorkItemStore(SwarmStore(tmp_path))
    again.load()
    assert again.items[item.id].state == "review"


def test_malformed_line_is_skipped_with_warning(tmp_path):
    (tmp_path / "work_items.jsonl").write_text("{not json\n", encoding="utf-8")
    room = SwarmStore(tmp_path)
    items = WorkItemStore(room)
    items.load()
    assert items.items == {}
    assert items.warnings
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/zen-agent/.worktrees/swarm-workitems && python -m pytest tests/test_swarm_workitems.py -v`
Expected: FAIL — `ModuleNotFoundError: zenagent.app.swarm.workitems`

- [x] **Step 3: 实现 `zenagent/app/swarm/workitems.py`**

照 `room.py` 的写法：`_work_item_record` 输出的键用 camelCase（`rootMessageId` / `openedBy` / `scoreNote` / `closedAt` / `mergedInto` / `openedFrom` / `continuedFrom` / `updatedAt`），`_apply_work_item` 反向解析，坏行返回 `None` 并 push warning。`update` 用 `dataclasses.replace` 追加一条同 id 记录。状态机与报错文案：

```python
_WORK_TRANSITIONS = {
    "open": {"review", "closed"},
    "review": {"open", "closed"},
    "closed": set(),
}
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_swarm_workitems.py -v`
Expected: PASS（6 passed）

- [x] **Step 5: Commit**

```bash
git add zenagent/app/swarm/workitems.py tests/test_swarm_workitems.py
git commit -m "feat(swarm): work item record and append-only store"
```

---

### Task 2: TaskRecord 增加 workItemId 与 runCost

**Files:**
- Modify: `zenagent/app/swarm/room.py`（`TaskRecord`、`_task_record`、`_apply_task`）
- Modify: `zenagent/app/swarm/runner.py:98-110`（填 `runCost`）
- Test: `tests/test_swarm_room.py`（追加）

**Interfaces:**
- Produces: `TaskRecord.work_item_id: str | None = None`、`TaskRecord.run_cost: float = 0.0`；`_task_record` 输出 `workItemId` / `runCost`；`_apply_task` 读回（缺失 → `None` / `0.0`）。
- 后续任务用 `task.work_item_id` 做调度键，用 `run_cost` 做 KPI 求和（**不用** `chain_cost`）。

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_swarm_room.py
def test_task_roundtrip_keeps_work_item_and_run_cost():
    task = _task(work_item_id="w1", run_cost=0.25)
    raw = _task_record(task)
    assert raw["workItemId"] == "w1"
    assert raw["runCost"] == 0.25


def test_legacy_task_line_has_no_work_item(tmp_path):
    (tmp_path / "tasks.jsonl").write_text(
        json.dumps({"type": "task", "id": "t1", "assignee": "a", "state": "queued"}) + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()
    assert store.tasks["t1"].work_item_id is None
    assert store.tasks["t1"].run_cost == 0.0
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_swarm_room.py -k "work_item or run_cost" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'work_item_id'`

- [x] **Step 3: 加字段并在 runner 里填值**

`runner.py` 的终态 `replace(...)` 里加 `run_cost=round(_run_cost(new_messages), 6)`（与既有 `chain_cost` 累加同一处，别重复算）。

- [x] **Step 4: 跑全量 room + runner 测试**

Run: `python -m pytest tests/test_swarm_room.py tests/test_swarm_runner.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add zenagent/app/swarm/room.py zenagent/app/swarm/runner.py tests/test_swarm_room.py
git commit -m "feat(swarm): task records carry workItemId and runCost"
```

---

### Task 3: session 按 `(role, work item)` 落盘

**Files:**
- Modify: `zenagent/app/swarm/roles.py:118-140`（`role_session_path`）
- Test: `tests/test_swarm_roles.py`（追加）

**Interfaces:**
- Produces: `role_session_path(role_id, *, root, work_item_id: str | None = None, fresh: bool = False) -> Path`
  - `work_item_id` 非空 → `~/.zenagent/projects/<key>/swarm/workitems/<wi>/agents/<role>/sessions/<stamp>.jsonl`
  - `None` → 保持今天的 `.../swarm/agents/<role>/sessions/<stamp>.jsonl`（ad-hoc 历史仍可 resume，不搬目录）

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_swarm_roles.py
def test_session_path_is_scoped_per_work_item(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = role_session_path("research", root=tmp_path, work_item_id="w1")
    b = role_session_path("research", root=tmp_path, work_item_id="w2")
    again = role_session_path("research", root=tmp_path, work_item_id="w1")
    assert a.parent != b.parent
    assert a == again          # 同工单复用同一份
    assert a.parent.parent.name == "w1"


def test_session_path_without_work_item_keeps_legacy_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = role_session_path("research", root=tmp_path)
    assert "workitems" not in path.parts
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_swarm_roles.py -k "work_item" -v`
Expected: FAIL — `TypeError: role_session_path() got an unexpected keyword argument 'work_item_id'`

- [x] **Step 3: 实现**

`_latest_session` 与新建逻辑不变，只把目录算成：

```python
base = project_state_root(root)
directory = (
    base / "workitems" / work_item_id / "agents" / role_id / "sessions"
    if work_item_id else base / "agents" / role_id / "sessions"
)
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_swarm_roles.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add zenagent/app/swarm/roles.py tests/test_swarm_roles.py
git commit -m "feat(swarm): per-work-item role session paths"
```

---

### Task 4: Router 带工单、并在工单边界重基链

**Files:**
- Modify: `zenagent/app/swarm/router.py`
- Test: `tests/test_swarm_router.py`（追加）

**Interfaces:**
- Consumes: `WorkItemStore`（Task 1）、`TaskRecord.work_item_id`（Task 2）
- Produces:
  - `Router.__init__(store, role_ids, *, work_items: WorkItemStore, max_chain_depth, max_chain_cost, inbox_cap)`
  - `Router.admit(message, *, inbox_sizes, work_item_id: str | None)`：任务带 `work_item_id`；busy/inbox 键在 manager 侧，签名不变的部分保持兼容（`work_item_id` 有关键字默认值 `None`，让既有测试不改也可跑）
  - `Router.new_chain_id()` 不变

规则：
- `source = store.tasks.get(message.task_id)`；`chain_id = source.chain_id if source else new`。
- **重基**：当 `work_item_id` 非空且 `source is not None` 且 `source.work_item_id != work_item_id` → `chain_id = new_chain_id()`、`chain_depth = 1`、`chain_cost = 0.0`。

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_swarm_router.py
def test_task_carries_work_item(tmp_path):
    store = SwarmStore(tmp_path)
    items = WorkItemStore(store)
    items.load()
    router = Router(store, {"research"}, work_items=items)
    admission = router.admit(_message(mentions=["research"]), inbox_sizes={}, work_item_id="w1")
    assert admission.accepted[0].work_item_id == "w1"


def test_crossing_work_items_rebases_the_chain(tmp_path):
    store = SwarmStore(tmp_path)
    items = WorkItemStore(store)
    items.load()
    router = Router(store, {"research", "coder"}, work_items=items)
    first = router.admit(_message(mentions=["research"]), inbox_sizes={}, work_item_id="w1").accepted[0]
    spent = replace(first, state="done", chain_depth=3, chain_cost=4.5)
    store.append_task(spent)
    follow = _message(id="m2", mentions=["coder"], task_id=first.id)
    task = router.admit(follow, inbox_sizes={}, work_item_id="w2").accepted[0]
    assert task.chain_id != spent.chain_id
    assert task.chain_depth == 1
    assert task.chain_cost == 0.0


def test_same_work_item_keeps_the_chain(tmp_path):
    store = SwarmStore(tmp_path)
    items = WorkItemStore(store)
    items.load()
    router = Router(store, {"research", "coder"}, work_items=items)
    first = router.admit(_message(mentions=["research"]), inbox_sizes={}, work_item_id="w1").accepted[0]
    store.append_task(replace(first, state="done", chain_depth=2, chain_cost=1.0))
    task = router.admit(_message(id="m2", mentions=["coder"], task_id=first.id),
                        inbox_sizes={}, work_item_id="w1").accepted[0]
    assert task.chain_id == first.chain_id
    assert task.chain_depth == 3
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_swarm_router.py -k "work_item or rebase" -v`
Expected: FAIL — `TypeError: __init__() missing keyword-only argument 'work_items'`

- [x] **Step 3: 实现重基规则**

- [x] **Step 4: 跑测试确认通过（含既有 router 测试全部，确保默认参数兼容）**

Run: `python -m pytest tests/test_swarm_router.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add zenagent/app/swarm/router.py tests/test_swarm_router.py
git commit -m "feat(swarm): route tasks into work items and rebase chains across them"
```

---

### Task 5: Manager 换键为 `(role, work item)`，名册与实例解耦

**Files:**
- Create: `zenagent/app/swarm/instances.py`（实例索引 + 懒建 + 空闲回收，纯逻辑，便于单测）
- Modify: `zenagent/app/swarm/manager.py`（薄钩子：换键 + 调用 instances）
- Modify: `zenagent/app/swarm/frames.py`（`_emit_agent` 带 `workItem`，新增 `_emit_work`）
- Test: `tests/test_swarm_manager.py`（追加）

**Interfaces:**
- Consumes: Task 1–4
- Produces:
  - `SwarmManager.work_items: WorkItemStore`（Task 1 的类型，在 `start()` 里 load）
  - `SwarmManager.role(role_id) -> RoleManifest`（按 id 取已发现角色，未知 id 抛 `KeyError`）
  - `SwarmManager.agents: dict[tuple[str, str | None], SwarmAgent]`
  - `SwarmAgent.work_item_id: str | None`（构造参数）
  - `SwarmManager.roles() -> list[dict]` **改为**由 `discover_roles(self.root)` 供源，`supportsVision` 从已建实例或 catalog 取安全缺省
  - `SwarmManager.role_ids` 来自 `discover_roles`（不是 `self.agents`）
  - `agents_state()` 每实例一行，含 `workItem`
  - `instances.py: class InstancePool`：`get(role, work_item_id)`、`put(...)`、`idle_keys(now, ttl_s)`、`discard(key)`

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_swarm_manager.py
async def test_roles_listed_before_any_instance_is_built(manager):
    # 懒建：一个实例都没有，名册仍完整
    manager.agents.clear()
    assert [role["id"] for role in manager.roles()] == ["coder", "research"]


async def test_two_work_items_same_agent_get_two_instances(manager):
    a = await manager._try_build_agent(manager.role("research"), work_item_id="w1")
    b = await manager._try_build_agent(manager.role("research"), work_item_id="w2")
    assert a.session.path != b.session.path
    assert manager.agents[("research", "w1")] is a
    assert manager.agents[("research", "w2")] is b


async def test_agents_state_carries_work_item(manager):
    await manager._try_build_agent(manager.role("research"), work_item_id="w1")
    rows = [row for row in manager.agents_state() if row["agent"] == "research"]
    assert rows[0]["workItem"] == "w1"
```

（`manager` fixture 参照本文件已有的写法；`role(id)` 是本任务新增的小查询方法。）

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_swarm_manager.py -k "work_item or before_any_instance" -v`
Expected: FAIL

- [x] **Step 3: 实现换键**

要点（逐个函数改，语义不变）：
- `_try_build_agent(role, *, work_item_id)` → `_build_agent(..., work_item_id=...)` → `role_session_path(..., work_item_id=...)`、`RuntimeOptions(...)` 不变、`SwarmAgent(..., work_item_id=...)`。
- `_next_entry`：`busy = set(self._running) | set(self._applying)`；键 `(task.assignee, task.work_item_id)`。
- `_inbox_sizes`：键 `(task.assignee, task.work_item_id)`。
- `_execute` / `cancel` / `reset` / `retry` / `_apply_role` / `delete_role` / `close`：全部按新键遍历；`_apply_role` 对该 role 的**所有**实例生效。
- `_post_from`：来源消息的 `task_id` 取实例上的 `current_task`，`chain_depth` 取来源任务。
- `roles()` / `role_ids`：改由 `discover_roles(self.root)`；`start()` 不再预建实例。
- `_emit_agent(key)`：多一个 `"workItem"` 字段。
- 空闲回收：在 `_schedule` 的等待分支里按 `InstancePool.idle_keys()` 关闭 runtime（`ttl` 读 `[swarm] work_item_runtime_idle_s`，缺省 600）。

- [x] **Step 4: 跑 swarm 全量**

Run: `python -m pytest tests/test_swarm_manager.py tests/test_rpc_swarm.py -v`
Expected: PASS

- [x] **Step 5: 检查行数预算**

Run: `python scripts/check_architecture.py`
Expected: PASS，且 `manager.py` ≤ 851（不增长；若增长，先把 `_schedule`/`_execute` 抽到 `instances.py` 或 `scheduler.py`）

- [x] **Step 6: Commit**

```bash
git add zenagent/app/swarm/ zenagent/app/swarm/manager.py tests/test_swarm_manager.py
git commit -m "feat(swarm): run units are (role, work item); roster decoupled from instances"
```

---

### Task 6: 开单 / 续单 / 请求关闭的接线

**Files:**
- Modify: `zenagent/app/swarm/manager.py`（`post()` 决定工单归属、`_admit` 传 `work_item_id`）
- Modify: `zenagent/app/swarm/protocol.py`（`build_work_tools()`：`work_open`、`work_request_close`）
- Test: `tests/test_swarm_manager.py`、`tests/test_swarm_protocol.py`（追加）

**Interfaces:**
- Produces:
  - `SwarmManager.post(text, *, ref=None, images=None) -> list[TaskRecord]` 内部解析工单归属（规则见 spec §5.2）
  - `SwarmManager.work_open(reason, *, opened_by) -> WorkItem`、`manager.request_close(work_item_id, note) -> WorkItem`
  - `build_work_tools(open_item, request_close) -> list[AgentTool]`
- 规则：
  - 人的 `@` 不引用 → 开新工单（标题取消息首行，≤80 字符）。
  - 引用某条消息 → 找该消息所属工单：`closed` → 开新工单并记 `continued_from`；`open`/`review` → 加入。
  - 引用消息属 ad-hoc（无工单）→ 任务的 `work_item_id = None`。
  - Agent 的 `@` → 用该实例绑定的工单；调 `work_open(reason)` 才开新单，卡片即根消息，记 `opened_from`；幂等：已有未使用的新单则复用。

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_swarm_manager.py
async def test_first_human_mention_opens_a_work_item(manager):
    tasks = await manager.post("@research 查一下竞品")
    item = manager.work_items.items[tasks[0].work_item_id]
    assert item.state == "open"
    assert item.opened_by_kind == "user"


async def test_quoting_a_closed_item_opens_a_continuation(manager):
    first = await manager.post("@research 查竞品")
    item_id = first[0].work_item_id
    manager.work_items.update(item_id, state="closed")
    again = await manager.post("@research 再补一条", ref=first[0].source_message_id)
    assert again[0].work_item_id != item_id
    assert manager.work_items.items[again[0].work_item_id].continued_from == item_id


async def test_agent_mention_joins_the_current_work_item(manager):
    first = await manager.post("@research 查竞品")
    item_id = first[0].work_item_id
    delegate = await manager._post_from("research", "@coder 帮我跑一下")
    assert delegate.work_item_id == item_id
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_swarm_manager.py -k "work_item or continuation" -v`
Expected: FAIL

- [x] **Step 3: 实现**

`post()` 在构造 `RoomMessage` 之后、`_admit` 之前解析工单；`_admit` 接 `work_item_id`。卡片消息用 `author_kind="system"`，`text` 形如 `工单已开：<title>（由 @research 发起：<reason>）`。

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_swarm_manager.py tests/test_swarm_protocol.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add zenagent/app/swarm/manager.py zenagent/app/swarm/protocol.py tests/
git commit -m "feat(swarm): open, join and close-request a work item"
```

---

### Task 7: 离线 transcript 解析器

**Files:**
- Modify: `zenagent/app/swarm/transcript.py`
- Test: `tests/test_swarm_transcript.py`（追加）

**Interfaces:**
- Produces: `read_task_transcript_from_disk(sessions_dir: Path, task_id: str) -> dict | None`（扫目录里每个 `*.jsonl`，找含该 task id 的记录；找不到返回 `None`）
- `SwarmManager.task_transcript(task_id)`：先试活实例，失败回落到磁盘路径（由 `(assignee, work_item_id)` 推 sessions 目录）。

- [ ] **Step 1: 写失败测试**

```python
def test_transcript_found_on_disk_without_a_live_agent(tmp_path):
    session = tmp_path / "agents" / "research" / "sessions"
    session.mkdir(parents=True)
    (session / "a.jsonl").write_text('{"task":"t1","text":"hi"}\n', encoding="utf-8")
    (session / "b.jsonl").write_text('{"task":"t2","text":"yo"}\n', encoding="utf-8")
    found = read_task_transcript_from_disk(session, "t2")
    assert found is not None and found["task"] == "t2"


def test_transcript_absent_returns_none(tmp_path):
    assert read_task_transcript_from_disk(tmp_path, "nope") is None
```

- [ ] **Step 2: 跑测试确认失败** → FAIL（`ImportError`）
- [ ] **Step 3: 实现**（复用现有行解析；损坏行跳过）
- [ ] **Step 4: 跑测试确认通过** → `python -m pytest tests/test_swarm_transcript.py -v`
- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/transcript.py tests/test_swarm_transcript.py
git commit -m "feat(swarm): resolve a task transcript from disk when no runtime is live"
```

---

### Task 8: RPC 面 + 帧 + 桌面类型

**Files:**
- Modify: `zenagent/cli/rpc/ops_swarm.py`（`works` 请求族）
- Modify: `zenagent/app/swarm/frames.py`（`_emit_work` → `swarm_work` 帧）
- Modify: `desktop/src/rpc/types-swarm.ts`、`desktop/src/rpc/types.ts`
- Test: `tests/test_rpc_swarm.py`、`tests/test_rpc_contract.py`

**Interfaces:**
- Produces:
  - 请求：`{kind: "swarm", op: "works", action: "list" | "detail" | "score" | "close" | "reopen" | "merge" | "rename", ...}`
  - `list` → `{works: SwarmWork[], adhoc: SwarmTask[]}`
  - `detail` → `{work: SwarmWork, rounds: SwarmTask[]}`
  - `score` → `{work, ok: true}`（`score` 1..5 或 `null`）
  - 帧：`{type: "swarm_work", swarm: true, ...SwarmWork}`
  - TS：`interface SwarmWork { id; title; state: 'open'|'review'|'closed'; rootMessageId; openedBy: SwarmAuthor; openedAt; closedAt: number|null; score: number|null; scoreNote: string|null; mergedInto: string|null; continuedFrom: string|null }`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_rpc_swarm.py
async def test_works_list_returns_open_items(host):
    await post(host, "@research 查竞品")
    frame = await request(host, {"kind": "swarm", "op": "works", "action": "list"})
    assert frame["ok"] is True
    assert len(frame["works"]) == 1
    assert frame["works"][0]["state"] == "open"


async def test_score_is_validated(host):
    item_id = (await request(host, {"kind": "swarm", "op": "works", "action": "list"}))["works"][0]["id"]
    bad = await request(host, {"kind": "swarm", "op": "works", "action": "score", "workId": item_id, "score": 9})
    assert bad["ok"] is False and bad["code"] == "swarm_invalid"
```

```python
# tests/test_rpc_contract.py 的帧清单里加入 "swarm_work"
```

- [ ] **Step 2: 跑测试确认失败** → FAIL（未知 op / 缺帧）
- [ ] **Step 3: 实现**（`works` 分支照 `roles` 的写法；新帧走 `self._write({..., "swarm": True})`；TS 两个文件同步）
- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_rpc_swarm.py tests/test_rpc_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/rpc/ops_swarm.py zenagent/app/swarm/frames.py desktop/src/rpc/ tests/
git commit -m "feat(swarm): works RPC family and swarm_work frame"
```

---

### Task 9: 工单面板（只读 + 打分）

**Files:**
- Create: `desktop/src/ui/SwarmWorks.tsx`、`desktop/src/ui/SwarmWorks.module.css`
- Modify: `desktop/src/ui/SwarmSidebar.tsx`（roster / works 两个 tab）
- Modify: `desktop/src/state/swarmStore.ts`、`desktop/src/i18n/messages-swarm.ts`
- Test: `desktop/tests/swarmWorks.test.tsx`（node --test，参照本目录既有写法）+ `desktop/tests/locale.test.ts`

**Interfaces:**
- Consumes: Task 8 的 `works` 请求与 `swarm_work` 帧
- Produces: store 上 `works: SwarmWork[]`、`loadWorks()`、`scoreWork(id, score, note)`、`closeWork(id)`

- [ ] **Step 1: 写失败测试** —— 列表渲染（有 open / review / closed 三态徽标）、选中详情显示轮次、打分后状态更新、ad-hoc 分组不出现打分按钮。
- [ ] **Step 2: 跑测试确认失败** → `cd desktop && node --test tests/swarmWorks.test.tsx`
- [ ] **Step 3: 实现**（沿用 `SwarmPanels.module.css` 的类名习惯；i18n 键 `swarm.works*` 双语）
- [ ] **Step 4: 跑测试确认通过** → `node --test tests/` 与 `python -m pytest tests/test_rpc_contract.py -v`
- [ ] **Step 5: Commit**

```bash
git add desktop/ && git commit -m "feat(desktop): swarm work item panel"
```

---

### Task 10: 架构账本与文档

**Files:**
- Modify: `docs/arch-gov/manifest.json`（`app.swarm` 的描述加"工单"）
- Modify: `CHANGELOG.md`（架构小节）
- Modify: `docs/architecture/allowlist.json`（若某文件缩小则下调其上限）

- [ ] **Step 1:** `python scripts/check_architecture.py` 全绿，必要时下调 allowlist 数字
- [ ] **Step 2:** 更新 manifest 描述与 CHANGELOG
- [ ] **Step 3:** `python -m pytest tests/ -x -q` 全量
- [ ] **Step 4: Commit**

```bash
git add docs/ CHANGELOG.md && git commit -m "docs(arch): record swarm work items in the architecture ledger"
```

---

## Phase 2 / 3（另立计划）

- **Phase 2 · KPI 与档案页**：`app/swarm/kpi.py` 纯聚合（完成率 / 返工率 / 单工单 `runCost` 与时长 / 已评样本平均分 + 样本数）、RPC `metrics`、Settings → Swarm 的档案页。依赖本计划全部落地。
- **Phase 3 · 自升级与 knowbase**：`app/swarm/fence.py`（path-deny：项目 agents 目录 + 全局库，覆盖 `write_file` / `edit_file` / shell 写模式）、`build_broker_for` 注入、`proposals.jsonl` / `receipts.jsonl`、三个自升级工具 + 补丁白名单、`plan` 模式拒绝规则、`knowbase/` 发现与注入、对账（manifest 级 + 文件指纹级）。与本计划独立，可并行开工。
