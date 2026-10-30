# 类型与事件

## 消息模型（`zenagent/types.py`）

```
Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage
```

内容块（只在 user/assistant/tool result 的 `content` 里出现）：

| 块 | 用途 |
|---|---|
| `TextContent(text)` | 文本 |
| `ThinkingContent(thinking)` | 推理/思考内容（不发给模型，只用于展示与保存） |
| `ImageContent(data, mime_type)` | base64 图片 |
| `ToolCall(id, name, arguments, arguments_error)` | 模型请求的工具调用 |

`AssistantMessage` 携带 `provider/model/usage/stop_reason/error_message/response_id`。
`stop_reason` 是 provider 的原始语义（`stop/length/tool_use/error/aborted`），
**策略性终止不会改写它**（见 [契约](../reference/contracts.md#4-stopreason-不被护栏改写)）。

`Usage` / `Cost` 按 input/output/cache_read/cache_write 分项，cost 是保守上界
（DeepSeek 按峰时价，没有时间维度）。

序列化：`message_to_json` / `message_from_json`（`types.py`）。会话文件就是逐行这些 JSON。
解析对未知字段宽容（旧会话可读），缺字段有默认。

## 两层事件

### `AssistantEvent`：provider 层

一次流式请求的细粒度事件，生命周期固定：

```
start → (text|thinking|toolcall)_(start|delta|end)* → done | error
```

关键设计：**每个事件都带 `partial`——同一个 `AssistantMessage` 对象的引用**。
所以订阅者看到的是「正在生长的消息」，不是快照。要保存历史必须拷贝（会话层用 JSON 序列化，
天然是拷贝）。

`content_index` 指向 `partial.content` 的下标，`delta` 是本段增量。

### `AgentEvent`：UI 层

loop 把 `AssistantEvent` 包装成更粗粒度的事件：

| 事件 | 时机 |
|---|---|
| `agent_start` / `agent_end` | 一次 run 的边界（`agent_end.new_messages` 是本轮新增） |
| `turn_start` / `turn_end` | 一次 provider 请求 |
| `message_start` / `message_update` / `message_end` | 一条消息的生命周期（assistant 为流式） |
| `tool_execution_start/update/end` | 一次工具执行（update 给流式输出） |

`message_update` 里同时带 `assistant_event` 和 `message=event.partial`。

## 取消：AbortToken / Aborted

```python
class AbortToken:
    def abort(self) -> None: ...
    @property
    def aborted(self) -> bool: ...
    async def wait(self) -> None: ...

class Aborted(Exception): ...
```

- loop、adapter、transport、工具共享同一个 token。
- 等待 HTTP/读字节的地方用「事件 + wait」组合，token 触发立即返回。
- transport 抛 `Aborted`；`safe_stream` 捕获后生成 `error(reason="aborted")`；
  loop 记录 `termination="aborted"`，session 里不留半条消息。
- **不要**把 `Aborted` 当 `CancelledError` 处理，也不要在工具里吞掉它。

## 常见陷阱

1. **不要把 `partial` 存起来**：它是活对象，后续 delta 会继续修改它。要保存就 `message_to_json`。
2. **不要假设 `content_index` 连续**：并行工具调用时 model 可能先发第 2 个 call 的 start。
3. **不要依赖 `message_end` 一定在 `turn_end` 之前**：以实际事件顺序为准。
4. **thinking 不进请求**：重放历史时 `ThinkingContent` 会被序列化保存，但适配器发请求时会忽略它们
   （Anthropic 需要重新构造 thinking block 的场景除外，见 providers 文档）。

## 源码对照

- 类型：`zenagent/types.py`（`TextContent`、`ToolCall`、`AssistantMessage`、`message_to_json`）
- 事件与取消：`zenagent/events.py`（`AssistantEvent`、`AgentEvent`、`AbortToken`、`Aborted`）
- 测试：`tests/test_types.py`、`tests/test_events.py`
