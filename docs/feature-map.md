# 功能地图

「这个能力在哪实现、怎么测、看哪篇文档」。按功能域组织。

## 1. 内核（Kernel）

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 消息/内容块/用量类型 | `zenagent/types.py` | `test_types.py` | [类型与事件](internals/types-events.md) |
| 两层事件 + 取消令牌 | `zenagent/events.py` | `test_events.py` | [事件全表](reference/events.md) |
| 无状态执行器 | `zenagent/agent/loop.py::agent_loop` | `test_loop.py` | [Agent Loop](internals/agent-loop.md) |
| 有状态包装 | `zenagent/agent/agent.py::Agent` | `test_agent_class.py` | [Agent 类](internals/agent-class.md) |
| 工具参数校验 | `zenagent/agent/validate.py` | `test_validate.py` | [护栏](internals/guardrails.md) |
| 会话 JSONL | `zenagent/agent/session.py` | `test_session.py` | [持久化](internals/persistence.md) |

## 2. Provider 层

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 模型目录/定价/兼容开关 | `providers/catalog.py` + `catalog.yaml` | `test_catalog.py` | [Provider 层](internals/providers.md) |
| thinking 归一化 | `providers/compat.py` | `test_catalog.py` | 同上 |
| HTTP/SSE/重试/用量 | `providers/transport.py` | `test_transport.py` | 同上 |
| 安全包装 `safe_stream` | `providers/registry.py` | `test_registry.py` | 同上 |
| OpenAI Chat Completions | `providers/openai_completions.py` | `test_openai_stream.py`、`test_openai_request.py` | 同上 |
| Anthropic Messages | `providers/anthropic_messages.py` | `test_anthropic.py` | 同上 |
| OpenAI Responses | `providers/openai_responses.py` | `test_openai_responses.py` | 同上 |
| Google Generative AI | `providers/google_generative_ai.py` | `test_google.py` | 同上 |
| message 级重试 | `retry.py` | `test_retry.py` | [持久化/重试](internals/persistence.md) |

## 3. 工具与权限

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 11 个内置工具 + `todo`/`task`/`ask`/`wait`/`checkpoint`/`rewind`/`lsp` + `github`（条件） + 工具内核（审批档/结果预算/分页/超时/循环守卫/hidden 注册表） | `tools/{fs,bash,search,todo,task,ask,wait,lsp,github}.py`、`tools/web/`、`app/rewind.py`、`agent/{result_budget,timeouts,registry}.py`、`providers/limits.py` | `test_tools_*.py`、`test_rewind.py`、`test_result_budget.py`、`test_todo.py`、`test_timeouts.py`、`test_provider_limits.py`、`test_tool_registry.py` | [工具与权限](guide/tools-permissions.md) |
| 权限门（deny/allow/read-only/危险命令） | `app/permissions.py` | `test_permissions.py` | 同上 |
| 风险判定与路径围栏（workspace / protected / destructive） | `app/safety.py`、`app/permissions.py::assess` | `test_safety.py`、`test_permissions.py` | 同上 |
| 写入快照与 `/restore` | `app/journal.py`、`app/restore.py`、`project.py::project_backups_dir` | `test_journal.py`、`test_restore.py` | 同上 |
| 运行模式 plan/ask/zen | `app/modes.py`、`app/permissions.py` | `test_modes.py` | 同上 |
| 交互审批（TUI 弹窗/超时/会话授权） | `app/permissions.py::PermissionBroker`、`cli/tui/screens/modals.py::ToolApproval` | `test_app_permissions.py`、`test_tui_approval.py` | 同上 |
| 参数校验 | `agent/validate.py` | `test_validate.py` | [工具内核](internals/tools-internals.md) |

## 4. 上下文

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 请求前整轮裁剪 | `config.py::naive_overflow_prune` + `ContextGuard` | `test_compact.py` | [上下文管理](internals/context-compaction.md) |
| 轮末总结压缩 | `app/compact.py` | `test_compact.py` | 同上 |
| AGENTS.md 上下文文件 | `context_files.py` | `test_context_files.py` | 同上 |

## 5. 持久化与可观测性

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 会话、恢复、fork/import/undo | `agent/session.py`、`cli/repl.py` | `test_session.py` | [会话](guide/sessions.md) |
| 项目作用域（根/key/布局/legacy/adopt） | `project.py`、`agent/session.py`、`app/sessions.py` | `test_project.py`、`test_project_scoping.py` | [项目作用域](guide/projects.md) |
| 会话显示名（brief/rename sidecar） | `agent/session.py`、`app/sessions.py` | `test_session.py`、`test_app_sessions.py` | 同上 |
| trace | `trace.py` | `test_trace.py` | 同上 |
| run 指标 | `telemetry.py` | `test_telemetry.py` | 同上 |
| 崩溃日志/诊断 | `crash.py`、`cli/doctor.py` | `test_crash.py`、`test_doctor.py` | 同上 |
| 导出 MD/HTML | `export.py`、`cli/exportcmd.py` | `test_export.py` | 同上 |
| 跨会话用量 | `usage.py`、`cli/usagecmd.py` | `test_usage.py` | 同上 |

## 5.5 TUI（Textual）

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| Textual 外壳、状态栏、输入 | `cli/tui/{app,run}.py`、`widgets/{status_bar,prompt_input}.py` | `test_tui_skeleton.py` | [TUI](guide/tui.md) |
| 富渲染（markdown/工具卡/usage） | `widgets/{chat_log,tool_card}.py` | `test_tui_chat.py` | 同上 |
| 队列/steer/中断 | `app.py`、`app/chat.py` | `test_tui_controls.py` | 同上 |
| 待发区与插话：输入框上方排队/插话 band、点击/Ctrl+S 升格、滞留插话续跑、跨会话丢弃 | `app/{views,chat}.py`、`cli/tui/widgets/pending_band.py`、`cli/tui/app.py`、`agent/agent.py` | `test_app_pending.py`、`test_tui_pending_band.py`、`test_tui_controls.py` | [TUI](guide/tui.md) |
| 模型/会话选择器、命令面板 | `screens/modals.py` | `test_tui_pickers.py` | 同上 |
| 工作面板侧栏（Todo/Subagent/Jobs）+ 后台活提示条 | `widgets/{sidebar,work_strip}.py`、`app.py` | `test_tui_sidebar.py`、`test_tui_layout.py` | [TUI](guide/tui.md) |
| todo 只读浮层（HUD/侧栏点击、命令面板入口） | `screens/todo_list.py`、`widgets/todo_hud.py` | `test_tui_todo.py` | 同上 |
| 工作面板打磨：拖拽调宽、ctx 组成（条+浮层）、job 日志浮层、todo 全量内联、语义色板 | `widgets/{sidebar,sidebar_resize}.py`、`screens/{context_breakdown,job_log,todo_list}.py`、`palette.py`、`app/context.py` | `test_tui_{sidebar_resize,context,job_log,palette}.py`、`test_app_context.py` | [TUI](guide/tui.md) |
| 外观与主题：dark/light/auto、统一色彩 token、TUI 双 Textual 主题 + REPL 派生、即时切换 | `app/appearance.py`、`cli/tui/app_theme.py`、`cli/tui/{palette.py,theme.tcss}`、`cli/theme.py` | `test_appearance.py`、`test_tui_{app_theme,theme_css,palette}.py`、`test_theme.py` | [TUI](guide/tui.md) |
| 界面语言（i18n）：en/zh catalog、`/lang`/`--lang`/设置面板、TUI+REPL+命令描述翻译、CJK 宽度 | `i18n/{__init__,en,zh}.py`、`cli/tui/{app.py,labels.py,textutil.py}`、`app/{commands,slash_args}.py`、`cli/{repl,completion}.py` | `test_i18n.py`、`test_i18n_tui.py`、`test_i18n_app.py`、`test_completion.py` | [i18n](guide/i18n.md) |
| 子代理转录：sidecar 持久化 + 只读查看 + resume 回看 | `agent/subagents.py`、`tools/task.py`、`app/chat.py`、`cli/tui/{widgets/subagent_banner.py,app.py}` | `test_subagents_store.py`、`test_task.py`、`test_app_chat.py`、`test_tui_subagent_view.py`、`test_tui_subagent_resume.py` | 同上 |
| 会话操作（new/fork/delete/export/rename） | `screens/modals.py::SessionPicker`、`cli/tui/app.py` | `test_tui_sessions.py` | 同上 |
| 收尾（窄屏/退出摘要） | `widgets/status_bar.py`、`app.py` | `test_tui_polish.py` | 同上 |
| P1/P2 收尾：read 分组、plan review、hotkeys/usage/MCP 向导、todo 编辑、剪贴板/外部编辑器、ctrl+shift+o/alt+l | `widgets/rows.py::ReadGroup`、`screens/modals.py::{PlanReview,HotkeysSheet,UsageDashboard,McpAdd,TodoEditor}`、`widgets/clipboard.py`、`app.py` | `test_tui_chat.py`、`test_tui_controls.py`、`test_tui_tool_card_families.py` | [TUI](guide/tui.md) |

## 6. CLI / REPL

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 参数解析 / headless / 子命令 | `cli/main.py` | `test_cli.py` | [CLI](guide/cli.md) |
| REPL 与斜杠命令（Runtime 装配 + 共享注册表 + 文本降级） | `cli/repl.py`、`app/commands.py` | `test_repl_logic.py`、`test_repl_commands.py` | [CLI](guide/cli.md) |
| 前端共享运行选项（thinking/trace/metrics/auto-compact/api-key/tools） | `app/runtime.py::RuntimeOptions` | `test_app_runtime.py` | [TUI](guide/tui.md)、[RPC](guide/rpc-sdk.md) |
| 流式渲染 / diff / 状态行 | `cli/render.py` | `test_render.py` | — |
| 补全（命令 + 参数值） | `cli/completion.py`、`app/slash_args.py` | `test_completion.py`、`test_slash_args.py` | [CLI](guide/cli.md) |
| TUI 提示/选择器（值补全 + mode/thinking picker） | `cli/tui/app.py`、`widgets/command_hints.py`、`screens/modals.py::ChoicePicker` | `test_tui_usability.py` | [TUI](guide/tui.md) |
| 图片输入 | `attachments.py` | `test_images.py` | — |
| 主题 / 键位 | `cli/theme.py`、`cli/keybindings.py` | `test_theme.py` | — |
| 会话链接（`/urls`、`/copy <n>`、`/open <n>`） | `app/links.py`、`app/commands.py` | `test_app_links.py`、`test_app_commands.py`、`test_tui_links.py` | — |

REPL/TUI 共用同一份命令表（`app/commands.py`，48 条），REPL 额外支持 legacy 主题名：
`/help /exit /quit /clear /model /thinking /mode /session /sessions /rename /fork /import /undo /restore /retry /tools /compact /image /images /skills /mcp /reload /theme /lang /hotkeys /pin /new /settings /search /doctor /jobs /copy /urls /open /btw /shake /handoff /add-dir /remove-dir /dirs /fresh /trace /export /usage /context /plan-review /todo /memory`。

REPL 的 screen 类命令（picker/浮层）由 `render_command_result` 渲染成文本，不再静默无输出。

## 6.5 前端能力对照

TUI / REPL / RPC 三个入口共享 `Runtime`/`ChatController`/`CommandRegistry`/`ModelManager`/
`SessionManager`（`zenagent/app`），差异只在“怎么呈现”。headless `-p` 是例外：它不走 app 层，
而是在 `cli/main.py::run_headless` 里用 `_build_agent` 自己装配 `Agent` + `Session`，能力与
前三者对齐（同一批工具、同一份 prompt 块、同一套 `ToolRegistry` 过滤与进程级收尾），但没有
`ChatController`，所以审批往返、ask、steering、排队、斜杠命令按设计缺席。

| 能力 | TUI | REPL | headless `-p` | RPC |
|---|---|---|---|---|
| 工具（含 checkpoint/rewind/lsp/github/todo/task/skill/memory） | ✅ | ✅ | ✅ | ✅ |
| MCP / skills / extensions（工具 + prompt + hooks） | ✅ | ✅ | ✅ | ✅ |
| 项目记忆（`memory`/`memory_write` + `## Project memory` 提示块） | ✅ 面板 | ✅ 文本 | ✅（无 UI） | ✅ |
| 图片输入 | ✅ `--image` / `/image` / 粘贴 | ✅ `--image` / `/image` | ✅ `--image` | ✅ `prompt.images` |
| 模板（`.zenagent/commands` 的 `/name args`） | ✅ | ✅ | ❌ 不加载 | ⚠️ 加载但不展开 |
| 扩展命令（`register_command`） | ✅ | ✅ | ❌ | ❌ `command` 只派发内置命令 |
| 会话持久化 / resume / fork / undo / compact / retry | ✅ 浮层 | ✅ 文本 | ⚠️ 仅 resume | ✅ `session` 请求 |
| 模型切换（key/thinking/session id） | ✅ | ✅ | ✅ 启动参数 | ✅ `model` 请求 |
| 审批 | ✅ 弹框 | ✅ 终端 y/n/a | ❌ 非交互（ask 下拒绝） | ✅ `approval_request` 往返 |
| ask 工具 | ✅ 弹框 | ✅ 终端问答 | ❌ 降级 | ❌ 降级 |
| steering / 排队 | ✅ 双 lane | ✅ 插话 | ❌ | ✅ `steer` |
| 异步子代理（`task` + `async` / `wait`） | ✅ 工具 + 侧栏实时状态 | ✅ 工具（文本） | ✅ 工具（无 UI） | ✅ 工具（事件 `details`） |
| 用量 / context / trace / metrics | ✅ 面板 + flag | ✅ 文本 + flag | ✅ `--json` / flag | ✅ `usage` / flag |
| 斜杠命令 | ✅ 48 | ✅ 48（文本降级） | ❌ | ✅ 48 内置（`command` 请求） |

RPC 的事件与状态数据最全：`event` 带 `message`/`new_messages`/工具 `details`/assistant `error`，
`status` 直接携带 TUI 同源 reducer 的 thinking、context 占用、todos、pending、jobs、subagents
与子代理花费；headless 只有 `--json` 的 `ok`/`text`/`usage`/`messages`/`error`/`termination`。

模板展开（`/name args`）与扩展命令在 TUI、REPL 都有，RPC 的 `command` 请求只派发内置命令；
TUI 的侧栏（todo/subagent/jobs/context）、job 日志浮层、子代理转录查看器是 TUI 独有的呈现，
见 [TUI](guide/tui.md)。REPL 额外保留 legacy 主题名（如 `/theme solarized`）。

## 7. MCP

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| JSON-RPC / stdio / HTTP(JSON+SSE) | `mcp/client.py` | `test_mcp_client.py` | [MCP](guide/mcp.md) |
| 配置解析 | `mcp/config.py` | `test_mcp_client.py` | 同上 |
| 生命周期 / 迟到重连 / 热替换 | `mcp/manager.py` | `test_mcp_manager.py` | [MCP 内核](internals/mcp-internals.md) |

## 8. 文件即扩展

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| Skills（SKILL.md、read_skill、索引） | `skills.py`、`frontmatter.py` | `test_skills.py` | [Skills/模板](guide/skills-templates.md) |
| 提示词模板（`/name args`） | `templates.py` | `test_templates.py` | 同上 |
| Prompt 体系（blocks / compose / golden / 预算） | `prompts/`、`app/support.py::system_prompt`（薄封装） | `test_prompts_*.py` | [设计](superpowers/specs/2026-10-31-prompt-system-design.md) |
| `/reload` 热加载 | `cli/repl.py::reload_runtime`、`app/runtime.py::rediscover` | `test_repl_logic.py`、`test_app_discovery.py` | 同上 |
| `/skills` 实时重扫 | `app/runtime.py::refresh_skills` | `test_app_discovery.py` | 同上 |
| Python 扩展（register(api)） | `extensions.py` | `test_extensions.py` | [扩展](guide/extensions.md)、[扩展内核](internals/extensions-internals.md) |
| 自定义 provider/模型（config.toml） | `config.py`、`providers/catalog.py::model_from_mapping` | `test_config.py` | [配置](guide/config.md) |

## 9. RPC / SDK

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| JSONL RPC 服务（v1 + v2：审批/steer/会话/命令） | `cli/rpc.py` | `test_rpc.py`、`test_rpc_v2.py` | [RPC 与 SDK](guide/rpc-sdk.md) |
| Python SDK（Agent 直接调用） | `agent/agent.py` | `test_agent_class.py` | 同上 |
| 可运行示例 | `examples/` | `test_examples.py` | 同上 |

## 10. 评测

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 行为评测 runner | `scripts/eval.py` | —（需要 key） | [测试策略](testing.md) |
| 报告/baseline 对比 | `evals_runner.py`、`scripts/eval.py` | `test_evals_runner.py` | 同上 |
| 18 条用例 | `evals/cases/*.json` | — | 同上 |

## 已知未做（有意留白）

- **分发/安装**：安装器、原生构建、Nix、monorepo 拆包。
- **MCP 进阶**：OAuth、resources、prompts、sampling、调用级 abort。
- **telemetry 远端上报**：当前只写本地 JSONL。
- **OS 级沙箱**：没有容器/权限隔离。现有的是 harness 层护栏（写路径围栏、受保护路径、破坏性命令确认）与项目级写入快照；它们防手滑，不防恶意仓库 / prompt injection。
- **plan mode 进阶**：`/plan-review` 只是复核最近一次 assistant 文本（`a` 切 zen），还没有独立的计划文件/结构化计划产物。
- **扩展 UI**：扩展只能贡献工具/命令/prompt/hooks，不能改 REPL 界面。
- **多模态输出**：能读图片，但不能生成图片/音频。
- **Python 版本**：`requires-python >=3.11`（3.11–3.14 已验证）。
