# ZenAgent v2 第一波：Harness 可观测性与交互补强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 2026-10-30 对标调研里判定为「确定 P1」的 8 项 harness 能力：工具卡标题/元信息、压缩分隔线（含 TUI 自动压缩）、队列取回与双击 Esc、Ctrl+R 历史搜索、错误横幅、`/tools` 与 REPL `/retry`、`/pin`、状态段（cache_hit/time_spent/token_rate）、TUI 图片附件。

**Architecture:** 全部改动落在 `zenagent/app/`（UI-agnostic 控制器与命令注册表）和 `zenagent/cli/`（REPL、TUI 前端）两层；内核（types/events/providers/agent/tools）不动。数据来源已经齐全——工具结果带 `details`、`Usage` 已聚合 cache 字段、`ContextGuard.last_usage` 记录每轮用量——本波只做「接线 + 渲染 + 交互」，不新增模型调用路径。压缩逻辑从 `cli/compact.py` 上移到 `app/compact.py`，让 TUI 与 REPL/headless 共用同一条可测试的路径。

**Tech Stack:** Python 3.11+、asyncio、Textual、rich、pytest/pytest-asyncio；内核仍然只依赖 httpx。

**Spec:** `docs/research/omp-cli-study/09-zenagent-vs-omp-gap.md` §0.1 与 §11 P1（第 1–7、10 项）；本计划同时实现该文档 §11 第 5 项里的 `/pin`。

---

## 本计划的范围

**做（对应 §0.1 编号）：**

| # | 能力 | Task |
|---|---|---|
| 1 | 工具卡按 omp 语法补标题/元信息（bash/read/edit/write/grep） | Task 1 |
| 6 | 状态段 `cache_hit`、`time_spent`、`token_rate` | Task 2 |
| 4 | 错误横幅（provider 错误常驻可见） | Task 3 |
| 3 | 队列取回（Alt+Up）、空草稿双击 Esc 回退 | Task 4 |
| 10 | TUI 历史搜索（Ctrl+R；即 §0.1 第 10 项「历史搜索/外部编辑器二选一」选历史搜索） | Task 5 |
| 5 | `/tools`、REPL `/retry`、`/pin` | Task 6、7、8 |
| 2 | compaction 分隔线；顺带补上 TUI 缺失的自动压缩 | Task 9 |
| 7 | TUI 图片附件 + attachment chip 带（路径附件；不含剪贴板图片粘贴） | Task 10 |

**不做（本计划明确排除，避免执行时扩大范围）：**

- §0.1 第 8 项 todo 工具 + HUD、第 9 项 plan review：都标注为**条件项**，依赖「走向长任务/正式 plan 工作流」的产品决定，各自需要独立 spec + plan，本波不做。
- v2 方向 A 的沙箱、方向 B 的 subagent、方向 C 的平台化：项目级系统，需要独立 spec，不在本计划内。
- TUI 剪贴板图片粘贴（omp 的 Ctrl+V 图片粘贴依赖终端/系统剪贴板桥接，成本远高于路径附件；先做 `/image <path>` + chips）。
- 外部编辑器（§0.1 第 10 项二选一，已选历史搜索）。

## Global Constraints

- 内核（types/events/providers/agent/tools）只依赖 httpx；`zenagent.app` 不得 import `textual`/`rich`/`prompt_toolkit`；`zenagent.cli.tui` 可以 import `zenagent.app`，反向不行。
- TUI 进程内**禁止**任何库层 `print()` 写 stdout：一切提示走 transcript 的 notice/divider。`maybe_auto_compact` 之类带 `print_fn=print` 默认值的函数，在 TUI/TUI controller 里不得以默认值调用。
- `ChatController` 每次事件必须保持 O(变更量)：新的渲染逻辑不得在流式 delta 上做全量 transcript 扫描。
- 会话仍是 append-only JSONL；压缩/undo/retry 都写**新**会话文件，不修改旧文件（沿用现有 `Session.rewrite` / `unique_session_path`）。
- REPL、headless、RPC 现有输出与退出码不变；除本计划新增的命令/行为外不得回归。
- 所有新逻辑必须有离线单测（不联网、不读真实 `~/.zenagent`：用 `app_env`/`tmp_path`/`monkeypatch`）；`bash scripts/check.sh`（ruff + 全量 pytest）全绿才算完成。
- 每个 Task 一次提交，提交信息用 conventional commits；Task 之间顺序执行（共享 `views.py`/`chat.py`/`app.py`/`commands.py`/`repl.py`，不适合并行改）。`app.py` 新增的 dock 组件初始必须 `display = False`，否则会破坏既有的 layout 几何断言。

## Review Focus

1. **压缩的时机与可恢复性**：流式进行中、有排队消息、或 transcript 为空时绝不压缩；压缩失败必须保持原 transcript 与当前会话不变；divider 必须只出现一次，且不能被紧随其后的 `reset_transcript` 丢掉。
2. **TUI 不得被库层打印污染**：TUI 触发压缩时 stdout 必须零输出（否则终端 alternate-screen 被截图级别的乱码打断）；失败信息走 notice。
3. **状态的生命周期**：附件在发出后清空、abort 后不残留到下一轮；历史文件损坏/不可写时启动与提交不能失败；错误横幅在下一次提交时清掉。
4. **Esc 语义边界**：运行中 Esc 只 abort、绝不 undo；空草稿两次 Esc 间隔超过阈值（0.6s）不得触发；无回合可撤销时只能提示，不能抛异常。
5. **降级路径**：历史会话回放没有 `details`、未知/新增工具名、超长命令或 diff——工具卡必须回退到通用标题，不能 KeyError 或把状态行折成多行。

---

### Task 1: 工具卡标题与元信息

**Files:**
- Modify: `zenagent/app/views.py`（`ToolCallView` 增加 `details`）
- Modify: `zenagent/app/chat.py`（tool 结果把 `details` 带进 view）
- Modify: `zenagent/cli/tui/widgets/tool_card.py`（按家族渲染标题）
- Test: `tests/test_tui_tool_card.py`

**Interfaces:**
- Consumes: `AgentEvent.result`（`ToolResultAgent`，已含 `details: dict`）、`ToolCallView.args`。
- Produces: `ToolCallView.details: dict`（默认 `{}`）；`ToolCard._head()` 的渲染约定如下，后续 Task 不改：
  - `bash`：`● bash  <command 首行>  ·  exit <code>  ·  <N>ms`
  - `read_file`：`● read_file  <path>  ·  <total_lines> lines[  · truncated]`
  - `edit_file`：`● edit_file  <path>  ⟨+<added>/-<removed>⟩`
  - `write_file`：`● write_file  <path>  ·  <len(content.splitlines())> lines`
  - `grep`：`● grep  '<pattern>'  ·  <matches> matches`
  - `glob`：`● glob  '<pattern>'`
  - 其它/缺 `details`：退回现有的 `name + args_preview + status + duration` 通用标题。
  - 末尾仍追加现有的 `  ·  <status>  ·  <duration>ms`。
- 约束：`details` 只用于 UI，不进入 `content`、不发给模型；`messages_to_views`（历史回放）不填 `details`。

- [ ] **Step 1: RED —— 写测试**

在 `tests/test_tui_tool_card.py` 增加（沿用文件里的 `view(**overrides)` 辅助）：

```python
def test_bash_card_shows_the_command_and_exit_code():
    card = ToolCard(view(name="bash", args={"command": "pytest -q"},
                        details={"exit_code": 1, "duration_ms": 120}))
    assert "$" not in card.plain  # 只加信息，不改变卡形状
    assert "pytest -q" in card.plain and "exit 1" in card.plain

def test_edit_card_shows_diff_stats():
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n+more\n"
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}))
    assert "x.py" in card.plain and "+2/-1" in card.plain

def test_read_card_shows_line_count_and_truncation():
    card = ToolCard(view(name="read_file", args={"path": "a.py"},
                        details={"total_lines": 400, "truncated": True}))
    assert "a.py" in card.plain and "400 lines" in card.plain and "truncated" in card.plain

def test_grep_card_shows_match_count():
    card = ToolCard(view(name="grep", args={"pattern": "TODO"},
                        details={"matches": 7}))
    assert "TODO" in card.plain and "7 matches" in card.plain

def test_cards_degrade_without_details_and_for_unknown_tools():
    # 历史回放没有 details，新工具名也可能出现：都必须回退到通用标题。
    assert "mystery" in ToolCard(view(name="mystery", details={})).plain
    assert "read_file" in ToolCard(view(name="read_file", details={})).plain

def test_long_bash_command_never_wraps_the_collapsed_row():
    card = ToolCard(view(name="bash", args={"command": "x" * 500},
                        details={"exit_code": 0}))
    assert card.size.height in (0, 1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_tool_card.py -k "bash_card or diff_stats or line_count or match_count or degrade or wraps"`
Expected: FAIL（`TypeError: unexpected keyword argument 'details'` 或断言失败）。

- [ ] **Step 3: 实现**

1. `views.py`：`ToolCallView` 增加 `details: dict = field(default_factory=dict)`。
2. `chat.py`：`_on_event` 的 `tool_execution_end` 分支里 `card.details = dict(getattr(event.result, "details", None) or {})`；`tool_execution_start` 分支保持 `details` 默认空。
3. `tool_card.py`：把 `_head()` 拆成 `_family_head() -> Text`（按 `view.name` 返回标题片段）+ `_diff_stats(diff) -> tuple[int, int]`（统计 `diff.splitlines()` 中 `startswith("+") and not startswith("+++")` / `-` 同理），再拼状态与耗时。参数截断沿用现有预算逻辑；`bash` 的命令取 `args.get("command", "").splitlines()[0]`。
4. 不修改任何工具实现（`details` 已由 bash/fs/search 提供）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_tool_card.py tests/test_app_views.py`
Expected: PASS；既有工具卡测试（含 `echo hi`、`19ms`、`earlier lines`）不回归。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/views.py zenagent/app/chat.py zenagent/cli/tui/widgets/tool_card.py tests/test_tui_tool_card.py
git commit -m "feat(tui): give tool cards per-tool titles and metadata"
```

---

### Task 2: 状态段 cache_hit / time_spent / token_rate

**Files:**
- Modify: `zenagent/app/views.py`（`RunStatus` 三个字段）
- Modify: `zenagent/app/chat.py`（回填与每轮计时）
- Modify: `zenagent/cli/tui/status/segments.py`、`zenagent/cli/tui/status/presets.py`
- Modify: `docs/guide/tui.md`（状态段清单）
- Test: `tests/test_tui_status_band.py`、`tests/test_app_chat.py`

**Interfaces:**
- Produces: `RunStatus.cache_write: int = 0`、`RunStatus.turn_ms: int = 0`、`RunStatus.turn_output_tokens: int = 0`。
- Produces: segment id `"cache_hit"`（`cache {pct:.0f}%`，`pct = cache_read * 100 / (input_tokens + cache_read)`，`cache_read == 0` 或分母为 0 时隐藏）、`"time_spent"`（上一轮墙钟 `f"{turn_ms / 1000:.1f}s"`，`turn_ms == 0` 隐藏）、`"token_rate"`（`f"{turn_output_tokens / (turn_ms / 1000):.1f} tok/s"`，任一为 0 隐藏）。
- Produces: `ChatController.status()` 回填 `cache_write`（来自 `usage_total(...).cache_write`）与 `turn_ms`/`turn_output_tokens`（来自 `ChatController._run` 的计时与用量差分）。
- 预设：`default.right` 变为 `("tokens", "cache_hit", "cost", "context_pct", "time_spent", "token_rate", "queued", "session_name")`；`compact`/`minimal` 不变。

- [ ] **Step 1: RED —— 写测试**

`tests/test_tui_status_band.py`：更新 `test_segment_ids_cover_the_documented_set` 的期望集合，加入三个新 id；并新增：

```python
def test_cache_hit_is_a_ratio_of_prompt_tokens():
    _, plain = render_status_line(status(cache_read=750, input_tokens=250), 160)
    assert "cache 75%" in plain

def test_cache_hit_hidden_without_cache_read():
    _, plain = render_status_line(status(cache_read=0), 160)
    assert "cache " not in plain

def test_turn_time_and_rate_need_a_finished_turn():
    _, plain = render_status_line(status(turn_ms=2000, turn_output_tokens=50), 160)
    assert "2.0s" in plain and "25.0 tok/s" in plain
    _, idle = render_status_line(status(turn_ms=0, turn_output_tokens=0), 160)
    assert "tok/s" not in idle and "2.0s" not in idle
```

`tests/test_app_chat.py`：新增一个带 cache 用量的 stream 与计时测试：

```python
async def test_status_reports_cache_write_and_last_turn_timing(runtime_factory):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(content=[], provider="p", model="m",
                                   usage=Usage(input=100, output=20, cache_read=60,
                                               cache_write=40),
                                   stop_reason="pending", timestamp=0)
        yield AssistantEvent(type="start", partial=partial)
        await asyncio.sleep(0.02)
        partial.content = [TextContent(text="hi")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta="hi")
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()
    status = chat.status
    assert status.cache_write == 40
    assert status.turn_ms >= 15 and status.turn_output_tokens == 20
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_status_band.py tests/test_app_chat.py -k "cache or rate or timing or segment_ids"`
Expected: FAIL（`RunStatus` 没有新字段 / 段不存在）。

- [ ] **Step 3: 实现**

1. `views.py`：`RunStatus` 加三个默认 0 字段。
2. `chat.py`：`__init__` 加 `self._turn_ms = 0`、`self._turn_output = 0`；`_run` 进入时记录 `started = time.monotonic()` 与 `before = usage_total(self.runtime.agent.messages).output`，`finally` 里（在 `self._running = False` 之后）计算 `self._turn_ms = int((time.monotonic() - started) * 1000)`、`self._turn_output = usage_total(...).output - before`；`status()` 回填三个字段。
3. `segments.py`：加三个渲染函数与 `SEGMENTS` 条目（`SEGMENT_IDS` 自动跟随）。
4. `presets.py`：按上面的顺序改 `default.right`；窄屏降级逻辑不需要改（现有从右往左丢弃规则会自然生效）。
5. 文档 `docs/guide/tui.md` 的状态带段落补三个段名与公式。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_status_band.py tests/test_app_chat.py`
Expected: PASS；`test_narrow_width_drops_right_group_first` 等既有降级测试保持绿（若宽度敏感用例因新增段变化，按实际宽度调整测试里的 `width`，不得删除降级断言）。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/views.py zenagent/app/chat.py zenagent/cli/tui/status tests/test_tui_status_band.py tests/test_app_chat.py docs/guide/tui.md
git commit -m "feat(tui): add cache-hit, turn-time and token-rate status segments"
```

---

### Task 3: provider 错误横幅

**Files:**
- Modify: `zenagent/app/views.py`（`RunStatus.last_error`）
- Modify: `zenagent/app/chat.py`（失败轮记录/清除错误）
- Create: `zenagent/cli/tui/widgets/error_banner.py`
- Modify: `zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`
- Modify: `docs/guide/tui.md`
- Test: `tests/test_tui_error_banner.py`（新建）、`tests/test_app_chat.py`

**Interfaces:**
- Produces: `RunStatus.last_error: str = ""`；`ChatController.status()` 回填 `ChatController._last_error`；`ChatController.clear_last_error() -> None`。
- 规则：只有 `agent.termination == "error"` 时记录横幅（用户 abort 的 `"aborted"` 不算错误）；文案取 `agent.error_message`，为空时用 `"the run failed"`。
- Produces: `ErrorBanner(Static)`，方法 `set_error(message: str) -> None`；`message == ""` 时 `display = False`，否则显示单行 `✘ <message>`（超过 160 字符截断加 `…`）；`on_click` 调 `app.dismiss_error()`。
- **初始状态必须 `display = False`**：`tests/test_tui_layout.py::test_dock_order_is_transcript_then_band_then_prompt` 依赖「band 紧贴 prompt」；隐藏的 dock 组件不能占行。
- Produces: `ZenAgentApp.dismiss_error() -> None`（清错误 + 刷新）。
- 生命周期：`ChatController.submit()` 开始时清空 `_last_error`；横幅随 `_refresh_status()` 更新。

- [ ] **Step 1: RED —— 写测试**

`tests/test_app_chat.py`：

```python
async def test_status_reports_provider_error_after_a_failed_run(runtime_factory):
    async def failing_stream(model, messages, tools, options):
        partial = AssistantMessage(content=[], provider="p", model="m", usage=Usage(),
                                   stop_reason="error", timestamp=0,
                                   error_message="provider exploded")
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="error", partial=partial, reason="error")

    runtime = await runtime_factory(stream_fn=failing_stream)
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()
    assert chat.status.last_error == "provider exploded"

async def test_submit_clears_the_previous_error(runtime_factory):
    # sequenced_stream: 第一次 error，第二次正常回答
    ...  # 详见 conftest.sequenced_stream 的用法
    await chat.submit("first"); await chat.wait_for_idle()
    assert chat.status.last_error
    await chat.submit("second"); await chat.wait_for_idle()
    assert chat.status.last_error == ""
```

`tests/test_tui_error_banner.py`（新建）：

```python
async def test_a_failed_run_shows_a_sticky_banner(runtime_factory):
    ...  # 用上面的 failing_stream 建 runtime
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause(); await pilot.pause()
        banner = app.query_one(ErrorBanner)
        assert banner.display is True and "provider exploded" in banner.plain

async def test_clicking_the_banner_dismisses_it(...):
    ...
        await pilot.click(ErrorBanner)
        await pilot.pause()
        assert app.query_one(ErrorBanner).display is False
        assert app.chat.status.last_error == ""

async def test_error_without_a_message_uses_a_fallback(...):
    ...  # partial.error_message = None → 横幅含 "the run failed"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_error_banner.py tests/test_app_chat.py -k "error or banner"`
Expected: FAIL（`ImportError: ErrorBanner` / 字段不存在）。

- [ ] **Step 3: 实现**

1. `views.py`：`RunStatus.last_error: str = ""`。
2. `chat.py`：`__init__` 加 `self._last_error = ""`；`submit()` 开头清空并 `self._notify()`；`_run` 的 `finally` 在计算 `termination` 后：`if self.runtime.agent.termination == "error": self._last_error = self.runtime.agent.error_message or "the run failed"`；`status()` 回填；`clear_last_error()` 清空并 notify。
3. `error_banner.py`：`ErrorBanner(Static)`，`set_error()` 用 `Text(f"✘ {message}")`（`_ERROR = "#e08787"`），空串时 `self.display = False` 并清空内容。
4. `app.py`：`compose()` 的 `#dock` 里 `yield ErrorBanner()`（放在 `CommandHints` 之前，即 dock 顶部）；`_refresh_status()` 里 `self.query_one(ErrorBanner).set_error(status.last_error)`；`dismiss_error()` 调 `self.chat.clear_last_error()` 后 `_refresh_status()`。
5. `theme.tcss`：`#error-banner { height: 1; background: #3a1d1f; color: #e08787; padding: 0 1; }`（保持单行，禁止折行）。
6. `docs/guide/tui.md` 加「错误横幅」一段（何时出现、点击关闭、下次提交自动清空）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_error_banner.py tests/test_app_chat.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/views.py zenagent/app/chat.py zenagent/cli/tui/widgets/error_banner.py zenagent/cli/tui/app.py zenagent/cli/tui/theme.tcss tests/test_tui_error_banner.py tests/test_app_chat.py docs/guide/tui.md
git commit -m "feat(tui): surface provider errors in a sticky banner"
```

---

### Task 4: 队列取回（Alt+Up）与双击 Esc 回退

**Files:**
- Modify: `zenagent/app/chat.py`（`dequeue_follow_up`）
- Modify: `zenagent/cli/tui/app.py`（键位 + 双击 Esc 逻辑 + 撤销动作）
- Modify: `zenagent/cli/tui/widgets/prompt_input.py`（`APP_CHORDS`）
- Modify: `zenagent/app/commands.py`（`HOTKEYS_TEXT`）
- Modify: `docs/guide/cli.md`（键盘表）
- Test: `tests/test_tui_controls.py`、`tests/test_app_chat.py`

**Interfaces:**
- Produces: `ChatController.dequeue_follow_up() -> str | None`：取走**最后一条**排队消息（omp 的 dequeue 语义），队列空返回 `None`，成功时 `_notify()`。
- Produces: `ZenAgentApp.action_dequeue()`：取回文本插入 prompt（`PromptInput.insert`）并聚焦；无排队时 no-op。
- Produces: `ZenAgentApp._undo_last_turn()`（async）：`await self._dispatch_command("undo", "")`（复用现成结果处理/提示）。
- 双击 Esc：`DOUBLE_ESCAPE_SECONDS = 0.6`（模块常量）；`action_interrupt` 规则：
  1. `chat.status.running` → 只 `abort()` + notify（并清空计时）；
  2. 草稿非空 → 不动草稿、清空计时；
  3. 空草稿 → 距上次空草稿 Esc `<= DOUBLE_ESCAPE_SECONDS` 时触发 `_undo_last_turn()`；否则只记录时间。
- 键位：app `BINDINGS` 加 `("alt+up", "dequeue", "Dequeue")`；`PromptInput.APP_CHORDS` 加 `"alt+up": "dequeue"`。

- [ ] **Step 1: RED —— 写测试**

`tests/test_app_chat.py`：

```python
async def test_dequeue_follow_up_takes_the_last_queued_message(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.05)
    chat.queue_follow_up("one"); chat.queue_follow_up("two")
    assert chat.dequeue_follow_up() == "two"
    assert chat.status.follow_ups == 1
    assert chat.dequeue_follow_up() == "one"
    assert chat.dequeue_follow_up() is None
    gate.set(); await task
```

`tests/test_tui_controls.py`：

```python
async def test_alt_up_puts_the_last_queued_message_back_in_the_prompt(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.insert("queued text")
        await pilot.press("ctrl+q")          # 入队
        await pilot.pause()
        assert app.chat.status.follow_ups == 1
        await pilot.press("alt+up")
        await pilot.pause()
        assert prompt.text == "queued text"
        assert app.chat.status.follow_ups == 0
        gate.set(); await app.chat.wait_for_idle()

async def test_double_escape_with_an_empty_prompt_undoes_the_last_turn(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi"); await app.chat.wait_for_idle()
        assert fake_runtime.agent.messages
        await pilot.press("escape"); await pilot.press("escape")
        await pilot.pause(); await pilot.pause()
        assert fake_runtime.agent.messages == []

async def test_single_escape_does_not_undo(fake_runtime):
    ...  # 只按一次 escape，messages 保持非空

async def test_escape_while_running_only_aborts(gated_app):
    ...  # 运行中 Esc：termination == "aborted"，messages 不被 undo 截断
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_controls.py tests/test_app_chat.py -k "dequeue or double_escape or single_escape or escape_while"`
Expected: FAIL（方法/键位不存在）。

- [ ] **Step 3: 实现**

1. `chat.py`：按接口实现 `dequeue_follow_up()`。
2. `prompt_input.py`：`APP_CHORDS` 加 `"alt+up": "dequeue"`。
3. `app.py`：import `time`；加常量与 `_last_escape_at`（`__init__` 里初始化 None）；`BINDINGS` 加 alt+up；实现 `action_dequeue`、改写 `action_interrupt`、新增 async `_undo_last_turn`。`_undo_last_turn` 结束调 `_refresh_status()`。
4. `commands.py`：`HOTKEYS_TEXT` 加一行 `alt+up dequeue · double esc rewind`。
5. `docs/guide/cli.md` 的键盘表补两行（alt+up 取回排队；空草稿双击 esc 撤销上一轮）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_controls.py tests/test_app_chat.py tests/test_app_undo_retry.py`
Expected: PASS（`/undo` 与 `/retry` 既有行为不回归）。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/chat.py zenagent/cli/tui/app.py zenagent/cli/tui/widgets/prompt_input.py zenagent/app/commands.py tests/test_tui_controls.py tests/test_app_chat.py docs/guide/cli.md
git commit -m "feat(tui): dequeue follow-ups and double-escape rewind"
```

---

### Task 5: 输入历史与 Ctrl+R 历史搜索

**Files:**
- Create: `zenagent/cli/tui/history.py`
- Modify: `zenagent/cli/tui/screens/modals.py`（`HistorySearch`）
- Modify: `zenagent/cli/tui/app.py`（历史状态、键位、模态回调）
- Modify: `zenagent/cli/tui/widgets/prompt_input.py`（`APP_CHORDS`）
- Modify: `docs/guide/tui.md`
- Modify: `tests/conftest.py`（历史默认路径的隔离 fixture）
- Test: `tests/test_tui_history.py`（新建）

**Interfaces:**
- Produces: `default_history_path() -> Path`（`Path.home() / ".zenagent" / "tui-history.jsonl"`）与 `PromptHistory(path: Path | None = None, *, limit: int = 500)`；`path is None` 时用 `default_history_path()`。
  - 文件格式：每行一个 `{"text": "..."}` JSON 对象；损坏行跳过；读取失败（权限/编码）视为空历史，不得抛异常。
  - `add(text: str) -> None`：跳过空白；与最后一条相同不重复；超过 `limit` 时丢弃最旧；追加写失败必须静默忽略（历史不是关键路径）。
  - `entries() -> list[str]`：按时间正序（最旧在前）。
  - `search(query: str) -> list[str]`：大小写不敏感的子串过滤，**最新在前**。
- **测试隔离**：`tests/conftest.py` 加 autouse fixture，monkeypatch `zenagent.cli.tui.history.default_history_path` 指向 `tmp_path / "tui-history.jsonl"`，防止现有 TUI pilot 测试把历史写进开发者真实 HOME。
- Produces: `HistorySearch(PaletteScreen[str])`：`HistorySearch(entries: list[str])`，最新在前；每行标签为单行预览（`text.replace("\n", " ⏎ ")` 截断 100 字符）；Enter 返回原始文本；Esc 返回 None。搜索框复用 `PaletteScreen` 的 `#palette-search`，不要另造输入框。
- Produces: `ZenAgentApp.__init__(..., history_path: Path | None = None)`；`self.history: PromptHistory`。
- 记录规则：只记录**非 slash**（不以 `/` 开头）且非空的提交；在 `on_prompt_input_submitted` 调 `chat.submit` 之前 `self.history.add(text)`。
- 键位：app `BINDINGS` 加 `("ctrl+r", "history_search", "History")`；`PromptInput.APP_CHORDS` 加 `"ctrl+r": "history_search"`。

- [ ] **Step 1: RED —— 写测试**

`tests/test_tui_history.py`：

```python
def test_history_roundtrips_and_skips_blank_and_duplicate(tmp_path):
    path = tmp_path / "hist.jsonl"
    history = PromptHistory(path)
    history.add("one"); history.add(""); history.add("one"); history.add("two")
    assert PromptHistory(path).entries() == ["one", "two"]

def test_search_is_newest_first_and_case_insensitive(tmp_path):
    history = PromptHistory(tmp_path / "h.jsonl")
    for text in ("fix login", "Fix LOGIN bug", "other"):
        history.add(text)
    assert history.search("fix") == ["Fix LOGIN bug", "fix login"]

def test_corrupt_history_lines_are_skipped(tmp_path):
    path = tmp_path / "hist.jsonl"
    path.write_text('{"text": "ok"}\nnot json\n', encoding="utf-8")
    assert PromptHistory(path).entries() == ["ok"]

def test_multiline_prompts_roundtrip(tmp_path):
    history = PromptHistory(tmp_path / "h.jsonl")
    history.add("line1\nline2")
    assert PromptHistory(tmp_path / "h.jsonl").entries() == ["line1\nline2"]

async def test_ctrl_r_searches_and_recalls_a_prompt(tmp_path, fake_runtime):
    app = ZenAgentApp(fake_runtime, history_path=tmp_path / "hist.jsonl")
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus(); prompt.insert("hello world")
        await pilot.press("enter")
        await app.chat.wait_for_idle()
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.query_one(HistorySearch) is not None
        for key in ("h", "e", "l", "l"):      # 焦点在搜索框，直接打字过滤
            await pilot.press(key)
        await pilot.pause()
        await pilot.press("enter")            # 选中唯一匹配并回填
        await pilot.pause()
        assert prompt.text == "hello world"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_history.py`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

1. 新建 `history.py`（只用标准库 `json`/`pathlib`，不 import textual）；实现 `default_history_path()` + `PromptHistory`。
2. `tests/conftest.py`：autouse fixture 把 `default_history_path` 指到 `tmp_path`（在 `isolated_settings` 附近，写注释说明防止真实 HOME 被写）。
3. `modals.py`：`HistorySearch(PaletteScreen[str])`；复用 `PaletteScreen` 的 `#palette-search` 搜索框与 `#palette-list` 列表（按 `palette.py` 现有实现使用，不要另造一套）。
3. `app.py`：`__init__` 接收 `history_path`、构造 `PromptHistory`；提交时记录；`action_history_search` 用 `open_menu("history", lambda: HistorySearch(self.history.search("") or self.history.entries()[::-1]), callback)`；回调里 `prompt.clear(); prompt.insert(text); prompt.focus()`。
4. `prompt_input.py`：加 `"ctrl+r": "history_search"`。
5. `docs/guide/tui.md` 补「历史搜索 Ctrl+R」；`commands.py::HOTKEYS_TEXT` 加 `ctrl+r history`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_history.py tests/test_tui_pickers.py tests/test_tui_usability.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/cli/tui/history.py zenagent/cli/tui/screens/modals.py zenagent/cli/tui/app.py zenagent/cli/tui/widgets/prompt_input.py tests/conftest.py tests/test_tui_history.py docs/guide/tui.md
git commit -m "feat(tui): prompt history with ctrl+r search"
```

---

### Task 6: `/tools` 命令与调色板派发修正

**Files:**
- Modify: `zenagent/app/support.py`（`describe_tools`）
- Modify: `zenagent/app/commands.py`（`tools` 命令）
- Modify: `zenagent/cli/tui/app.py`（`_run_command` 走统一 dispatch）
- Modify: `zenagent/cli/completion.py`、`zenagent/cli/repl.py`（REPL `/tools`）
- Modify: `docs/guide/cli.md`、`docs/guide/tui.md`
- Test: `tests/test_app_commands.py`、`tests/test_repl_logic.py`、`tests/test_tui_usability.py`

**Interfaces:**
- Produces: `describe_tools(tools: list[AgentTool]) -> str`，格式固定：

```
tools (9):
  bash: Run a shell command in the working directory. ...
  read_file: Read a UTF-8 text file. ...
```

  （`tools` = `runtime.agent.tools`，因此 MCP/skill 工具同样会被列出。）
- Produces: `CommandRegistry` 的 `tools` 命令（无参数），返回 `CommandResult(message=describe_tools(runtime.agent.tools))`。
- Produces: `ZenAgentApp._run_command(name)` 改为 `self.run_worker(self._dispatch_command(name, ""))`——修掉旧的「palette 里除了 model/sessions/quit 都提示 unavailable」。
- Produces: REPL `SLASH_COMMANDS["/tools"]` 与 `handle_slash` 分支（`print_fn(describe_tools(agent.tools))`）。

- [ ] **Step 1: RED —— 写测试**

`tests/test_app_commands.py`：

```python
async def test_tools_lists_names_and_descriptions(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("tools", "")
    names = [tool.name for tool in services.runtime.agent.tools]
    assert result.message and "read_file" in result.message and "bash" in result.message
    assert f"tools ({len(names)})" in result.message
```

`tests/test_repl_logic.py`：

```python
async def test_slash_tools_lists_the_tool_set(isolated_sessions, capsys):
    code = await repl("glm-5.3", stream_fn=scripted_stream(), api_key="k",
                      input_fn=scripted_input("/tools", "/exit"),
                      print_fn=lambda line: print(line))
    assert code == 0
    out = capsys.readouterr().out
    assert "tools (" in out and "read_file" in out
```

`tests/test_tui_usability.py`：

```python
async def test_palette_commands_dispatch_through_the_registry(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app._run_command("tools")
        await pilot.pause(); await pilot.pause()
        assert any("read_file" in row.plain for row in app.query(NoticeRow))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_app_commands.py tests/test_repl_logic.py tests/test_tui_usability.py -k "tools or palette_commands"`
Expected: FAIL。

- [ ] **Step 3: 实现**

1. `support.py`：`describe_tools`（描述取 `tool.description.strip()`；按工具顺序；每行两个空格缩进）。
2. `commands.py`：`@_command("tools", "list the tools the model can call")` + 注册进 `build_default_commands`。
3. `app.py`：`_run_command` 改为 worker 调用 `_dispatch_command`（保留 `SystemCommand` 生成器不变）。
4. `completion.py`：`SLASH_COMMANDS` 加 `/tools`；`repl.py` 的 `handle_slash` 加分支（`from zenagent.app.support import describe_tools`）。
5. 文档：`docs/guide/cli.md` 命令表、`docs/guide/tui.md` 斜杠命令列表各补 `/tools`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_app_commands.py tests/test_repl_logic.py tests/test_tui_usability.py tests/test_completion.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/support.py zenagent/app/commands.py zenagent/cli/tui/app.py zenagent/cli/completion.py zenagent/cli/repl.py tests/test_app_commands.py tests/test_repl_logic.py tests/test_tui_usability.py docs/guide/cli.md docs/guide/tui.md
git commit -m "feat: add /tools and route palette commands through the registry"
```

---

### Task 7: REPL `/retry` 对齐

**Files:**
- Create: `zenagent/app/retry.py`
- Modify: `zenagent/app/chat.py`（`retry()` 改为复用共享实现）
- Modify: `zenagent/cli/repl.py`、`zenagent/cli/completion.py`
- Modify: `docs/guide/cli.md`
- Test: `tests/test_repl_logic.py`、`tests/test_app_undo_retry.py`

**Interfaces:**
- Produces: `prepare_retry(agent: Agent, session: Session) -> tuple[str, Session] | None`：
  - 空 transcript / 最后一条不是 `UserMessage` 的回复（即最后一条 user 之后没有 assistant 内容）/ user 文本为空 → `None`；
  - 否则 `truncated = session_module.truncate_last_turn(...)` 直到**最后一条 user 之前**（复用 `zenagent.agent.session.truncate_last_turn`），`agent.replace_messages(truncated)`，`Session(session.path).rewrite(truncated)` 生成新会话，返回 `(user_text, new_session)`。
  - `agent.is_streaming` 时直接返回 `None`。
- Produces: `ChatController.retry()` 用 `prepare_retry` 实现，行为不变（返回重试文本或 None）。
- Produces: REPL `/retry`：在主循环解析出 command 之后、进入 `handle_slash` 之前拦截；成功后把取回的文本当成下一轮 prompt 立即执行（`command = ""`），失败打印 `nothing to retry`。
- 补全/帮助：`SLASH_COMMANDS` 加 `"/retry": "re-run the last turn (new session file)"`。

- [ ] **Step 1: RED —— 写测试**

`tests/test_repl_logic.py`：

```python
async def test_slash_retry_reruns_the_last_prompt(isolated_sessions, capsys):
    stream = scripted_stream(0, "first answer", "retry answer")
    code = await repl("glm-5.3", stream_fn=stream, api_key="k",
                      input_fn=scripted_input("hello", "/retry", "/exit", settle=0.05),
                      print_fn=lambda *a: None)
    assert code == 0
    assert len(stream.requests) == 2
    # 两次请求里都只有一条 user 消息，且都是同一句 "hello"（retry 不复制 prompt）。
    for request in stream.requests:
        users = [m for m in request if m.to_json()["role"] == "user"]
        assert len(users) == 1 and users[0].content == "hello"
```

（`scripted_stream(delay, *turns)` 的每个 turn 是一段回复文本，按文件内现有用法传入。）

`tests/test_app_undo_retry.py`：既有 retry 用例全部保持绿（不改断言语义）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_repl_logic.py -k retry`
Expected: FAIL（`unknown command: /retry`）。

- [ ] **Step 3: 实现**

1. `zenagent/app/retry.py`：`from zenagent.agent.session import Session, truncate_last_turn`；`_user_text` 用与 `chat.py` 相同的内容提取逻辑（`TextContent`/`ImageContent` 标记）。
2. `chat.py`：`retry()` 改为调用 `prepare_retry`，其余（reload transcript、启动 `_run`、notify）保持不变；删除重复的查找/截断代码。
3. `repl.py`：主循环里 `command, argument = split_input(line)` 之后加拦截块（见 Interfaces），成功后 `switch_session(new_session)` 并让 `prompt_text` 走正常 `agent.prompt` 路径。
4. `completion.py`：加 `/retry` 条目。
5. `docs/guide/cli.md` 的 REPL 命令表补 `/retry`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_repl_logic.py tests/test_app_undo_retry.py tests/test_app_sessions.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/app/retry.py zenagent/app/chat.py zenagent/cli/repl.py zenagent/cli/completion.py tests/test_repl_logic.py tests/test_app_undo_retry.py docs/guide/cli.md
git commit -m "feat(repl): align /retry with the TUI"
```

---

### Task 8: `/pin`（会话置顶）

**Files:**
- Modify: `zenagent/agent/session.py`（pins sidecar 读写）
- Modify: `zenagent/app/sessions.py`（排序与 toggle）
- Modify: `zenagent/app/commands.py`（`pin` 命令）
- Modify: `zenagent/cli/tui/screens/modals.py`（`SessionPicker` 标记）
- Modify: `docs/guide/sessions.md`
- Test: `tests/test_session.py`、`tests/test_app_sessions.py`、`tests/test_app_commands.py`

**Interfaces:**
- Produces（`agent/session.py`，与 `.titles.json` 同目录的 `.pins.json`）：
  - `PINS_FILENAME = ".pins.json"`；
  - `read_session_pins(directory: Path | None = None) -> set[str]`（文件名集合；损坏/缺失返回空集）；
  - `set_session_pin(session_file: Path, pinned: bool) -> bool`（返回最终状态；写失败只影响置顶状态，不抛给会话逻辑——写失败时向上抛 OSError 由调用方决定）。
- Produces（`app/sessions.py`）：
  - `SessionEntry.pinned: bool = False`；
  - `SessionManager.entries()` 排序：pinned 在前，其余保持 `list_sessions` 的时间序；
  - `SessionManager.toggle_pin(path: Path) -> bool`（目标必须是已存在的 `*.jsonl`，否则 `ValueError`）。
- Produces：`/pin` 命令：无参数时 toggle **当前**会话，返回 `pinned <title>` / `unpinned <title>`。
- Produces：`SessionPicker` 的行标记：pinned 用 `★`，当前会话仍用 `●`（`★●` 叠加顺序固定为先 star 后 current）。

- [ ] **Step 1: RED —— 写测试**

`tests/test_session.py`：

```python
def test_pins_sidecar_roundtrip_and_corruption(tmp_path):
    session = tmp_path / "s.jsonl"; session.touch()
    assert read_session_pins(tmp_path) == set()
    assert set_session_pin(session, True) is True
    assert read_session_pins(tmp_path) == {"s.jsonl"}
    (tmp_path / ".pins.json").write_text("{broken", encoding="utf-8")
    assert read_session_pins(tmp_path) == set()

def test_set_session_pin_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError):
        set_session_pin(tmp_path / "nope.jsonl", True)
```

`tests/test_app_sessions.py`：

```python
async def test_pinned_sessions_sort_first(session_runtime, monkeypatch):
    manager = SessionManager(session_runtime)
    await manager.new()
    first = manager.current()
    await manager.new()
    manager.toggle_pin(first)
    entries = manager.entries()
    assert entries[0].path == first and entries[0].pinned is True
```

`tests/test_app_commands.py`：

```python
async def test_pin_command_toggles_the_current_session(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("pin", "")
    assert result.message and "pinned" in result.message
    assert services.sessions.entries()[0].pinned is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_session.py tests/test_app_sessions.py tests/test_app_commands.py -k pin`
Expected: FAIL（函数/字段不存在）。

- [ ] **Step 3: 实现**

1. `session.py`：按 `.titles.json` 的现有写法实现 `.pins.json`（读写都用 `json`，损坏回退空集；`set_session_pin` 先校验 `suffix == ".jsonl"` 且 `is_file()`）。
2. `app/sessions.py`：`entries()` 读取 pins，回填 `pinned` 并 `sorted(entries, key=lambda e: not e.pinned)`（稳定排序）；实现 `toggle_pin`。
3. `commands.py`：`@_command("pin", "pin or unpin the current session")`，无参数 toggle，带参数时解析 `on|off`（非法参数给提示）。
4. `modals.py`：`SessionPicker` 的 label 构造加 `★` 标记。
5. `docs/guide/sessions.md` 补 `/pin` 与 picker 标记说明。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_session.py tests/test_app_sessions.py tests/test_app_commands.py tests/test_tui_sessions.py tests/test_project_scoping.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add zenagent/agent/session.py zenagent/app/sessions.py zenagent/app/commands.py zenagent/cli/tui/screens/modals.py tests/test_session.py tests/test_app_sessions.py tests/test_app_commands.py docs/guide/sessions.md
git commit -m "feat: pin sessions to the top of the picker"
```

---

### Task 9: TUI 自动压缩 + transcript 分隔线

**Files:**
- Move: `zenagent/cli/compact.py` → `zenagent/app/compact.py`（`git mv`）
- Modify: `zenagent/cli/repl.py`、`zenagent/cli/main.py`（import 路径；行为不变）
- Modify: `zenagent/app/views.py`（`TranscriptNotice`）
- Modify: `zenagent/app/chat.py`（`compact_now`、自动压缩、notice 队列）
- Modify: `zenagent/app/commands.py`（`compact` 命令真正执行）
- Modify: `zenagent/cli/tui/widgets/rows.py`（`DividerRow`）、`zenagent/cli/tui/widgets/transcript.py`（`append_divider`）、`zenagent/cli/tui/app.py`（drain notice）
- Modify: `docs/internals/context-compaction.md`、`docs/guide/tui.md`、`docs/feature-map.md`、`docs/reference/file-map.md`、`docs/guide/sessions.md`、`docs/internals/persistence.md`（路径引用改为 `app/compact.py`）
- Test: `tests/test_compact.py`、`tests/test_app_chat.py`、`tests/test_tui_compact.py`（新建）

**Interfaces:**
- Produces（`app/views.py`）：

```python
@dataclass(frozen=True)
class TranscriptNotice:
    kind: Literal["divider", "info"]
    text: str
```

- Produces（`ChatController`）：
  - `self._notices: list[TranscriptNotice]`；
  - `take_notices() -> list[TranscriptNotice]`（取走并清空，不 notify）；
  - `async def compact_now(self) -> str | None`：
    - `self._running` 或 `agent.is_streaming` → 返回 `None`；
    - `not agent.messages` → 返回 `None`；
    - `before = self.runtime.guard.last_usage`（`input + cache_read`；为 None 时用 `usage_total(agent.messages)` 的 `input + output`）；
    - `path = await compact(self.runtime.agent)`；成功后 `await self.runtime.switch_session(path)`、`self.reload_transcript()`、append `TranscriptNotice("divider", f"compacted · {before:,} prompt tokens → summary session")`，返回该文案；
    - `compact()` 抛出的 `ValueError/RuntimeError` 原样向上抛，由命令层转成提示（不得留下半截状态：`compact()` 保证失败不改 transcript）。
  - `_run` 的 `finally`：在 `self._running = False` 之后、启动下一条排队消息之前，若 `agent.termination != "aborted"` 且 `should_auto_compact(self.runtime.guard.last_usage, agent.model.context_window)`，执行 `try: await self.compact_now() except Exception as exc: self._notices.append(TranscriptNotice("info", f"auto-compaction failed: {exc}"))`。
- Produces：`/compact` 命令改为 `message = await services.chat.compact_now(); return CommandResult(message=message or "nothing to compact", reset_transcript=True)`（TUI 里不再是「turn 后执行」的占位话）。
- Produces：`DividerRow(Static)`（dim 单行 `── <text> ──`，`height: 1`）；`TranscriptView.append_divider(text: str) -> None`。
- 时序约束：`ZenAgentApp._refresh_status()` 在 render 状态之后 drain `chat.take_notices()`，`kind=="divider"` → `append_divider`，否则 `append_notice`。这样 `reset_transcript` 之后 divider 仍会补挂上。
- `app/compact.py` 保留原 `__all__`（`AUTO_COMPACT_THRESHOLD`、`SUMMARIZE_PROMPT`、`compact`、`maybe_auto_compact`、`should_auto_compact`）。

- [ ] **Step 1: RED —— 写测试**

`tests/test_compact.py`：仅更新 import 到 `zenagent.app.compact`；既有断言不变（先跑一次确认仍全绿，作为移动的基线）。

`tests/test_app_chat.py`：

```python
async def test_compact_now_switches_to_the_summary_session(runtime_factory, capsys):
    runtime = await runtime_factory(stream_fn=sequenced_stream(["hi", "summary text"]))
    chat = ChatController(runtime)
    await chat.submit("hello"); await chat.wait_for_idle()
    old_path = runtime.session.path
    message = await chat.compact_now()
    assert message and "compacted" in message
    assert runtime.session.path != old_path and runtime.session.path.exists()
    assert len(runtime.agent.messages) == 1  # 只剩摘要 UserMessage
    notices = chat.take_notices()
    assert notices and notices[0].kind == "divider" and "compacted" in notices[0].text
    assert capsys.readouterr().out == "", "TUI controller must never print to stdout"

async def test_compact_now_refuses_while_streaming(runtime_factory):
    ...  # gated_stream 运行中 → await chat.compact_now() is None

async def test_auto_compaction_runs_after_a_turn(runtime_factory, monkeypatch):
    import zenagent.app.chat as chat_module
    monkeypatch.setattr(chat_module, "should_auto_compact", lambda *a, **k: True)
    runtime = await runtime_factory(stream_fn=sequenced_stream(["hi", "summary text"]))
    chat = ChatController(runtime)
    await chat.submit("hello"); await chat.wait_for_idle()
    assert chat.take_notices()[0].kind == "divider"
```

`tests/test_tui_compact.py`（新建）：

```python
async def test_slash_compact_shows_a_divider_and_the_summary(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi"); await app.chat.wait_for_idle()
        prompt = app.query_one(PromptInput)
        prompt.focus(); prompt.insert("/compact")
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        dividers = list(app.query(DividerRow))
        assert dividers and "compacted" in dividers[0].plain
        # 压缩后的摘要是一条 UserMessage，所以渲染成 UserBlock 而不是 Markdown。
        assert any("此前会话摘要" in block.plain for block in app.query(UserBlock))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_app_chat.py tests/test_tui_compact.py -k "compact"`
Expected: FAIL（`compact_now` 不存在 / `DividerRow` 不存在）。

- [ ] **Step 3: 实现（顺序很重要）**

1. `git mv zenagent/cli/compact.py zenagent/app/compact.py`；改 3 处 import（`cli/repl.py`、`cli/main.py`、`tests/test_compact.py`）并先跑 `tests/test_compact.py` 确认「纯移动」不回归。
2. `views.py` 加 `TranscriptNotice`。
3. `chat.py`：notice 队列 + `compact_now` + `_run` 自动压缩（注意 import `should_auto_compact`、`compact` 自 `zenagent.app.compact`；**不得**传默认 `print_fn=print`）。
4. `commands.py`：`_compact` 改为 `await services.chat.compact_now()`；`CommandResult(..., reset_transcript=True)`。
5. `rows.py`：`DividerRow`（`Text("── " + text + " ──", style=_DIM)`）；`transcript.py`：`append_divider`（mount + `scroll_end`）。
6. `app.py::_refresh_status()`：drain notices（见 Interfaces）。
7. 文档路径与行为更新（6 个文件按 Files 列）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_compact.py tests/test_app_chat.py tests/test_tui_compact.py tests/test_repl_logic.py`
Expected: PASS；REPL 的 `auto-compacted into ...` 输出与 `test_compact.py` 全绿。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add -A zenagent/cli/compact.py zenagent/app/compact.py zenagent/app/views.py zenagent/app/chat.py zenagent/app/commands.py zenagent/cli/tui zenagent/cli/repl.py zenagent/cli/main.py tests/test_compact.py tests/test_app_chat.py tests/test_tui_compact.py docs
git commit -m "feat(tui): auto-compact at the threshold with a transcript divider"
```

---

### Task 10: TUI 图片附件与 chip 带

**Files:**
- Move: `zenagent/cli/images.py` → `zenagent/attachments.py`（`git rm` 旧文件，无兼容 shim）
- Modify: `zenagent/cli/repl.py`、`tests/test_images.py`、`tests/test_repl_logic.py`（import 路径）
- Modify: `zenagent/app/chat.py`（附件状态 + 带图提交）
- Modify: `zenagent/app/commands.py`（`image`、`images` 命令）
- Create: `zenagent/cli/tui/widgets/attachments.py`（`AttachmentChips`、`Chip`）
- Modify: `zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`
- Modify: `docs/guide/tui.md`、`docs/guide/cli.md`
- Test: `tests/test_app_commands.py`、`tests/test_tui_attachments.py`（新建）

**Interfaces:**
- Produces（`zenagent/attachments.py`）：原 `MAX_IMAGE_BYTES`、`read_image_argument`（实现原样搬移）。
- Produces（`ChatController`）：
  - `attachments` property（返回副本）、`attach_image(block: ImageContent) -> None`、`remove_attachment(index: int) -> None`、`clear_attachments() -> None`（三者的写操作都要 `_notify()`，否则 chips 不刷新）；
  - `submit(text: str, *, images: list[ImageContent] | None = None)`：未显式传 `images` 时使用并清空 pending 附件；运行中提交仍只排队文本（附件保留到下一次真正发送）；
  - `_run(text, images=None)` 内部 `await self.runtime.agent.prompt(text, images=images or None)`。
- Produces（命令，注册进 `build_default_commands`）：
  - `/image <path>`：非 vision 模型 → `"<model> does not accept images"`；`read_image_argument` 的 `ValueError` 原样作为 message；成功 → `"attached <mime> (<N> pending)"`；
  - `/images`：无参数列出 `1. image/png (12345 base64 chars)` 或 `"no attached images"`；`/images clear` 清空并返回 `"cleared attached images"`。
- Produces（TUI）：
  - `Chip(Static)`：一行 `[image/png]`，`on_click` 调 `app.remove_attachment(self.index)`；
  - `AttachmentChips(Horizontal)`：`set_attachments(blocks)`（按 mime 重建 `Chip` 子节点；空列表时 `display = False`，**初始也是 `display = False`**，保证 `test_tui_layout.py` 的 dock 相邻断言不被破坏）；
  - `ZenAgentApp.remove_attachment(index)`：`chat.remove_attachment(index)` + `_refresh_status()`；
  - `_refresh_status()` 调 `AttachmentChips.set_attachments(self.chat.attachments)`；dock 布局顺序：`CommandHints` → `AttachmentChips` → `StatusBand` → prompt row。

- [ ] **Step 1: RED —— 写测试**

`tests/test_app_commands.py`：

```python
async def test_image_command_attaches_and_images_lists(services, tmp_path):
    png = tmp_path / "pic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    registry = build_default_commands(services)
    result = await registry.dispatch("image", str(png))
    assert result.message and "attached image/png" in result.message
    assert len(services.chat.attachments) == 1
    listed = await registry.dispatch("images", "")
    assert listed.message and "image/png" in listed.message
    cleared = await registry.dispatch("images", "clear")
    assert cleared.message and services.chat.attachments == []

async def test_image_command_rejects_non_vision_models(services, monkeypatch, tmp_path):
    png = tmp_path / "pic.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    monkeypatch.setattr(services.runtime.agent.model, "supports_vision", False)
    result = await build_default_commands(services).dispatch("image", str(png))
    assert "does not accept images" in (result.message or "")
    assert services.chat.attachments == []
```

`tests/test_tui_attachments.py`（新建）：

```python
async def test_image_command_shows_a_chip_and_submits_the_image(tmp_path, runtime_factory):
    seen: list[list] = []

    async def recording_stream(model, messages, tools, options):
        seen.append(list(messages))
        partial = AssistantMessage(content=[TextContent(text="ok")], provider="p",
                                   model="m", usage=Usage(), stop_reason="pending",
                                   timestamp=0)
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=recording_stream)
    app = ZenAgentApp(runtime)
    png = tmp_path / "pic.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit(f"/image {png}")
        await pilot.pause(); await pilot.pause()
        chips = app.query_one(AttachmentChips)
        assert chips.display is True
        assert any("image/png" in chip.plain for chip in app.query(Chip))
        await app.chat.submit("look")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.chat.attachments == [] and chips.display is False
        user = next(m for m in seen[-1] if m.to_json()["role"] == "user")
        assert any(block.to_json()["type"] == "image" for block in user.content)

async def test_clicking_a_chip_removes_the_attachment(fake_runtime, tmp_path):
    ...  # /image → pilot.click(app.query(Chip).first()) → chat.attachments == [] 且 chips.display is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_tui_attachments.py tests/test_app_commands.py -k "image"`
Expected: FAIL。

- [ ] **Step 3: 实现**

1. `git mv zenagent/cli/images.py zenagent/attachments.py`；更新 `cli/repl.py`、`tests/test_images.py`、`tests/test_repl_logic.py` 的 import。
2. `chat.py`：附件状态与带图 `submit`/`_run`（注意 `is_streaming` 与排队分支：排队时不清附件）。
3. `commands.py`：`image`/`images` 命令（import `Path`、`read_image_argument`、`ImageContent` 不需要），注册。
4. 新建 chips widget；`app.py` dock/刷新/`remove_attachment`；`theme.tcss` 加 `#attachment-chips`（`height: 1; display: none? no — 由 display set 控制`，背景 `#141c24`）与 chip 样式。
5. `docs/guide/tui.md` 图片一段（`/image`、chip、点击移除、无图片粘贴）；`docs/guide/cli.md` 的 `/image`/`/images` 条目注明 TUI 同样可用。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q tests/test_tui_attachments.py tests/test_app_commands.py tests/test_images.py tests/test_repl_logic.py`
Expected: PASS。

- [ ] **Step 5: 全量闸门 + 提交**

```bash
bash scripts/check.sh
git add -A zenagent/cli/images.py zenagent/attachments.py zenagent/app/chat.py zenagent/app/commands.py zenagent/cli/tui tests/test_tui_attachments.py tests/test_app_commands.py tests/test_images.py tests/test_repl_logic.py zenagent/cli/repl.py docs/guide/tui.md docs/guide/cli.md
git commit -m "feat(tui): attach images with a chip bar"
```

---

## 收尾验收（全部 Task 完成后）

- [ ] `bash scripts/check.sh` 全绿（ruff + 全量 pytest）。
- [ ] 手工冒烟（真实终端，任一可用 provider）：
  - `/tools` 列出工具；拿一个不存在的模型/断网触发 provider error，横幅出现、点击关闭；
  - 一次长任务后用 `/compact` 出现 divider 且 transcript 只剩摘要；
  - `ctrl+r` 能搜到上一轮 prompt；`alt+up` 能取回排队的消息；
  - `zen agent`（REPL）里 `/retry`、`/tools` 可用；
  - `/image <png>` → chip 出现 → 发送后模型收到图片（vision 模型）。
- [ ] `docs/guide/tui.md`、`docs/guide/cli.md`、`docs/internals/context-compaction.md` 的符号/路径与代码一致；`docs/feature-map.md` 中压缩路径已更新。
- [ ] 在 `09-zenagent-vs-omp-gap.md` 的 §0.1 表格里把已完成项从「值得做」标记为 ✅（可选，留给文档收尾）。
