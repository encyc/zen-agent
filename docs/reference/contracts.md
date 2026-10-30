# 核心契约速查

这些契约是整套代码的「宪法」。改任何模块前先确认没有破坏它们。

## 1. StreamFn 永不抛异常

```python
StreamFn = Callable[[Model, list[Message], list[ToolDef], StreamOptions],
                    AsyncIterator[AssistantEvent]]
```

- 适配器内部任何错误（网络、解析、认证）都必须变成 `AssistantEvent(type="error", reason=...)`。
- 由 `safe_stream()`（`providers/registry.py`）兜底：它捕获所有 `Exception`（含 `Aborted`），
  生成终局事件；**唯一例外**是 `asyncio.CancelledError` 会继续向上传播（task 取消是外部语义）。
- 违反后果：loop 里会出现未捕获异常，整个 run 变成 Python 异常而不是 `agent_end`。

## 2. 取消是协议内状态

- `AbortToken`：`abort()` / `aborted` / `await wait()`。
- transport 在等待响应或读取字节时检查 token，抛出 `Aborted(Exception)`。
- `safe_stream` 把它翻译成 `error(reason="aborted")`。
- 不用 `asyncio.CancelledError` 的原因见 [架构总览](../architecture.md#3-为什么取消不用-cancellederror)。

## 3. 工具永远返回结果

`AgentTool.execute(tool_call_id, args, abort, on_update)` 的返回类型是 `ToolResultAgent`：

- 参数 JSON 解析失败 → `is_error=True`，内容说明原因；
- schema 校验失败 → `is_error=True`，错误信息里带路径；
- 工具自身抛异常 → loop 捕获并转成 `is_error=True`（截断到 500 字符）；
- 被权限门 veto → `is_error=True`，内容为拒绝原因，`execute` 不会被调用；
- 返回的 `content` 是**唯一**发给模型的内容；`details` 只给 UI/日志。

所以 loop 里没有任何 `try/except` 包裹工具执行——这是有意为之。

## 4. StopReason 不被护栏改写

`StopReason = Literal["stop","length","tool_use","error","aborted"]`（`types.py`）。
`max_turns` / `max_cost` / 上下文压缩等「策略性终止」**不修改** assistant 消息的 `stop_reason`，
而是记录在 `AgentState.termination` / `Agent.termination`：

- `"max_turns"`、`"max_cost"`、`None`（正常结束）。
- 好处：历史消息保持 provider 的原始语义，`--resume` 后仍可重放/审计。

## 5. 消息顺序与配对

- 每个 `ToolCall.id` 必须有且只有一个 `ToolResultMessage.tool_call_id == id` 对应。
- 即使 `stop_reason="length"` 截断了工具调用，loop 也会**为每个 call 合成一条错误结果**
  再结束这一轮，绝不留下悬空 call（否则下一次请求会被 provider 拒绝）。
- 并行执行时结果仍按请求顺序写回。

## 6. 会话文件只追加，压缩写新文件

- `Session` 只追加 JSONL 行；`/undo` 用 `truncate_last_turn` 重写文件但保留旧内容到新文件？
  —— 实际做法见 [持久化](../internals/persistence.md)：不可逆操作（压缩、`/undo`、`/fork`）
  总是**写新 session 文件**，旧文件字节不变。
- `--resume` 只是读取；不修改原文件。

## 7. 配置与密钥

- 密钥只从环境变量读取，名字由 catalog 的 `api_key_env` 决定。
- `doctor --report`、metrics、crash log **绝不写密钥或 prompt 原文**。
- config.toml 可以覆盖 base_url/模型，但**不能内联密钥**。

## 8. MCP 工具与权限

- MCP 工具名空间 `mcp__<server>__<tool>`，不与内置工具冲突（内置优先）。
- MCP 工具同样经过 `PermissionGate`，`--read-only` 下全部拒绝。
- 启动失败**不是致命错误**：server 保持 `failed` 状态，后续可重试（见 [MCP](../guide/mcp.md)）。

## 9. 扩展隔离

- 一个扩展模块 import 失败 / `register()` 抛异常 → 只 warning，不影响其他扩展和会话。
- 扩展命令与内置斜杠命令重名时，**内置优先**；扩展之间重名保留第一个。
- 扩展 hook 异常被捕获并记录，不会中断 run。

## 10. RPC 协议

- stdout **只写协议 JSON**，一行一个对象；日志走 stderr。
- 每个请求都有 `id`；未知 type / 坏 JSON 只返回 error，不退出进程。
- `shutdown` 会等待运行中的 run 被 abort 并回收。
