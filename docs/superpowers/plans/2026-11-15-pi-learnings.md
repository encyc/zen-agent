# Pi Learnings (1/3/4/5/7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 pi 调研中五条值得学的设计（session 分支持久化、compaction 切点纪律、steer/followUp 双队列、extension 命名 section、空会话不落盘的验证）native 地落进 zen-agent。

**Architecture:** 全部改动落在既有模块边界内，不新增 Python 模块：内核项进 `agent/loop.py` + `agent/agent.py` + `agent/session.py`；compaction 进 `app/compact.py` + `prompts/`；分支持久化进 `app/records.py`（词汇表）+ `app/rewind.py` + `app/runtime.py`（写盘点）；extension section 进 `extensions.py` + `prompts/__init__.py`。会话文件保持 append-only、`Message.to_json()` 即消息行、side-channel record 词汇表归 `app.records` 所有——kernel 只新增一个结构化的 `window` 回调，不认识任何 record 种类。

**Tech Stack:** 纯 Python 3.11+，零新增依赖；pytest + ruff + mypy（现有 ratchet 必须保持绿）。

**Spec:** pi 调研对照（本会话产出）+ 下列各项的 pi 锚点：
- ① pi `session-manager.ts:1597-1602`（branch = 移 leafId 指针）、`:984-994`（id+parentId 树）
- ③ pi `compaction.ts:446-501`（findCutPoint 永不在 toolResult 处切）、`:713-729`（多级 UPDATE_SUMMARIZATION_PROMPT）、`:126-130`（reserveTokens/keepRecentTokens）
- ④ pi `agent.ts:308-315`（steer vs followUp 双队列）、`agent-loop.ts:185,311`（两个 poll 点）
- ⑤ pi `docs/extensions.md:617-657`（section diff 只下发补丁）
- ⑦ pi `session-manager.ts:1174-1197`（首条真实消息才建文件）

## Global Constraints

- 六层依赖只向下（tests/test_layering.py 钉住）：app/ 不 import cli；kernel 不认识 record 种类。
- 会话文件 append-only；消息行是 `Message.to_json()`；record 是无 `"role"` 的 `{"type": ...}` 对象。
- StreamFn 永不抛（safe_stream）；工具永远返回结果；取消是协议内状态。
- mypy ratchet：改动文件不得引入新错误（当前 251 文件全绿）。
- ruff check . 全绿；`scripts/check.sh` 是唯一门禁。
- 新行为默认关闭语义：`get_followup_messages=None`、`window=None`、`extension_sections=()` 时行为与今日逐字节一致。

## Review Focus

1. **rewind 落盘与内存投影的一致性**：落盘后的 resume 视图必须等于同进程投影视图（head + report + future）。测试钉在 Task 5。
2. **compaction 切点不孤立 toolResult**：tail 内每个 ToolResultMessage 的 toolCall 必须也在 tail 内。测试钉在 Task 4。
3. **followUp 不在异常终止点生效**：abort/error/max_turns/max_cost/tool_terminate 终止时 follow-up 不得续跑。测试钉在 Task 1。
4. **lazy 会话的显式物化**：命名/置顶必须仍能落盘（`materialize`）。测试钉在 Task 2。
5. **旧文件零回归**：无 branch record 的旧会话、无 section 的旧扩展、无 keep 预算的旧行为，逐字节不变。

---

### Task 1: followUp 队列（内核双队列）

**Files:**
- Modify: `zenagent/agent/loop.py`（AgentConfig + agent_loop 末尾 break 处）
- Modify: `zenagent/agent/agent.py`（Agent 队列 API + _config）
- Test: `tests/test_agent_class.py`

**Interfaces:**
- Produces: `AgentConfig.get_followup_messages: Callable[[], Awaitable[list[Message]]] | None = None`；`Agent.follow_up(text: str) -> None`；`Agent.pending_follow_up: int`；`Agent.take_follow_up() -> list[str]`。
- 语义：只在 plain end-of-turn（loop.py `else: break`，约 :352）poll；aborted/error/max_turns/max_cost/tool_terminate/no_stream 不 poll。drain 全部并聚合成一条 `UserMessage`（无 `steering=True` 标记），与 steering 的聚合方式一致。

- [ ] **Step 1: 写失败测试**（`tests/test_agent_class.py` 追加）

```python
async def test_follow_up_continues_run_at_plain_stop(fake_model_stream, ...):
    # 第一轮 plain stop 后，loop drain follow-up、注入 user 消息、继续一轮
async def test_follow_up_not_consumed_on_abort(...):
    # abort 终止时队列原封不动（len == 1）
async def test_reset_clears_follow_up(...):
```

- [ ] **Step 2: 跑测试确认失败**：`uv run pytest tests/test_agent_class.py -q -k follow_up` → FAIL（AttributeError: follow_up）
- [ ] **Step 3: 实现**：AgentConfig 加字段（默认 None）；loop 在 `else: break` 前插 poll 块；Agent 加队列三件套 + `_config()` 接线 + `reset()` 清空。
- [ ] **Step 4: 跑测试确认通过**（同 Step 2 命令）
- [ ] **Step 5: Commit** `feat(agent): follow-up queue keeps a run alive at plain stop (pi learnings ④)`

### Task 2: 空会话不落盘——验证与补钉（pi ⑦）

**Files:**
- Test: `tests/test_agent_class.py` 或既有 session 测试文件（先 `rg -n "lazy" tests/` 定位既有覆盖）

**Interfaces:** 只读验证 + 缺口补钉。现状：`Session.lazy`（session.py:385）+ `materialize()`（:470）+ `list_sessions` 跳 header-only（:756-763）已实现 pi 行为。

- [ ] **Step 1:** `rg -n "lazy|materialize" tests/ | head -20` 清点既有覆盖
- [ ] **Step 2:** 若缺以下任一断言则补测：①从不发消息的 lazy 会话 `path.exists() is False`；②`materialize()` 后文件存在且只有 header；③首条消息 append 后 header + meta 记录按原顺序落盘；④`list_sessions` 不含空文件但含被 pin 的空文件。
- [ ] **Step 3: Commit** `test(session): pin lazy-session no-file-on-idle behavior (pi learnings ⑦)`

### Task 3: extension 命名 section + 字节稳定性（pi ⑤）

**Files:**
- Modify: `zenagent/extensions.py`（ExtensionAPI + LoadedExtensions）
- Modify: `zenagent/prompts/__init__.py`（PromptContext.extension_sections + compose_system 每节一个 block）
- Modify: `zenagent/app/bootstrap.py:207` 附近与 `zenagent/app/skills_recompose.py:223` 附近（runtime 持有 sections 元组）
- Modify: `zenagent/app/runtime.py`（`_extension_sections` 字段 + `_build_prompt` 传入）
- Test: `tests/test_extensions.py`（不存在则建，名字跟既有惯例）、`tests/test_prompt_parity*`（只跑不改动）

**Interfaces:**
- Produces: `ExtensionAPI.add_system_prompt(text: str, *, section: str | None = None) -> None`；`LoadedExtensions.sections: dict[str, str]`（插入序，同 key 替换保位）；`LoadedExtensions.prompt_text`（fragments 在前、sections 按插入序在后，`"\n\n"` join）；`PromptContext.extension_sections: Sequence[tuple[str, str]] = ()`；compose_system 对每节发一个 `RenderedBlock(id=f"extension.{name}", cache_class="session")`。
- 语义：`section=None` 走旧 fragments 路径（零回归）；跨文件同名 section 报 warning 并保留第一个（与 commands 一致）；同一文件内重复注册为替换。

- [ ] **Step 1: 写失败测试**

```python
def test_add_system_prompt_section_replaces_in_place():
def test_sections_keep_position_across_reregistration():
def test_compose_system_renders_one_block_per_section():
def test_unchanged_inputs_render_byte_identical():
```

- [ ] **Step 2: 跑测试确认失败**：`uv run pytest tests/test_extensions.py -q` → FAIL
- [ ] **Step 3: 实现**（按 Interfaces 签名；compose_system 中 sections 渲染排在 fragments block 之后、mode block 之前）
- [ ] **Step 4: 跑测试通过 + prompt parity/golden 测试不回归**：`uv run pytest tests/ -q -k "prompt or golden"`
- [ ] **Step 5: Commit** `feat(extensions): named prompt sections with stable ordering (pi learnings ⑤)`

### Task 4: compaction 切点纪律 + 多级摘要合并（pi ③）

**Files:**
- Modify: `zenagent/app/compact.py`（cut 选择 + tail 保留 + previous summary 检测）
- Modify: `zenagent/prompts/__init__.py`（`compose_compact(transcript, *, previous_summary: str | None = None, root=None)`）
- Create: `zenagent/prompts/blocks/compact.update.md`（UPDATE prompt：旧摘要 + 新 transcript → 更新后的摘要）；`zenagent/prompts/spec.py` 登记
- Modify: `zenagent/app/records.py`（`compaction()` 加可选 `kept: int = 0` 字段）
- Test: `tests/test_compact.py`

**Interfaces:**
- Produces: `compact(agent, session, stream_fn, *, reason="manual", tokens_before=None, keep_recent_tokens: int = 20_000)`；模块常量 `KEEP_RECENT_TOKENS = 20_000`；`_choose_cut(messages, keep_tokens) -> int`（pure）。
- 切点规则：tail = `messages[cut:]`，预算 `tokens(tail) <= keep_recent_tokens`（`providers.limits.estimate_text_tokens` 估）；若 tail 存在 ToolResultMessage 其 toolCall 在 tail 外 → cut 回退直到安全；`cut == 0` 退化为旧行为（全量摘要、无 tail）。
- 落盘顺序：compaction record（含 kept）→ summary 消息 → tail 消息逐条 `session.append`；`agent.replace_messages([summary, *tail])`。replay（boundary 后）= summary + tail。
- 多级合并：live 视图首条消息是 `compact_result(...)` 产物时，改用 `compose_compact(transcript_of_rest, previous_summary=旧摘要)` 的 update 模板，摘要调用 usage 不变。

- [ ] **Step 1: 写失败测试**（`tests/test_compact.py` 追加）

```python
def test_choose_cut_never_orphans_tool_result():
async def test_compact_keeps_recent_tail(...):          # fake stream；断言 replace_messages == [summary, *tail]
async def test_compact_full_when_budget_huge(...):       # cut==0 旧行为
async def test_compact_updates_previous_summary(...):    # 第二次 compact 走 update 模板
```

- [ ] **Step 2: 跑测试确认失败**：`uv run pytest tests/test_compact.py -q` → FAIL
- [ ] **Step 3: 实现**（_choose_cut 纯函数先行；compose_compact 加参；compact 主体改三段式落盘）
- [ ] **Step 4: 跑测试通过 + 既有 compact 测试全绿**：`uv run pytest tests/test_compact.py tests/ -q -k compact`
- [ ] **Step 5: Commit** `feat(compact): keep-recent tail with safe cut points and summary merge (pi learnings ③)`

### Task 5: session 分支持久化——rewind 落盘可回（pi ①）

**Files:**
- Modify: `zenagent/agent/session.py`（Session 加 `window: Callable[[dict, list[Message]], list[Message] | None] | None = None`；load() 在 record 分支内应用）
- Modify: `zenagent/app/records.py`（`BRANCH = "branch"` 常量入 KINDS/KNOWN_KINDS；`branch(cut, skip, goal="")` builder；`is_branch`；`branch_window(record, messages) -> list | None`；不进 `keep_for_rewrite`（位置型 record 改写后失效，KNOWN_KINDS 自动丢弃））
- Modify: `zenagent/app/rewind.py`（RewindState 加 `recorded: bool`；抽 `report_message() -> UserMessage`，transform() 复用；checkpoint()/clear() 复位 recorded）
- Modify: `zenagent/app/runtime.py`（`_transform_context`：armed 且未 recorded 时写 branch record + report 消息，再置 recorded）
- Modify: `zenagent/app/records.py::open_session`（加 `window=branch_window`）
- Test: `tests/test_app_records.py`（branch builder/window/roundtrip）、`tests/test_agent_class.py` 或 rewimd 相关测试文件（先 `rg -l "RewindState" tests/`）

**Interfaces:**
- Produces: record 形状 `{"type": "branch", "cut": int, "skip": int, "goal": str, "timestamp": ...}`；`branch_window(record, messages)`：`cut/skip` 均为 int 且 `0 <= cut`、`skip >= 1`、`cut + skip <= len(messages)` 时返回 `messages[:cut] + messages[cut+skip:]`，否则 None（坏 record 忽略，永不丢 transcript）。
- kernel 规则（session.py docstring 写明）：`window` 是结构化重放选择器——record 可定义对当前重放缓冲的一次投影；kernel 不知道 branch 语义。
- Runtime 写盘点：在 `_transform_context` 内、`rewind.transform` 判定 armed 之后写盘一次；写盘失败（OSError）由 `append_meta` 吞掉，不影响本次请求。
- 交互矩阵：branch 之后又 compaction → replay 取最后 boundary 段（branch 只影响其所在段）；compaction 之后 rewind → window 作用于当前段缓冲。

- [ ] **Step 1: 写失败测试**

```python
def test_branch_window_projection_and_validation():
async def test_session_window_survives_resume(...):      # rewind 落盘 → 新 Session(open_session) load == head+report+future
async def test_invalid_branch_record_ignored(...):       # cut 越界 → 全量重放
def test_branch_records_dropped_on_rewrite():            # keep_for_rewrite
async def test_rewind_projection_matches_resume_view(...):  # Review Focus #1
```

- [ ] **Step 2: 跑测试确认失败**：`uv run pytest tests/test_app_records.py -q -k branch` → FAIL
- [ ] **Step 3: 实现**（顺序：records builder/window → session.window → rewind.report_message/recorded → runtime 写盘 → open_session 接线）
- [ ] **Step 4: 跑测试通过 + rewind/records/session 相关全绿**：`uv run pytest tests/ -q -k "rewind or records or session"`
- [ ] **Step 5: Commit** `feat(session): durable rewind branches via window records (pi learnings ①)`

### Task 6: 全门禁收尾

- [ ] **Step 1:** `bash scripts/check.sh`（ruff + mypy + architecture budget + pytest 全量）
- [ ] **Step 2:** 若架构预算报新文件/新行数问题，按其输出修正或登记
- [ ] **Step 3:** `git log --oneline main..` 核对 6 个 commit；报告留档

## Self-Review

- 覆盖：①→Task 5、③→Task 4、④→Task 1、⑤→Task 3、⑦→Task 2，无遗漏。
- 类型一致：`branch_window`/`window` 签名、`extension_sections` 元组形状、`compose_compact` 新参均已锁定。
- Review Focus 五条各有钉测（Task 5/4/1/2/全任务）。
- 比例：无算法正文，只有签名 + 断言 + 命令。
