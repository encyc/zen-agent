# 收尾包：运行模式与会话管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 ZenAgent 补上 `/mode plan|ask|full` 三档运行权限模式与完整会话管理（按首个问题生成 brief 名称、支持改名、TUI 会话操作与审批弹窗）。

**Architecture:** 权限模式收敛为 app 层 `zenagent/app/modes.py` 的纯函数 + `PermissionGate` 的 `mode` 属性；`Runtime` 持有 gate 与当前 mode，`ChatController.status` 回填一直为空的 `RunStatus.mode`。会话名称用 `~/.zenagent/sessions/.titles.json` sidecar 覆盖，不改 append-only JSONL 格式；派生名取首个用户消息的 brief。TUI 审批弹窗通过 `PermissionBroker.set_confirm` 接入，`/mode ask` 下弹 Allow / Allow for session / Deny（120s 超时按 Deny）。

**Tech Stack:** Python 3.11+、asyncio、Textual、pytest/pytest-asyncio；内核仍只依赖 httpx。

**Spec:** 用户 2026-10-30 会话中确认的范围（`/mode` 三模式 + 会话 brief 命名/改名/会话管理），无独立 spec 文件。

## Global Constraints

- 内核（types/events/providers/agent/tools）只依赖 httpx；`zenagent.app` 不得 import textual/rich/prompt_toolkit。
- 会话 JSONL 是 append-only 的 `Message.to_json()`，标题/改名只能走 sidecar，不得改变既有文件格式。
- 所有新逻辑必须有离线单测；`bash scripts/check.sh` 全绿（ruff + 全量 pytest）才算完成。
- 既有 CLI 兼容：`--yes`、`--read-only`、`-p`、RPC 输出不破坏；`--yes` 等价 full，`--read-only` 等价 plan。
- 破坏性会话操作（删除）必须二次确认，删除走 `.trash/` 不 unlink。

## Review Focus

1. `/mode` 在 run 进行中切换：新 mode 必须对同一 run 的后续工具调用立即生效，且不中断当前流。
2. ask 模式下 TUI 审批：并发工具调用可能同时请求审批——弹窗必须串行；120s 无响应按 Deny，且不得悬挂 run。
3. 标题 sidecar 损坏/缺失/并发写：读取必须回退到派生 brief，写入失败不能影响会话本身。
4. 空会话/无用户消息：brief 必须给出可读占位（`(empty)`），不能抛异常。
5. `SessionManager.rename` 的目标不存在、标题为空或超长：必须给出明确错误/归一化，不能写坏索引。

---

### Task 1: 模式核心（modes + PermissionGate + Runtime/status）

**Files:**
- Create: `zenagent/app/modes.py`
- Modify: `zenagent/app/permissions.py`、`zenagent/app/runtime.py`、`zenagent/app/chat.py`、`zenagent/app/views.py`（仅复用 `RunStatus.mode`）
- Test: `tests/test_modes.py`（新建）、`tests/test_permissions.py`

**Interfaces:**
- Produces: `MODES: tuple[str, str, str]`、`DEFAULT_MODE: str`、`normalize_mode(raw: str) -> str | None`、`resolve_mode(*, explicit: str | None, assume_yes: bool = False, read_only: bool = False) -> str`、`mode_prompt_note(mode: str) -> str`、`describe_modes() -> str`。
- Produces: `PermissionGate(..., mode: str = "ask").mode`、`PermissionGate.set_mode(mode: str) -> None`。
- Produces: `RuntimeOptions.mode: str | None`、`Runtime.mode -> str`、`Runtime.set_mode(mode: str) -> str`、`Runtime.gate`。
- `ChatController.status.mode` 回填 `runtime.mode`。

- [ ] Step 1: RED — 写 `tests/test_modes.py`：plan 拒写/放行读；full 免确认放行写；ask 非交互拒绝；非法 mode 报错；`resolve_mode` 优先级（explicit > yes > read-only > ask）。
- [ ] Step 2: 跑测试确认失败（模块不存在）。
- [ ] Step 3: 实现 modes.py、PermissionGate.mode/set_mode、Runtime mode 接线、status 回填；plan 模式 prompt note。
- [ ] Step 4: 全量 pytest；`test_permissions.py` 既有用例保持绿。
- [ ] Step 5: gate 全绿后提交（`feat: add plan/ask/full permission modes`）。

### Task 2: 模式前端（CLI / REPL / CommandRegistry / TUI 状态带）

**Files:**
- Modify: `zenagent/cli/main.py`（`--mode`、TUI/headless/REPL 解析）、`zenagent/cli/repl.py`（`/mode`、gate 保留引用、prompt note）、`zenagent/cli/completion.py`（`/mode` 帮助/补全）、`zenagent/app/commands.py`（`mode` 命令）、`zenagent/cli/tui/app.py`（dispatch 后刷新 status）
- Test: `tests/test_cli.py`、`tests/test_repl_logic.py`、`tests/test_app_commands.py`、`tests/test_tui_usability.py`

**Interfaces:**
- Consumes: Task 1 的 `resolve_mode`/`Runtime.set_mode`/`normalize_mode`。
- Produces: CLI `--mode {plan,ask,full}`；REPL `/mode [plan|ask|full]`；CommandRegistry `mode` 命令（TUI `/mode` 可用）；`handle_slash(..., set_mode=...)`。

- [ ] Step 1: RED — CLI `--mode full` 不经 `--yes` 也放行写；`/mode plan` 后写工具被拒；TUI status band 显示 mode；非法参数给出提示。
- [ ] Step 2: 跑测试确认失败。
- [ ] Step 3: 实现前端接线；`/help` 与补全表加入 `/mode`。
- [ ] Step 4: 全量 pytest 全绿。
- [ ] Step 5: 提交（`feat(cli): expose /mode across REPL and TUI`）。

### Task 3: 会话命名核心（brief + sidecar + SessionManager.rename）— 交给 teammate

**Files:**
- Modify: `zenagent/agent/session.py`、`zenagent/app/sessions.py`、`docs/guide/sessions.md`
- Test: `tests/test_session.py`、`tests/test_app_sessions.py`

**Interfaces:**
- Produces: `brief(text: str, *, limit: int = 40) -> str`；`session_title(path: Path) -> str`；`set_session_title(path: Path, title: str) -> str`；`read_session_titles() -> dict[str, str]`；`SessionInfo.title` = 覆盖名或派生 brief；`SessionManager.rename(path: Path, title: str) -> str`；`SessionManager.entries()[i].title` 为显示名。
- Constraint: `.titles.json` 损坏时读取回退空 dict；空标题 raise ValueError；标题归一化压空白、上限 80 字符；不修改 JSONL。

- [ ] Step 1: RED — brief 多行/markdown/超长/空；覆盖名持久化后 `list_sessions` 与 `read_info` 返回覆盖名；损坏 sidecar 回退；空标题报错。
- [ ] Step 2: 跑测试确认失败。
- [ ] Step 3: 实现；确认 `fork()/rewrite()` 产生的新会话不继承旧名。
- [ ] Step 4: 相关测试 + 全量 pytest 全绿。
- [ ] Step 5: 更新 `docs/guide/sessions.md`。

### Task 4: TUI 审批弹窗 + ask 模式接线

**Files:**
- Modify: `zenagent/cli/tui/screens/modals.py`（`ToolApproval`）、`zenagent/cli/tui/app.py`（`request_tool_approval`）、`zenagent/cli/main.py`（broker + `set_confirm`）、`zenagent/app/permissions.py`（`PermissionBroker.set_mode/mode`、回调支持 `allow|always|deny`、并发串行锁）
- Test: `tests/test_app_permissions.py`、`tests/test_tui_approval.py`（新建）

**Interfaces:**
- Produces: `ToolApproval(name: str, arguments: dict)`（ModalScreen[str]：`y` allow / `a` always / `n`、`escape` deny / `enter` allow）；`ZenAgentApp.request_tool_approval(name, arguments) -> str`（120s 超时 Deny、重复弹窗串行）。
- `PermissionBroker.before_tool_call` 在 mode == ask 时调用 confirm；full 直接放行；plan 由 gate 拒绝。

- [ ] Step 1: RED — broker ask + confirm 回调 deny/allow/always；full 不调用 confirm；plan 拒绝；ToolApproval 键位返回值；超时按 deny。
- [ ] Step 2: 跑测试确认失败。
- [ ] Step 3: 实现；TUI 入口用 broker 包 gate，并在 app 构造后 `set_confirm`。
- [ ] Step 4: 全量 pytest 全绿。
- [ ] Step 5: 提交（`feat(tui): approval modal and ask-mode wiring`）。

### Task 5: 会话管理前端（TUI picker 操作 + /rename + 列表显示名）

**Files:**
- Modify: `zenagent/cli/tui/screens/modals.py`（`SessionPicker` 返回动作、`RenamePrompt`）、`zenagent/cli/tui/app.py`（n/f/d/e/r 处理）、`zenagent/app/commands.py`（`rename` 命令）、`zenagent/cli/repl.py`（`/rename`、`/sessions` 显示名）、`zenagent/cli/completion.py`
- Test: `tests/test_app_commands.py`、`tests/test_repl_logic.py`、`tests/test_tui_pickers.py`

**Interfaces:**
- Consumes: Task 3 的 `SessionManager.rename` / `entries().title`。
- Produces: TUI SessionPicker 键位 `n` new / `f` fork / `d` delete（Confirm 二次确认）/ `e` export / `r` rename；REPL `/rename [name]`；`/sessions` 以 brief 名为主、文件名次之。

- [ ] Step 1: RED — picker 返回动作；n/f 后当前会话变化；d 弹 Confirm、取消不删、确认移入 `.trash/`；r 改名后列表显示新名；REPL `/rename` 与 `/sessions` 输出。
- [ ] Step 2: 跑测试确认失败。
- [ ] Step 3: 实现。
- [ ] Step 4: 全量 pytest 全绿。
- [ ] Step 5: 提交（`feat: session management actions and rename`）。

### Task 6: 文档与总闸门

**Files:**
- Modify: `docs/guide/cli.md`、`docs/guide/tui.md`、`README.md`、`docs/feature-map.md`
- Test: `bash scripts/check.sh`

- [ ] Step 1: 更新 `/mode`、审批弹窗、会话操作、`/rename`、`--mode` 文档；修 README 过期 Status 段。
- [ ] Step 2: `bash scripts/check.sh` 全绿（ruff + 全量 pytest）。
- [ ] Step 3: 提交（`docs: document modes and session management`）。

---

## Self-Review

1. **Spec 覆盖**：`/mode` 三模式（T1/T2/T4）、brief 命名与改名（T3/T5）、会话管理操作（T5）、审批弹窗与超时（T4）均有任务；用户未要求版本号/发布，未纳入。
2. **步骤扫描**：每个任务都有 RED→实现→全绿→提交；接口名在 T1/T3 定义、T2/T4/T5 消费，名称一致。
3. **Review Focus→测试**：#1 T2、#2 T4、#3 T3、#4 T3、#5 T3/T5 均有对应断言。
4. **比例**：计划只钉接口、文件与断言，不预写实现体。
