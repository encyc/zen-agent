# 批次 2：会话记录 schema 归属 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让会话侧信道记录有一个唯一的 schema 归属点（`zenagent/app/records.py`），内核不再认识 harness 的记录种类，并让 `tool_end` 由 Runtime 而不是 UI 控制器生产（`zen -p` 会话与交互式同构）。

**Architecture:** 记录仍是扁平 `{"type": ..., "timestamp": ..., ...}`，只追加、只经 `Session.append_meta`。新增 `app/records.py` 持有：类型常量、类型化构造器、谓词（`is_*`）与"重写后该保留哪些记录"的谓词工厂。内核 `Session.carry_records/fork_at/rewrite` 改为接受 `keep: Callable[[dict], bool] | None`；`None` 时只用**结构**规则（有 `call_id` 的记录跟着仍存活的调用走，其余原样保留），不再出现任何 `"tool_end"`/`"compaction"` 字面量。`tool_end` 生产者从 `ChatController._on_event` 移到 `SessionMetaRecorder`（Runtime 的订阅者）。

**Tech Stack:** Python 3.11+、JSONL、pytest/pytest-asyncio、ruff。无新依赖。

**Spec:** `docs/reviews/2026-11-06-architecture-review.md`（C3、C4，及 Important「四种只写不读的记录」）

## Global Constraints

- 记录形状不变：扁平 dict、无 `role` 键、只经 `Session.append_meta` 写入（`OSError` 必须被吞掉，run 不能被观测性打断）。
- 普通消息行字节不变；`RECORD_VERSION` 仍是 1（本批纯重构 + 一处生产者位置变化，老文件照读）。
- 内核 `zenagent/agent/` 里不得再出现 `"tool_end"`/`"compaction"`/`"run_start"`/`"run_end"`/`"retry"`/`"mode"` 这些记录类型字面量（`grep` 验证）。
- `app/` 不得 import `zenagent.cli`（`tests/test_layering.py` 已钉住）。
- 每个任务只 `git add` 该任务 `Files` 列出的路径；禁止 `git add -A`。
- 全量门禁：`bash scripts/check.sh`（ruff clean + pytest 全绿；基线 2193 passed）。

## Review Focus

1. **写盘失败**：磁盘满 / 只读目录下追加记录不能中断 run（`append_meta` 吞 `OSError`），且 `tool_end` 搬家后仍如此（Task 3）。
2. **老会话文件**：没有 header / 没有记录 / 有未知 `type` 的文件仍能 `--resume`，未知记录在重写时原样跟随（Task 2）。
3. **孤儿调用**：`/undo`、`/fork`、`/compact`、`/shake` 之后，`tool_end` 记录不得指向已不存在的 `call_id`（Task 2）。
4. **RPC 快照**：`transcript` 里的 `compactions` 与 divider 通知不重复（Task 1 迁移后行为不变）。
5. **子品牌差异**：`zen -p` 与交互式对同一段对话写出**同一组**记录类型（Task 3）。

## File Structure

| File | Task | 职责 |
|---|---|---|
| `zenagent/app/records.py` | 1, 2 | 类型常量、构造器、谓词、`keep_for_rewrite` |
| `zenagent/app/session_meta.py` | 1, 3 | 构造器改用 `records`；接 `tool_execution_end` |
| `zenagent/app/compact.py` | 1, 2 | `compaction` 构造 + carry 谓词 |
| `zenagent/app/views.py`、`zenagent/cli/rpc.py` | 1 | 解码改用 `records.is_*` |
| `zenagent/app/chat.py` | 3 | 删掉 `tool_end` 写入，只保留视图状态 |
| `zenagent/agent/session.py` | 2 | `carry_records/fork_at/rewrite` 收谓词 |
| `zenagent/app/sessions.py`、`app/shake.py`、`app/retry.py` | 2 | carry 调用点传谓词 |
| `docs/internals/persistence.md` | 4 | 记录表 + 版本说明 |

---

### Task 1: `app/records.py` 与所有调用点迁移

**Files:**
- Create: `zenagent/app/records.py`, `tests/test_app_records.py`
- Modify: `zenagent/app/session_meta.py`, `zenagent/app/compact.py`, `zenagent/app/views.py`, `zenagent/cli/rpc.py`, `zenagent/app/chat.py`

**Interfaces:**
- Produces（后续任务依赖）：
  - `TOOL_END = "tool_end"`, `COMPACTION = "compaction"`, `RUN_START = "run_start"`, `RUN_END = "run_end"`, `RETRY = "retry"`, `MODE = "mode"`
  - `tool_end(call_id, tool_name, duration_ms, is_error, details) -> dict`、`compaction(tokens_before, derived_from, reason) -> dict`、`run_start() -> dict`、`run_end(*, termination, turns, duration_ms, detail) -> dict`、`retry(...) -> dict`、`mode(mode) -> dict`
  - `is_tool_end(record)`, `is_compaction(record)`, `record_type(record)`
  - `keep_for_rewrite(*, keep_tool_end: bool = True) -> Callable[[dict], bool]`

- [ ] **Step 1: 写失败的测试** `tests/test_app_records.py`：断言每个构造器产出的 dict 与今天逐字相同（`tool_end(...) == {"type": "tool_end", "call_id": ..., "tool_name": ..., "is_error": False}` 等）、`is_*` 谓词只认自己的类型、`keep_for_rewrite()` 保留 `compaction` + 命中 `call_ids` 的 `tool_end`、丢弃 `run_end`/`retry`/`mode`/`run_start`；`keep_tool_end=False` 时连 `tool_end` 也丢。
- [ ] **Step 2: 跑测试确认失败**（`ImportError: cannot import name 'records'`）。
- [ ] **Step 3: 实现 `zenagent/app/records.py`**：常量 + 构造器（`timestamp` 由 `append_meta` 盖，所以构造器不写它）+ 谓词 + `keep_for_rewrite`；`TOOL_DETAILS_LIMIT`/`TOOL_DIFF_MAX_LINES`/`_bound_details` 从 `session_meta.py` 搬进来（`session_meta` 仍 re-export 这两个常量以保持 import 兼容）。
- [ ] **Step 4: 迁移写/读点**：`session_meta.py`（`SessionMetaRecorder`、`make_retry_recorder`、`mode_record` 改为调用 `records.*`）、`compact.py`（`records.compaction(...)`）、`views.py::_apply_tool_records`（`records.is_tool_end`）、`cli/rpc.py` 的 compactions 过滤（`records.is_compaction`）、`chat.py` 的 `source="compaction"` 改用 `records.COMPACTION`。
- [ ] **Step 5: 跑相关测试**：`uv run pytest tests/test_app_records.py tests/test_app_session_meta.py tests/test_session.py tests/test_app_views.py tests/test_rpc_v2.py tests/test_compact.py tests/test_app_chat.py -q` → 全 PASS。
- [ ] **Step 6: 提交** `refactor(app): give session records one schema module`。

---

### Task 2: 内核 `carry_records` 改收谓词

**Files:**
- Modify: `zenagent/agent/session.py`（`carry_records`、`fork_at`、`rewrite`）、`zenagent/app/sessions.py`、`zenagent/app/shake.py`、`zenagent/app/retry.py`, `zenagent/app/compact.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `records.keep_for_rewrite(...)`（Task 1）。
- Produces: `Session.carry_records(messages, *, keep: Callable[[dict], bool] | None = None) -> list[dict]`；`Session.fork_at(index, *, keep=None)`；`Session.rewrite(messages, *, keep=None)`。`keep is None` → 结构规则：有 `call_id` 的记录只在 id 仍存活时保留，其余原样保留。

- [ ] **Step 1: 改造既有测试以表达新归属**（`tests/test_session.py`）：`test_carry_records_keeps_live_calls_and_compactions` 与 `test_carry_records_can_skip_tool_end` 改为传入 `records.keep_for_rewrite()` / `records.keep_for_rewrite(keep_tool_end=False)`；新增 `test_carry_records_default_carries_structurally`（无谓词时：孤儿 `tool_end` 丢、无 `call_id` 的记录留）与 `test_kernel_has_no_record_kind_literals`（读 `zenagent/agent/*.py` 源码，断言不含 `"tool_end"`/`"compaction"` 字面量）。
- [ ] **Step 2: 跑测试确认失败**（谓词参数不存在 / 字面量仍在 `session.py`）。
- [ ] **Step 3: 实现**：`carry_records` 的 `keep` 分支与结构化默认；`fork_at`/`rewrite` 转发 `keep`；`app/sessions.py::fork`/`undo`、`app/shake.py`、`app/retry.py`、`app/compact.py` 的调用点传 `records.keep_for_rewrite(...)`（shake 传 `keep_tool_end="tools" not in kinds`）。
- [ ] **Step 4: 跑测试**：`uv run pytest tests/test_session.py tests/test_app_sessions.py tests/test_shake.py tests/test_app_undo_retry.py tests/test_compact.py -q` → 全 PASS。
- [ ] **Step 5: 提交** `refactor(session): carry records by predicate, not by record kind`。

---

### Task 3: `tool_end` 由 Runtime 生产

**Files:**
- Modify: `zenagent/app/session_meta.py`（`SessionMetaRecorder` 处理 `tool_execution_end`）、`zenagent/app/chat.py`（删写入、保留 spent/todo 视图逻辑）
- Test: `tests/test_app_session_meta.py`, `tests/test_app_runtime.py`

**Interfaces:**
- Consumes: `SessionMetaRecorder(get_session)`（已在 `Runtime.bootstrap` 订阅）、`records.tool_end(...)`。
- Produces: `tool_end` 记录不再依赖 `ChatController`；`SessionMetaRecorder` 只在 `event.tool_call_id` 非空且 `event.type == "tool_execution_end"` 时写一条。

- [ ] **Step 1: 写失败的测试**（`tests/test_app_runtime.py`）：用 `runtime_factory(stream_fn=<fake 调一次 read_file 的流>)` 跑一次 `runtime.agent.prompt(...)`，`await runtime.agent.wait_for_idle()`，然后读会话文件，断言存在 `{"type": "tool_end", "call_id": ...}` 记录且 `tool_name == "read_file"`（**不构造 ChatController**）。
- [ ] **Step 2: 跑测试确认失败**（记录缺失）。
- [ ] **Step 3: 实现**：`SessionMetaRecorder.__call__` 增加 `tool_execution_end` 分支（用 `records.tool_end`）；`ChatController._on_event` 删掉 `append_meta(tool_end_record(...))` 那一段（保留 subagent spend / todo HUD）。
- [ ] **Step 4: 跑测试**：`uv run pytest tests/test_app_runtime.py tests/test_app_session_meta.py tests/test_app_chat.py tests/test_cli.py tests/test_headless_parity.py -q` → 全 PASS（若 `test_app_chat` 里有断言"chat 写 tool_end"的用例，改为断言记录仍存在但由 runtime 写）。
- [ ] **Step 5: 提交** `refactor(session): write tool_end from the runtime, not the UI controller`。

---

### Task 4: 文档与冻结决策

**Files:**
- Modify: `docs/internals/persistence.md`
- Test: `tests/test_session.py`（补一条 round-trip 用例）

- [ ] **Step 1: 写测试**：`test_side_channel_records_round_trip`——写 run_start/run_end/retry/mode/tool_end/compaction 各一条，`Session(path).records` 原样读回，未知 `type`（如 `{"type": "future", ...}`）不抛异常且被 `keep_for_rewrite()` 丢弃。
- [ ] **Step 2: 跑测试确认失败**（未知 `type` 被结构默认保留 → 断言不符）。
- [ ] **Step 3: 实现/调整**：按测试结果调整 `keep_for_rewrite`（未知类型不保留）后转绿。
- [ ] **Step 4: 文档**：`docs/internals/persistence.md` 增加"侧信道记录"小节：类型表（谁写、谁读）、`RECORD_VERSION = 1`、重写规则（谓词在 `app/records.py`）、`tool_end` 现在由 Runtime 写。
- [ ] **Step 5: 全量门禁 + 提交**：`bash scripts/check.sh` → ruff clean、pytest 全绿；`docs: record the session side-channel vocabulary`。

---

## Self-Review

- **Spec coverage**：C3（schema 归属 + 内核不认 kind）→ Task 1、2；C4（`tool_end` 生产者）→ Task 3；"只写不读的四种记录"→ Task 4 的 round-trip 测试把它变成显式决定（继续写、可读回、重写时丢弃），不新增 UI（成本最低的诚实选项）。
- **风险**：Task 2 改变 `Session.rewrite/fork_at` 的默认 carry 语义（结构化），所有 app 调用点都显式传谓词，所以用户可见行为不变；测试用例被改写以表达"谓词属于 app 层"。
- **不引入**：新记录类型、新 RPC 字段、版本号变更。
