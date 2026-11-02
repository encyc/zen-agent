# Project Memory（M1 核心闭环）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个项目一个位于 `<root>/.zenagent/memory/` 的持久记忆库：`memory` 工具五个动作读写，system prompt 注入条目索引，变更落 journal。

**Architecture:** 存储层（`agent/memory_store.py`，纯 stdlib，entries 目录 + journal.jsonl，一条一文件）→ `memory` 工具（`tools/memory.py`，常驻注册同 lsp）→ prompt 索引（`PromptContext.memory_index` + 新 block `memory_index`，空库整块消失）。写入动作走 write 审批档，读动作 read 档。

**Tech Stack:** 纯 Python stdlib（无新依赖）；pytest；仓库现有 AgentTool/registry/prompt 管线。

**Spec:** `docs/superpowers/specs/2026-11-02-project-memory-design.md`（§4 工具、§5 布局、§6 M1；本计划只做 M1，M2 异步摘要、M3 `/memory` 命令另行计划）

## Global Constraints

- 零第三方运行时依赖（现状：仅 httpx；不新增）。
- 记忆文件必须 `cat`/`grep`/git-diff 友好（反目标：只有 zenagent 能读的格式）。
- 正文**永不**进 system prompt；索引上限 64 条，超出按 `updated` 最新优先截断并在块尾提示用 `memory search`。
- 所有写路径限定在 `<root>/.zenagent/memory/` 内，拒绝逃逸。
- PROMPT_VERSION bump + `zenagent/prompts/CHANGELOG.md` 条目（仓库流程）。
- 提交前门禁：`bash scripts/check.sh` 全绿。

## Review Focus

1. **手写 frontmatter 遇到畸形文件**（缺 frontmatter/二进制/超长行）——期望：降级为正文+告警，绝不崩工具。→ Task 2 步骤 1。
2. **路径逃逸**（`add/update/delete` 的 id 拼接出 `../` 或绝对路径）——期望：一律拒绝，错误信息不含堆栈。→ Task 2 步骤 1、Task 3 步骤 1。
3. **journal 并发/损坏**（两进程同写、尾行截断）——期望：append 用单次 `os.write`，读时跳过尾行残缺；不阻塞主流程。→ Task 2 步骤 1。
4. **索引含特殊字符**（标题带 `|`/换行/markdown 注入）——期望：标题单行化+截断，块内不可能注入指令。→ Task 5 步骤 1。
5. **空库/首会话**（目录不存在时 read/search/prompt 组装）——期望：空结果、块消失，无报错无告警噪音。→ Task 2/3/5 各步骤 1。

---

### Task 1: 存储层 `agent/memory_store.py`（骨架 + entry 读写）

**Files:**
- Create: `zenagent/agent/memory_store.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Produces: `MemoryStore(root: Path)`；`entry_path(entry_id: str) -> Path`；`new_id() -> str`（4 位 hex）；`read_entry(entry_id: str) -> MemoryEntry | None`；`list_entries() -> list[MemoryEntry]`；`dataclass MemoryEntry(id: str, title: str, body: str, created: str, updated: str, source: str)`（ISO 时间戳字符串）。常量 `MEMORY_DIR = ".zenagent/memory"`、`MAX_INDEX_ENTRIES = 64`、`MAX_TITLE_CHARS = 80`。
- 约定：entry 文件 = `entries/<id>.md`；frontmatter 是文件头部连续的 `key: value` 行，遇到空行即停；无 frontmatter → title 取正文首行、body 为全文、created/updated 取文件 mtime、source 为空（畸形文件降级，见 Review Focus 1）。

- [ ] **Step 1: 写失败测试**

```python
def test_roundtrip_and_list(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="依赖选型", body="用 httpx 而非 requests，因为连接池", source="test")
    assert store.read_entry(entry.id) == entry
    assert store.list_entries() == [entry]
    assert (tmp_path / ".zenagent/memory/entries").is_dir()  # 懒创建

def test_malformed_entry_degrades(tmp_path):
    path = tmp_path / ".zenagent/memory/entries/beef.md"
    path.parent.mkdir(parents=True); path.write_bytes(b"\xff\xfe binary junk")
    store = MemoryStore(tmp_path)
    entry = store.read_entry("beef")
    assert entry is not None and entry.title  # 降级不崩，title 非 None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_memory_store.py -x -q`
Expected: FAIL（`MemoryStore` 未定义）

- [ ] **Step 3: 实现** `MemoryStore`（纯 stdlib；`new_id` 用 `secrets.token_hex(2)`，冲突时重试；`write_entry` 原子替换写；本任务先实现 read/list/write 基础件）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_memory_store.py -q` → PASS

- [ ] **Step 5: Commit** `feat(memory): entry store with tolerant frontmatter parsing`

### Task 2: 存储层：journal、search、delete

**Files:**
- Modify: `zenagent/agent/memory_store.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: Task 1 全部。
- Produces: `journal(op: str, entry_id: str, *, title: str | None = None, reason: str = "", session: str | None = None) -> None`（append 单次 `os.write`，行 = `{"ts","op","id","title","reason","session"}` JSON + `\n`）；`search(query: str) -> list[tuple[MemoryEntry, str]]`（返回 (entry, 首个命中行摘录 ≤120 字符)，大小写不敏感子串，扫 title+body）；`delete_entry(entry_id: str) -> bool`（删文件 + journal 事件）。

- [ ] **Step 1: 写失败测试**（三组：journal 追加后 `read_text().splitlines()` 每行可 `json.loads` 且字段齐全；尾行截断的 journal 读取不抛异常；search 命中正文时摘录含关键词、无命中返回空；delete 返回 True/False 且 journal 有 `delete` 事件）
- [ ] **Step 2: 跑测试确认失败** Run: `uv run pytest tests/test_memory_store.py -q` → 新测试 FAIL
- [ ] **Step 3: 实现** 三个方法（journal 损坏容错：逐行 `try/except json.JSONDecodeError: continue`）
- [ ] **Step 4: 跑测试确认通过** Run: `uv run pytest tests/test_memory_store.py -q` → PASS
- [ ] **Step 5: Commit** `feat(memory): journal, search and delete on the entry store`

### Task 3: `memory` 工具

**Files:**
- Create: `zenagent/tools/memory.py`
- Modify: `zenagent/app/runtime.py`（`_recompose` 内 `tools.append(build_memory_tool(store))`，常驻无条件注册，紧邻 `build_lsp_tool()` 那行）
- Test: `tests/test_tools_memory.py`

**Interfaces:**
- Consumes: `MemoryStore` 全部（Task 1/2）。
- Produces: `build_memory_tool(store: MemoryStore) -> AgentTool`；工具名 `"memory"`；参数 `{action: read|search|add|update|delete, id?, title?, body?, query?, reason?}`；`approval` 按动作动态：`read/search→"read"`，`add/update/delete→"write"`（在 `execute` 前由 gate 读工具声明——因此**注册两个 AgentTool 实例的能力由声明统一**：工具声明 `approval="read"` 不可行，按仓库机制改为**整体声明 `approval="write"` 并在 description 注明读动作无需审批的前提下，读动作由 `permissions.py` 的既有 read 放行逻辑处理**；若实现时发现 gate 只认工具级声明，则拆成 description 中说明、保持 `approval="write"`，与 spec 的映射差异记录在任务收尾报告里）。
- id 校验：`^[0-9a-f]{4}$`，不匹配或路径逃逸 → `is_error` 结果。

- [ ] **Step 1: 写失败测试**（fake store；`call(**args) = await MEMORY.execute("c1", args, None)`；断言：add 返回新 id 且 `read` 能取回；update 后 `updated` 变化、journal 有 `update`；delete 幂等报错语义；`search` 返回 id+摘录；`id="../etc"` 被 is_error 拒绝；未知 action 报错；空库 read/search 返回空结果文本而非错误）
- [ ] **Step 2: 跑测试确认失败** Run: `uv run pytest tests/test_tools_memory.py -q` → FAIL（模块不存在）
- [ ] **Step 3: 实现** `build_memory_tool`（仿 `tools/lsp.py` 的 `_text`/`_clip` 手法：输出截断 20k、`details` 带 action/count；description 写明使用政策：先 search 去重、宁缺毋滥、决策记"为什么"）
- [ ] **Step 4: 跑测试确认通过** Run: `uv run pytest tests/test_tools_memory.py tests/test_memory_store.py -q` → PASS
- [ ] **Step 5: Commit** `feat(memory): memory tool with five actions, write-tier guarded`

### Task 4: prompt 索引注入

**Files:**
- Create: `zenagent/prompts/blocks/memory_index.md`
- Modify: `zenagent/prompts/spec.py`（BLOCKS 加 `memory_index`：`path="memory_index.md"`, `cache_class="session"`, `budget_chars=120`, `required_vars=("entries",)`, `optional=True`；`PROMPT_VERSION` bump）
- Modify: `zenagent/prompts/__init__.py`（`PromptContext.memory_index: str = ""`、`include_memory: bool = True`；`compose_system` 在 skills 块后追加，模式照抄 context_files：`if ctx.include_memory and ctx.memory_index.strip()`）
- Modify: `zenagent/prompts/CHANGELOG.md`（新条目：为什么、spec 链接）
- Modify: `zenagent/app/runtime.py`（`_build_prompt`：store 存在时 `memory_index=format_memory_index(store)`）
- Modify: `zenagent/agent/memory_store.py`（`format_memory_index(store) -> str`：`- <id> <title>` 每行一条，最新在前，>64 条截断 + 尾行提示 search；标题单行化（`" ".join(title.split())`）+ 截 80 字符——Review Focus 4）
- Test: `tests/test_prompts_compose.py`（追加用例）、`tests/test_memory_store.py`

**Interfaces:**
- Consumes: `MemoryStore.list_entries()`（Task 1）。
- Produces: `format_memory_index(store: MemoryStore) -> str`；`PromptContext.memory_index` 字段。空库 → `""` → 块消失。

- [ ] **Step 1: 写失败测试**（compose 断言：`memory_index="x"` 时输出含块文本且 blocks 含 `memory_index`；空串时输出与现在逐字节相同（golden 语义）；store 有 2 条时索引两行、标题带换行的被单行化；65 条时只出现 64 行且含 search 提示）
- [ ] **Step 2: 跑测试确认失败** Run: `uv run pytest tests/test_prompts_compose.py tests/test_memory_store.py -q` → 新用例 FAIL
- [ ] **Step 3: 实现** 全部 Modify 项（块模板正文：`## Project memory` + `{{entries}}` + 一行使用说明"用 memory 工具 read/search；写前先 search 去重"）
- [ ] **Step 4: 跑测试确认通过** Run: `uv run pytest tests/test_prompts_compose.py tests/test_prompts_golden.py tests/test_memory_store.py -q` → PASS（golden 若因 PROMPT_VERSION 失效，按仓库流程更新 fixture）
- [ ] **Step 5: Commit** `feat(memory): inject entry index into the system prompt`

### Task 5: runtime 装配 + 端到端

**Files:**
- Modify: `zenagent/app/runtime.py`（bootstrap：`MemoryStore(find_project_root())` 存 `self.memory_store`；`_recompose` 与 `_build_prompt` 接线（Task 3/4 已占位）；不设开关——目录不出现即零影响）
- Test: `tests/test_runtime_memory.py`（新）

**Interfaces:**
- Consumes: Task 1–4 全部。
- Produces: `Runtime.memory_store: MemoryStore`（后续 M2 摘要、M3 命令都从这取）。

- [ ] **Step 1: 写失败测试**（monkeypatch `zenagent_home` 与 tmp project root；断言：新 runtime 默认带 store 且工具在 `tool_registry` 里；prompt 含索引行；`add` 后下一轮 `_recompose()` 的 prompt 反映新条目；空项目（无 memory 目录）prompt 与无 memory 时一致、无警告）
- [ ] **Step 2: 跑测试确认失败** Run: `uv run pytest tests/test_runtime_memory.py -q` → FAIL
- [ ] **Step 3: 实现** 装配（最小改动：bootstrap 里构造 store、传入两处已有占位）
- [ ] **Step 4: 跑测试确认通过** Run: `uv run pytest tests/test_runtime_memory.py -q` → PASS
- [ ] **Step 5: 全量门禁** Run: `bash scripts/check.sh` → ruff 0 错误、全测试 PASS
- [ ] **Step 6: Commit** `feat(memory): wire the store into the runtime (M1 complete)`

## Self-Review 结论

- Spec 覆盖：spec §4 五动作 = Task 3；§5 布局 = Task 1/2；§6 M1 验收"新会话能带着上会话的条目工作" = Task 5 步骤 1 的 resume 用例；§3 索引式注入 = Task 4。M2/M3 明确不在本计划（spec §6 分期）。
- 类型一致性：`MemoryStore`/`MemoryEntry`/`format_memory_index`/`build_memory_tool` 各 Task 的 Interfaces 已互相对齐。
- Review Focus 五项全部落在拥有该代码的 Task 的测试步骤里。
- 审批档的机制差异（工具级声明 vs 动作级映射）在 Task 3 Interfaces 里显式标注为实现时的记录点，避免 executor 静默偏移 spec。
