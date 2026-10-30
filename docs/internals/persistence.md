# 持久化、重试与崩溃处理

## 会话文件（`agent/session.py`）

格式：一行一个 JSON（消息），`~/.zenagent/sessions/<prefix>-<时间戳>.jsonl`。

```python
class Session:
    path: Path
    def append(self, message): ...       # 只追加
    def load(self) -> list[Message]: ... # 逐行解析，坏行进 warnings
    def read_info(self) -> SessionInfo
    def fork_at(self, index) -> Path     # 写新文件
    def rewrite(self, messages) -> Path  # 写新文件
```

不变量：

- **只追加**：正常对话不会重写已有字节。
- 不可逆操作（压缩、`/undo`、`/fork`）**总是写新文件**，旧文件保留。
- 解析宽容：坏行/旧格式行记 warning 并跳过，不让整个会话打不开。
- `messages` 属性是缓存视图，`append` 保持同步。

辅助函数：

- `truncate_last_turn(messages)`：`/undo` 用，删除最后一个完整 turn（含其工具结果）。
- `latest_session()` / `list_sessions()`：`--resume latest`、`/sessions`。

## 恢复

- `--resume [PATH|latest]`：读会话、`agent.replace_messages(...)`、打印 warnings。
- REPL `/session` 看当前文件；`/sessions` 列表；`/fork` 复制当前会话；`/import PATH` 载入外部会话。

## 重试（两层）

### transport 层

`providers/transport.py` 对 HTTP 状态码重试：429/5xx，尊重 `Retry-After`，等待可被 abort 打断。
这是「请求没成功发出去 / 服务端明确失败」。

### message 层

`retry.py::stream_with_retry` 处理「HTTP 200 但流内失败」：

- `is_retryable(message)` 只看 `stop_reason="error"` 且错误文本命中瞬时特征
  （timeout、connection reset、5xx、overloaded、rate limit…）；
- 确定性错误（quota、401/403、400、context length）**永不重试**；
- `retries=0`（默认）直接透传事件、保持流式；启用重试时每次尝试的事件先缓冲，
  失败丢弃 → 不产生重复文本（代价：该轮文本在调用结束才出现）；
- 退避 0.5s、1s；退避期间 abort 立即停止；
- 由 `AgentConfig.retries` / `--retries` 控制，默认 0（保持流式）。

为什么重试不会重放工具副作用：失败发生在 assistant 消息完成之前，此时**没有任何工具被执行**；
重试只是重新请求模型。工具只在成功的 assistant 消息之后才运行。

## 崩溃日志（`crash.py`）

- `install_excepthook(directory)`：未捕获异常写 `crash-<ts>.log`（traceback、版本、平台、argv、cwd）。
- 幂等：重复安装保留第一个目录。
- `KeyboardInterrupt` 走默认处理（不写崩溃日志）。
- 日志**不包含**环境变量或密钥。

## 诊断报告

`zen doctor --report`（`collect_report`）：

- 版本、Python、平台、cwd、config 路径；
- doctor 的全部检查结果；
- key 只显示「是否设置」，不显示值。

## 指标与 trace

- `--metrics PATH`：每 run 追加一行 `RunMetrics`（turns/tool_calls/tokens/cache/cost/termination），
  无 prompt 或输出文本。
- `--trace PATH`：JSONL，`turn_start` / `tool_start` / `tool_end`，含耗时与参数摘要。
- `zen usage` 反过来读会话文件做跨会话聚合。

## 源码对照

- 会话：`zenagent/agent/session.py`（`Session`、`truncate_last_turn`、`latest_session`、`list_sessions`）
- 压缩：`zenagent/app/compact.py::compact`
- 传输重试：`zenagent/providers/transport.py::post_stream`
- 消息重试：`zenagent/retry.py`
- 崩溃/诊断：`zenagent/crash.py`
- trace/metrics：`zenagent/trace.py`、`zenagent/telemetry.py`
- 测试：`tests/test_session.py`、`test_compact.py`、`test_retry.py`、`test_transport.py`、`test_crash.py`、`test_trace.py`、`test_telemetry.py`
