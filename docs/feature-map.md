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
| 9 个内置工具 + `todo` + 工具内核（审批档/结果预算/分页/超时/循环守卫） | `tools/{fs,bash,search,todo}.py`、`tools/web/`、`agent/{result_budget,timeouts}.py`、`providers/limits.py` | `test_tools_*.py`、`test_result_budget.py`、`test_todo.py`、`test_timeouts.py`、`test_provider_limits.py` | [工具与权限](guide/tools-permissions.md) |
| 权限门（deny/allow/read-only/危险命令） | `app/permissions.py` | `test_permissions.py` | 同上 |
| 运行模式 plan/ask/full | `app/modes.py`、`app/permissions.py` | `test_modes.py` | 同上 |
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
| 模型/会话选择器、侧边栏、命令面板 | `screens/modals.py`、`widgets/sidebar.py` | `test_tui_pickers.py` | 同上 |
| 会话操作（new/fork/delete/export/rename） | `screens/modals.py::SessionPicker`、`cli/tui/app.py` | `test_tui_sessions.py` | 同上 |
| 收尾（窄屏/退出摘要） | `widgets/status_bar.py`、`app.py` | `test_tui_polish.py` | 同上 |

## 6. CLI / REPL

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| 参数解析 / headless / 子命令 | `cli/main.py` | `test_cli.py` | [CLI](guide/cli.md) |
| REPL 与斜杠命令 | `cli/repl.py` | `test_repl_logic.py` | [CLI](guide/cli.md) |
| 流式渲染 / diff / 状态行 | `cli/render.py` | `test_render.py` | — |
| 补全（命令 + 参数值） | `cli/completion.py`、`app/slash_args.py` | `test_completion.py`、`test_slash_args.py` | [CLI](guide/cli.md) |
| TUI 提示/选择器（值补全 + mode/thinking picker） | `cli/tui/app.py`、`widgets/command_hints.py`、`screens/modals.py::ChoicePicker` | `test_tui_usability.py` | [TUI](guide/tui.md) |
| 图片输入 | `attachments.py` | `test_images.py` | — |
| 主题 / 键位 | `cli/theme.py`、`cli/keybindings.py` | `test_theme.py` | — |

REPL 命令：`/help /exit /clear /model /thinking /mode /session /sessions /rename /fork /import /undo /compact /image /images /skills /mcp /reload /theme /hotkeys`。

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
| `/reload` 热加载 | `cli/repl.py::reload_runtime`、`app/runtime.py::rediscover` | `test_repl_logic.py`、`test_app_discovery.py` | 同上 |
| `/skills` 实时重扫 | `app/runtime.py::refresh_skills` | `test_app_discovery.py` | 同上 |
| Python 扩展（register(api)） | `extensions.py` | `test_extensions.py` | [扩展](guide/extensions.md)、[扩展内核](internals/extensions-internals.md) |
| 自定义 provider/模型（config.toml） | `config.py`、`providers/catalog.py::model_from_mapping` | `test_config.py` | [配置](guide/config.md) |

## 9. RPC / SDK

| 功能 | 源码 | 测试 | 文档 |
|---|---|---|---|
| JSONL RPC 服务 | `cli/rpc.py` | `test_rpc.py` | [RPC 与 SDK](guide/rpc-sdk.md) |
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
- **沙箱**：没有容器/权限隔离；安全性依赖部署方式（CI 容器、只读挂载）。
- **plan mode 进阶**：当前是「只读 + 计划提示」，没有独立的计划产物/审阅（omp 的 `/plan-review` 未做）。
- **扩展 UI**：扩展只能贡献工具/命令/prompt/hooks，不能改 REPL 界面。
- **多模态输出**：能读图片，但不能生成图片/音频。
- **Python 版本**：`requires-python >=3.11`（3.11–3.14 已验证）。
