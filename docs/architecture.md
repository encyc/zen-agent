# 架构总览

## 五层，依赖只向下

```
┌─────────────────────────────────────────────────────────────┐
│ cli/        main · repl · render · permissions · rpc         │  交互层
│             doctor · export/usage · theme · keybindings      │
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

**规则**：上面只 import 下面，绝不反向。`kernel` 级的 `agent/` 不知道 CLI 存在；`tools/` 不知道
`agent/` 的循环细节；`providers/` 只认 `StreamFn` 契约。

所有「扩展面」（MCP、skills、templates、extensions、RPC、导出、指标、崩溃日志）都在 `agent/` 之上
或旁边**接线**，不修改 loop。

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
   ├─ stream_with_retry(stream_fn, …)  providers/retry.py
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
| `zenagent/providers/` | 协议适配与传输 | `StreamFn`、`safe_stream`、`stream_fn_for`、`Model`、`CompatFlags` |
| `zenagent/agent/loop.py` | 无状态执行器 | `agent_loop`、`AgentConfig`、`AgentState`、`AgentTool` |
| `zenagent/agent/agent.py` | 有状态包装 | `Agent`（subscribe/steer/abort/wait_for_idle） |
| `zenagent/agent/validate.py` | 参数 schema 校验 | `validate_arguments` |
| `zenagent/agent/session.py` | JSONL 会话持久化 | `Session`、`latest_session`、`truncate_last_turn` |
| `zenagent/tools/` | 内置工具 | `read_file`/`write_file`/`edit_file`/`list_dir`/`bash`/`glob`/`grep`/`web_search`/`fetch` |
| `zenagent/cli/` | 所有交互面 | `main`、`repl`、`PermissionGate`、`RpcServer` |
| `zenagent/mcp/` | MCP 客户端与生命周期 | `McpClient`、`StdioTransport`、`HttpTransport`、`McpManager` |
| `zenagent/skills.py` / `templates.py` | 文件即扩展 | `discover_skills`、`build_skill_tool`、`expand` |
| `zenagent/extensions.py` | Python 扩展 | `ExtensionAPI`、`load_extensions` |
| `zenagent/usage.py` / `export.py` / `telemetry.py` / `crash.py` | 可观测性 | `collect_usage`、`export_session`、`MetricsRecorder` |

## 源码对照

- 分层与依赖：`pyproject.toml` 无第三方依赖（除 httpx），可 `grep -rn "^from zenagent" zenagent | sort` 查看依赖方向
- 一次 run：`zenagent/agent/loop.py::agent_loop`
- 事件定义：`zenagent/events.py`
- 对应测试：`tests/test_loop.py`、`tests/test_agent_class.py`、`tests/test_events.py`
