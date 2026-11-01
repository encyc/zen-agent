# TUI 待发队列与插话实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 运行中输入的文本有一个可见的待发区（`排队` / `插话` 两组），并可用 Ctrl+S 或点击排队行把消息升格为插话（本轮下一个边界注入、不打断）；顺带补掉「插话滞留到下一次提交」和「待发跨会话泄漏」两个洞。

**Architecture:** 待发模型放在 `zenagent/app`：`PendingMessage`（`views.py`）+ `ChatController._pending`，经 `RunStatus.pending` 交给 TUI；TUI 新增 `PendingBand`（StatusBand 与输入行之间），内核只加 `Agent.take_steering()`。run 结束时的归置（延续运行 / 降级回排队 / 压缩顺序）全部在 `ChatController._run` 的 finally 完成，`agent/loop.py` 不改。

**Tech Stack:** Python 3.11+、Textual 8.2、rich、pytest + Textual `run_test()`；无新依赖。

**Spec:** `docs/superpowers/specs/2026-11-01-tui-pending-queue-design.md`

**工作树：** `.agents/worktrees/feat/tui-pending-queue`（分支 `feat/tui-pending-queue`，基线 `c6bfd1b`，baseline 1704 tests 全绿）。

## Global Constraints

- `zenagent/app/**` 不得 import `textual` / `rich` / `prompt_toolkit`。
- 内核只改 `zenagent/agent/agent.py`（加 `take_steering()`）；`zenagent/agent/loop.py` 一行不改。
- 键位语义固定，不新增 Ctrl+Enter：运行中 Enter=排队；Ctrl+S=插话/升格最新排队；Alt+↑=取回最后一条排队；Ctrl+X=清空两条 lane；Esc=中断。
- 待发区最多 6 条消息行，空时 `display=False`；底部输入行贴底、`NARROW_WIDTH = 100` 规则不变。
- i18n zh/en 同 key 集；默认 locale 是 `en`，测试断言英文文案。
- `RunStatus.follow_ups / steering` 计数、状态栏 `queued` 段、侧栏 Runtime 行保留（回答"有没有"；待发区回答"是什么"）。
- 每个任务结束 `bash scripts/check.sh` 必须绿（ruff + 全量 pytest，约 85s）。

## Review Focus

1. 运行中连续提交两条再 Alt+↑：取回的是最后一条、待发区少一条、剩下那条仍会在本轮结束后发送。（T2/T7）
2. 工具轮进行中插话：tool 结果回来后、下一次模型请求前注入；transcript 出现一条（多行合并的）用户消息，待发区镜像同步消失。（T3）
3. 本轮正常结束（`stop`）后才发现还有未送达插话：自动续跑一轮，把插话作为下一次请求的输入，不滞留、不重复。（T4）
4. provider 报错或 Esc 中断时仍有未送达插话：降级为可见的排队条目；`aborted` 不自动重跑、错误横幅不被吞。（T4）
5. 会话切换 / `/new` / `/undo` / `/fork` / `/retry` 时清空待发并留提示；手动/自动压缩不清空。（T5）
6. 90 列 + 侧栏开合、带图片条目、超过 6 条折叠时：待发区不溢出、点击行号与条目一致、带图片条目拒绝升格。（T6/T7）

## 文件结构

```
zenagent/agent/agent.py                  新增 take_steering()
zenagent/app/views.py                    PendingLane / PendingMessage、RunStatus.pending
zenagent/app/chat.py                     待发模型、steer/promote/清空/跨会话丢弃、run 结束归置
zenagent/app/commands.py                 /undo /new /fork 的 reload_transcript 带 drop_pending
zenagent/cli/tui/widgets/pending_band.py 新增：待发区 widget（渲染 + 行点击）
zenagent/cli/tui/app.py                  compose/刷新/promote/Ctrl+S/Ctrl+X、_reset_after_switch
zenagent/i18n/{en,zh}.py                 tui.queue.* + notice.pending_dropped
tests/test_agent_class.py                take_steering
tests/test_app_pending.py                新增：app 层全部行为
tests/test_tui_pending_band.py           新增：widget 渲染与点击
tests/test_tui_controls.py               TUI 接线与键位
docs/{guide/tui.md,feature-map.md}、CHANGELOG.md
```

---

### Task 1: 内核 `take_steering()`

**Files:**
- Modify: `zenagent/agent/agent.py:153-160`（`pending_steering` / `steer` 之后）
- Test: `tests/test_agent_class.py`（`test_pending_steering_counts_and_drains` 之后）

**Interfaces:**
- Produces: `Agent.take_steering() -> list[str]` —— 原子取走并清空 `_steering`，保持 FIFO 顺序；空队列返回 `[]`。

- [ ] **Step 1: 写失败测试**

```python
async def test_take_steering_removes_and_returns_in_order():
    agent = make_agent(scripted([("text", "x"), end()]))
    agent.steer("first")
    agent.steer("second")
    assert agent.pending_steering == 2
    assert agent.take_steering() == ["first", "second"]
    assert agent.pending_steering == 0
    assert agent.take_steering() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_class.py::test_take_steering_removes_and_returns_in_order -v`
Expected: FAIL，`AttributeError: 'Agent' object has no attribute 'take_steering'`

- [ ] **Step 3: 实现**：在 `steer()` 之后加方法，交换 `self._steering` 引用并返回旧列表（不要逐条 pop）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_class.py::test_take_steering_removes_and_returns_in_order -v`
Expected: PASS

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh` → ruff 干净、pytest 全绿。

- [ ] **Step 6: 提交** `feat(agent): take_steering() to reclaim undelivered steering`

---

### Task 2: app 待发模型（排队 lane）

**Files:**
- Modify: `zenagent/app/views.py`（`__all__`、`RunStatus` 附近）
- Modify: `zenagent/app/chat.py`（init、`submit/queue_follow_up/dequeue_follow_up/clear_follow_ups`、`status`、`_run` finally）
- Test: 新建 `tests/test_app_pending.py`

**Interfaces:**
- Produces（views.py）：
  ```python
  PendingLane = Literal["queue", "steer"]

  @dataclass(frozen=True)
  class PendingMessage:
      id: int
      lane: PendingLane
      text: str
      images: int = 0
      created_at: float = 0.0
  ```
  `RunStatus.pending: list[PendingMessage] = field(default_factory=list)`；`PendingMessage`、`PendingLane` 加入 `__all__`。
- Produces（chat.py）：`ChatController._pending: list[_PendingItem]`、`_next_pending_id: int`；
  `queue_follow_up(text, images=None)`（空白文本忽略，`images=None` 时吃掉 `_attachments`）；
  `dequeue_follow_up() -> str | None`（取最后一条 queue lane，图片放回 `_attachments`）；
  `clear_pending() -> int`（清空两条 lane，返回条数）；`clear_follow_ups()` 为 `clear_pending()` 的兼容别名；
  `status.pending` 按创建顺序快照；`status.follow_ups` = queue lane 条数；`status.steering` 暂仍取 `agent.pending_steering`。
- 内部：`_add_pending(text, images, lane) -> PendingMessage`、`_take_next_queue_item() -> _PendingItem | None`。

- [ ] **Step 1: 写失败测试**（新文件，复制 `test_app_chat.py` 的 `gated_stream` helper 到本文件顶部，保持独立）

```python
async def test_second_submit_becomes_a_visible_queue_entry(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    await chat.submit("follow up")

    assert [(p.lane, p.text) for p in chat.status.pending] == [("queue", "follow up")]
    assert chat.status.follow_ups == 1
    gate.set()
    await task


async def test_dequeue_returns_the_last_queue_entry_and_restores_images(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")
    assert [p.text for p in chat.status.pending] == ["one", "two"]
    assert chat.dequeue_follow_up() == "two"
    assert [p.text for p in chat.status.pending] == ["one"]
    assert chat.dequeue_follow_up() == "one"
    assert chat.dequeue_follow_up() is None
    gate.set()
    await task


async def test_clear_pending_returns_the_cleared_count(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")
    assert chat.clear_pending() == 2
    assert chat.status.pending == []
    assert chat.status.follow_ups == 0
    gate.set()
    await task


async def test_pending_ids_increase_and_survive_dequeue(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")
    ids = [p.id for p in chat.status.pending]
    chat.dequeue_follow_up()
    chat.queue_follow_up("three")
    assert ids[0] < ids[1]
    assert chat.status.pending[-1].id > ids[1]
    gate.set()
    await task
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_pending.py -v` → FAIL（`AttributeError: pending` / `clear_pending`）

- [ ] **Step 3: 实现**

  `views.py`：加 `PendingLane`/`PendingMessage`、`RunStatus.pending`。
  `chat.py`：加 `from dataclasses import dataclass, field`、`from zenagent.app.views import ... PendingMessage`；内部 `_PendingItem` dataclass；把 `_follow_ups: list[tuple[str, list[ImageContent]]]` 全部换到 `_pending`：
  - `queue_follow_up`：`if not text.strip(): return` → 取 pending 附件 → `_add_pending(text, pending, "queue")` → `_notify()`。
  - `dequeue_follow_up`：倒序找 queue lane；移除；`self._attachments[:0] = item.images`；返回文本。
  - `clear_pending`：`cleared = len(self._pending)`，清空，`_notify()` 当 `cleared`（Task 3 再补 `take_steering`）。
  - `_run` finally：压缩条件 `not self._follow_ups` → `not self._pending`；续跑 `self._follow_ups.pop(0)` → `item = self._take_next_queue_item()`，`self._run(item.message.text, item.images)`。
  - `status`：`pending=[item.message for item in self._pending]`、`follow_ups=sum(1 for item in self._pending if item.message.lane == "queue")`。

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/test_app_pending.py tests/test_app_chat.py -v` → PASS

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`

- [ ] **Step 6: 提交** `feat(app): pending-message view model and queue lane`

---

### Task 3: app 插话 lane、升格与镜像回收

**Files:**
- Modify: `zenagent/app/chat.py`（`steer`、`promote`、`_on_event`、`clear_pending`）
- Test: `tests/test_app_pending.py`

**Interfaces:**
- Produces：
  - `ChatController.steer(text: str) -> None`：`agent.is_streaming` 为真 → `_add_pending(text, [], "steer")` + `agent.steer(text)`；否则退回 `queue_follow_up(text)`。
  - `ChatController.promote(pending_id: int) -> Literal["ok", "blocked_images", "not_running", "missing"]`：只处理 queue lane；带图片 → `blocked_images`；`agent.is_streaming` 为假 → `not_running`；条目不存在或已是 steer lane → `missing`；成功则从 `_pending` 移除并以 steer lane 追加（顺序按升格先后）+ `agent.steer(text)`。
  - `_on_event`：任何事件处理后若 `agent.pending_steering == 0`，删除所有 steer lane 镜像（并触发一次 `_notify`）。

- [ ] **Step 1: 写失败测试**（本文件追加；`gated_tool_stream` 是新 helper：首轮发 `read_file` 工具调用并在 `gate` 处等待，第二轮直接给文本）

```python
async def test_steer_while_streaming_shows_in_the_steer_lane(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("actually, check the logs")

    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("steer", "actually, check the logs")
    ]
    assert runtime.agent.pending_steering == 1
    gate.set()
    await task


async def test_promote_moves_a_queued_entry_to_the_steer_lane(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("use the staging config")
    entry = chat.status.pending[0]
    assert chat.promote(entry.id) == "ok"
    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("steer", "use the staging config")
    ]
    assert runtime.agent.pending_steering == 1
    gate.set()
    await task


async def test_promote_rejects_entries_with_images(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.attach_image(ImageContent(...))  # 用 zenagent.types.ImageContent 构造最小图片
    chat.queue_follow_up("with a screenshot")
    entry = chat.status.pending[0]
    assert chat.promote(entry.id) == "blocked_images"
    assert [p.lane for p in chat.status.pending] == ["queue"]
    assert runtime.agent.pending_steering == 0
    gate.set()
    await task


async def test_promote_when_idle_is_not_running(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.queue_follow_up("later")
    entry = chat.status.pending[0]
    assert chat.promote(entry.id) == "not_running"
    assert [p.lane for p in chat.status.pending] == ["queue"]
    assert chat.promote(999) == "missing"


async def test_steer_when_idle_falls_back_to_the_queue(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.steer("later")
    assert [(p.lane, p.text) for p in chat.status.pending] == [("queue", "later")]
    assert runtime.agent.pending_steering == 0


async def test_injected_steering_clears_the_mirror_and_lands_in_transcript(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_tool_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("check the readme"))
    await asyncio.sleep(0.05)

    chat.steer("actually, check the logs")
    gate.set()
    await task
    await chat.wait_for_idle()

    assert chat.status.pending == []
    assert runtime.agent.pending_steering == 0
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("actually, check the logs" in str(m.content) for m in users)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_app_pending.py -v` → FAIL

- [ ] **Step 3: 实现**：

```python
def steer(self, text: str) -> None:
    if not text.strip():
        return
    if not self.runtime.agent.is_streaming:
        self.queue_follow_up(text)
        return
    self._add_pending(text, [], "steer")
    self.runtime.agent.steer(text)
    self._notify()
```

  `promote()` 按 Interfaces 实现；`_on_event` 在 `changed = self._builder.apply(event)` 之后加镜像回收（若删除了条目则 `changed = True`）；`clear_pending()` 在清空前先 `self.runtime.agent.take_steering()`（Task 1 的方法）防止后续静默注入。

- [ ] **Step 4: 跑测试通过** `uv run pytest tests/test_app_pending.py tests/test_app_chat.py -v`

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`

- [ ] **Step 6: 提交** `feat(app): steering lane, promote, and mirror reconciliation`

---

### Task 4: run 结束的归置（延续运行 / 降级 / 压缩顺序）

**Files:**
- Modify: `zenagent/app/chat.py`（`_run`、新增 `_demote_steering`）
- Test: `tests/test_app_pending.py`

**Interfaces:**
- Produces：`ChatController._run(text: str | None, images: list[ImageContent] | None = None)`；`text is None` 时调 `agent.continue_()`（`ValueError` 时 `_demote_steering()` 兜底）。`_demote_steering() -> None`：`agent.take_steering()` → 把 steer lane 镜像移除并以 queue lane 追加（顺序保持），随后 `_notify()`。
- finally 顺序（判定用 `termination = self.runtime.agent.termination`）：
  1. 若 `agent.pending_steering`：`termination == "stop"` → 记 `continuation = True`；否则 `_demote_steering()`。
  2. `termination != "aborted" and not self._pending` → `await self._maybe_auto_compact()`。
  3. `_running = False; _task = None`；`continuation` → `self._task = asyncio.create_task(self._run(None))`；否则取队列第一条续跑（`termination != "aborted"`）。

- [ ] **Step 1: 写失败测试**

```python
async def test_stranded_steering_continues_the_run(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content = [TextContent(text=f"answer{len(calls)}")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("also add tests")   # 本轮没有工具轮，插话等不到边界
    gate.set()
    await task
    await chat.wait_for_idle()

    assert len(calls) == 2, "the stranded steer must trigger a continuation run"
    assert chat.status.running is False
    assert chat.status.pending == []
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("also add tests" in str(m.content) for m in users)


async def test_error_ending_demotes_steering_to_the_queue(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []
    states: list[tuple[bool, list[PendingMessage], str]] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        if len(calls) == 1:
            partial.stop_reason = "error"
            yield AssistantEvent(type="error", partial=partial, reason="error")
        else:
            partial.content = [TextContent(text="carried on")]
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(
        runtime,
        on_change=lambda: states.append(
            (chat.status.running, list(chat.status.pending), chat.status.last_error)
        ),
    )
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("save the logs")
    gate.set()
    await task
    await chat.wait_for_idle()

    demoted = [
        state
        for state in states
        if state[2] and state[1] and all(p.lane == "queue" for p in state[1])
    ]
    assert demoted, "the steer must become a visible queue entry before the next run"
    assert len(calls) == 2
    assert chat.status.pending == []
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("save the logs" in str(m.content) for m in users)


async def test_aborted_run_keeps_steering_as_a_visible_queue_entry(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        if options.abort is not None and options.abort.aborted:
            raise Aborted("cancelled by user")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("save the logs")
    chat.abort()
    gate.set()
    await task
    await chat.wait_for_idle()

    assert len(calls) == 1, "an aborted run must not auto-continue"
    assert chat.status.running is False
    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("queue", "save the logs")
    ]


async def test_continuation_runs_before_auto_compaction(runtime_factory, monkeypatch):
    import zenagent.app.chat as chat_module

    gate = asyncio.Event()
    compacted: list[list[str]] = []
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content = [TextContent(text=f"answer{len(calls)}")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    monkeypatch.setattr(chat_module, "should_auto_compact", lambda *a, **k: True)

    async def fake_compact(force: bool = False) -> str | None:
        compacted.append([p.lane for p in chat.status.pending])
        return None

    monkeypatch.setattr(chat, "compact_now", fake_compact)

    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)
    chat.steer("also add tests")
    gate.set()
    await task
    await chat.wait_for_idle()

    assert compacted == [[]], "compaction must wait until the pending lanes are empty"
```

- [ ] **Step 2: 跑测试确认失败** → FAIL（当前实现不会续跑、错误/中断时插话悬空）

- [ ] **Step 3: 实现**：按 Interfaces 重写 `_run` 的 `try/finally`；`continue_` 的 `ValueError` 用 `try/except` 包住并降级；保留现有 `_last_error` 逻辑与 turn 计时。

- [ ] **Step 4: 跑测试通过** `uv run pytest tests/test_app_pending.py tests/test_app_chat.py -v`

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`

- [ ] **Step 6: 提交** `feat(app): deliver stranded steering, demote it on abnormal endings`

---

### Task 5: 待发不跨会话

**Files:**
- Modify: `zenagent/app/chat.py`（`_drop_pending`、`reload_transcript`、`clear`、`retry`）
- Modify: `zenagent/app/commands.py`（`_undo`、`_new`、`_fork`）
- Modify: `zenagent/cli/tui/app.py`（`_reset_after_switch`）
- Modify: `zenagent/i18n/{en,zh}.py`（`notice.pending_dropped`）
- Test: `tests/test_app_pending.py`、`tests/test_app_undo_retry.py`（若需要）

**Interfaces:**
- Produces：`ChatController.drop_pending() -> int` —— `clear_pending()` 后若有内容，`_notices.append(TranscriptNotice("info", t("notice.pending_dropped", n=cleared)))` 并 `_notify()`。
- `reload_transcript(*, drop_pending: bool = False) -> None`：为真时先 `drop_pending()`。
- `clear()` 与 `retry()` 入口先 `drop_pending()`。
- 调用点改 `reload_transcript(drop_pending=True)`：`commands.py::_undo`、`_new`、`_fork`，`cli/tui/app.py::_reset_after_switch`。压缩路径（`compact_now`）保持默认 `False`。

- [ ] **Step 1: 写失败测试**

```python
async def test_session_switch_drops_pending_with_a_notice(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.queue_follow_up("leftover")
    assert chat.drop_pending() == 1
    assert chat.status.pending == []
    notices = chat.take_notices()
    assert notices and "pending messages" in notices[-1].text


async def test_clear_drops_pending(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.queue_follow_up("leftover")
    chat.clear()
    assert chat.status.pending == []


async def test_reload_transcript_keeps_pending_by_default(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.queue_follow_up("keep me")
    chat.reload_transcript()
    assert [p.text for p in chat.status.pending] == ["keep me"]
    chat.reload_transcript(drop_pending=True)
    assert chat.status.pending == []
```

- [ ] **Step 2: 跑测试确认失败** → FAIL

- [ ] **Step 3: 实现**（含 `notice.pending_dropped` 的中英文案：
  `"Dropped {n} pending messages (session changed)"` / `"已丢弃 {n} 条待发消息（会话已切换）"`；
  `notice.*` 一律单键带 `{n}`，不用 `tn()`/`.one/.other`）

- [ ] **Step 4: 跑测试通过** `uv run pytest tests/test_app_pending.py tests/test_app_undo_retry.py tests/test_app_commands.py -v`

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`

- [ ] **Step 6: 提交** `feat(app): drop pending messages on session changes`

---

### Task 6: `PendingBand` widget

**Files:**
- Create: `zenagent/cli/tui/widgets/pending_band.py`
- Modify: `zenagent/i18n/{en,zh}.py`（`tui.queue.title.queued/steer`、`hint`、`more`、`images`）
- Test: 新建 `tests/test_tui_pending_band.py`

**Interfaces:**
- Produces：`PendingBand(Static)`，id `#pending-band`，`MAX_MESSAGE_ROWS = 6`。
  - `set_pending(pending: list[PendingMessage], width: int) -> None`
  - `set_handler(handler: Callable[[int], None] | None) -> None`（点击排队行时回调 pending id）
  - `plain -> str`（测试用）；`display` 空时 False。
- 渲染（en locale 示例，宽度不足一律 `clip_cells`；`text` 的 `\n` 先折成 ` ↵ `）：
  ```
  Queued · 2
    1. first message
    2. second message
  Steering · 1
    1. stop that
  └ alt+↑ edit · ctrl+x clear · click a queued row to steer
  ```
  两条 lane 各自按创建顺序；header 用 `palette.ACCENT`、条目前缀 `  1. ` 与文本用 `palette.MUTED`、带图片行尾追加 ` · img {n}`（`palette.DIM`）、提示行 `palette.DIM`；超过 6 条消息行时，第 6 行之后一条 `… {n} more`；空 lane 不渲染 header。
- 点击：只把 **queue lane** 的消息行映射到 pending id（`{行号: id}` 存于实例），`on_click(event)` 用 `event.y`（Textual 的 `MouseEvent.y` 相对 widget）查表调用 handler；header / steer 行 / 提示行不映射。

- [ ] **Step 1: 写失败测试**

```python
from zenagent.app.views import PendingMessage
from zenagent.cli.tui.widgets.pending_band import PendingBand


async def test_band_renders_groups_in_order(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending(
            [
                PendingMessage(id=1, lane="queue", text="first message"),
                PendingMessage(id=2, lane="queue", text="second message"),
                PendingMessage(id=3, lane="steer", text="stop that"),
            ],
            120,
        )
        await pilot.pause()

        lines = band.plain.splitlines()
        assert lines[0] == "Queued · 2"
        assert lines[1].strip().startswith("1. first message")
        assert lines[2].strip().startswith("2. second message")
        assert lines[3] == "Steering · 1"
        assert lines[4].strip().startswith("1. stop that")
        assert lines[-1].startswith("└ alt+↑ edit")
        assert band.display is True


async def test_band_hides_when_empty(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending([], 120)
        await pilot.pause()
        assert band.display is False
        assert band.plain == ""


async def test_band_folds_beyond_six_message_rows(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending(
            [PendingMessage(id=i, lane="queue", text=f"m{i}") for i in range(1, 8)],
            120,
        )
        await pilot.pause()
        lines = band.plain.splitlines()
        assert len([line for line in lines if line.strip()[:1].isdigit()]) == 6
        assert "… 1 more" in band.plain


async def test_band_clips_long_text_and_folds_newlines(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending(
            [PendingMessage(id=1, lane="queue", text="a\nb" * 200)], 40
        )
        await pilot.pause()
        assert "↵" in band.plain
        assert all(len(line) <= 40 for line in band.plain.splitlines())


async def test_band_clicking_a_queued_row_calls_the_handler(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        seen: list[int] = []
        band.set_handler(seen.append)
        band.set_pending(
            [
                PendingMessage(id=7, lane="queue", text="first"),
                PendingMessage(id=8, lane="queue", text="second"),
            ],
            120,
        )
        await pilot.pause()

        await pilot.click(PendingBand, offset=(4, 1))   # 第二行 = 第一条消息
        await pilot.pause()
        assert seen == [7]


async def test_band_clicking_header_steer_row_or_hint_does_nothing(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        seen: list[int] = []
        band.set_handler(seen.append)
        band.set_pending(
            [
                PendingMessage(id=7, lane="queue", text="first"),
                PendingMessage(id=9, lane="steer", text="soon"),
            ],
            120,
        )
        await pilot.pause()

        await pilot.click(PendingBand, offset=(2, 0))   # header
        await pilot.click(PendingBand, offset=(2, 3))   # steer 行
        await pilot.click(PendingBand, offset=(2, 4))   # 提示行
        await pilot.pause()
        assert seen == []
```

- [ ] **Step 2: 跑测试确认失败** `uv run pytest tests/test_tui_pending_band.py -v`

- [ ] **Step 3: 实现** widget（`DEFAULT_CSS`：`height: auto; background: $surface; padding: 0 1;`），按渲染规格拼 `rich.text.Text`，维护 `self._line_to_id`。

- [ ] **Step 4: 跑测试通过** `uv run pytest tests/test_tui_pending_band.py -v`

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`（i18n key 集校验会跟着跑）

- [ ] **Step 6: 提交** `feat(tui): pending band widget`

---

### Task 7: TUI 接线与键位

**Files:**
- Modify: `zenagent/cli/tui/app.py`（import、`compose`、`on_mount`、`_refresh_status`、`_promote_pending`、`action_steer`、`action_clear_queue`）
- Modify: `zenagent/i18n/{en,zh}.py`（`tui.queue.promoted`、`blocked_images`、`cleared`）
- Test: `tests/test_tui_controls.py`

**Interfaces:**
- Produces：`ZenAgentApp._promote_pending(pending_id: int) -> None`：`chat.promote(id)`；`"ok"` → `notify(t("tui.queue.promoted"), timeout=2)`；`"blocked_images"` → `notify(t("tui.queue.blocked_images"), timeout=2)`；其余静默；之后 `_refresh_status()`。
- `action_steer`（Ctrl+S）：输入框有文本且 `chat.status.running` → 现有行为；输入框为空且存在 queue lane 条目 → `_promote_pending(最后一条 queue lane 的 id)`；否则 no-op。
- `action_clear_queue`（Ctrl+X）：`cleared = chat.clear_pending()`；`cleared` 时 `notify(t("tui.queue.cleared"), timeout=2)`；`_refresh_status()`。
- `compose()` 在 `yield StatusBand()` 之后 `yield PendingBand()`；`_refresh_status()` 中 `for band in self.query(PendingBand): band.set_pending(status.pending, width)`（width 沿用 `_band_width()`）；`on_mount` 注册 `set_handler(self._promote_pending)`。
- 文案：`"Now steering; delivered at the next step"` / `"已转为插话，下一步立即送达"`；`"A queued message with images can't steer; it sends after this run"` / `"带图片的排队消息不能插话，本轮结束后发送"`；`"Pending messages cleared"` / `"已清空待发消息"`。

- [ ] **Step 1: 写失败测试**（`tests/test_tui_controls.py` 追加；顶部补 `from zenagent.cli.tui.widgets.pending_band import PendingBand`）

```python
async def test_mid_run_submit_shows_the_pending_band(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("later")
        await pilot.press("enter")
        await pilot.pause()

        band = app.query_one(PendingBand)
        assert band.display is True
        assert "Queued · 1" in band.plain
        assert "later" in band.plain

        gate.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.query_one(PendingBand).display is False


async def test_ctrl_s_with_an_empty_prompt_promotes_the_newest_queued(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("one")
        app.chat.queue_follow_up("two")
        app.query_one(PromptInput).focus()
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert [(p.lane, p.text) for p in app.chat.status.pending] == [
            ("queue", "one"),
            ("steer", "two"),
        ]
        assert "Steering · 1" in app.query_one(PendingBand).plain

        gate.set()
        await app.chat.wait_for_idle()


async def test_ctrl_x_clears_both_lanes(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("one")
        app.chat.steer("two")
        await pilot.press("ctrl+x")
        await pilot.pause()

        assert app.chat.status.pending == []
        assert app.query_one(PendingBand).display is False

        gate.set()
        await app.chat.wait_for_idle()


async def test_pending_band_follows_the_dock_width_with_the_sidebar_open(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("a long queued message " * 6)
        await pilot.press("ctrl+b")   # 打开侧栏，dock 变窄
        await pilot.pause()

        band = app.query_one(PendingBand)
        assert band.display is True
        assert max(len(line) for line in band.plain.splitlines()) <= app._band_width()

        await pilot.press("ctrl+b")
        gate.set()
        await app.chat.wait_for_idle()
```

- [ ] **Step 2: 跑测试确认失败** `uv run pytest tests/test_tui_controls.py -v`

- [ ] **Step 3: 实现** app 接线（compose 顺序：`StatusBand` → `PendingBand` → `#prompt-row`）；`_refresh_status` 传 `_band_width()`；`action_steer`/`action_clear_queue` 按 Interfaces 改写。

- [ ] **Step 4: 跑测试通过** `uv run pytest tests/test_tui_controls.py tests/test_tui_pending_band.py tests/test_tui_layout.py -v`

- [ ] **Step 5: 全量闸门** `bash scripts/check.sh`

- [ ] **Step 6: 提交** `feat(tui): wire the pending band, ctrl+s promote, ctrl+x clear`

---

### Task 8: 文档与收尾

**Files:**
- Modify: `docs/guide/tui.md`（键位表：Ctrl+S 增加"空输入时升格最新排队"；Ctrl+X 改为"清空待发（排队+插话）"；新增一段待发区说明与截图式示例）
- Modify: `docs/feature-map.md`（5.5 TUI 表加一行：待发区与插话）
- Modify: `CHANGELOG.md`（Unreleased → Added 一条）
- Modify: `docs/superpowers/specs/2026-11-01-tui-pending-queue-design.md`（状态改为"已实现"）

- [ ] **Step 1: 更新文档**（三处，文案与实现一致；feature-map 行给出源码/测试/文档列）
- [ ] **Step 2: 全量闸门** `bash scripts/check.sh` → ruff + 1704+ 全绿
- [ ] **Step 3: 提交** `docs: document the pending queue and steering band`
- [ ] **Step 4: 交接**：把分支交回 Lead 做整支 review；review 通过后合并 main。
