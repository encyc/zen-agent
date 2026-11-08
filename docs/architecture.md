# 架构总览

## 六层，依赖只向下

```
┌─────────────────────────────────────────────────────────────┐
│ cli/        main · repl · render · rpc · tui                 │  交互层
│             doctor · export/usage · theme · keybindings      │
├─────────────────────────────────────────────────────────────┤
│ app/        runtime（装配）· chat · commands · views          │  应用层
│             permissions · safety · sessions · records        │  （UI 无关）
├─────────────────────────────────────────────────────────────┤
│ tools/      fs · bash · search（AgentTool 的具体实现）        │  能力层
├─────────────────────────────────────────────────────────────┤
│ agent/      loop（无状态执行器）· agent（外层状态机）          │  运行时
│             session · validate                               │
├─────────────────────────────────────────────────────────────┤
│ providers/  catalog · compat · transport · registry          │  协议层
│             openai_completions · anthropic · responses       │
│             google · retry（message 级重试）                  │
├─────────────────────────────────────────────────────────────┤
│ types/ events   （数据类型 + 事件类型 + AbortToken）           │  基础层
└─────────────────────────────────────────────────────────────┘
```

**规则**：上面只 import 下面，绝不反向（`tests/test_layering.py` 钉住层序，以及
「`app/` 不 import `zenagent.cli`、不 import argparse/textual/rich/prompt_toolkit」）。
`app/` 是四个前端（REPL、headless `-p`、JSONL RPC、Textual TUI）共同的装配与状态层：
`Runtime.bootstrap` 是唯一的装配入口，`ChatController`/`CommandRegistry`/`SessionManager`
是它们共享的对象。`kernel` 级的 `agent/` 不知道 CLI 存在；`tools/` 不知道
`agent/` 的循环细节；`providers/` 只认 `StreamFn` 契约。

所有「扩展面」（MCP、skills、templates、extensions、RPC、导出、指标、崩溃日志）都在 `agent/` 之上
或旁边**接线**，不修改 loop。

## 2026-11-07 架构清理（本轮优化了什么）

完整依据见 [`reviews/2026-11-07-architecture-review.md`](reviews/2026-11-07-architecture-review.md) 与
[`superpowers/plans/2026-11-07-architecture-cleanup.md`](superpowers/plans/2026-11-07-architecture-cleanup.md)。
分层与进程模型**一字未动**，优化集中在四条线：

**契约归位（改错位，不改行为）**

- `StreamOptions` 从具体适配器 `openai_completions.py` 迁到中立的 `providers/stream_options.py`
  ——此前通用 loop 和另外三个适配器都要 import 一个厂商实现。
- 工具协议（`AgentTool`/`ToolContext`/`ToolResultAgent`）从 `loop.py` 抽到 `agent/protocol.py`
  ——工具/MCP/skills/extensions 不再为了两个 dataclass 依赖整个 loop；loop 里唯一的函数内
  延迟 import 也随之消灭。
- 根包 `zenagent/__init__` 现在导出门面：`from zenagent import Agent, agent_loop` 即可用库。

**前端去重（一份逻辑，四个前端）**

- usage 聚合曾抄了 4 份（`views.usage_total` + rpc/render/main 各一），现在只此一份。
- slash 命令补全曾有一张与注册表平行的静态表（往注册表加命令，REPL 补全静默缺失），
  现在 `completion.slash_commands(registry)` 直接从活注册表派生。
- **四个前端（REPL/headless/Textual TUI/JSONL RPC）的装配仪式收敛为 `app/bootstrap.py::prepare`**。
  历史上它复制了 4 份且已漂移过一次（只有 RPC 转发了 `read_only`）；现在 broker 与 runtime
  读同一个 `RuntimeOptions`，参数化测试钉死每个模式都透传。
- `task` 工具 854 行里藏着子代理运行时（进程监管/看门狗/子 Agent 构造）——搬到了
  `agent/subagents_runtime.py`，`tools/task.py` 只剩 320 行的工具面。
- `set_ask_handler`/`set_subagent_observer` 是进程级全局变量（同进程双前端互踩）——改为
  每 Runtime 槽位（`runtime.ask_handler`），工具在调用时解析。

**GUI 防线（desktop）**

- Tauri CSP 从 `null` 改为严格策略；`rpc_start` 的登录 shell 环境采集加 3s 超时
  （坏 shell 配置不再挂死每次连接）。
- RPC 加 `hello` 协议版本握手（`PROTOCOL_VERSION = 2`），版本不符走诊断卡；
  `tests/test_rpc_contract.py` 双向钉住 Python 分派与 TS `REQUEST_KINDS`，单边改动直接红。
- `ToolCallView` 加 `kind` 判别字段，桌面端不再靠 `'kind' in view` 启发式分派。
- chatStore 流式增量从全量扫描改为尾部定位（长会话不再 O(n)/delta）。

**效率与门禁**

- 4 个 provider 适配器共用进程级 `httpx.AsyncClient`（`transport.get_shared_async_client`），
  每次流式请求不再重建 TLS 会话；`fs` 大文件读取移出事件循环。
- `scripts/check_architecture.py` + `docs/architecture/allowlist.json`：源文件 800 行上限，
  存量大文件登记基线**只许降**；挂在 CI 的 `check.sh` 与 `release-check.sh` 里。
  `crash.py` 的崩溃日志钩子在 CLI 启动接线（此前是死代码）。

## 一次请求的完整数据流

```
用户 prompt
   │
   ▼
Agent.prompt(text)                     agent/agent.py
   │  create task → _drive()
   ▼
agent_loop(prompt, state, config)      agent/loop.py
   │
   ├─ 组装 request_messages  = [system] + state.messages
   │     └─ transform_context()  （压缩、裁剪；只影响本次请求）
   │
   ├─ stream_with_retry(stream_fn, …)  retry.py（message 级重试）
   │     └─ safe_stream()              providers/registry.py
   │           └─ adapter.stream()      providers/{openai,anthropic,…}.py
   │                 └─ transport.post_stream()  httpx + 重试 + 取消
   │
   ├─ AssistantEvent 流 → 更新 partial → 发出 AgentEvent 给订阅者
   │
   ├─ 终止？ stop / length / tool_use / error / aborted / max_turns / max_cost
   │
   ├─ tool_use：校验参数 → before_tool_call 权限门 → 并行/串行执行
   │     └─ 每个结果都写回 state（顺序、id 一一对应）
   │
   └─ 下一轮；工具后检查 steering 消息并追加
   ▼
agent_end 事件
```

## 三个「为什么」

### 1. 为什么 loop 是 async generator，而 Agent 又包一层？

- `agent_loop()` **纯函数式**：输入 prompt + 可变 state，产出事件流。适合测试、嵌入、以及
  「不关心会话」的场景。
- `Agent` 负责**长期存在的状态**：消息历史、订阅者、steering 队列、abort、运行中 task。
  它把一次 run 包装成 task，让 REPL 可以在流式输出时继续接收输入（回车 steer / Ctrl+C abort）。

### 2. 为什么错误都变成事件，而不是异常？

因为一个 agent run 里**失败是常态**：网络错误、工具失败、参数校验失败、上下文溢出。
如果每种失败都抛异常，调用方要写大量 `try/except`，而且容易在中途留下「半个状态」。
所以：

- `StreamFn` 契约规定「永不抛异常」；`safe_stream` 把任何异常（包括 `Aborted`）翻译成
  terminal `AssistantEvent(type="error", reason=...)`。
- 工具执行失败变成 `ToolResultAgent(is_error=True)`；loop 继续把结果喂回模型。
- 只有「编程错误」才抛异常（例如给 `Agent` 传了非法 model）。

### 3. 为什么取消不用 `CancelledError`？

`asyncio.CancelledError` 是 `BaseException`，语义是「这个 task 正在被取消」，会穿透
`except Exception` 并影响 task 监管。而我们只需要「停止当前这次 LLM 请求」。
所以定义 `Aborted(Exception)`，由 transport 在等待/读取时抛出，`safe_stream` 把它变成
`reason="aborted"` 的终局事件；`Agent.abort()` 设置 `AbortToken` 并取消 task。

## 模块职责速览

| 模块 | 职责 | 关键符号 |
|---|---|---|
| `zenagent/types.py` | 消息/内容块/用量/成本 | `AssistantMessage`、`ToolCall`、`Usage`、`Cost` |
| `zenagent/events.py` | 两层事件 + 取消令牌 | `AssistantEvent`、`AgentEvent`、`AbortToken`、`Aborted` |
| `zenagent/providers/` | 协议适配与传输 | `StreamFn`、`safe_stream`、`stream_fn_for`、`StreamOptions`（中立模块 `stream_options.py`）、`Model`、`CompatFlags` |
| `zenagent/agent/protocol.py` | 工具协议（扩展面的唯一 import 目标） | `AgentTool`、`ToolContext`、`ToolResultAgent` |
| `zenagent/agent/loop.py` | 无状态执行器 | `agent_loop`、`AgentConfig`、`AgentState`（协议类型从 `protocol` re-export） |
| `zenagent/agent/agent.py` | 有状态包装 | `Agent`（subscribe/steer/abort/wait_for_idle） |
| `zenagent/agent/subagents_runtime.py` | 子代理运行时 | `TaskManager`、`run_child`、`construct_child`、`child_allowed`（`task` 工具只是它的表面） |
| `zenagent/agent/validate.py` | 参数 schema 校验 | `validate_arguments` |
| `zenagent/agent/session.py` | JSONL 会话持久化 | `Session`、`latest_session`、`truncate_last_turn` |
| `zenagent/tools/` | 内置工具 | `base_tools(ask_tool)` = `read_file`/`write_file`/`edit_file`/`list_dir`/`bash`/`wait`/`ask`/`glob`/`grep`/`web_search`/`fetch`（ask 每 Runtime 一份，handler 挂在 `runtime.ask_handler`）；`Runtime` 另外注入 `todo`/`task`/`memory`/`checkpoint`/`lsp`/`github`/skills/MCP |
| `zenagent/app/` | UI 无关的应用层 | `Runtime`、`ChatController`、`CommandRegistry`、`SessionManager`、`PermissionGate`/`PermissionBroker`、`RunStatus`、`records`、`bootstrap.prepare`（四前端唯一装配序列） |
| `zenagent/cli/` | 所有交互面 | `main`、`repl`、`rpc`、`tui`（`cli/permissions.py` 只是兼容 shim） |
| `zenagent/mcp/` | MCP 客户端与生命周期 | `McpClient`、`StdioTransport`、`HttpTransport`、`McpManager` |
| `zenagent/skills.py` / `templates.py` | 文件即扩展 | `discover_skills`、`build_skill_tool`、`expand` |
| `zenagent/extensions.py` | Python 扩展 | `ExtensionAPI`、`load_extensions` |
| `zenagent/usage.py` / `export.py` / `telemetry.py` / `crash.py` | 可观测性 | `collect_usage`、`export_session`、`MetricsRecorder` |

## 源码对照

- 分层与依赖：`pyproject.toml` 无第三方依赖（除 httpx），可 `grep -rn "^from zenagent" zenagent | sort` 查看依赖方向
- 一次 run：`zenagent/agent/loop.py::agent_loop`
- 事件定义：`zenagent/events.py`
- 前端装配：`zenagent/app/bootstrap.py::prepare`（四个前端唯一入口）
- 线协议：`zenagent/cli/rpc/`（权威）↔ `desktop/src/rpc/types.ts`（`REQUEST_KINDS`），契约测试 `tests/test_rpc_contract.py` 双向钉住
- 行数预算：`scripts/check_architecture.py` + `docs/architecture/allowlist.json`（只许降）
- 对应测试：`tests/test_loop.py`、`tests/test_agent_class.py`、`tests/test_events.py`
