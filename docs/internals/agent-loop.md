# Agent Loop：无状态执行器

`agent_loop(prompt, state, config)` 是一个 async generator，是整套系统的核心。
它只做一件事：**把「模型请求 → 工具轮 → 下一轮」跑完，并报告发生了什么**。

## 签名与状态

```python
async def agent_loop(prompt: str | None, state: AgentState, config: AgentConfig) -> AsyncIterator[AgentEvent]

class AgentState:
    messages: list[Message]
    termination: str | None   # max_turns / max_cost / aborted / ...
```

- `state.messages` 是唯一可变状态；`system_prompt` 不存进去，每次请求时临时拼在最前面。
- `config` 是本次 run 的不可变配置（model/prompt/tools/hooks/上限/重试）。
- `Agent` 每次都传入**新的** `AgentConfig`，所以在 REPL 里改 model/thinking 下一轮就生效。

## 一轮的完整流程

```
turn_start
 ├─ 拼 request_messages = [SystemMessage(system_prompt)] + state.messages
 ├─ transform_context(request_messages)   # 只改本次请求
 ├─ options = StreamOptions(api_key, thinking, session_id, abort)
 ├─ stream_with_retry(...)                # 或 safe_stream，取决于 retries
 │    └─ 每个 AssistantEvent → message_start（首次）/ message_update
 ├─ 终局 AssistantMessage 追加到 state
 ├─ stop_reason == tool_use ?
 │    ├─ 校验参数（JSON → schema）
 │    ├─ before_tool_call（权限/自定义拦截）
 │    ├─ 并行批执行 → exclusive（write tier/sequential）按序执行
 │    ├─ 每个结果：after_tool_call 可替换 → 追加 ToolResultMessage
 │    └─ 所有结果都 terminate → 结束
 ├─ 否则结束
 └─ 下一轮：steering 消息在工具轮后追加
turn_end
```

## 终止条件（按优先级）

| 条件 | termination | 说明 |
|---|---|---|
| abort token 触发 | `aborted` | 立即停止，不给模型下一轮 |
| `turns >= max_turns` | `max_turns` | 默认上限 100（`_MAX_TURNS`） |
| 已花费 > `max_cost` | `max_cost` | 在开下一轮**之前**检查，保证 transcript 配对完整 |
| assistant `stop_reason="error"` | `error` | 错误信息在消息里 |
| 无工具调用的正常结束 | `stop` / `length` | 原始 stop reason |
| 本批工具结果全部 `terminate=True` | `stop` | 工具主动要求结束 |
| 流没有 terminal 事件 | `no_stream` | 防御性分支 |

`agent_end.new_messages` 是本 run 追加的所有消息（便于持久化/回放）；`agent_end.tool_stats` 是按工具名聚合的 `{calls, errors, ms}`（仅本 run，无工具调用时为 None），供 telemetry/evals 使用。

## 工具轮的关键细节

### 参数校验先于权限

顺序是：JSON 解析 → `validate_arguments`（schema 子集）→ `before_tool_call` → `execute`。
所以权限 hook 和工具永远只看到合法输入；非法输入直接变成 `is_error` 结果。

### 并行与 exclusive

- 默认 `execution_mode="parallel"`：同一轮的所有工具用 `asyncio.gather` 并发执行。
- exclusive = `execution_mode="sequential"` 或 `approval="write"`：**先等并行批结束**，
  再按模型请求顺序逐个执行（文件写类工具不与读/搜索交错）。旧实现的两轨并发不是
  omp 的 exclusive 语义，已修正。

### 超时、错误类别与循环守卫

- 每次工具调用按 `agent/timeouts.py` 的 effective deadline 包 `asyncio.wait_for`；
  超时变成 `error_kind="timeout"` 的 is_error 结果，loop 继续。
- 降级结果带 `error_kind`：`unknown_tool` / `invalid_arguments` / `policy` /
  `tool_error` / `timeout`。
- **循环守卫**：同一批工具（name + 规范化 args 完全相同）连续 5 轮重复时，追加一条
  `[tool-loop guard] …` 的 user 消息到**模型上下文**（每 run 最多一次）。它不写入
  `new_messages`、不发 message 事件、不进 session JSONL：UI/回放会过滤它（`messages_to_views`），
  它只用于纠正模型。`sleep/tail/ps/pgrep/top/jobs/wait` 等纯轮询命令豁免。
- 结果**按请求顺序**写回 state，`tool_call_id` 一一对应，与完成顺序无关。

### 错误隔离

- 工具抛异常 → `is_error=True` 的结果（文本截断 500 字符）。
- 工具返回 `None` / 非法类型 → 防御性转成错误结果。
- 工具找不到 → `is_error=True`（"unknown tool"）。
- 任何情况下**每个 call 都有且只有一条 result**。

### length 截断的坑

模型可能返回 `stop_reason="length"` 且带工具调用，但参数被截断（不是合法 JSON）。
此时：

- **不执行**这些调用；
- 为每个 call 合成一条「参数不完整/被截断」的错误结果，保持配对；
- 重新请求模型继续（`_MAX_LENGTH_FAILURES = 2` 次上限），避免死循环。

这是最容易漏、但会被 provider 拒绝（悬空 tool call）的地方，有专门的 `tests/test_loop.py` 覆盖。

## Steering

`get_steering_messages()` 在两个时机被调用：

1. run 开始时（上一轮结束前排队输入的消息）；
2. 每个工具轮之后。

消息会追加到 state 并发出 `message_start/message_end`，所以模型下一轮能看到。REPL 的
「运行中回车」就是走这条路径。

## 成本累计

`_run_cost(new_messages)` 只统计本 run 新增的 assistant usage，所以 `max_cost` 是**本 run**
的预算，不是会话总预算（需要总预算由调用方控制）。

## 源码对照

- 主循环：`zenagent/agent/loop.py::agent_loop`
- 配置/状态：`AgentConfig`、`AgentState`
- 工具执行：同文件 `_execute_tool_calls` 相关分支
- 校验：`zenagent/agent/validate.py`
- 重试：`zenagent/retry.py::stream_with_retry`
- 测试：`tests/test_loop.py`（终止、并行、length、steering、错误隔离）、`tests/test_validate.py`
