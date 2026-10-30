# 事件全表

## AssistantEvent（provider 层）

`type` 取值：

| type | 额外字段 | 说明 |
|---|---|---|
| `start` | `partial` | 请求开始，`partial` 是空 AssistantMessage |
| `text_start` | `partial`, `content_index` | 一个新的文本块开始 |
| `text_delta` | `partial`, `content_index`, `delta` | 文本增量 |
| `text_end` | `partial`, `content_index`, `content` | 文本块结束 |
| `thinking_start` / `thinking_delta` / `thinking_end` | 同上 | 推理内容（不发给模型） |
| `toolcall_start` | `partial`, `content_index`, `tool_call` | 工具调用开始 |
| `toolcall_delta` | `partial`, `content_index`, `delta`, `tool_call` | 参数增量（原始 JSON 片段） |
| `toolcall_end` | `partial`, `content_index`, `tool_call` | 参数解析完成 |
| `done` | `partial`, `reason` | 正常结束；reason = stop/length/tool_use |
| `error` | `partial`, `reason` | 失败；reason = error/aborted |

不变量：`partial` 是**同一个活对象**，每个事件都能看到当前完整消息。

## AgentEvent（UI 层）

| type | 关键字段 | 说明 |
|---|---|---|
| `agent_start` | — | run 开始 |
| `agent_end` | `new_messages` | run 结束；本 run 追加的所有消息 |
| `turn_start` | — | 一次 provider 请求开始 |
| `turn_end` | — | 一次请求结束 |
| `message_start` | `message` | 一条消息开始（assistant 是流式） |
| `message_update` | `assistant_event`, `message` | 流式更新 |
| `message_end` | `message` | 一条消息完成（usage 可从这里读） |
| `tool_execution_start` | `tool_call_id`, `tool_name`, `args` | 工具开始 |
| `tool_execution_update` | `tool_call_id`, `partial_result` | 工具流式部分结果 |
| `tool_execution_end` | `tool_call_id`, `tool_name`, `result`, `is_error`, `duration_ms` | 工具结束 |

## 终止状态（`Agent.termination` / `AgentState.termination`）

| 值 | 含义 |
|---|---|
| `None` | 尚未运行 / 正常结束且 stop_reason 为 stop/length |
| `stop` / `length` | 模型原始结束原因（在消息里） |
| `aborted` | 用户取消 |
| `max_turns` | 达到轮次上限 |
| `max_cost` | 达到成本上限 |
| `no_stream` | 防御性：流没有终局事件 |

注意：`termination` 只记录「策略性/框架性」判断；`AssistantMessage.stop_reason` 始终是原始值。

## 订阅示例

```python
def on_event(event):
    if event.type == "message_update" and event.assistant_event.type == "text_delta":
        print(event.assistant_event.delta, end="")
    elif event.type == "tool_execution_start":
        print(f"\n[{event.tool_name}]")
    elif event.type == "tool_execution_end":
        print("error" if event.is_error else "ok")

agent.subscribe(on_event)
```

## 源码对照

- 定义：`zenagent/events.py`
- 发射位置：`zenagent/agent/loop.py`（turn/message/tool）、`zenagent/agent/agent.py`（agent_start/end）
- 测试：`tests/test_events.py`、`tests/test_loop.py`
