# 批次 3：熵减（纯搬运，不改行为） 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消掉审查报告里那几处"复制粘贴型"重复与 god 模块：工具的构造助手、loop 的工具执行与 loop-guard、session 的格式与"会话库"。

**Architecture:** 纯搬家/提取，**零行为变化**。每个任务都先让现有测试绿（或先写一条指向新模块的 import/行为 pin），搬完再跑同一批测试与全量门禁；任何断言变化都说明搬错了。

**Tech Stack:** Python 3.11+、pytest/pytest-asyncio、ruff。无新依赖。

**Spec:** `docs/reviews/2026-11-06-architecture-review.md`（I21、I22，及 `agent/loop.py`/`agent/session.py` 的 god-module 条目）

## Global Constraints

- 行为不变：不新增/删除任何工具、事件、记录；截断文案、超时夹取区间、错误 `error_kind` 全部保持逐字一致。
- 搬迁后 `ruff check .` clean、`pytest -q` 全绿（基线 2208 passed）；`tests/test_layering.py` 仍绿。
- 每个任务只 `git add` 该任务 `Files` 列出的路径；禁止 `git add -A`。
- 内核仍不认识 harness 记录种类（`tests/test_session.py::test_kernel_does_not_know_record_kinds` 必须继续绿）。

## Review Focus

1. **截断文案**：`tools/_result.py` 的 `clip` 必须产出与各模块逐字相同的 `"\n… (truncated)"`（memory/lsp/github 三个调用点行为不变）。
2. **`details` 传参**：`text_result(...)` 的 `**details` 必须与 `_text` 一样原样放进 `ToolResultAgent.details`，不能吞掉 `None` 与空 dict 的区别（`details={}` 与 `details=None` 都要按 `_text` 原样传）。
3. **loop guard 前缀**：`is_loop_guard_message` 搬到 `agent/guard.py` 后，`app/views.py` 的调用点仍走同一个实现（不能出现第二个前缀判定）。
4. **工具执行顺序**：`agent/tool_exec.py` 提取后，并行/串行批次与结果写回顺序必须与今天一致（`tests/test_loop.py` 的顺序断言是判据）。
5. **会话库搬迁**：`list_sessions/latest_session/read_session_titles/set_session_title/brief` 搬到 `app/` 后，`--resume latest`、`/sessions`、TUI 会话列表、RPC 会话列表全部仍工作。

## File Structure

| File | Task | 职责 |
|---|---|---|
| `zenagent/tools/_result.py` | 1 | `text_result`、`clip` |
| `zenagent/tools/{fs,bash,search,wait,ask,memory,lsp,github,web/tools}.py` | 1 | 删本地 `_text`/`_clip`，改用共享助手 |
| `zenagent/agent/guard.py` | 2 | loop-guard：前缀、签名、轮询判定、注入文案 |
| `zenagent/agent/tool_exec.py` | 3 | 单次工具执行、批调度、独占判定、结果预算调用 |
| `zenagent/agent/session.py` / `zenagent/app/session_library.py` | 4 | 格式（Session）与"会话库"（列表/标题/置顶/预览）分离 |

---

### Task 1: `tools/_result.py`

**Files:**
- Create: `zenagent/tools/_result.py`, `tests/test_tools_result.py`
- Modify: `zenagent/tools/{fs,bash,search,wait,ask,memory,lsp,github,web/tools}.py`

**Interfaces:**
- Produces: `text_result(text: str, *, is_error: bool = False, **details) -> ToolResultAgent`；
  `clip(text: str, limit: int) -> tuple[str, bool]`（超出时返回 `text[:limit] + "\n… (truncated)", True`）。

- [ ] **Step 1: 写测试** `tests/test_tools_result.py`：`text_result("x")` 的 content/details/is_error 形状；
  `text_result("x", is_error=True, exit_code=0).details == {"exit_code": 0}`；
  `clip("short", 100) == ("short", False)`；`clip("x" * 200, 100) == ("x" * 100 + "\n… (truncated)", True)`。
- [ ] **Step 2: 跑测试确认失败**（模块不存在）。
- [ ] **Step 3: 实现 `_result.py`**，并在 9 个工具模块里删掉本地 `_text`、把 `_clip` 换成 `clip`（memory 用 `body, _ = clip(body, _MAX_OUTPUT_CHARS)` 语义等价的写法）。
- [ ] **Step 4: 跑测试**：`uv run pytest tests/test_tools_result.py tests/test_tools_fs.py tests/test_tools_bash.py tests/test_tools_search.py tests/test_tools_wait.py tests/test_ask.py tests/test_memory.py tests/test_lsp.py tests/test_tools_github.py tests/test_tools_web.py -q` → 全 PASS（文件名以实际为准，用 `ls tests | grep` 对准）。
- [ ] **Step 5: 提交** `refactor(tools): one shared result helper`.

---

### Task 2: `agent/guard.py`

**Files:**
- Create: `zenagent/agent/guard.py`
- Modify: `zenagent/agent/loop.py`、`zenagent/app/views.py`
- Test: `tests/test_loop.py`（既有 loop-guard 用例即判据）

**Interfaces:**
- Produces: `LOOP_GUARD_PREFIX`、`is_loop_guard_message(message) -> bool`、`calls_signature(calls) -> str`、`is_poll_call(call) -> bool`、`corrective_message(streak: int) -> UserMessage`。
- 兼容：`zenagent/agent/loop.py` 仍 re-export `LOOP_GUARD_PREFIX`/`is_loop_guard_message`（`app/views.py` 与既有测试都从这里取）。

- [ ] **Step 1: 搬迁**：把 `_LOOP_GUARD_TEXT`/`_LOOP_GUARD_THRESHOLD`/`_POLL_COMMAND`/`is_loop_guard_message`/`_calls_signature`/`_is_poll_call` 移到 `guard.py` 并改成公开名（`LOOP_GUARD_TEXT` 等），在 `loop.py` 里 `from zenagent.agent.guard import ...` 并保留 re-export。
- [ ] **Step 2: 改调用点**：`loop.py` 的 streak 逻辑与 `app/views.py` 的 import。
- [ ] **Step 3: 跑测试**：`uv run pytest tests/test_loop.py tests/test_app_views.py tests/test_agent_class.py -q` → 全 PASS。
- [ ] **Step 4: 提交** `refactor(agent): move the loop guard out of the loop`.

---

### Task 3: `agent/tool_exec.py`

**Files:**
- Create: `zenagent/agent/tool_exec.py`
- Modify: `zenagent/agent/loop.py`
- Test: `tests/test_loop.py`、`tests/test_result_budget.py`

**Interfaces:**
- Produces: `ToolOutcome`（原 `_ToolOutcome`）、`execute_tool(config, call, abort, on_update=None) -> ToolOutcome`、`run_tool_batch(config, calls, abort, emit) -> list[ToolOutcome]`、`is_exclusive(tool) -> bool`。
- 兼容：`loop.py` 保留 `_ToolOutcome = ToolOutcome` 别名一版，随后删除所有内部引用。

- [ ] **Step 1: 搬迁**：`_execute_tool`/`_run_tool`/`_run_tool_batch`/`_is_exclusive`/`_ToolOutcome` 移入 `tool_exec.py`，`loop.py` 只 import 并调用。
- [ ] **Step 2: 跑测试**：`uv run pytest tests/test_loop.py tests/test_result_budget.py tests/test_tool_timeout.py -q` → 全 PASS（并行/串行与写回顺序由既有断言覆盖）。
- [ ] **Step 3: 提交** `refactor(agent): give tool execution its own module`.

---

### Task 4: `app/session_library.py`

**Files:**
- Create: `zenagent/app/session_library.py`
- Modify: `zenagent/agent/session.py`（只留格式：`Session`、`session_path`、`unique_session_path`、`truncate_last_turn`、`RECORD_VERSION`、`HEADER_TYPE`）、`zenagent/app/sessions.py`、`zenagent/cli/{sessionscmd,repl,rpc}.py`、`zenagent/cli/tui/**`（改 import）
- Test: `tests/test_session.py`、`tests/test_app_sessions.py`、`tests/test_project_scoping.py`、`tests/test_cli.py`、`tests/test_rpc_v2.py`

**Interfaces:**
- Produces: `zenagent.app.session_library` 导出 `list_sessions`、`latest_session`、`read_session_titles`、`set_session_title`、`read_session_pins`、`set_session_pin`、`brief`、`session_title`、`sessions_dir`、`all_session_dirs`；`agent/session.py` 为兼容保留同名 re-export（下一批删除）。
- 兼容点：`Session` 类本身不动（append/load/records/carry/fork_at/rewrite）。

- [ ] **Step 1: 搬迁**：把浏览/标题/置顶/预览函数移到 `app/session_library.py`，`agent/session.py` 加 `from zenagent.app.session_library import ...  # noqa: F401` 之类的显式 re-export（注意不能形成循环：`session_library` 不得 import `Session` 以外的东西）。
- [ ] **Step 2: 改 import**：`app/sessions.py`、`cli/sessionscmd.py`、`cli/repl.py`、`cli/rpc.py`、`cli/tui/**` 改为从 `app.session_library` 取；`app/` 之外的调用点一次性替换。
- [ ] **Step 3: 跑测试**：`uv run pytest tests/test_session.py tests/test_app_sessions.py tests/test_project_scoping.py tests/test_cli.py tests/test_rpc_v2.py tests/test_tui_sessions.py -q` → 全 PASS。
- [ ] **Step 4: 全量门禁 + 提交**：`bash scripts/check.sh` → ruff clean、pytest 全绿；`refactor(session): split the format from the session library`。

---

## Self-Review

- 全部任务是搬家，唯一的"新代码"是 `_result.py`（有单测）与两个空壳模块的 export。
- 判据统一：既有测试 + 全量门禁；`Review Focus` 的五条各自在既有断言里有对应（截断文案、details、guard 前缀、并行顺序、会话列表）。
- 逐条删除列表（本批不做）：`providers/catalog.py` 的 YAML 子集解析器、`skills/templates` 的 discovery 合并、`tools/task.py` 的 supervisor 抽象——它们需要新设计，不属于"纯搬运"。
