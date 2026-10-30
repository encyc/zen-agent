# 文件地图

「我想改 X，应该看哪个文件？」按任务索引。

## 内核

| 想改什么 | 文件 | 关键符号 |
|---|---|---|
| 消息/内容块/用量结构 | `zenagent/types.py` | `AssistantMessage`、`ToolCall`、`Usage`、`Cost`、`message_to_json`/`message_from_json` |
| 事件类型 | `zenagent/events.py` | `AssistantEvent`、`AgentEvent`、`AbortToken`、`Aborted` |
| 一轮对话怎么跑 | `zenagent/agent/loop.py` | `agent_loop`、`AgentConfig`、`AgentState`、`AgentTool`、`ToolResultAgent` |
| 多轮/订阅/取消 | `zenagent/agent/agent.py` | `Agent` |
| 工具参数校验 | `zenagent/agent/validate.py` | `validate_arguments` |
| 会话读写 | `zenagent/agent/session.py` | `Session`、`session_path`、`latest_session`、`truncate_last_turn` |

## Provider 层

| 想改什么 | 文件 |
|---|---|
| 模型目录/定价/兼容开关 | `zenagent/providers/catalog.py`、`zenagent/catalog.yaml` |
| thinking 等级归一化 | `zenagent/providers/compat.py` |
| HTTP、SSE、重试、用量解析 | `zenagent/providers/transport.py` |
| 适配器注册与安全包装 | `zenagent/providers/registry.py`（`safe_stream`） |
| OpenAI Chat Completions | `zenagent/providers/openai_completions.py` |
| Anthropic Messages | `zenagent/providers/anthropic_messages.py` |
| OpenAI Responses | `zenagent/providers/openai_responses.py` |
| Google Generative AI | `zenagent/providers/google_generative_ai.py` |
| message 级重试 | `zenagent/retry.py` |

## 工具

| 工具 | 文件 |
|---|---|
| `read_file` / `write_file` / `edit_file` / `list_dir` | `zenagent/tools/fs.py` |
| `bash`（危险命令拦截） | `zenagent/tools/bash.py` |
| `glob` / `grep` | `zenagent/tools/search.py` |
| 工具清单 | `zenagent/tools/__init__.py`（`ALL_TOOLS`） |

## CLI / 交互

| 想改什么 | 文件 |
|---|---|
| 参数解析、headless、子命令 | `zenagent/cli/main.py` |
| REPL 主循环、斜杠命令 | `zenagent/cli/repl.py` |
| 流式渲染、状态行、diff | `zenagent/cli/render.py` |
| 权限门 | `zenagent/cli/permissions.py` |
| 上下文压缩 | `zenagent/cli/compact.py`（+ `zenagent/config.py::ContextGuard`） |
| 自动补全 | `zenagent/cli/completion.py` |
| `doctor` | `zenagent/cli/doctor.py` |
| 图片输入 | `zenagent/cli/images.py` |
| RPC | `zenagent/cli/rpc.py` |
| 导出/用量/主题/键位 | `zenagent/cli/{exportcmd,usagecmd,theme,keybindings}.py` |

## 扩展面

| 想改什么 | 文件 |
|---|---|
| MCP 传输/客户端 | `zenagent/mcp/client.py`、`zenagent/mcp/config.py` |
| MCP 生命周期/工具接入 | `zenagent/mcp/manager.py` |
| Skills | `zenagent/skills.py`（+ `frontmatter.py`） |
| 提示词模板 | `zenagent/templates.py` |
| Python 扩展 | `zenagent/extensions.py` |
| 配置解析 | `zenagent/config.py` |
| 上下文文件（AGENTS.md） | `zenagent/context_files.py` |

## 可观测性

| 想改什么 | 文件 |
|---|---|
| trace（turn/tool 序列） | `zenagent/trace.py` |
| 指标（每 run 一行 JSON） | `zenagent/telemetry.py` |
| 崩溃日志/诊断报告 | `zenagent/crash.py` |
| 会话导出 | `zenagent/export.py` |
| 跨会话用量 | `zenagent/usage.py` |

## 测试对应（节选）

| 测试 | 覆盖 |
|---|---|
| `tests/test_loop.py` | 循环、工具执行、终止条件、steering |
| `tests/test_agent_class.py` | 订阅、abort、wait_for_idle、reset |
| `tests/test_openai_stream.py` / `test_anthropic.py` / `test_openai_responses.py` / `test_google.py` | 各协议 fixture 回放 |
| `tests/test_transport.py` / `test_retry.py` | 重试、取消、用量解析 |
| `tests/test_tools_*.py` | 工具行为与边界 |
| `tests/test_permissions.py` | 权限门优先级 |
| `tests/test_mcp_client.py` / `test_mcp_manager.py` | MCP 协议与迟到重连 |
| `tests/test_skills.py` / `test_templates.py` / `test_extensions.py` | 文件即扩展 |
| `tests/test_session.py` / `test_compact.py` | 持久化与压缩 |
| `tests/test_rpc.py` | RPC 协议 |
| `tests/test_cli.py` / `test_repl_logic.py` | CLI/REPL 接线 |
| `evals/cases/*.json` | 行为评测（真实模型） |
