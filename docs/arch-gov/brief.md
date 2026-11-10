# zen-agent 架构简报（Agent 首读）

> 由 manifest.json 单源生成（`scaffold.py render`），勿手改。manifest v3 · 2026-11-10 · 41 节点 / 190 边。讨论架构：本文件 + nodes.md + deps.json 三件套为准。

## 1. 项目定位
zen-agent 是零第三方依赖（仅 httpx）的终端 AI 编码代理。四个前端（REPL / headless -p / JSONL RPC / Textual TUI）共用 app 层唯一装配序列 bootstrap.prepare；agent 内核以「事件流 + 工具永远返回结果 + 取消是协议内状态」为契约；providers 适配多家 LLM；插件（声明式连接器包，经 MCP 单向外呼）与 MCP/skills/模板/扩展/桌面 GUI(经 RPC) 都在内核之上接线不改内核。

## 2. 结构总览
### 簇 基础层（`base`）
- `__main__` **__main__.py** — python -m zenagent 入口，转发 cli.main（1 文件，被 0 节点依赖）
- `attachments` **attachments.py** — 图片附件读取：magic bytes 定 MIME、本地校验后构造 ImageContent（1 文件，被 4 节点依赖）
- `config` **config.py** — config.toml 读写与默认配置（~/.zenagent/config.toml）（1 文件，被 5 节点依赖）
- `context_files` **context_files.py** — AGENTS.md 等项目指令文件发现，注入 system prompt（--no-context-files 可关）（1 文件，被 1 节点依赖）
- `crash` **crash.py** — 崩溃日志钩子：未捕获异常落盘（1 文件，被 1 节点依赖）
- `evals_runner` **evals_runner.py** — 行为评测纯函数部分（报告读写/基线对比）；runner 在 scripts/eval.py（1 文件，被 0 节点依赖）
- `events` **events.py** — 两层事件体系 + AbortToken/Aborted：取消是协议内正常状态（1 文件，被 11 节点依赖）
- `export` **export.py** — 会话导出（1 文件，被 2 节点依赖）
- `extensions` **extensions.py** — Python 扩展加载与 ExtensionAPI（1 文件，被 1 节点依赖）
- `frontmatter` **frontmatter.py** — 极简 frontmatter 解析（skills/模板共用，免 YAML 依赖）（1 文件，被 3 节点依赖）
- `http_client` **http_client.py** — 进程级共享 httpx.AsyncClient 构造（抗敌意 shell 环境）（1 文件，被 4 节点依赖）
- `limits` **limits.py** — [limits]/[task] 预算唯一注册表与外科手术式写盘（write_managed 唯一写路径）（1 文件，被 4 节点依赖）
- `plugins` **plugins** — 声明式能力包：manifest 校验、发现、安装/卸载、启停与凭据、贡献解析（6 文件，被 2 节点依赖）
- `project` **project.py** — 项目身份与目录布局：<root>/.zenagent/ 随仓库 vs ~/.zenagent/projects/ 本机状态（1 文件，被 11 节点依赖）
- `retry` **retry.py** — 通用重试 helper（与 providers/message 级重试无关）（1 文件，被 1 节点依赖）
- `settings` **settings.py** — settings.json(0600)：API keys 与 UI 偏好，优先级高于环境变量（1 文件，被 8 节点依赖）
- `skills` **skills.py** — 文件即扩展：SKILL.md 发现 + skill 工具构造（1 文件，被 3 节点依赖）
- `telemetry` **telemetry.py** — （职责待 enrich）（1 文件，被 1 节点依赖）
- `templates` **templates.py** — prompt 模板展开（expand）（1 文件，被 3 节点依赖）
- `trace` **trace.py** — append-only JSONL 执行 trace：工具/参数/结果/耗时（1 文件，被 1 节点依赖）
- `types` **types.py** — 全部数据类型：消息/内容块/用量/成本（全图 fan-in 最高）（1 文件，被 20 节点依赖）
- `update_check` **update_check.py** — TUI 专用 PyPI 版本检查（后台、静默失败）（1 文件，被 1 节点依赖）
- `usage` **usage.py** — 用量聚合 collect_usage（四前端唯一一份）（1 文件，被 2 节点依赖）

### 簇 运行时内核（`agent`）
- `agent.loop` **无状态执行内核** — 无状态执行器：agent_loop 组装请求、流式调 provider、终止判定；tool_exec 工具调度（权限门→校验→并行/串行）与结果回写；result_budget/timeouts/guard 循环护栏。已知常量回指环在本节点内部（cycles_seen）（8 文件，被 11 节点依赖）
- `agent.rt` **有状态运行时** — 有状态运行时：Agent（历史/订阅者/steering/abort）、Session JSONL 持久化、子代理运行时（task 工具的背后）、memory_store/artifacts（6 文件，被 11 节点依赖）

### 簇 应用层（`app`）
- `app.assembly` **装配与防线** — 装配与防线：bootstrap.prepare（四前端唯一装配序列）、Runtime（对象槽位/工具注入）、PermissionGate/Broker 权限门、safety 风险模型、modes、error_notice 失败文案统一（15 文件，被 7 节点依赖）
- `app.chat` **会话交互** — 会话交互控制：ChatController（run 生命周期/last_error）、slash 命令注册表与补全、views UI 无关视图模型、模型切换、子代理跟踪（13 文件，被 8 节点依赖）
- `app.taiji` **taiji** — Taiji 编排面：Router 会话(taiji.jsonl)+锁、TaijiSupervisor、5 个 worker 工具；workers = `zen rpc --cwd worktree --yes` 子进程（11 文件，被 2 节点依赖）
- `app.transcript` **会话历史与恢复** — 会话文件与历史操作：sessions/records 词汇表、session_meta 边车、journal 写快照(/restore)、compact 摘要边界、shake 卸载、rewind（9 文件，被 6 节点依赖）

### 簇 交互层（`cli`）
- `cli` **cli** — 交互层入口：main 参数分发、repl、render/doctor/export/usage/theme/keybindings（15 文件，被 3 节点依赖）
- `cli.rpc` **rpc** — JSONL RPC 服务器与 ops 分派（hello 协议握手、[limits] 契约）（10 文件，被 1 节点依赖）
- `cli.tui` **tui** — Textual TUI 应用壳与 run（11 文件，被 4 节点依赖）
- `cli.tui.app_parts` **app_parts** — TUI 主应用组成部件（display/commands/sessions/views…）（11 文件，被 1 节点依赖）
- `cli.tui.screens` **screens** — TUI 全屏界面（info/todo/job_log/modals/pickers…）（11 文件，被 2 节点依赖）
- `cli.tui.widgets` **widgets** — TUI 组件库（transcript/sidebar/tool_card…20 文件，同质 UI 部件故不拆）（20 文件，被 3 节点依赖）

### 簇 界面语言（`i18n`）
- `i18n` **i18n** — stdlib-only i18n：扁平 dict 目录、英文目录为准、缺失回退可见（3 文件，被 9 节点依赖）

### 簇 MCP 客户端（`mcp`）
- `mcp` **mcp** — MCP 客户端与生命周期：Stdio/Http transport、McpManager；工具包装成 AgentTool 注入（5 文件，被 3 节点依赖）

### 簇 提示词（`prompts`）
- `prompts` **prompts** — prompt 子系统：blocks/*.md 块文件 + compose_system 唯一组装契约 + catalog（4 文件，被 8 节点依赖）

### 簇 协议层（`providers`）
- `providers` **providers** — 协议层：StreamFn 契约、safe_stream（永不抛）、4 家适配器、transport（共享连接池/取消）+ message 级 retry、catalog/compat（12 文件，被 10 节点依赖）

### 簇 能力层（`tools`）
- `tools` **tools** — 内置工具面：read/write/edit/list_dir/bash/wait/ask/glob/grep + todo/task/memory/checkpoint/lsp/github（15 文件，被 6 节点依赖）
- `tools.web` **web** — 网络工具：web_search 多供应商链 + fetch（7 文件，被 6 节点依赖）

**簇间依赖**：`agent` → `base`（7 条边）；`agent` → `providers`（2 条边）；`app` → `agent`（8 条边）；`app` → `base`（23 条边）；`app` → `i18n`（3 条边）；`app` → `mcp`（1 条边）；`app` → `prompts`（3 条边）；`app` → `providers`（3 条边）；`app` → `tools`（4 条边）；`base` → `agent`（4 条边）；`base` → `cli`（1 条边）；`base` → `prompts`（2 条边）；`base` → `providers`（2 条边）；`cli` → `agent`（5 条边）；`cli` → `app`（15 条边）；`cli` → `base`（29 条边）；`cli` → `i18n`（6 条边）；`cli` → `mcp`（2 条边）；`cli` → `prompts`（1 条边）；`cli` → `providers`（1 条边）；`cli` → `tools`（6 条边）；`mcp` → `agent`（1 条边）；`mcp` → `base`（3 条边）；`prompts` → `providers`（1 条边）；`providers` → `base`（3 条边）；`tools` → `agent`（3 条边）；`tools` → `base`（5 条边）；`tools` → `prompts`（2 条边）；`tools` → `providers`（1 条边）

## 3. 边表
| from | to | 语义 |
|---|---|---|
| `__main__` | `cli` | — |
| `agent.loop` | `events` | AgentEvent |
| `agent.loop` | `providers` | stream_with_retry → safe_stream → adapter.stream → transport |
| `agent.loop` | `retry` | stream_with_retry |
| `agent.loop` | `types` | SystemMessage、TextContent、ToolDef、ToolResultMessage |
| `agent.rt` | `agent.loop` | Agent.prompt → _drive → agent_loop（每 run 一个 task） |
| `agent.rt` | `config` | ContextGuard |
| `agent.rt` | `events` | AbortToken |
| `agent.rt` | `project` | all_session_dirs、legacy_sessions_dir、project_key_for_session、project_sessions_dir |
| `agent.rt` | `providers` | — |
| `agent.rt` | `types` | TextContent、UserMessage、message_from_json |
| `app.assembly` | `agent.loop` | ToolRegistry |
| `app.assembly` | `agent.rt` | Runtime 构造 Agent、挂 ask_handler/订阅者 |
| `app.assembly` | `app.chat` | AppServices、ChatController、build_default_commands、git_status |
| `app.assembly` | `app.taiji` | is_taiji_session、router_tool_gate、runtime_taiji_provider、taiji_tools_for |
| `app.assembly` | `app.transcript` | RewindState、SessionManager、SessionMetaRecorder、attach_journal |
| `app.assembly` | `config` | ContextGuard、UserConfig、apply_user_config、load_user_config |
| `app.assembly` | `context_files` | discover_context_files、load_context_files |
| `app.assembly` | `extensions` | load_extensions |
| `app.assembly` | `i18n` | t、t.startswith |
| `app.assembly` | `limits` | — |
| `app.assembly` | `mcp` | Runtime 注入 MCP 工具 |
| `app.assembly` | `plugins` | plugin_wiring 把插件贡献翻译成 MCP 配置 |
| `app.assembly` | `project` | find_project_root、project_key、project_key_for_session、write_project_meta |
| `app.assembly` | `prompts` | compose_system 组装 system prompt |
| `app.assembly` | `providers` | StreamOptions、estimate_text_tokens、find_model、load_catalog |
| `app.assembly` | `settings` | disabled_skills、get_api_key |
| `app.assembly` | `skills` | build_skill_tool、default_skill_roots、discover_skills、format_skill_entries |
| `app.assembly` | `telemetry` | MetricsRecorder、append_metrics |
| `app.assembly` | `templates` | default_template_roots、discover_templates |
| `app.assembly` | `tools` | Runtime 注入 base_tools + todo/task/memory/checkpoint/lsp/github |
| `app.assembly` | `tools.web` | SearchRequest |
| `app.assembly` | `trace` | TraceRecorder |
| `app.assembly` | `types` | UserMessage |
| `app.chat` | `agent.loop` | — |
| `app.chat` | `agent.rt` | Session、SubagentStore、carry_task_dir、get_task_manager |
| `app.chat` | `app.assembly` | ChatController 驱动 Runtime |
| `app.chat` | `app.transcript` | build_context_report、compact、is_compaction、is_tool_end |
| `app.chat` | `attachments` | read_image_argument |
| `app.chat` | `events` | — |
| `app.chat` | `frontmatter` | parse_frontmatter |
| `app.chat` | `i18n` | t |
| `app.chat` | `providers` | StreamOptions、safe_stream、stream_fn_for |
| `app.chat` | `skills` | — |
| `app.chat` | `tools` | coerce_todo_phases、get_job_manager、load_todo_phases |
| `app.chat` | `tools.web` | default_provider、format_results、run_search |
| `app.chat` | `types` | Cost、SystemMessage、Usage、UserMessage |
| `app.taiji` | `agent.loop` | AgentTool、ToolResultAgent |
| `app.taiji` | `agent.rt` | set_session_title |
| `app.taiji` | `app.assembly` | worktree_overview |
| `app.taiji` | `app.chat` | git_status |
| `app.taiji` | `project` | find_project_root、project_dir、project_key、project_sessions_dir |
| `app.taiji` | `prompts` | tool_description |
| `app.taiji` | `types` | TextContent |
| `app.transcript` | `agent.loop` | AgentTool、ToolResultAgent |
| `app.transcript` | `agent.rt` | Session、carry_task_dir、get_task_manager、list_sessions |
| `app.transcript` | `app.assembly` | — |
| `app.transcript` | `events` | — |
| `app.transcript` | `export` | export_session |
| `app.transcript` | `i18n` | t |
| `app.transcript` | `project` | legacy_sessions_dir、project_backups_dir、project_root_for_sessions_dir、project_sessions_dir |
| `app.transcript` | `prompts` | compact_result、compose_compact |
| `app.transcript` | `providers` | StreamOptions、estimate_prompt_tokens、estimate_tool_tokens、safe_stream |
| `app.transcript` | `types` | SystemMessage、TextContent、UserMessage |
| `attachments` | `types` | ImageContent |
| `cli` | `agent.loop` | — |
| `cli` | `agent.rt` | latest_session、list_sessions、sessions_dir、split_title_body |
| `cli` | `app.assembly` | RuntimeOptions、api_key_for、describe_modes、prepare |
| `cli` | `app.chat` | TranscriptBuilder、argument_values、build_skill_submission、resolve_theme |
| `cli` | `app.transcript` | adopt_legacy_session、maybe_auto_compact |
| `cli` | `attachments` | read_image_argument |
| `cli` | `cli.rpc` | main |
| `cli` | `cli.tui` | run_tui_session |
| `cli` | `config` | UserConfig、apply_user_config、config_path、load_user_config |
| `cli` | `crash` | install_excepthook |
| `cli` | `events` | — |
| `cli` | `export` | export_session |
| `cli` | `i18n` | resolve_language、set_locale、t |
| `cli` | `limits` | describe |
| `cli` | `project` | legacy_sessions_dir、project_config_path |
| `cli` | `prompts` | PromptContext、compose_system、read_block |
| `cli` | `providers` | load_catalog |
| `cli` | `settings` | describe、get_api_key、get_pref、set_pref |
| `cli` | `templates` | expand |
| `cli` | `tools` | — |
| `cli` | `types` | — |
| `cli` | `usage` | collect_usage、render_report、session_paths |
| `cli.rpc` | `agent.rt` | MemoryStore |
| `cli.rpc` | `app.assembly` | RuntimeOptions、list_directory、prepare、probe_mcp_server |
| `cli.rpc` | `app.chat` | build_skill_submission、parse_skill_display、usage_total |
| `cli.rpc` | `app.taiji` | close_taiji、ensure_taiji_session、existing_taiji、own_taiji_lock |
| `cli.rpc` | `app.transcript` | is_compaction |
| `cli.rpc` | `attachments` | read_image_argument |
| `cli.rpc` | `cli` | add_runtime_flags |
| `cli.rpc` | `config` | config_path、load_user_config |
| `cli.rpc` | `events` | — |
| `cli.rpc` | `i18n` | set_locale |
| `cli.rpc` | `limits` | KNOB_BY_KEY.get、coerce_value、read_managed、write_managed |
| `cli.rpc` | `mcp` | load_mcp_layers、remove_mcp_server、save_mcp_server |
| `cli.rpc` | `plugins` | ops_plugins：内置目录、发现、安装/卸载、启停与凭据 |
| `cli.rpc` | `project` | find_project_root、project_config_path、project_dir、project_key |
| `cli.rpc` | `settings` | describe、disabled_skills、get_api_key、set_api_key |
| `cli.rpc` | `skills` | default_skill_roots、discover_skills |
| `cli.rpc` | `tools.web` | KEY_ENVS.items、KEY_ENVS.values、LABELS.get |
| `cli.rpc` | `types` | — |
| `cli.tui` | `app.assembly` | ModelManager、tool_target |
| `cli.tui` | `app.chat` | AppServices、ChatController、build_default_commands、resolve_theme |
| `cli.tui` | `app.transcript` | SessionManager |
| `cli.tui` | `cli.tui.app_parts` | — |
| `cli.tui` | `cli.tui.screens` | AskQuestion、Confirm、RenamePrompt、ToolApproval |
| `cli.tui` | `cli.tui.widgets` | AttachmentChips、CommandHints、ErrorBanner、NewContentBar |
| `cli.tui` | `i18n` | resolve_language、set_locale、t、tn |
| `cli.tui` | `settings` | get_pref |
| `cli.tui.app_parts` | `agent.rt` | session_title |
| `cli.tui.app_parts` | `app.chat` | AppServices、build_default_commands、build_skill_submission、read_clipboard_text |
| `cli.tui.app_parts` | `attachments` | read_image_argument |
| `cli.tui.app_parts` | `cli` | argument_matches、command_matches、hint_context、skill_matches |
| `cli.tui.app_parts` | `cli.tui` | bind、theme_name |
| `cli.tui.app_parts` | `cli.tui.screens` | HistorySearch、JobLogScreen、SecretInput |
| `cli.tui.app_parts` | `cli.tui.widgets` | — |
| `cli.tui.app_parts` | `i18n` | resolve_language、set_locale、t |
| `cli.tui.app_parts` | `project` | find_project_root、project_key、project_key_for_session |
| `cli.tui.app_parts` | `settings` | get_api_key、set_api_key、set_pref |
| `cli.tui.app_parts` | `templates` | expand |
| `cli.tui.app_parts` | `tools.web` | LABELS.get |
| `cli.tui.app_parts` | `update_check` | format_notice、installed_version、is_disabled、mark_notified |
| `cli.tui.screens` | `agent.rt` | — |
| `cli.tui.screens` | `app.assembly` | — |
| `cli.tui.screens` | `app.chat` | argument_values |
| `cli.tui.screens` | `app.transcript` | — |
| `cli.tui.screens` | `cli.tui` | STATE_COLORS.get、clip_cells、context_label、pad_cells |
| `cli.tui.screens` | `cli.tui.widgets` | APP_CHORDS.items |
| `cli.tui.screens` | `i18n` | t |
| `cli.tui.screens` | `mcp` | save_server |
| `cli.tui.screens` | `settings` | describe、set_pref |
| `cli.tui.screens` | `tools` | save_todo_phases、unified_diff |
| `cli.tui.screens` | `tools.web` | LABELS.get、available_providers、default_provider |
| `cli.tui.screens` | `usage` | collect_usage、render_report、session_paths |
| `cli.tui.widgets` | `app.chat` | — |
| `cli.tui.widgets` | `cli.tui` | CONTEXT_COLORS.get、SECTION_ACCENTS.get、STATE_COLORS.get、clip_cells |
| `cli.tui.widgets` | `i18n` | t、tn |
| `cli.tui.widgets` | `tools` | summarize_todo |
| `cli.tui.widgets` | `types` | — |
| `config` | `limits` | parse_limits |
| `config` | `project` | project_config_path |
| `config` | `providers` | ProviderConfig、model_from_mapping |
| `config` | `types` | — |
| `context_files` | `prompts` | read_block、render |
| `events` | `types` | — |
| `export` | `agent.rt` | Session |
| `export` | `types` | — |
| `extensions` | `agent.loop` | — |
| `mcp` | `agent.loop` | AgentTool、ToolResultAgent |
| `mcp` | `http_client` | async_client |
| `mcp` | `project` | project_mcp_path |
| `mcp` | `types` | ImageContent、TextContent |
| `plugins` | `settings` | state 读写 settings.api_keys 中的 plugin:<id>:<key> |
| `prompts` | `providers` | estimate_text_tokens |
| `providers` | `events` | Aborted、AssistantEvent |
| `providers` | `http_client` | async_client |
| `providers` | `types` | AssistantMessage、Cost、TextContent、ThinkingContent |
| `retry` | `events` | — |
| `retry` | `providers` | safe_stream |
| `retry` | `types` | — |
| `skills` | `agent.loop` | AgentTool、ToolResultAgent |
| `skills` | `frontmatter` | parse_frontmatter |
| `skills` | `project` | find_project_root |
| `skills` | `prompts` | read_block、render、tool_description |
| `skills` | `types` | TextContent |
| `telemetry` | `events` | — |
| `telemetry` | `types` | Usage |
| `templates` | `frontmatter` | parse_frontmatter |
| `templates` | `project` | find_project_root |
| `tools` | `agent.loop` | AgentTool、ToolResultAgent |
| `tools` | `agent.rt` | task 工具背后 = subagents_runtime |
| `tools` | `config` | — |
| `tools` | `events` | — |
| `tools` | `prompts` | read_block、render、tool_description |
| `tools` | `providers` | — |
| `tools` | `tools.web` | — |
| `tools` | `types` | TextContent |
| `tools.web` | `agent.loop` | — |
| `tools.web` | `http_client` | async_client |
| `tools.web` | `prompts` | — |
| `tools.web` | `settings` | get_api_key、get_pref |
| `tools.web` | `tools` | text_result |
| `trace` | `events` | — |
| `update_check` | `http_client` | async_client |
| `usage` | `agent.rt` | all_session_dirs、sessions_dir |
| `usage` | `types` | message_from_json |

## 4. 不变量
- 六层依赖只向下：cli → app → tools → agent → providers → types/events；tests/test_layering.py 钉住；app/ 不 import cli 与 argparse/textual/rich/prompt_toolkit
- StreamFn 永不抛异常：一切失败翻译成 terminal error 事件（safe_stream）
- 工具永远返回结果：失败 = is_error 结果回喂模型，loop 内没有包裹工具执行的 try/except
- 取消是协议内正常状态：Aborted(Exception) 而非 asyncio.CancelledError，AbortToken 贯穿 transport
- bootstrap.prepare 是四个前端唯一装配序列；broker 与 runtime 读同一 RuntimeOptions（参数化测试钉死）
- 已知常量回指环 loop↔tool_exec↔result_budget↔timeouts 在 agent.loop 节点内部，cycles_seen 登记
- 插件只做单向外呼：zen → 插件 MCP 进程 → 外部应用；plugins/ 不 import mcp/app/cli/agent/tools/providers（tests/test_plugin_layering.py 钉住），只有 app/plugin_wiring.py 把插件贡献翻译成 MCP 配置

## 5. 健康红点
最近 gov_health（2026-11-10）：违规 112 项，新增 error 0，恶化 0，基线 112 项。
红点节点：`(unmapped)`(8E/27W)、`cli.rpc`(3E/9W)、`tools`(0E/9W)、`cli`(1E/7W)、`app.chat`(2E/5W)、`(cycles)`(7E/0W)、`app.assembly`(2E/4W)、`providers`(0E/5W)

## 6. 最近治理（deltas.md 最后一条）
## [v3] 2026-11-10 — 插件系统接入
  - 增：节点 `plugins`（6 文件，base 簇）与符号 `PluginManifest` / `ResolvedContributions`
  - 增：边 `app.assembly → plugins`（plugin_wiring 把贡献翻译成 MCP 配置）、`cli.rpc → plugins`（ops_plugins）、`plugins → settings`（凭据，hidden）
  - 改：`app.assembly` 并入 `plugin_wiring.py`；`cli.rpc` 并入 `ops_plugins.py`、`errors.py`；`mcp` 并入 `redact.py`
  - 改：app 包环因 `plugin_wiring ↔ runtime`（TYPE_CHECKING 导入）扩为 11 模块；新增 facade 环 `plugins ↔ plugins.install`（同 `tools ↔ tools.fs` 模式），二者登记 `cycles_seen`
  - 改：不变量 +1（插件单向外呼、`plugins/` 禁 import 上层、只有 `app/plugin_wiring.py` 桥接）
  - 熵增：refresh deps 暴露 2026-11-09 基线之后的存量漂移（`ops_config.py` 854→941、`server.py` 744→792 等 10 项，非本分支引入），随本次 `--update-baseline` 按当前值重登记；基线 110→111（+plugins 环、+test_plugin_layering 孤儿，-scripts.import_graph 孤儿）
  - 合并 `main@202d6f9`（无缝切换/懒加载会话）：`agent/session.py` 767 行使 `Session` 越过上帝类阈值、runtime/server/test 行数增长；均为 main 侧改动，按当前值一并重登记（基线 111→112），后续如需降熵另开任务拆 `Session`
