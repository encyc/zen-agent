# Agent 类：有状态的外壳

`Agent`（`zenagent/agent/agent.py`）把 `agent_loop` 包装成「可长期存在、可取消、可订阅」的对象。
REPL、RPC、SDK 用的都是它。

## 职责

- 持有消息历史（`AgentState`）；
- 管理订阅者（`subscribe`）；
- 管理 steering 队列；
- 把一次 run 变成 `asyncio.Task`，从而支持「运行中继续输入/取消」；
- 把 run 快照成 `AgentConfig`（改 model/tools 下一轮生效）。

## 生命周期

```
idle ──prompt/continue_──▶ running ──完成/取消──▶ idle
  ▲                            │
  └────────wait_for_idle───────┘
```

- 同一时间**只允许一个 run**；`prompt()` 在 running 时抛 `RuntimeError`（防止状态交叉）。
- `_run` 保存 task + abort token；`wait_for_idle()` 等待并吞掉异常（异常已经变成事件/`error_message`）。
- `abort()`：设置 token + 取消 task；文本输出可能已经部分显示，但消息不会写入历史（除非已完成）。

## 公开 API

| 成员 | 说明 |
|---|---|
| `prompt(text, images=None)` | 开一次新 run（追加 user 消息） |
| `continue_()` | 不带新 prompt 继续（用于工具后继续或恢复） |
| `subscribe(listener) -> unsubscribe` | 订阅 `AgentEvent` |
| `steer(text)` | 排队一条 steering 消息 |
| `abort()` | 取消当前 run |
| `wait_for_idle()` | 等 run 结束 |
| `replace_messages(messages)` | 恢复会话（会重置 state） |
| `reset()` | 清空历史 |
| `messages` | 只读视图 |
| `is_streaming` / `is_idle` | 状态 |
| `termination` / `error_message` | 结果 |

## 事件分发

`_dispatch(event)` 遍历订阅者，**捕获每个订阅者异常**并继续——一个坏 UI 监听器不能影响 run。
扩展 hook 也走这条路径。

## run 快照（为什么 /model 立即生效）

每次 `prompt()` 时，`_config()` 读取**当前**的 `self.model` / `self.system_prompt` / `self.tools` /
`self.thinking` 构造 `AgentConfig`。所以：

- REPL 的 `/model`、`/thinking` 只需要改 `agent.model` / `agent.thinking`；
- `/reload` 更新 `agent.tools` + `agent.system_prompt` 后，下一轮立即生效；
- 正在跑的 run 不受影响（它持有旧快照）。

## 取消的实现细节

- `AbortToken` 给 loop/transport；
- 同时 `task.cancel()` 作为兜底（例如阻塞在非 abort-aware 的 I/O）；
- `CancelledError` 在 `_drive` 里被转换为 `termination="aborted"`，然后 `agent_end` 照常发出
  ——保证订阅者总能看到结束事件。

## 常见陷阱

1. **不要在 `subscribe` 里改 agent 状态**：分发是同步遍历，重入容易出问题。
2. **不要并发调用 `prompt`**：用 `wait_for_idle` 或检查 `is_streaming`。
3. **`messages` 是只读视图的约定**：不要原地 append，用 `replace_messages`。
4. **异常不会从 `prompt` 抛出**：失败体现在 `termination`/`error_message` 和消息里。

## 源码对照

- `zenagent/agent/agent.py::Agent`（`prompt`、`_drive`、`_config`、`subscribe`、`abort`）
- 测试：`tests/test_agent_class.py`
