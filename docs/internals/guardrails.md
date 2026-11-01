# 护栏与校验

「护栏」在这里指五类机制：**参数校验**、**权限门**、**风险判定**、**预算/循环上限**、
**写入快照**。它们都不改写模型语义。

## 1. 参数校验（进工具之前）

`zenagent/agent/validate.py::validate_arguments` 是手写 JSON Schema 子集：

- 必须 object；
- `required` 必须存在；
- 属性类型匹配 string/number/integer/boolean/object/array；
- `bool` 不算 integer/number；
- 不递归、不支持 anyOf/oneOf/format/default、不拒绝额外字段。

为什么这么窄：内核零依赖；工具自己会防御内部；过严的 schema 会把模型卡在「格式不对」的循环里。
校验失败 → `is_error=True` 的常规工具结果，模型下一轮能自己修。

## 2. 权限门（`cli/permissions.py`）

```python
allowed, reason = await gate.check(tool_name, args)
```

顺序（详见 [工具与权限](../guide/tools-permissions.md)）：

```
assess(): 灾难命令 > deny > plan/--read-only > destructive confirm > protected confirm > outside confirm > allow
confirm: 会话级目标授权 / CLI、全局配置放行 > 交互确认 > 拒绝（带 flag 提示）
allow:  allow 模式 > 只读工具 > 目标授权 > full/--yes > 交互确认 > 拒绝
```

关键设计：

- `before_tool_call` 返回 `{"block": True, "reason": ...}`，loop 生成 `is_error` 结果且**不执行**工具
  —— 所以权限拒绝对模型是可见、可恢复的。
- 拒绝原因会写进工具结果，模型能据此换一种做法。
- `--read-only` 是「能力边界」而非「提示」：未声明只读的 MCP 工具一起拒；
  工具自己声明 `approval="read"`（或 MCP `readOnlyHint`）的按只读放行。

## 2b. 风险判定（`app/safety.py`）

`PermissionGate.assess()` 把写/执行调用分成三档（`allow` / `confirm` / `refuse`），
风险标签只有三种：

| 风险 | 判定 | 放行方式 |
|---|---|---|
| `outside` | 写类工具的路径 `expanduser()` + `resolve()` 后不在 workspace（项目根） | 交互确认；headless 需 `--allow-outside PATH` |
| `protected` | 目标是 `~/.zenagent/**` 或凭据/私钥清单里的路径 | 交互确认；headless 需 `--allow-protected` |
| `destructive` | bash 命中 `destructive_command_reason`（git 破坏性命令、`sudo`、`curl|sh`、项目外 `rm -rf` 等） | 交互确认；headless 需 `--allow-destructive` |

关键不变量：

- **full / `--yes` 不自动放行 confirm 档**——模式表达的是"普通工具不用问"，不是"允许越界"。
- `[tools] allow` 与工具级 always 不能绕过风险判定；只有目标精确的会话级 always 或显式 flag 可以。
- 风险判定是启发式（尤其 bash），不构成隔离；它防的是手滑，不防恶意内容。

## 3. 预算与循环上限

| 机制 | 位置 | 行为 |
|---|---|---|
| `max_turns` | `AgentConfig.max_turns` / `--max-turns` | 在**开下一轮前**检查；`termination="max_turns"` |
| `max_cost` | `AgentConfig.max_cost` / `--max-cost` | 本 run 累计 cost 超限即停；`termination="max_cost"`；不开新请求，保证消息配对 |
| `_MAX_TURNS=100` | loop 默认 | 没显式配置时的兜底 |
| `_MAX_LENGTH_FAILURES=2` | loop | 连续 length 截断工具调用的重试上限 |
| `--retries` | retry | 只对瞬时错误重试，不对确定性错误重试 |
| bash 超时 | 工具 | 默认 120s，可传 `timeout_s`；超时杀进程组 |

`termination` 与 `stop_reason` 分离：历史里保留 provider 的原始 stop_reason，策略终止记录在
`AgentState.termination`。

## 4. 上下文的护栏

见 [上下文管理](context-compaction.md)：请求前裁剪 + 轮末总结，两者都有硬阈值，
不会出现「无限增长直到 provider 报错」的情况。

## 5. 危险命令

`dangerous_command_reason(command)` 检查：

- 根路径递归删除（`rm -rf /`、`rm -rf /*`）；
- 下载即执行（`curl ... | sh`、`wget ... | bash`）；
- fork bomb；
- 其他明显破坏形态。

`--yes` **不能**放行；只有显式 `--allow-dangerous` 才行。这是「用户明确知道自己在做什么」的通道。

## 6. 写入快照与 `/restore`（`app/journal.py`、`app/restore.py`）

每次成功的 `write_file` / `edit_file` 在写入前把旧内容存进项目级日志：

```
~/.zenagent/projects/<key>/backups/{manifest.jsonl, blobs/<sha[:2]>/<sha>}
```

- 捕获在 `before_tool_call`（审批通过后），提交在 `after_tool_call`（`is_error` 的结果丢弃）；
  日志失败只会降级为"本次备份不可用"，绝不打断 run。
- `/restore` 按 run 恢复；恢复前校验当前哈希 == agent 写后哈希，用户改过的文件跳过并报告。
- 单文件超过 `[backup] max_file_bytes` 时不存内容；bash 的 `rm`/`mv` 不在范围内。
- 数据结构见 `SnapshotJournal` 的模块 docstring；设计取舍见
  `docs/superpowers/specs/2026-11-01-hand-slip-guardrails-design.md`。

## 7. 什么是「不做」的护栏

- 不做 OS 级隔离（没有容器/权限隔离）；写路径围栏是 harness 策略，bash 仍以进程权限运行；
- 不审计工具参数（不记录到日志，只记录工具名/耗时/trace）；
- 不在内核里做内容过滤（prompt injection 防护不是内核职责）；
- 不限制读路径（`read_file`/`grep`/`glob` 可读任何位置；读凭据路径不弹确认）。

这些属于「部署层」或威胁模型 #2 的问题，故意留给使用者（CI 容器、只读挂载、独立账号）。

## 源码对照

- 校验：`zenagent/agent/validate.py`
- 权限：`zenagent/app/permissions.py::PermissionGate`（`assess`/`check`）、`PermissionBroker`、
  `READ_ONLY_TOOLS`、`dangerous_command_reason`
- 风险与快照：`zenagent/app/safety.py`、`zenagent/app/journal.py::SnapshotJournal`、
  `zenagent/app/restore.py::run_restore_command`
- 循环上限：`zenagent/agent/loop.py`（`_MAX_TURNS`、`_MAX_LENGTH_FAILURES`、终止分支）
- 预算：`AgentConfig.max_turns/max_cost`、`--max-turns/--max-cost`
- 测试：`tests/test_validate.py`、`tests/test_permissions.py`、`tests/test_loop.py`、
  `tests/test_safety.py`、`tests/test_journal.py`、`tests/test_restore.py`
