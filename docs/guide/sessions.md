# 会话、导出、用量与可观测性

## 会话文件

- 位置：`~/.zenagent/sessions/<时间戳>.jsonl`
- 一行一个消息（user/assistant/tool result），**只追加**，不做原地修改。
- 每次启动新会话都会建新文件；`--resume latest` 或 `--resume PATH` 继续。
- 旧文件不会被重写：压缩、`/undo`、`/fork` 都**写新文件**，保证历史可审计。

REPL 里：`/session` 看当前、`/sessions` 列出、`/fork` 复制、`/import PATH` 载入、`/undo` 撤销最后一轮、
`/compact` 手动压缩。

## 会话名称

- 默认显示名是首个用户消息的 brief：压空白、去 Markdown 前缀（`#`、`>`、`-`、`*`、`+`、反引号）、
  取首行，超过 40 字符截断并加 `…`；没有可读内容（空会话、只有 assistant 消息）时显示 `(empty)`。
- 改名：REPL `/rename <name>`（TUI 会话选择器里按 `r`）。标题会压空白、去首尾空白，上限 80 字符；
  空标题报错。`SessionManager.rename(path, title)` 是同一操作的 app 层入口。
- 标题存在 sidecar `~/.zenagent/sessions/.titles.json`，内容是 `{会话文件名: 标题}`，**不写进 JSONL**：
  会话文件仍是 append-only 的 `Message.to_json()`，`zen export` 和任何其他消费方照旧解析。
- sidecar 缺失、损坏或不是 dict 时读取回退到派生 brief，绝不报错；写入先写临时文件再 `os.replace`，
  不会留下半截 JSON，也不影响会话文件本身。
- `fork` / `/undo`（rewrite）产生的是**新文件**，按自己的首个用户消息重新派生名称，不继承旧标题。
- TUI 会话选择器（`/sessions` 或 `ctrl+k` → sessions）按 `n` 新建、`f` fork、`d` 删除（二次确认）、
  `e` 导出、`r` 改名；列表显示会话名，文件名只作副信息。

## 上下文压缩

- 自动：每个 run 结束后如果上下文超过阈值，会压缩（`--no-auto-compact` 关闭）。
- 手动：`/compact`。
- 压缩策略：保留 system + 最近消息，把更早的对话总结成一条摘要消息；**写新 session 文件**。
- 真正的请求改写发生在 `transform_context` 钩子里（`ContextGuard`），state 本身不变。

## 导出

```bash
zen export latest --format md
zen export PATH.jsonl --format html -o share/index.html
```

- Markdown：角色分节、thinking 折叠、tool call JSON、错误标注。
- HTML：单文件内联 CSS，**所有文本 `html.escape`**（模型输出是不可信输入）。
- 导出只读，原 JSONL 字节不变。

## 用量

```bash
zen usage
zen usage --json
zen usage --since 2026-10-01
```

按模型聚合 calls / input / output / cache_read / cache_write / cost 和总计。
容错：坏行、旧格式行跳过；`--since` 先校验日期再扫目录。

## Trace 与指标

```bash
zen -p "..." --trace run.jsonl
zen -p "..." --metrics ~/.zenagent/metrics.jsonl
```

- trace：turn / 工具调用 / 耗时，给调试用。
- metrics：每 run 一行 JSON（turns、tool_calls、tokens、cache、cost、termination），
  **不含 prompt 或模型输出文本**。

## 崩溃日志与诊断

- 未捕获异常 → `~/.zenagent/logs/crash-<ts>.log`（traceback、版本、平台、argv、cwd）。
- `zen doctor --report` 输出脱敏诊断（只报「key 是否设置」，不打印值）。
- 日志不会记录环境变量值或密钥。

## 源码对照

- 会话：`zenagent/agent/session.py`（`Session`、`latest_session`、`truncate_last_turn`）
- 压缩：`zenagent/cli/compact.py`、`zenagent/config.py::ContextGuard`
- 导出：`zenagent/export.py`、`zenagent/cli/exportcmd.py`
- 用量：`zenagent/usage.py`、`zenagent/cli/usagecmd.py`
- trace/metrics/crash：`zenagent/{trace,telemetry,crash}.py`
- 对应测试：`tests/test_session.py`、`test_compact.py`、`test_export.py`、`test_usage.py`、`test_telemetry.py`、`test_crash.py`、`test_trace.py`
