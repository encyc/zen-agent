# 上下文管理：两层策略

模型窗口有限，而会话会无限增长。ZenAgent 用**两层**处理：

```
每次请求前（便宜、无损）        每轮结束后（贵、有损，写新会话）
ContextGuard.transform     →    maybe_auto_compact
裁剪最老整轮                    LLM 总结 → 新 session
PRUNE_TRIGGER=0.85              AUTO_COMPACT_THRESHOLD=0.85
→ 降到 0.70                     用户可 --no-auto-compact / /compact
```

## 第一层：请求前裁剪（不改变 state）

`ContextGuard`（`config.py`）实现 `transform_context` 钩子：

```python
async def transform(self, messages):
    return await naive_overflow_prune(messages, self.context_window, self.last_usage)
```

- **估算用量**：优先用上一轮 provider 返回的真实 `usage`（`ContextGuard.on_event` 记录），
  没有则用 `token_estimate` 的「字符数 / 4」启发式。
- 用量 ≤ 窗口 × `PRUNE_TRIGGER`（0.85）→ 原样返回。
- 超了 → 按「整轮」丢弃最老的 user turn，直到估算 ≤ 窗口 × `PRUNE_TARGET`（0.70）。
- **永不丢**：开头的 system 消息、最新一轮。
- **配对安全**：一个 turn 里的 `tool_call` 和 `tool_result` 一起丢，不会产生悬空调用。
- 只影响本次请求；`state.messages` 不变，所以历史完整保留。

为什么用「整轮丢弃」而不是截断大文本：整轮丢弃的语义边界清晰（不会留下半个工具轮），
实现也简单；代价是可能丢得比需要的多，但对 agent 场景够用。

## 第二层：总结压缩（改变会话，写新文件）

`zenagent/cli/compact.py`：

- 触发：每轮结束后，若**上一轮真实 usage** ≥ 窗口 × `AUTO_COMPACT_THRESHOLD`（0.85）。
- 动作：把当前 transcript 渲染成文本，用一次独立 LLM 调用生成「交接摘要」，
  然后**新建 session 文件**：system 提示 + 一条「# 此前会话摘要」消息。
- 返回新 session 路径，REPL 切换过去（`switch_session`）。
- 旧 session 文件字节不变——可审计、可回退。
- 失败（空摘要/请求错误）→ 抛 `RuntimeError`，自动压缩只打印警告，不中断会话。
- `--no-auto-compact` 关闭自动；`/compact` 手动触发。

为什么压缩恢复成新消息而不是改写旧文件：JSONL 只追加的语义最简单，且「压缩前」是排障时
最有价值的证据。

## 两个阈值为什么都是 0.85

- prune 与 compact 用同一触发线，避免「刚裁剪完又立刻压缩」的抖动。
- prune 把用量降到 0.70 留出空间，下一轮工具结果 + 输出不会立刻再次越线。

## 常见陷阱

1. **估算不是精确 token 数**：字符/4 对中文偏差大，但只用来触发，宁可保守。
2. **裁剪会让模型「忘记」早期内容**：需要长期记忆的内容应写进 AGENTS.md / skills，而不是指望历史。
3. **压缩后 `usage` 归零**：新会话的 `last_usage` 需要重新积累，所以压缩后第一轮不会再触发压缩。
4. **`/undo` 与压缩交叉**：`/undo` 在压缩后的新会话里只能撤销压缩之后的内容；压缩前的内容在新文件里。

## 源码对照

- 裁剪：`zenagent/config.py::naive_overflow_prune`、`token_estimate`、`_split_turns`、`PRUNE_TRIGGER/PRUNE_TARGET`
- 钩子：`zenagent/config.py::ContextGuard.transform` + `Agent` 的 `transform_context`
- 压缩：`zenagent/cli/compact.py::compact`、`should_auto_compact`、`maybe_auto_compact`
- 测试：`tests/test_compact.py`、`tests/test_session.py`、`tests/test_loop.py`（超大上下文场景）
