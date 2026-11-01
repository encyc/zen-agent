# P1/P2 收尾实现计划（omp 对标剩余项）

> **For agentic workers:** 逐任务执行；每个任务先写失败测试（RED），再实现（GREEN），跑定向测试，最后 `bash scripts/check.sh` 全量闸门。任务用 `- [ ]` 跟踪。
> **状态**：2026-11-01 制定；基线 `d5f1dcf`（v0.5.1），分支 `feat/p1-p2-completion`。
> **来源**：2026-11-01 全量代码核查（09/10/12 三份调研文档已过时，以核查结果为准）。
> **Goal**：把 2026-11-01 报告里的 P1、P2 项全部排期并实现，交付一个可合并的 worktree。

## Global Constraints

- Python ≥3.11；**不新增第三方运行时依赖**（LSP/github 走外部二进制探测，缺失即不注册/报提示）。
- 内核契约不变：`StreamFn` 永不抛异常、工具永远返回结果、取消是协议内状态；新事件/字段只增不改。
- `zenagent/app/**` 不得 import `zenagent/cli/**`；`zenagent/cli/**` 可依赖 app。
- 用户可见文案走 `zenagent.i18n.t()`，en/zh 两份 catalog 同步（`tests/test_i18n.py` 会拦）；模型可见文案保持英文。
- 每个任务一个 conventional commit；分支内提交，不 push。
- 闸门：`bash scripts/check.sh`（ruff + 全量离线 pytest）是唯一绿灯定义。
- 旧文档（09/10/12）不改历史结论，只加“历史基线，最新见本计划”的指向；新的状态以本计划和代码为准。

## 0. 范围（来自 2026-11-01 核查）

### P1（值得做，未做）

| # | 项 | 现状 | 规模 |
|---|---|---|---|
| P1-1 | openai-responses 的 `max_tokens` 拟合 | 4 个适配器只接了 3 个，Responses 仍可能 400 | S |
| P1-2 | MCP 调用级重连重试 | `client.call_tool` 无重试；只有服务器级 `retry_failed` | S |
| P1-3 | partial result 全前端 | 只有 TUI 消费；REPL/headless/RPC 不消费 | M |
| P1-4 | 状态带 git/cache 段 | `git_branch` 是死代码；无 cache_read/cache_write 段 | S |
| P1-5 | TUI 高频键位 | 缺 F5/Alt+R 重试、Shift+Ctrl+P 反向循环、Alt+Shift+P plan 开关 | S |
| P1-6 | ToolRegistry / hidden / default_inactive | 只有 `_recompose`，无 registry 抽象 | M |
| P1-7 | 工具声明 `context` 对象 | 钩子读不到工具声明、after 不能改 args 重校验 | M |
| P1-8 | plan review | plan 仍=只读+prompt note，无计划产物/复核 | M |
| P1-9 | TUI↔REPL 功能裂缝 | 模板/扩展命令只有 REPL；/pin /new /settings /search /doctor 只有 TUI；/import 只有 REPL | M |
| P1-10 | 转录/工具卡降噪 | 连续 read 不分组、task/todo/ask/wait/fetch 无专用卡、无 warning 级 | M |
| P1-11 | 图片粘贴 + 转录图片行 | 只有 /image 路径 + dock chips | M |
| P1-12 | Ctrl+G 外部编辑器 | 缺 | S |

### P2（条件/可选，全部排期）

| # | 项 | 现状 | 规模 |
|---|---|---|---|
| P2-1 | github 只读子集 | 缺（`gh` 子进程，缺失不注册） | M |
| P2-2 | checkpoint/rewind | 缺（模型主动压缩：checkpoint(goal)→探索→rewind(report)） | M |
| P2-3 | LSP 最小版 | 零实现（先 pyright 探针，4 只读 action + rename） | L |
| P2-4 | 内核小项 | write 可执行位、read 行号、glob `.gitignore`/mtime | M |
| P2-5 | 命令批 | /jobs(+kill)、/trace、/shake、/handoff、/btw、/copy、/add-dir|/remove-dir|/dirs、/fresh、/restart | M |
| P2-6 | 浮层批 | hotkeys sheet、session tree、usage dashboard、settings 全屏、MCP add 向导 | L |
| P2-7 | dock/交互 | 菜单 raise/focus、Esc 分层、tools toggleVisibility、display.reset、cycle backward | S |
| P2-8 | todo 用户编辑 | HUD/浮层只读，无 `/todo` 编辑入口 | M |

### 明确不做（维持 09/10/12 裁决）

browser/computer、debug/DAP、IDA、security_scan、tts/generate_image、eval 内核、memory backend、managed skill、marketplace/plugins、语音/录屏/collab/share、worktree/git UI/ps-top、Agent Hub、goal/vibe/loop/ratchet、hashline、find 语义搜索、27 个搜索后端、其余 7 种 composer 形状、27 段/24 行/60 渲染器/69 浮层/17 dock 的“全量复刻”。

> 修正：**live steering 已由 v0.5.1 实现**，从“不做”清单移出；`/forget-approvals` 不适用（授权只在会话内存）。

---

## 1. 里程碑排期

| 里程碑 | 主题 | 任务 | 依赖 | 规模 |
|---|---|---|---|---|
| **M1** | 内核收尾 | T1–T5 | 无 | 1–2 天 |
| **M2** | 工具治理 | T6–T8 | M1 | 1–2 天 |
| **M3** | 前端一致性 | T9–T13 | M2（T9 依赖 T6） | 2–3 天 |
| **M4** | 条件工具 | T14–T17 | M2 | 3–5 天 |
| **M5** | 命令与浮层 | T18–T21 | M3 | 2–3 天 |
| **M6** | 收尾 | T22 | 全部 | 0.5 天 |

执行顺序：M1 → M2 → M3 → M4 → M5 → M6；每个任务独立提交、独立可回滚。

---

## 2. 任务清单

### M1 · 内核收尾

#### T1 · openai-responses 输出预算拟合

- [ ] `zenagent/providers/openai_responses.py::stream` 在 `build_params` 前调用 `fit_stream_options(model, messages, options, tools)`，与另外 3 个适配器一致。
- [ ] 测试：`tests/test_openai_responses.py` 构造“窗口放不下默认 max_tokens”的场景，断言请求体 `max_output_tokens` 被下调、且不低于 1024；正常场景不变。
- [ ] 顺带修 CHANGELOG 的 “all three provider adapters” → 4 个。

#### T2 · MCP 调用级重连重试

- [ ] `zenagent/mcp/client.py` 增加 `McpTransportError(McpError)`；把传输层失败（连接关闭、写失败、进程退出、HTTP 传输错误、非法 JSON/SSE 不匹配）改抛该类；JSON-RPC 业务错误保持 `McpError`。
- [ ] `zenagent/mcp/manager.py::call`：捕获 `McpTransportError` → 关闭并丢弃旧 client → `_connect(server)` 一次 → 重发一次；仍失败返回可读 `McpError`。**不重试** `McpTimeout` 与 JSON-RPC 业务错误。
- [ ] 测试：新增 fixture `tests/fixtures/mcp_crash_once_server.py`（首次 `tools/call` 直接退出并留标记，重启后正常）；`tests/test_mcp_manager.py` 断言重连后调用成功、业务错误不重连（client 身份不变）。

#### T3 · partial result 全前端

- [ ] `zenagent/cli/repl.py`：消费 `tool_execution_update`，在 stderr/渲染器打一行 1s 节流进度（复用 `cli/render.py::tool_progress_line`，删除死代码状态）。
- [ ] `zenagent/cli/rpc.py`：事件序列化带上 `partial_result` 文本（截断），不改变既有字段。
- [ ] headless（`cli/main.py`）保持安全忽略，并加注释说明；`tests/test_rpc.py`、`tests/test_repl_logic.py` 覆盖。
- [ ] 单测：update 事件不丢/不乱序；REPL 同一 call 的进度行被替换而非刷屏。

#### T4 · 状态带 git / cache 段

- [ ] `RunStatus` 增加 `git_branch`/`git_dirty`/`git_staged`/`git_untracked` 或一个 `git_status` 字符串；`ChatController` 构造状态时填充（`git status --porcelain -b`，带 2s 缓存，失败静默）。
- [ ] `status/segments.py` 增加 `cache_read`/`cache_write` 段（数据已有），`git` 段显示 `branch ±n`；presets 补位。
- [ ] 测试：`tests/test_tui_status_band.py` 覆盖 branch/dirty/clean、无 git 仓库时段消失、cache 段数值。

#### T5 · TUI 键位补齐

- [ ] `app.py` BINDINGS：`f5`/`alt+r` → retry（与 `/retry` 同一实现）；`shift+ctrl+p` → 反向循环模型；`alt+shift+p` → plan↔上次模式切换。
- [ ] `i18n` 补 hotkeys 文案；`docs/guide/tui.md` 键位表同步。
- [ ] 测试：`tests/test_tui_controls.py` 断言三个 action 的效果（无 run 时 retry 提示、模式切换、模型反向）。

### M2 · 工具治理

#### T6 · ToolRegistry / hidden / default_inactive

- [ ] `AgentTool` 增加 `hidden: bool = False`、`default_inactive: bool = False`（内核只声明）。
- [ ] 新增 `zenagent/agent/registry.py`：`ToolRegistry`（active set、`reconcile(desired)`、变更通知）；`hidden` 工具不进 prompt、不可被模型调用；`default_inactive` 初始不在 active。
- [ ] `Runtime` 改用 registry：`_recompose` → `registry.reconcile(...)`，MCP/skills/extensions 热更新走同一路径；TUI/REPL `/reload` 复用。
- [ ] 测试：hidden 不进 prompt/不可调用；reconcile 只重建一次 prompt；MCP 后补连接后工具出现。

#### T7 · 工具声明 context 对象

- [ ] `before_tool_call`/`after_tool_call` 钩子拿到解析后的 `AgentTool`（已有）＋ `ToolContext`（cwd、approval、timeout、call_id）；after 钩子可改写 args 并重新校验。
- [ ] 保持向后兼容（现有钩子签名不改；新信息通过可选参数/字段）。
- [ ] 测试：钩子读到声明；after 改写 args 后校验失败 → 错误结果。

#### T8 · 文档一致性修复

- [ ] `docs/feature-map.md`：9 → 11 个内置工具 + todo/task；REPL 命令清单补 `/tools /restore /retry`。
- [ ] `docs/internals/providers.md`：工具数量；`docs/guide/tools-permissions.md`：补 `task`、`replace_all`。
- [ ] `docs/guide/tui.md`：修正 git branch/快捷键重复聚焦的描述；补新键位。
- [ ] `CHANGELOG.md`：Unreleased 段记录本分支交付。

### M3 · 前端一致性

#### T9 · TUI 模板/扩展命令 + REPL 命令补齐

- [ ] TUI `on_prompt_input_submitted` 支持 `/name args` 模板展开与扩展注册命令（与 REPL 同一解析器）。
- [ ] REPL 补 `/pin` `/new` `/settings` `/search` `/doctor`；TUI 补 `/import`。
- [ ] 抽出共享命令表或把 REPL 接到 `app/commands.py`（二选一，优先接共享注册表）。
- [ ] 测试：`tests/test_tui_usability.py`、`tests/test_repl_logic.py` 覆盖新增命令与模板展开。

#### T10 · Ctrl+G 外部编辑器

- [ ] `app.py`：Ctrl+G 把草稿写入临时文件、`self.suspend()` 调 `$VISUAL`/`$EDITOR`、回读替换草稿；退出码非 0 保留原草稿。
- [ ] 测试：monkeypatch editor 为脚本；无 editor 时提示。

#### T11 · 图片粘贴 + 转录图片行

- [ ] Ctrl+V：读剪贴板（`pbpaste`/`wl-paste`/`xclip` 探测），若是存在的图片路径或 file:// URL → 走 `/image` 同一条附件通道；否则按文本粘贴。
- [ ] 转录区：带图用户消息显示 `[image/png]` 行（复用 attachment chips 的模型）。
- [ ] 测试：`tests/test_images.py`、`tests/test_tui_chat.py`。

#### T12 · 转录/工具卡降噪

- [ ] 连续 `read_file` 调用合并为一张组卡（展开看每张）；`task`/`todo`/`ask`/`wait`/`fetch` 增加专用标题行。
- [ ] `NoticeRow` 增加 warning 级（黄），与 error 区分。
- [ ] 测试：`tests/test_tui_chat.py`、`tests/test_tui_rows.py`。

#### T13 · plan review 最小版

- [ ] plan 模式下模型输出计划后，`/plan-review` 打开只读复核浮层（计划全文 + “批准并切 full”/“继续 plan”）；批准=切模式，不改会话。
- [ ] 计划来源：最后一条 assistant 文本；无计划时提示。
- [ ] 测试：`tests/test_tui_usability.py` + `tests/test_app_commands.py`。

### M4 · 条件工具

#### T14 · github 只读子集

- [ ] 新工具 `github`：`pr_view`/`pr_diff`/`pr_checks`/`run_logs` 4 op；启动时 `shutil.which("gh")` 不存在则不注册；全部 read 档；输出走结果预算。
- [ ] 测试：假 `gh` 脚本（fixtures）覆盖 4 op 与缺失不注册。

#### T15 · checkpoint/rewind

- [ ] 新工具 `checkpoint(goal)` / `rewind(report)`：checkpoint 标记 + goal；rewind 用 report 替换中间探索上下文（复用 compaction 管线，保留 checkpoint 前内容），失败可回退。
- [ ] 测试：`tests/test_checkpoint.py` 覆盖 rewind 后上下文、失败回退、会话不损坏。

#### T16 · LSP 最小版（条件项）

- [ ] 先探针：`zenagent/tools/lsp/` 子进程 + JSON-RPC；pyright、typescript-language-server 按扩展名映射 + PATH 探测；缺 server 报 “server not installed” 而不是工具消失。
- [ ] 4 个只读 action（diagnostics/symbols/references/definition）+ rename（write 档）；只读 action=read 档；超时/崩溃隔离；不新增 pip 依赖。
- [ ] 测试：两个 server 的集成测试（环境缺失可 skip）；协议层 fake server 单测。
- [ ] 探针不达标 → 记录结论，最小版降级为“不注册”，在计划里标注 v0.6。

#### T17 · 内核小项

- [ ] `write_file` 保留/设置可执行位（目标已有可执行位时保持）；`read_file` 输出行号（`N| `）；`glob` 支持 `.gitignore` 过滤与 mtime 排序。
- [ ] 测试：`tests/test_tools_fs.py`、`tests/test_tools_search.py`。

### M5 · 命令与浮层

#### T18 · 命令批

- [ ] `/jobs`（列表 + kill）、`/trace`（打开当前 trace 文件）、`/shake`（分级瘦身：工具结果/图片/thinking）、`/handoff`（总结成交接文档后压缩）、`/btw`（侧问答，不污染主上下文）、`/copy`（选段复制）、`/add-dir` `/remove-dir` `/dirs`（多根工作区）、`/fresh` `/restart`。
- [ ] 测试：`tests/test_app_commands.py`、`tests/test_repl_logic.py`；多根工作区改 `Runtime`/工具 cwd 解析。

#### T19 · 浮层批

- [ ] hotkeys sheet（按键表浮层）、session tree（基于会话消息树的选择器）、usage dashboard（按会话/模型聚合）、settings 全屏中心（现有 palette 升级）、MCP add 向导（写 `~/.zenagent/mcp.json`）。
- [ ] 测试：`tests/test_tui_pickers.py`、`tests/test_tui_settings.py`。

#### T20 · dock/交互

- [ ] 菜单重复请求 focus 已开浮层；浮层内 Esc 三层（清搜索→退子视图→关面板）；`tools.toggleVisibility`（Ctrl+Shift+O）；`display.reset`（Alt+L）；模型反向循环键（与 T5 合并）。
- [ ] 测试：`tests/test_tui_controls.py`。

#### T21 · todo 用户编辑

- [ ] `/todo` 命令 + 可编辑浮层（新增/完成/删除/阻塞），与模型 `todo` 工具共用 sidecar。
- [ ] 测试：`tests/test_tui_todo.py`、`tests/test_todo.py`。

### M6 · 收尾

#### T22 · 闸门与文档

- [ ] `bash scripts/check.sh` 全绿；`bash scripts/release-check.sh --skip-build` 通过。
- [ ] 更新 `docs/feature-map.md`、`docs/guide/tui.md`、`docs/guide/tools-permissions.md`、`docs/guide/cli.md`、`README.md`（Tools/Status 段）。
- [ ] `CHANGELOG.md` Unreleased 段完整记录。
- [ ] 09/10/12 三份调研文档加“历史基线”指向本计划与 2026-11-01 核查结论。
- [ ] 汇总提交（或每任务提交 + 最终 merge commit）。

---

## 3. 验收定义

1. `bash scripts/check.sh` 全绿（ruff + 全量离线测试），新增测试覆盖每个任务的关键行为。
2. 每个任务独立 commit，conventional style；分支 `feat/p1-p2-completion` 可合并。
3. 文档与代码一致：本计划勾选完毕；feature-map/tui/README/CHANGELOG 同步。
4. 明确不做清单不变；live steering 从 N 移到已做。
5. 不新增第三方运行时依赖；`pyproject.toml` 的 `dependencies` 仍只有 `httpx`。

## 4. 风险与决策

| 风险 | 处置 |
|---|---|
| LSP 外部二进制与“纯 Python”定位冲突 | 先探针（pyright 一个 server），验收不达标就只保留“缺失提示”，最小版降 v0.6 |
| 图片粘贴依赖终端/系统剪贴板 | 探测 `pbpaste`/`wl-paste`/`xclip`，都缺则提示；不引入依赖 |
| plan review 语义边界 | 最小版只做“计划文本 + 批准切 full”，不做计划文件落盘/多轮修订 |
| 多根工作区改 cwd 模型 | 先只让工具解析 `extra_dirs`，不动项目作用域与权限围栏的根 |
| 任务量大 | 按里程碑独立提交，任一任务失败可回滚；M4/M5 条件项允许在证据不足时降级并记录 |

## 5. 与旧文档的关系

- 本计划取代 `09`/`10`/`12` 的“现状列”与“剩余项”结论；它们的 omp 调研与取舍理由仍然有效。
- 状态核对报告见本计划 §0；执行进度见任务复选框。
