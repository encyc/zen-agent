# zen-agent 节点册

> manifest v3 单源生成，勿手改。每节 = 一个图节点。

## 簇：基础层（`base`）

### `__main__` — __main__.py

python -m zenagent 入口，转发 cli.main

- 文件：`zenagent/__main__.py`
- 出边：→ `cli`（—）
- 依赖强度：被 0 个节点 import；依赖 1 个节点

### `attachments` — attachments.py

图片附件读取：magic bytes 定 MIME、本地校验后构造 ImageContent

- 文件：`zenagent/attachments.py`
- 出边：→ `types`（ImageContent）
- 入边：`app.chat` →（read_image_argument）；`cli` →（read_image_argument）；`cli.rpc` →（read_image_argument）；`cli.tui.app_parts` →（read_image_argument）
- 依赖强度：被 4 个节点 import；依赖 1 个节点

### `config` — config.py

config.toml 读写与默认配置（~/.zenagent/config.toml）

- 文件：`zenagent/config.py`
- 关键符号：`UserConfig`、`ContextGuard`
- 出边：→ `limits`（parse_limits）；→ `project`（project_config_path）；→ `providers`（ProviderConfig、model_from_mapping）；→ `types`（—）
- 入边：`agent.rt` →（ContextGuard）；`app.assembly` →（ContextGuard、UserConfig、apply_user_config、load_user_config）；`cli` →（UserConfig、apply_user_config、config_path、load_user_config）；`cli.rpc` →（config_path、load_user_config）；`tools` →（—）
- 依赖强度：被 5 个节点 import；依赖 4 个节点
- 🔴 健康红点：0 error / 2 warn（见 health/report.md）

### `context_files` — context_files.py

AGENTS.md 等项目指令文件发现，注入 system prompt（--no-context-files 可关）

- 文件：`zenagent/context_files.py`
- 出边：→ `prompts`（read_block、render）
- 入边：`app.assembly` →（discover_context_files、load_context_files）
- 依赖强度：被 1 个节点 import；依赖 1 个节点

### `crash` — crash.py

崩溃日志钩子：未捕获异常落盘

- 文件：`zenagent/crash.py`
- 入边：`cli` →（install_excepthook）
- 依赖强度：被 1 个节点 import；依赖 0 个节点

### `evals_runner` — evals_runner.py

行为评测纯函数部分（报告读写/基线对比）；runner 在 scripts/eval.py

- 文件：`zenagent/evals_runner.py`
- 依赖强度：被 0 个节点 import；依赖 0 个节点

### `events` — events.py

两层事件体系 + AbortToken/Aborted：取消是协议内正常状态

- 文件：`zenagent/events.py`
- 关键符号：`AgentEvent`、`AssistantEvent`、`AbortToken`、`Aborted`
- 出边：→ `types`（—）
- 入边：`agent.loop` →（AgentEvent）；`agent.rt` →（AbortToken）；`app.chat` →（—）；`app.transcript` →（—）；`cli` →（—）；`cli.rpc` →（—）；`providers` →（Aborted、AssistantEvent）；`retry` →（—）；`telemetry` →（—）；`tools` →（—）；`trace` →（—）
- 依赖强度：被 11 个节点 import；依赖 1 个节点

### `export` — export.py

会话导出

- 文件：`zenagent/export.py`
- 出边：→ `agent.rt`（Session）；→ `types`（—）
- 入边：`app.transcript` →（export_session）；`cli` →（export_session）
- 依赖强度：被 2 个节点 import；依赖 2 个节点

### `extensions` — extensions.py

Python 扩展加载与 ExtensionAPI

- 文件：`zenagent/extensions.py`
- 关键符号：`ExtensionAPI`、`LoadedExtensions`、`ExtensionCommand`
- 出边：→ `agent.loop`（—）
- 入边：`app.assembly` →（load_extensions）
- 依赖强度：被 1 个节点 import；依赖 1 个节点

### `frontmatter` — frontmatter.py

极简 frontmatter 解析（skills/模板共用，免 YAML 依赖）

- 文件：`zenagent/frontmatter.py`
- 入边：`app.chat` →（parse_frontmatter）；`skills` →（parse_frontmatter）；`templates` →（parse_frontmatter）
- 依赖强度：被 3 个节点 import；依赖 0 个节点

### `http_client` — http_client.py

进程级共享 httpx.AsyncClient 构造（抗敌意 shell 环境）

- 文件：`zenagent/http_client.py`
- 入边：`mcp` →（async_client）；`providers` →（async_client）；`tools.web` →（async_client）；`update_check` →（async_client）
- 依赖强度：被 4 个节点 import；依赖 0 个节点

### `limits` — limits.py

[limits]/[task] 预算唯一注册表与外科手术式写盘（write_managed 唯一写路径）

- 文件：`zenagent/limits.py`
- 关键符号：`Limit`、`Limits`、`LimitsWriteError`
- 入边：`app.assembly` →（—）；`cli` →（describe）；`cli.rpc` →（KNOB_BY_KEY.get、coerce_value、read_managed、write_managed）；`config` →（parse_limits）
- 依赖强度：被 4 个节点 import；依赖 0 个节点
- 🔴 健康红点：0 error / 1 warn（见 health/report.md）

### `plugins` — plugins

声明式能力包：manifest 校验、发现、安装/卸载、启停与凭据、贡献解析

- 文件：`zenagent/plugins/__init__.py`、`zenagent/plugins/manifest.py`、`zenagent/plugins/state.py`、`zenagent/plugins/discovery.py`、`zenagent/plugins/install.py`、`zenagent/plugins/catalog.py`
- 关键符号：`PluginManifest`、`ResolvedContributions`
- 出边：→ `settings`（state 读写 settings.api_keys 中的 plugin:<id>:<key>）
- 入边：`app.assembly` →（plugin_wiring 把插件贡献翻译成 MCP 配置）；`cli.rpc` →（ops_plugins：内置目录、发现、安装/卸载、启停与凭据）
- 依赖强度：被 2 个节点 import；依赖 1 个节点

### `project` — project.py

项目身份与目录布局：<root>/.zenagent/ 随仓库 vs ~/.zenagent/projects/ 本机状态

- 文件：`zenagent/project.py`
- 关键符号：`ProjectRelocation`、`ProjectMeta`
- 入边：`agent.rt` →（all_session_dirs、legacy_sessions_dir、project_key_for_session、project_sessions_dir）；`app.assembly` →（find_project_root、project_key、project_key_for_session、write_project_meta）；`app.transcript` →（legacy_sessions_dir、project_backups_dir、project_root_for_sessions_dir、project_sessions_dir）；`cli` →（legacy_sessions_dir、project_config_path）；`cli.rpc` →（find_project_root、project_config_path、project_dir、project_key）；`cli.tui.app_parts` →（find_project_root、project_key、project_key_for_session）；`config` →（project_config_path）；`mcp` →（project_mcp_path）；`skills` →（find_project_root）；`templates` →（find_project_root）
- 依赖强度：被 10 个节点 import；依赖 0 个节点

### `retry` — retry.py

通用重试 helper（与 providers/message 级重试无关）

- 文件：`zenagent/retry.py`
- 出边：→ `events`（—）；→ `providers`（safe_stream）；→ `types`（—）
- 入边：`agent.loop` →（stream_with_retry）
- 依赖强度：被 1 个节点 import；依赖 3 个节点

### `settings` — settings.py

settings.json(0600)：API keys 与 UI 偏好，优先级高于环境变量

- 文件：`zenagent/settings.py`
- 关键符号：`SettingsStore`
- 入边：`app.assembly` →（disabled_skills、get_api_key）；`cli` →（describe、get_api_key、get_pref、set_pref）；`cli.rpc` →（describe、disabled_skills、get_api_key、set_api_key）；`cli.tui` →（get_pref）；`cli.tui.app_parts` →（get_api_key、set_api_key、set_pref）；`cli.tui.screens` →（describe、set_pref）；`plugins` →（state 读写 settings.api_keys 中的 plugin:<id>:<key>）；`tools.web` →（get_api_key、get_pref）
- 依赖强度：被 8 个节点 import；依赖 0 个节点

### `skills` — skills.py

文件即扩展：SKILL.md 发现 + skill 工具构造

- 文件：`zenagent/skills.py`
- 关键符号：`Skill`
- 出边：→ `agent.loop`（AgentTool、ToolResultAgent）；→ `frontmatter`（parse_frontmatter）；→ `project`（find_project_root）；→ `prompts`（read_block、render、tool_description）；→ `types`（TextContent）
- 入边：`app.assembly` →（build_skill_tool、default_skill_roots、discover_skills、format_skill_entries）；`app.chat` →（—）；`cli.rpc` →（default_skill_roots、discover_skills）
- 依赖强度：被 3 个节点 import；依赖 5 个节点

### `telemetry` — telemetry.py

（职责待 enrich：manifest resp 为空）

- 文件：`zenagent/telemetry.py`
- 关键符号：`MetricsRecorder`、`RunMetrics`
- 出边：→ `events`（—）；→ `types`（Usage）
- 入边：`app.assembly` →（MetricsRecorder、append_metrics）
- 依赖强度：被 1 个节点 import；依赖 2 个节点

### `templates` — templates.py

prompt 模板展开（expand）

- 文件：`zenagent/templates.py`
- 关键符号：`PromptTemplate`
- 出边：→ `frontmatter`（parse_frontmatter）；→ `project`（find_project_root）
- 入边：`app.assembly` →（default_template_roots、discover_templates）；`cli` →（expand）；`cli.tui.app_parts` →（expand）
- 依赖强度：被 3 个节点 import；依赖 2 个节点

### `trace` — trace.py

append-only JSONL 执行 trace：工具/参数/结果/耗时

- 文件：`zenagent/trace.py`
- 关键符号：`TraceRecorder`、`TraceRecord`
- 出边：→ `events`（—）
- 入边：`app.assembly` →（TraceRecorder）
- 依赖强度：被 1 个节点 import；依赖 1 个节点

### `types` — types.py

全部数据类型：消息/内容块/用量/成本（全图 fan-in 最高）

- 文件：`zenagent/types.py`
- 关键符号：`AssistantMessage`、`Cost`、`ToolCall`、`Usage`、`UserMessage`
- 入边：`agent.loop` →（SystemMessage、TextContent、ToolDef、ToolResultMessage）；`agent.rt` →（TextContent、UserMessage、message_from_json）；`app.assembly` →（UserMessage）；`app.chat` →（Cost、SystemMessage、Usage、UserMessage）；`app.transcript` →（SystemMessage、TextContent、UserMessage）；`attachments` →（ImageContent）；`cli` →（—）；`cli.rpc` →（—）；`cli.tui.widgets` →（—）；`config` →（—）；`events` →（—）；`export` →（—）；`mcp` →（ImageContent、TextContent）；`providers` →（AssistantMessage、Cost、TextContent、ThinkingContent）；`retry` →（—）；`skills` →（TextContent）；`telemetry` →（Usage）；`tools` →（TextContent）；`usage` →（message_from_json）
- 依赖强度：被 19 个节点 import；依赖 0 个节点

### `update_check` — update_check.py

TUI 专用 PyPI 版本检查（后台、静默失败）

- 文件：`zenagent/update_check.py`
- 出边：→ `http_client`（async_client）
- 入边：`cli.tui.app_parts` →（format_notice、installed_version、is_disabled、mark_notified）
- 依赖强度：被 1 个节点 import；依赖 1 个节点

### `usage` — usage.py

用量聚合 collect_usage（四前端唯一一份）

- 文件：`zenagent/usage.py`
- 关键符号：`UsageReport`、`ModelUsage`
- 出边：→ `agent.rt`（all_session_dirs、sessions_dir）；→ `types`（message_from_json）
- 入边：`cli` →（collect_usage、render_report、session_paths）；`cli.tui.screens` →（collect_usage、render_report、session_paths）
- 依赖强度：被 2 个节点 import；依赖 2 个节点

## 簇：运行时内核（`agent`）

### `agent.loop` — 无状态执行内核

无状态执行器：agent_loop 组装请求、流式调 provider、终止判定；tool_exec 工具调度（权限门→校验→并行/串行）与结果回写；result_budget/timeouts/guard 循环护栏。已知常量回指环在本节点内部（cycles_seen）

- 文件：`zenagent/agent/loop.py`、`zenagent/agent/tool_exec.py`、`zenagent/agent/result_budget.py`、`zenagent/agent/timeouts.py`、`zenagent/agent/guard.py`、`zenagent/agent/validate.py`、`zenagent/agent/protocol.py`、`zenagent/agent/registry.py`
- 关键符号：`AgentConfig`、`AgentState`、`ToolOutcome`、`AgentTool`、`ToolResultAgent`
- 出边：→ `events`（AgentEvent）；→ `providers`（stream_with_retry → safe_stream → adapter.stream → transport）；→ `retry`（stream_with_retry）；→ `types`（SystemMessage、TextContent、ToolDef、ToolResultMessage）
- 入边：`agent.rt` →（Agent.prompt → _drive → agent_loop（每 run 一个 task））；`app.assembly` →（ToolRegistry）；`app.chat` →（—）；`app.transcript` →（AgentTool、ToolResultAgent）；`cli` →（—）；`extensions` →（—）；`mcp` →（AgentTool、ToolResultAgent）；`skills` →（AgentTool、ToolResultAgent）；`tools` →（AgentTool、ToolResultAgent）；`tools.web` →（—）
- 依赖强度：被 10 个节点 import；依赖 4 个节点
- 🔴 健康红点：0 error / 2 warn（见 health/report.md）

### `agent.rt` — 有状态运行时

有状态运行时：Agent（历史/订阅者/steering/abort）、Session JSONL 持久化、子代理运行时（task 工具的背后）、memory_store/artifacts

- 文件：`zenagent/agent/agent.py`、`zenagent/agent/session.py`、`zenagent/agent/subagents.py`、`zenagent/agent/subagents_runtime.py`、`zenagent/agent/memory_store.py`、`zenagent/agent/artifacts.py`
- 关键符号：`Agent`、`_Run`、`Session`、`SessionInfo`、`SubagentStore`
- 出边：→ `agent.loop`（Agent.prompt → _drive → agent_loop（每 run 一个 task））；→ `config`（ContextGuard）；→ `events`（AbortToken）；→ `project`（all_session_dirs、legacy_sessions_dir、project_key_for_session、project_sessions_dir）；→ `providers`（—）；→ `types`（TextContent、UserMessage、message_from_json）
- 入边：`app.assembly` →（Runtime 构造 Agent、挂 ask_handler/订阅者）；`app.chat` →（Session、SubagentStore、carry_task_dir、get_task_manager）；`app.transcript` →（Session、carry_task_dir、get_task_manager、list_sessions）；`cli` →（latest_session、list_sessions、sessions_dir、split_title_body）；`cli.rpc` →（MemoryStore）；`cli.tui.app_parts` →（session_title）；`cli.tui.screens` →（—）；`export` →（Session）；`tools` →（task 工具背后 = subagents_runtime）；`usage` →（all_session_dirs、sessions_dir）
- 依赖强度：被 10 个节点 import；依赖 6 个节点
- 🔴 健康红点：1 error / 3 warn（见 health/report.md）

## 簇：应用层（`app`）

### `app.assembly` — 装配与防线

装配与防线：bootstrap.prepare（四前端唯一装配序列）、Runtime（对象槽位/工具注入）、PermissionGate/Broker 权限门、safety 风险模型、modes、error_notice 失败文案统一

- 文件：`zenagent/app/bootstrap.py`、`zenagent/app/runtime.py`、`zenagent/app/permissions.py`、`zenagent/app/safety.py`、`zenagent/app/modes.py`、`zenagent/app/support.py`、`zenagent/app/metrics.py`、`zenagent/app/probes.py`、`zenagent/app/workspace.py`、`zenagent/app/error_notice.py`、`zenagent/app/plugin_wiring.py`、`zenagent/app/retry.py`、`zenagent/app/models.py`、`zenagent/app/__init__.py`、`zenagent/agent/__init__.py`
- 关键符号：`PreparedFrontend`、`Runtime`、`RuntimeOptions`、`PermissionGate`、`PermissionBroker`
- 出边：→ `agent.loop`（ToolRegistry）；→ `agent.rt`（Runtime 构造 Agent、挂 ask_handler/订阅者）；→ `app.chat`（AppServices、ChatController、build_default_commands、git_status）；→ `app.transcript`（RewindState、SessionManager、SessionMetaRecorder、attach_journal）；→ `config`（ContextGuard、UserConfig、apply_user_config、load_user_config）；→ `context_files`（discover_context_files、load_context_files）；→ `extensions`（load_extensions）；→ `i18n`（t、t.startswith）；→ `limits`（—）；→ `mcp`（Runtime 注入 MCP 工具）；→ `plugins`（plugin_wiring 把插件贡献翻译成 MCP 配置）；→ `project`（find_project_root、project_key、project_key_for_session、write_project_meta）；→ `prompts`（compose_system 组装 system prompt）；→ `providers`（StreamOptions、estimate_text_tokens、find_model、load_catalog）；→ `settings`（disabled_skills、get_api_key）；→ `skills`（build_skill_tool、default_skill_roots、discover_skills、format_skill_entries）；→ `telemetry`（MetricsRecorder、append_metrics）；→ `templates`（default_template_roots、discover_templates）；→ `tools`（Runtime 注入 base_tools + todo/task/memory/checkpoint/lsp/github）；→ `tools.web`（SearchRequest）；→ `trace`（TraceRecorder）；→ `types`（UserMessage）
- 入边：`app.chat` →（ChatController 驱动 Runtime）；`app.transcript` →（—）；`cli` →（RuntimeOptions、api_key_for、describe_modes、prepare）；`cli.rpc` →（RuntimeOptions、list_directory、prepare、probe_mcp_server）；`cli.tui` →（ModelManager、tool_target）；`cli.tui.screens` →（—）
- 依赖强度：被 6 个节点 import；依赖 22 个节点
- 🔴 健康红点：2 error / 5 warn（见 health/report.md）

### `app.chat` — 会话交互

会话交互控制：ChatController（run 生命周期/last_error）、slash 命令注册表与补全、views UI 无关视图模型、模型切换、子代理跟踪

- 文件：`zenagent/app/chat.py`、`zenagent/app/commands.py`、`zenagent/app/views.py`、`zenagent/app/skill_commands.py`、`zenagent/app/slash_args.py`、`zenagent/app/pending.py`、`zenagent/app/btw.py`、`zenagent/app/links.py`、`zenagent/app/clipboard.py`、`zenagent/app/git.py`、`zenagent/app/appearance.py`、`zenagent/app/attachments_state.py`、`zenagent/app/subagent_tracker.py`
- 关键符号：`ChatController`、`CommandRegistry`、`CommandResult`、`AppServices`、`Command`
- 出边：→ `agent.loop`（—）；→ `agent.rt`（Session、SubagentStore、carry_task_dir、get_task_manager）；→ `app.assembly`（ChatController 驱动 Runtime）；→ `app.transcript`（build_context_report、compact、is_compaction、is_tool_end）；→ `attachments`（read_image_argument）；→ `events`（—）；→ `frontmatter`（parse_frontmatter）；→ `i18n`（t）；→ `providers`（StreamOptions、safe_stream、stream_fn_for）；→ `skills`（—）；→ `tools`（coerce_todo_phases、get_job_manager、load_todo_phases）；→ `tools.web`（default_provider、format_results、run_search）；→ `types`（Cost、SystemMessage、Usage、UserMessage）
- 入边：`app.assembly` →（AppServices、ChatController、build_default_commands、git_status）；`cli` →（TranscriptBuilder、argument_values、build_skill_submission、resolve_theme）；`cli.rpc` →（build_skill_submission、parse_skill_display、usage_total）；`cli.tui` →（AppServices、ChatController、build_default_commands、resolve_theme）；`cli.tui.app_parts` →（AppServices、build_default_commands、build_skill_submission、read_clipboard_text）；`cli.tui.screens` →（argument_values）；`cli.tui.widgets` →（—）
- 依赖强度：被 7 个节点 import；依赖 13 个节点
- 🔴 健康红点：2 error / 5 warn（见 health/report.md）

### `app.transcript` — 会话历史与恢复

会话文件与历史操作：sessions/records 词汇表、session_meta 边车、journal 写快照(/restore)、compact 摘要边界、shake 卸载、rewind

- 文件：`zenagent/app/sessions.py`、`zenagent/app/records.py`、`zenagent/app/session_meta.py`、`zenagent/app/journal.py`、`zenagent/app/compact.py`、`zenagent/app/shake.py`、`zenagent/app/rewind.py`、`zenagent/app/restore.py`、`zenagent/app/context.py`
- 关键符号：`SessionManager`、`SessionEntry`、`SessionMetaRecorder`、`SnapshotJournal`、`RunInfo`
- 出边：→ `agent.loop`（AgentTool、ToolResultAgent）；→ `agent.rt`（Session、carry_task_dir、get_task_manager、list_sessions）；→ `app.assembly`（—）；→ `events`（—）；→ `export`（export_session）；→ `i18n`（t）；→ `project`（legacy_sessions_dir、project_backups_dir、project_root_for_sessions_dir、project_sessions_dir）；→ `prompts`（compact_result、compose_compact）；→ `providers`（StreamOptions、estimate_prompt_tokens、estimate_tool_tokens、safe_stream）；→ `types`（SystemMessage、TextContent、UserMessage）
- 入边：`app.assembly` →（RewindState、SessionManager、SessionMetaRecorder、attach_journal）；`app.chat` →（build_context_report、compact、is_compaction、is_tool_end）；`cli` →（adopt_legacy_session、maybe_auto_compact）；`cli.rpc` →（is_compaction）；`cli.tui` →（SessionManager）；`cli.tui.screens` →（—）
- 依赖强度：被 6 个节点 import；依赖 10 个节点
- 🔴 健康红点：1 error / 1 warn（见 health/report.md）

## 簇：交互层（`cli`）

### `cli` — cli

交互层入口：main 参数分发、repl、render/doctor/export/usage/theme/keybindings

- 文件：`zenagent/cli/__init__.py`、`zenagent/cli/completion.py`、`zenagent/cli/doctor.py`、`zenagent/cli/editor.py`、`zenagent/cli/exportcmd.py`、`zenagent/cli/flags.py`、`zenagent/cli/keybindings.py`、`zenagent/cli/main.py`、`zenagent/cli/permissions.py`、`zenagent/cli/prompts_cmd.py`、`zenagent/cli/render.py`、`zenagent/cli/repl.py`、`zenagent/cli/sessionscmd.py`、`zenagent/cli/theme.py`、`zenagent/cli/usagecmd.py`
- 关键符号：`ReplCompleter`、`Check`、`_PlainOutput`、`AssistantLiveRenderer`、`LiveUpdate`
- 出边：→ `agent.loop`（—）；→ `agent.rt`（latest_session、list_sessions、sessions_dir、split_title_body）；→ `app.assembly`（RuntimeOptions、api_key_for、describe_modes、prepare）；→ `app.chat`（TranscriptBuilder、argument_values、build_skill_submission、resolve_theme）；→ `app.transcript`（adopt_legacy_session、maybe_auto_compact）；→ `attachments`（read_image_argument）；→ `cli.rpc`（main）；→ `cli.tui`（run_tui_session）；→ `config`（UserConfig、apply_user_config、config_path、load_user_config）；→ `crash`（install_excepthook）；→ `events`（—）；→ `export`（export_session）；→ `i18n`（resolve_language、set_locale、t）；→ `limits`（describe）；→ `project`（legacy_sessions_dir、project_config_path）；→ `prompts`（PromptContext、compose_system、read_block）；→ `providers`（load_catalog）；→ `settings`（describe、get_api_key、get_pref、set_pref）；→ `templates`（expand）；→ `tools`（—）；→ `types`（—）；→ `usage`（collect_usage、render_report、session_paths）
- 入边：`__main__` →（—）；`cli.rpc` →（add_runtime_flags）；`cli.tui.app_parts` →（argument_matches、command_matches、hint_context、skill_matches）
- 依赖强度：被 3 个节点 import；依赖 22 个节点
- 🔴 健康红点：1 error / 7 warn（见 health/report.md）

### `cli.rpc` — rpc

JSONL RPC 服务器与 ops 分派（hello 协议握手、[limits] 契约）

- 文件：`zenagent/cli/rpc/__init__.py`、`zenagent/cli/rpc/cli.py`、`zenagent/cli/rpc/codec.py`、`zenagent/cli/rpc/errors.py`、`zenagent/cli/rpc/ops_config.py`、`zenagent/cli/rpc/ops_files.py`、`zenagent/cli/rpc/ops_plugins.py`、`zenagent/cli/rpc/ops_session.py`、`zenagent/cli/rpc/server.py`
- 关键符号：`ConfigOps`、`FilesOps`、`SessionOps`、`RpcServer`
- 出边：→ `agent.rt`（MemoryStore）；→ `app.assembly`（RuntimeOptions、list_directory、prepare、probe_mcp_server）；→ `app.chat`（build_skill_submission、parse_skill_display、usage_total）；→ `app.transcript`（is_compaction）；→ `attachments`（read_image_argument）；→ `cli`（add_runtime_flags）；→ `config`（config_path、load_user_config）；→ `events`（—）；→ `i18n`（set_locale）；→ `limits`（KNOB_BY_KEY.get、coerce_value、read_managed、write_managed）；→ `mcp`（load_mcp_layers、remove_mcp_server、save_mcp_server）；→ `plugins`（ops_plugins：内置目录、发现、安装/卸载、启停与凭据）；→ `project`（find_project_root、project_config_path、project_dir、project_key）；→ `settings`（describe、disabled_skills、get_api_key、set_api_key）；→ `skills`（default_skill_roots、discover_skills）；→ `tools.web`（KEY_ENVS.items、KEY_ENVS.values、LABELS.get）；→ `types`（—）
- 入边：`cli` →（main）
- 依赖强度：被 1 个节点 import；依赖 17 个节点
- 🔴 健康红点：4 error / 6 warn（见 health/report.md）

### `cli.tui` — tui

Textual TUI 应用壳与 run

- 文件：`zenagent/cli/tui/__init__.py`、`zenagent/cli/tui/app.py`、`zenagent/cli/tui/app_theme.py`、`zenagent/cli/tui/history.py`、`zenagent/cli/tui/labels.py`、`zenagent/cli/tui/palette.py`、`zenagent/cli/tui/run.py`、`zenagent/cli/tui/status/__init__.py`、`zenagent/cli/tui/status/presets.py`、`zenagent/cli/tui/status/segments.py`、`zenagent/cli/tui/textutil.py`
- 关键符号：`ZenAgentApp`、`PromptHistory`、`_LiveMap`、`Preset`、`SegmentContext`
- 出边：→ `app.assembly`（ModelManager、tool_target）；→ `app.chat`（AppServices、ChatController、build_default_commands、resolve_theme）；→ `app.transcript`（SessionManager）；→ `cli.tui.app_parts`（—）；→ `cli.tui.screens`（AskQuestion、Confirm、RenamePrompt、ToolApproval）；→ `cli.tui.widgets`（AttachmentChips、CommandHints、ErrorBanner、NewContentBar）；→ `i18n`（resolve_language、set_locale、t、tn）；→ `settings`（get_pref）
- 入边：`cli` →（run_tui_session）；`cli.tui.app_parts` →（bind、theme_name）；`cli.tui.screens` →（STATE_COLORS.get、clip_cells、context_label、pad_cells）；`cli.tui.widgets` →（CONTEXT_COLORS.get、SECTION_ACCENTS.get、STATE_COLORS.get、clip_cells）
- 依赖强度：被 4 个节点 import；依赖 8 个节点
- 🔴 健康红点：1 error / 2 warn（见 health/report.md）

### `cli.tui.app_parts` — app_parts

TUI 主应用组成部件（display/commands/sessions/views…）

- 文件：`zenagent/cli/tui/app_parts/__init__.py`、`zenagent/cli/tui/app_parts/appearance.py`、`zenagent/cli/tui/app_parts/commands.py`、`zenagent/cli/tui/app_parts/display.py`、`zenagent/cli/tui/app_parts/drafts.py`、`zenagent/cli/tui/app_parts/hints.py`、`zenagent/cli/tui/app_parts/models.py`、`zenagent/cli/tui/app_parts/queue.py`、`zenagent/cli/tui/app_parts/sessions.py`、`zenagent/cli/tui/app_parts/update_notice.py`、`zenagent/cli/tui/app_parts/views.py`
- 关键符号：`AppearanceMixin`、`CommandsMixin`、`DisplayMixin`、`DraftsMixin`、`HintsMixin`
- 出边：→ `agent.rt`（session_title）；→ `app.chat`（AppServices、build_default_commands、build_skill_submission、read_clipboard_text）；→ `attachments`（read_image_argument）；→ `cli`（argument_matches、command_matches、hint_context、skill_matches）；→ `cli.tui`（bind、theme_name）；→ `cli.tui.screens`（HistorySearch、JobLogScreen、SecretInput）；→ `cli.tui.widgets`（—）；→ `i18n`（resolve_language、set_locale、t）；→ `project`（find_project_root、project_key、project_key_for_session）；→ `settings`（get_api_key、set_api_key、set_pref）；→ `templates`（expand）；→ `tools.web`（LABELS.get）；→ `update_check`（format_notice、installed_version、is_disabled、mark_notified）
- 入边：`cli.tui` →（—）
- 依赖强度：被 1 个节点 import；依赖 13 个节点
- 🔴 健康红点：0 error / 1 warn（见 health/report.md）

### `cli.tui.screens` — screens

TUI 全屏界面（info/todo/job_log/modals/pickers…）

- 文件：`zenagent/cli/tui/screens/appearance.py`、`zenagent/cli/tui/screens/context_breakdown.py`、`zenagent/cli/tui/screens/info.py`、`zenagent/cli/tui/screens/job_log.py`、`zenagent/cli/tui/screens/memory_manager.py`、`zenagent/cli/tui/screens/modals.py`、`zenagent/cli/tui/screens/palette.py`、`zenagent/cli/tui/screens/pickers.py`、`zenagent/cli/tui/screens/providers.py`、`zenagent/cli/tui/screens/secrets.py`、`zenagent/cli/tui/screens/todo_list.py`
- 关键符号：`ContextBreakdownScreen`、`JobLogScreen`、`MemoryManager`、`MemoryEditor`、`AskQuestion`
- 出边：→ `agent.rt`（—）；→ `app.assembly`（—）；→ `app.chat`（argument_values）；→ `app.transcript`（—）；→ `cli.tui`（STATE_COLORS.get、clip_cells、context_label、pad_cells）；→ `cli.tui.widgets`（APP_CHORDS.items）；→ `i18n`（t）；→ `mcp`（save_server）；→ `settings`（describe、set_pref）；→ `tools`（save_todo_phases、unified_diff）；→ `tools.web`（LABELS.get、available_providers、default_provider）；→ `usage`（collect_usage、render_report、session_paths）
- 入边：`cli.tui` →（AskQuestion、Confirm、RenamePrompt、ToolApproval）；`cli.tui.app_parts` →（HistorySearch、JobLogScreen、SecretInput）
- 依赖强度：被 2 个节点 import；依赖 12 个节点
- 🔴 健康红点：1 error / 1 warn（见 health/report.md）

### `cli.tui.widgets` — widgets

TUI 组件库（transcript/sidebar/tool_card…20 文件，同质 UI 部件故不拆）

- 文件：`zenagent/cli/tui/widgets/attachments.py`、`zenagent/cli/tui/widgets/command_hints.py`、`zenagent/cli/tui/widgets/error_banner.py`、`zenagent/cli/tui/widgets/markdown_stream.py`、`zenagent/cli/tui/widgets/new_content.py`、`zenagent/cli/tui/widgets/pending_band.py`、`zenagent/cli/tui/widgets/prompt_gutter.py`、`zenagent/cli/tui/widgets/prompt_input.py`、`zenagent/cli/tui/widgets/rows.py`、`zenagent/cli/tui/widgets/sidebar.py`、`zenagent/cli/tui/widgets/sidebar_resize.py`、`zenagent/cli/tui/widgets/skill_block.py`、`zenagent/cli/tui/widgets/status_band.py`、`zenagent/cli/tui/widgets/subagent_banner.py`、`zenagent/cli/tui/widgets/todo_hud.py`、`zenagent/cli/tui/widgets/tool_card.py`、`zenagent/cli/tui/widgets/transcript.py`、`zenagent/cli/tui/widgets/welcome.py`、`zenagent/cli/tui/widgets/work_indicator.py`、`zenagent/cli/tui/widgets/work_strip.py`
- 关键符号：`AttachmentChips`、`Chip`、`CommandHints`、`ErrorBanner`、`StreamingMarkdown`
- 出边：→ `app.chat`（—）；→ `cli.tui`（CONTEXT_COLORS.get、SECTION_ACCENTS.get、STATE_COLORS.get、clip_cells）；→ `i18n`（t、tn）；→ `tools`（summarize_todo）；→ `types`（—）
- 入边：`cli.tui` →（AttachmentChips、CommandHints、ErrorBanner、NewContentBar）；`cli.tui.app_parts` →（—）；`cli.tui.screens` →（APP_CHORDS.items）
- 依赖强度：被 3 个节点 import；依赖 5 个节点
- 🔴 健康红点：1 error / 2 warn（见 health/report.md）

## 簇：界面语言（`i18n`）

### `i18n` — i18n

stdlib-only i18n：扁平 dict 目录、英文目录为准、缺失回退可见

- 文件：`zenagent/i18n/__init__.py`、`zenagent/i18n/en.py`、`zenagent/i18n/zh.py`
- 入边：`app.assembly` →（t、t.startswith）；`app.chat` →（t）；`app.transcript` →（t）；`cli` →（resolve_language、set_locale、t）；`cli.rpc` →（set_locale）；`cli.tui` →（resolve_language、set_locale、t、tn）；`cli.tui.app_parts` →（resolve_language、set_locale、t）；`cli.tui.screens` →（t）；`cli.tui.widgets` →（t、tn）
- 依赖强度：被 9 个节点 import；依赖 0 个节点

## 簇：MCP 客户端（`mcp`）

### `mcp` — mcp

MCP 客户端与生命周期：Stdio/Http transport、McpManager；工具包装成 AgentTool 注入

- 文件：`zenagent/mcp/__init__.py`、`zenagent/mcp/client.py`、`zenagent/mcp/config.py`、`zenagent/mcp/redact.py`、`zenagent/mcp/manager.py`
- 关键符号：`StdioTransport`、`HttpTransport`、`McpClient`、`McpTool`、`McpTransportError`
- 出边：→ `agent.loop`（AgentTool、ToolResultAgent）；→ `http_client`（async_client）；→ `project`（project_mcp_path）；→ `types`（ImageContent、TextContent）
- 入边：`app.assembly` →（Runtime 注入 MCP 工具）；`cli.rpc` →（load_mcp_layers、remove_mcp_server、save_mcp_server）；`cli.tui.screens` →（save_server）
- 依赖强度：被 3 个节点 import；依赖 4 个节点

## 簇：提示词（`prompts`）

### `prompts` — prompts

prompt 子系统：blocks/*.md 块文件 + compose_system 唯一组装契约 + catalog

- 文件：`zenagent/prompts/__init__.py`、`zenagent/prompts/registry.py`、`zenagent/prompts/render.py`、`zenagent/prompts/spec.py`
- 关键符号：`PromptContext`、`RenderedBlock`、`RenderedPrompt`、`PromptRenderError`、`BlockSpec`
- 出边：→ `providers`（estimate_text_tokens）
- 入边：`app.assembly` →（compose_system 组装 system prompt）；`app.transcript` →（compact_result、compose_compact）；`cli` →（PromptContext、compose_system、read_block）；`context_files` →（read_block、render）；`skills` →（read_block、render、tool_description）；`tools` →（read_block、render、tool_description）；`tools.web` →（—）
- 依赖强度：被 7 个节点 import；依赖 1 个节点

## 簇：协议层（`providers`）

### `providers` — providers

协议层：StreamFn 契约、safe_stream（永不抛）、4 家适配器、transport（共享连接池/取消）+ message 级 retry、catalog/compat

- 文件：`zenagent/providers/__init__.py`、`zenagent/providers/anthropic_messages.py`、`zenagent/providers/catalog.py`、`zenagent/providers/compat.py`、`zenagent/providers/google_generative_ai.py`、`zenagent/providers/history.py`、`zenagent/providers/limits.py`、`zenagent/providers/openai_completions.py`、`zenagent/providers/openai_responses.py`、`zenagent/providers/registry.py`、`zenagent/providers/stream_options.py`、`zenagent/providers/transport.py`
- 关键符号：`_Block`、`Model`、`ProviderConfig`、`CompatFlags`、`_ToolBlock`
- 出边：→ `events`（Aborted、AssistantEvent）；→ `http_client`（async_client）；→ `types`（AssistantMessage、Cost、TextContent、ThinkingContent）
- 入边：`agent.loop` →（stream_with_retry → safe_stream → adapter.stream → transport）；`agent.rt` →（—）；`app.assembly` →（StreamOptions、estimate_text_tokens、find_model、load_catalog）；`app.chat` →（StreamOptions、safe_stream、stream_fn_for）；`app.transcript` →（StreamOptions、estimate_prompt_tokens、estimate_tool_tokens、safe_stream）；`cli` →（load_catalog）；`config` →（ProviderConfig、model_from_mapping）；`prompts` →（estimate_text_tokens）；`retry` →（safe_stream）；`tools` →（—）
- 依赖强度：被 10 个节点 import；依赖 3 个节点
- 🔴 健康红点：0 error / 5 warn（见 health/report.md）

## 簇：能力层（`tools`）

### `tools` — tools

内置工具面：read/write/edit/list_dir/bash/wait/ask/glob/grep + todo/task/memory/checkpoint/lsp/github

- 文件：`zenagent/tools/__init__.py`、`zenagent/tools/_result.py`、`zenagent/tools/ask.py`、`zenagent/tools/bash.py`、`zenagent/tools/fs.py`、`zenagent/tools/github.py`、`zenagent/tools/jobs.py`、`zenagent/tools/lsp.py`、`zenagent/tools/memory.py`、`zenagent/tools/read_state.py`、`zenagent/tools/search.py`、`zenagent/tools/shell.py`、`zenagent/tools/task.py`、`zenagent/tools/todo.py`、`zenagent/tools/wait.py`
- 关键符号：`JobManager`、`Job`、`UnknownJobError`、`JobStartError`、`LspClient`
- 出边：→ `agent.loop`（AgentTool、ToolResultAgent）；→ `agent.rt`（task 工具背后 = subagents_runtime）；→ `config`（—）；→ `events`（—）；→ `prompts`（read_block、render、tool_description）；→ `providers`（—）；→ `tools.web`（—）；→ `types`（TextContent）
- 入边：`app.assembly` →（Runtime 注入 base_tools + todo/task/memory/checkpoint/lsp/github）；`app.chat` →（coerce_todo_phases、get_job_manager、load_todo_phases）；`cli` →（—）；`cli.tui.screens` →（save_todo_phases、unified_diff）；`cli.tui.widgets` →（summarize_todo）；`tools.web` →（text_result）
- 依赖强度：被 6 个节点 import；依赖 8 个节点
- 🔴 健康红点：0 error / 9 warn（见 health/report.md）

### `tools.web` — web

网络工具：web_search 多供应商链 + fetch

- 文件：`zenagent/tools/web/__init__.py`、`zenagent/tools/web/net.py`、`zenagent/tools/web/providers.py`、`zenagent/tools/web/query.py`、`zenagent/tools/web/registry.py`、`zenagent/tools/web/tools.py`、`zenagent/tools/web/types.py`
- 关键符号：`StructuredQuery`、`QuerySyntax`、`Term`、`SearchChain`、`ChainResult`
- 出边：→ `agent.loop`（—）；→ `http_client`（async_client）；→ `prompts`（—）；→ `settings`（get_api_key、get_pref）；→ `tools`（text_result）
- 入边：`app.assembly` →（SearchRequest）；`app.chat` →（default_provider、format_results、run_search）；`cli.rpc` →（KEY_ENVS.items、KEY_ENVS.values、LABELS.get）；`cli.tui.app_parts` →（LABELS.get）；`cli.tui.screens` →（LABELS.get、available_providers、default_provider）；`tools` →（—）
- 依赖强度：被 6 个节点 import；依赖 5 个节点

