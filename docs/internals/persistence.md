# 持久化、重试与崩溃处理

## 会话文件（`agent/session.py`）

格式：一行一个 JSON（消息或记录），`~/.zenagent/projects/<slug>-<hash8>/sessions/<YYYYmmdd-HHMMSS>[-<prefix>].jsonl`
（升级前的 legacy 目录是扁平的 `~/.zenagent/sessions/`，只读保留）。

文件第一行是**信封**（`{"type": "session", "version": 1, ...}`，`RECORD_VERSION = 1`），
之后每一行要么是一条消息（带 `role`），要么是一条**侧信道记录**（带 `type`，扁平、无 `role`）。

```python
class Session:
    path: Path
    def append(self, message): ...       # 只追加
    def load(self) -> list[Message]: ... # 逐行解析，坏行进 warnings
    def read_info(self) -> SessionInfo
    def fork_at(self, index) -> Path     # 写新文件
    def rewrite(self, messages) -> Path  # 写新文件
```

侧信道记录（词汇表在 `app/records.py`，由 `Session.append_meta` 写入，写失败被吞掉，
观测性永不打断 run）：

| 记录 | 谁写 | 谁读 | 用途 |
|---|---|---|---|
| `run_start` / `run_end` | `SessionMetaRecorder`（Runtime 订阅者） | 无 UI 消费者（事后剖解 CLI 用） | 每轮从哪开始、为何结束（终止原因/轮数/耗时/最后错误） |
| `retry` | `make_retry_recorder`（`on_retry`） | 同上 | 被吞掉的瞬时失败与退避 |
| `mode` | `Runtime.bootstrap` / `/mode` | 同上 | 会话运行过的权限模式 |
| `tool_end` | `SessionMetaRecorder`（**Runtime**，不是 UI 控制器） | `app/views.py`、`zenagent/cli/rpc/` 快照 | 工具卡片重放：耗时、错误、有界 details（16 384 字符 / diff 400 行） |
| `compaction` | `app/compact.py` | `app/views.py`、`zenagent/cli/rpc/` 快照 | 恢复后重画分隔线一次 |

重写（`/undo`、`/fork`、`/compact`、`/shake`、重试）会写新文件，并按
`records.keep_for_rewrite()` 决定哪些记录跟随：`compaction` 总是跟随，`tool_end` 跟随仍存在的
`call_id`；run 边界、重试、mode 与未知类型丢弃。内核只应用**结构**规则（带 `call_id` 的记录跟着
仍存活的调用走）并跳过信封行，不认识任何记录种类。

不变量：

- **只追加**：正常对话不会重写已有字节。
- 不可逆操作（压缩、`/undo`、`/fork`）**总是写新文件**，旧文件保留。
- 解析宽容：坏行/旧格式行记 warning 并跳过，不让整个会话打不开。
- `messages` 属性是缓存视图，`append` 保持同步。

## Session writer lease

一个 session 文件同一时间只允许一个 writer Runtime。`app/session_lease.py` 分两层：

- **进程内**：registry 以 resolved path 为键，owner 是 Runtime 对象；第二个 Runtime
  在 `Runtime.session` setter 处抛 `SessionLeaseBusy`，不会先 abort 当前 run。
- **跨进程**：`~/.zenagent/locks/<sha256(path)>.lock` 上的 advisory lock
  （POSIX `fcntl.flock`，Windows `msvcrt.locking`）。拿不到锁同样拒绝；文件系统不支持
  锁时降级为进程内 registry。

接线点：

- `Runtime.session` setter 在换文件时先 acquire 新租约、成功后才 release 旧租约；
  同一路径重绑 no-op。
- `switch_session` 在 abort 之前 `ensure_session_free`，目标 busy 时当前 run 不受影响。
- `close` 末尾释放租约；只读 `Session` / transcript 快照不参与。

锁文件不删除（避免 unlink 后第三方重建的竞态），只保留很小的空文件；`runtime.py`
仍不超过 1061 行 allowlist。

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
