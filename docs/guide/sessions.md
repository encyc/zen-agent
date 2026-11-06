# 会话、导出、用量与可观测性

## 会话文件

- 位置：`~/.zenagent/projects/<项目key>/sessions/<时间戳>.jsonl`（按项目隔离；项目根=最近 `.git`）。
- 一行一个消息（user/assistant/tool result），**只追加**，不做原地修改。
- 每次启动新会话都会建新文件；`--resume latest` 只续**当前项目**最新会话，`--resume PATH` 可显式续任意会话。
- 默认视图只含当前项目；`/sessions all`、TUI picker 的 `tab`、`zen sessions list --all` 才列出全部项目。
- 列表条目带 `created_at`（header 的 `createdAt`，legacy 文件回退到 mtime）、`updated_at`
  （当前 mtime，即最后活动时间）、`model`（最后一条 assistant 消息的模型）和 `derived_from`
  （header 的 `derivedFrom`，原创会话为空）。
- 升级前的扁平 `~/.zenagent/sessions/*.jsonl` 保留为 legacy，只在 all 视图出现；`zen sessions adopt PATH` 可归入当前项目。
- 旧文件不会被重写：压缩、`/undo`、`/fork` 都**写新文件**，保证历史可审计。

## 旁路记录（side-channel records）

- 消息行之外，会话文件还混有**旁路记录**：顶层 JSON 对象带字符串 `type` 且没有 `role` 键
  （带 `role` 的行永远是消息）。每条记录带 `timestamp`（epoch 秒），与消息时间戳同制式。
- `session` 头记录：每个**新建**会话文件的第一行——`id`（创建时的文件名 stem）、`createdAt`、
  `cwd`、`app`/`appVersion`；`/fork`、`/undo`、`/compact`、`/shake` 产生的新文件还带
  `derivedFrom`（来源会话 stem）。续接（resume）或 legacy 文件**不补写**头记录，字节不动。
- `run_start` / `run_end`：每次 run（一次 prompt 到落定，含 `continue_` 这类无用户消息的 run）
  的边界；`run_end` 记录 `termination`（stop/max_turns/max_cost/aborted/error/no_stream/
  tool_terminate）、`turns`（本次 run 的请求数）、`duration_ms`，run 以 error 收尾时另有
  `detail`（终止 assistant 消息的 error_message）。
- `retry`：被消息级重试吞掉的瞬态失败（每次退避前一条）——`attempt`、`max_retries`、
  `delay_ms`、`error`、`model`。不开启 `--retries` 时没有这类记录。
- `mode`：bootstrap 时的初始权限模式，以及每次 `/mode` 切换——`mode`（zen/plan/ask）。
- `tool_end`：每次工具调用结束写一条——`call_id`、`tool_name`、`duration_ms`、
  `is_error`、`details`（`details` 序列化上限 16 KiB；`diff` 先按 400 行截断，
  仍超限则先丢 `diff`，再逐个丢最大字段，并置 `diff_truncated`/`details_truncated`）。
  重写/分叉/撤销时只携带“保留消息里仍有该 `call_id`”的记录；`/shake tools`
  不携带（它本就是要丢掉这些负载）。
- `compaction`：每次 `/compact`（含自动压缩）在新文件、摘要之前写一条——
  `tokensBefore`、`derivedFrom`、`reason`（`auto` | `manual`）。RPC `transcript`
  结果里的 `compactions` 数组按文件顺序返回，GUI 用它画一条且仅一条分隔线；
  带 `source="compaction"` 的 notice 不出现在快照里。
- steering：通过 steer 通道送达的用户消息带 `"steering": true`（普通消息不写这个键）；
  多条 steer 会聚合成一条消息，标记按“送达的消息”计。
- RPC 视图 id（`v1`、`v2`…）是加载时按顺序生成的，不写入文件；同一文件重复加载
  得到同样的 id。`messages_to_views`/live builder 对无文本、无 thinking 的 assistant
  行直接丢弃；没有结果的工具调用显示为 `interrupted`。
- 加载规则：`Session.load` 把旁路记录收进 `session.records` / `session.header`（首条 `session`
  记录），不算消息也不产生 warning；`zen export`、`zen usage`、列表等消费方照常只看消息。
  写入是尽力而为（`append_meta` 吞掉 I/O 错误），可观测性永远不会弄挂一次运行。
- 用 `jq` 直接看：`jq -c 'select(.type)' <session>.jsonl`。

REPL 里：`/session` 看当前、`/sessions` 列出、`/fork` 复制、`/import PATH` 载入、`/undo` 撤销最后一轮、
`/compact` 手动压缩、`/pin [on|off]` 置顶/取消置顶当前会话。

## 会话置顶（pin）

- `/pin` 切换当前会话的置顶状态，`/pin on`、`/pin off` 显式指定；TUI 会话选择器里置顶行显示 `★`。
- 置顶只影响列表/选择器排序（置顶组在前，组内仍按时间倒序），不改变会话内容。
- 状态存在与 `.titles.json` 同目录的 `.pins.json`（JSON 数组，风险同标题 sidecar：缺失/损坏回退空集，
  原子写入）；会话 JSONL 仍保持不变。

## 会话名称

- 默认显示名是首个用户消息的 brief：压空白、去 Markdown 前缀（`#`、`>`、`-`、`*`、`+`、反引号）、
  取首行，超过 40 字符截断并加 `…`；没有可读内容（空会话、只有 assistant 消息）时显示 `(empty)`。
- 改名：REPL `/rename <name>`（TUI 会话选择器里按 `r`）。标题会压空白、去首尾空白，上限 80 字符；
  空标题报错。`SessionManager.rename(path, title)` 是同一操作的 app 层入口。
- 标题存在 sidecar `<项目 sessions 目录>/.titles.json`，内容是 `{会话文件名: 标题}`，**不写进 JSONL**：
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
- artifacts：超大工具结果落在 `<session 目录>/artifacts/<session stem>/`（64 文件 / 8 MiB 上限），
  notice 会给出路径，`read_file` 可回读；
- metrics：每 run 一行 JSON（turns、tool_calls、tokens、cache、cost、termination，
  以及 `tools`：按工具名的 `{calls, errors, ms}` 汇总），
  **不含 prompt 或模型输出文本**。

## 崩溃日志与诊断

- 未捕获异常 → `~/.zenagent/logs/crash-<ts>.log`（traceback、版本、平台、argv、cwd）。
- `zen doctor --report` 输出脱敏诊断（只报「key 是否设置」，不打印值）。
- 日志不会记录环境变量值或密钥。

## 源码对照

- 会话：`zenagent/agent/session.py`（`Session`、`latest_session`、`truncate_last_turn`）
- 旁路记录：`zenagent/app/session_meta.py`（`SessionMetaRecorder`、`make_retry_recorder`）
- 压缩：`zenagent/app/compact.py`、`zenagent/config.py::ContextGuard`
- 导出：`zenagent/export.py`、`zenagent/cli/exportcmd.py`
- 用量：`zenagent/usage.py`、`zenagent/cli/usagecmd.py`
- trace/metrics/crash：`zenagent/{trace,telemetry,crash}.py`
- 对应测试：`tests/test_session.py`、`test_compact.py`、`test_export.py`、`test_usage.py`、`test_telemetry.py`、`test_crash.py`、`test_trace.py`
