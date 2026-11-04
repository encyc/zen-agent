# Subagent Super Child（模式跟随 + 保险丝 + 产出保障）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** 无独立 spec 文档——设计输入是 2026-11-03 的 session 语料审计（12 次去重 subagent 运行，67% 失败，95% token 蒸发在无产出运行上；失败模式 FM1–FM7）与本文 Context 节中的用户裁决。计划即裁决记录。

**Goal:** 把 `task` 子代理从硬编码只读六件套升级为"能力复制主 Agent、身份不复制"的 super child，并补上墙钟/花费保险丝与部分产出抢救，使预算截断的运行不再空手而归。

**Architecture:** 一条核心规则——**child 能力上限 = 父当前权限模式**：zen 模式继承父 Agent 激活工具集（`registry.active()` 减黑名单），其他模式再交 `approval=="read"`。工具集从构建时冻结改为 execute 时按 live getter 求值（MCP 刷新、`/mode` 切换对下一次派发生效）。墙钟预算不走 loop 级 `AgentTool.timeout_s`（600s 钳制且取消协程丢 transcript 终态行），在 task 工具内做 child 侧 watchdog。prompt 面只改既有块，不新增块。

**Tech Stack:** Python 3.11+，pytest；prompt 改动受 spec.py/goldens/source-gate/budget 四重闸门约束；闸门 `uv run pytest` + `uv run python scripts/prompt_goldens.py --check`。

**Status:** 已执行完毕（2026-11-04，commit `500882c`，分支 `feat/subagent-super-child`，全量 2063 passed）。checkbox 记录实际完成情况。

## Global Constraints

- child 永不获得：`task`（递归）、`ask`（无用户通道）、`wait`（job 归父）、`todo`、`rewind`、`memory*`/`checkpoint*`（共享会话状态）——名单是硬边界，任何模式下都排除。
- `mode` 为 None 时按非 zen 处理（保守默认）。
- 派发指引散文必须写在块文件里，不得进 `.py`（source gate 禁止）。
- prompt 内容变更必须：bump `PROMPT_VERSION`、`prompts/CHANGELOG.md` 加条目、重生成 goldens、模板长度不超 `budget_chars`。
- `[task]` 键仅全局层（项目层拒绝），数值校验错误信息带 `task.<key>`。
- 不新增依赖；README 不动（长度有测试守卫）。

## Review Focus

- **黑名单漏网**：按名/前缀排除依赖工具的真实命名——`rewind` 不带 `checkpoint` 前缀，漏掉就把回滚能力交给 child。测试：Task 1 的 zen/非 zen 双断言（`tests/test_app_runtime.py`，真实 runtime 工具集）。
- **watchdog 与用户 abort 的语义混淆**：看门狗中止的信封必须是 `truncated/timeout`（预算结果），不是 `aborted`（用户打断）。测试：Task 4 的 `test_timeout_watchdog_truncates_a_runaway_child`。
- **抢救不能把 error 信封变成假成功**：`_final_text` 只回扫文本，状态判定不变。测试：Task 5 的 `test_final_text_rescues_an_earlier_checkpoint`。
- **budget 三闸联动**：task.md 描述增量吃 `TOOL_TOTAL_BUDGET_CHARS`（4400，余量极小）、child prompt 吃自身 `budget_chars`、散文吃 source gate。测试：`tests/test_prompts_budget.py` + `scripts/prompt_goldens.py --check`。
- **headless 与 runtime 双构建点漂移**：两个 `build_task_tool` 调用点必须传齐相同语义的新参。测试：Task 6 的 wiring 测试（`tests/test_app_runtime.py::test_bootstrap_wires_the_task_budget_from_config`）。

---

### Task 1: 动态工具集 + 模式跟随 — `zenagent/tools/task.py`

**Files:**
- Modify: `zenagent/tools/task.py`（`CHILD_TOOL_NAMES` → `_CHILD_TOOL_DENYLIST`/`_CHILD_TOOL_DENY_PREFIXES`，新增 `child_allowed()`，重写 `child_tool_names()`）
- Modify: `zenagent/app/runtime.py`、`zenagent/cli/main.py`（构建点传 `live_tools`/`mode` live getter）
- Test: `tests/test_task.py`、`tests/test_app_runtime.py`

**Interfaces:**
- Produces: `child_allowed(tool: AgentTool, *, zen: bool) -> bool`；`child_tool_names(base_tools, *, zen: bool = False) -> set[str]`；`build_task_tool(..., live_tools: Callable[[], Sequence[AgentTool]] | None = None, mode: str | Callable[[], str | None] | None = None, timeout_s: int = DEFAULT_TASK_TIMEOUT_S)`

- [x] 白名单改黑名单：`_CHILD_TOOL_DENYLIST = {task, ask, wait, todo, rewind}` + 前缀 `(memory, checkpoint)`；zen → 全量减黑名单，非 zen → 再 ∩ `approval=="read"`
- [x] `child_tools` 移入 execute 求值；`child_system_prompt` 渲染随之移入（工具名单随派发变化）
- [x] 双构建点接线：runtime 传 `lambda: runtime.tool_registry.active()` + `lambda: runtime.mode`；headless 传 `lambda: tool_list` + `resolved_mode`
- [x] 测试：zen/非 zen 双分支、`read_skill`/MCP 读工具可入、黑名单全数排除（含 `rewind` 单列断言）

### Task 2: child prompt 重写 — `task_child_system.md` + `spec.py`

**Files:**
- Modify: `zenagent/prompts/blocks/task_child_system.md`、`zenagent/prompts/spec.py`、`zenagent/prompts/CHANGELOG.md`
- Test: `tests/test_prompts_tools.py`

- [x] 身份改"delegated subagent reporting to the parent"，动态能力行 `Your tools: {{child_tools}}.`
- [x] 新增检查点纪律（每 ~10 turns 一段）与"改码必跑测试并附结果"
- [x] `required_vars=("max_turns", "child_tools")`、`budget_chars` 600→900、`PROMPT_VERSION` → `2026-11-03.1`、prompts/CHANGELOG 加条目

### Task 3: 派发指引（父侧）

**Files:**
- Modify: `zenagent/prompts/blocks/tools/task.md`、`zenagent/prompts/blocks/system/tool_policy.md`

- [x] task.md 原地重写（受 `TOOL_TOTAL_BUDGET_CHARS` 约束，4403→裁回 4399 以内）：声明模式跟随 + 派发清单（确认文件存在 / child 缺工具时父先落盘（如 diff）/ 声明输出契约）
- [x] tool_policy.md 加一条 Delegation bullet（同样清单的常驻版）

### Task 4: 保险丝 — `[task].timeout_s` / `[task].max_cost`

**Files:**
- Modify: `zenagent/config.py`（`_TASK_KEYS`、`_parse_task`、`_positive_float_or_none`、`UserConfig`、`_merge_user_configs`、`__all__`）
- Modify: `zenagent/tools/task.py`（execute 内 watchdog，与 `watch_abort` 并列，finally 合并清理）
- Modify: `zenagent/app/runtime.py`、`zenagent/cli/main.py`（`max_cost = [task].max_cost if set else 会话级透传`）
- Test: `tests/test_config.py`、`tests/test_task.py`、`tests/test_app_runtime.py`

- [x] `DEFAULT_TASK_TIMEOUT_S = 900`（0 关闭）、`DEFAULT_TASK_MAX_COST = None`；校验：timeout_s 非负整数、max_cost 正数
- [x] watchdog：`asyncio.sleep(timeout_s)` → `child.abort()`；`timed_out and status=="aborted"` → `status="truncated"`、`reason="timeout"`
- [x] 测试：config 默认/覆写/非法值/unknown-key；watchdog 假 slow child → truncated/timeout；wiring 断言补 `timeout_s`/`max_cost`/`live_tools`/`mode`

### Task 5: 部分产出抢救 — `_final_text`

**Files:**
- Modify: `zenagent/tools/task.py`
- Test: `tests/test_task.py`

- [x] 倒序找第一条含非空 text 的 assistant 消息（无文本时仍返回 ""，信封 `(no output)` 行为不变）

### Task 6: read 页宽 — `zenagent/tools/fs.py`

**Files:**
- Modify: `zenagent/tools/fs.py`（`DEFAULT_READ_LIMIT` 400→1000、`MAX_READ_LIMIT` 2000→5000）
- Modify: `tests/test_tools_fs.py`（401 边界测试常量化）
- Test: goldens `tools.json`（read_file 描述内嵌限额数字）

- [x] 两常量改值；fs 测试改用 `DEFAULT_READ_LIMIT + 1`；goldens 重生成

### Task 7: skill 模板 zen-agent 兼容变体（仓库外）

**Files:**
- Modify: `~/.zenagent/skills/requesting-code-review/code-reviewer.md`、`SKILL.md`

- [x] diff 由派发方预生成落盘（新增 `{DIFF_FILE_PATH}` 占位符与落盘步骤）；`git worktree add` 指令移除，改为"无 bash 时如实说明缺什么"

### 文档与 goldens

- [x] `docs/guide/config.md` `[task]` 节补 `timeout_s`/`max_cost` 并修正"只读工具集"表述；`docs/guide/tools-permissions.md` 同步 read_file 行宽与 task 行
- [x] 仓库 `CHANGELOG.md` Unreleased 条目（Added/Changed 分节）
- [x] goldens：`uv run python scripts/prompt_goldens.py` 重生成 20 个文件，`--check` 通过

## 不做（明确出 scope）

worktree 隔离、异步任务、per-dispatch 模型参数、写/exec 权限的非 zen 路由——留待下一期（异步落地时 worktree 从可选变硬前提，届时 worker 型子代理才值得开）。

## 验证

1. `uv run pytest -q` — 全量 2063 passed
2. `uv run python scripts/prompt_goldens.py --check` — golden check ok
3. 冒烟：真实配置解析（turns 300 / timeout 900 / max_cost None）；工具面判定——ask 模式 `read_file/read_skill/mcp 读工具`，zen 追加 `bash/write_file`，`memory/rewind` 两模式皆排除
4. 用户侧验收（待办）：真实 review 类派发（diff 先落盘再递路径），对照审计基线确认 `(no output)` 绝迹
