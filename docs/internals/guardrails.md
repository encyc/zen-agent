# 护栏与校验

「护栏」在这里指三类机制：**参数校验**、**权限门**、**预算/循环上限**。它们都不改写模型语义。

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
危险命令 > deny 模式 > --read-only > allow 模式 > 只读工具 > 交互/--yes
```

关键设计：

- `before_tool_call` 返回 `{"block": True, "reason": ...}`，loop 生成 `is_error` 结果且**不执行**工具
  —— 所以权限拒绝对模型是可见、可恢复的。
- 拒绝原因会写进工具结果，模型能据此换一种做法。
- `--read-only` 是「能力边界」而非「提示」：连 MCP 工具一起拒。

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

## 6. 什么是「不做」的护栏

- 不自动沙箱（没有容器/权限隔离）；
- 不限制文件访问范围（工具按进程权限运行）；
- 不审计工具参数（不记录到日志，只记录工具名/耗时/trace）；
- 不在内核里做内容过滤（prompt injection 防护不是内核职责）。

这些属于「部署层」问题，故意留给使用者（例如在 CI 容器里跑、用只读挂载）。

## 源码对照

- 校验：`zenagent/agent/validate.py`
- 权限：`zenagent/cli/permissions.py::PermissionGate`、`READ_ONLY_TOOLS`、`dangerous_command_reason`
- 循环上限：`zenagent/agent/loop.py`（`_MAX_TURNS`、`_MAX_LENGTH_FAILURES`、终止分支）
- 预算：`AgentConfig.max_turns/max_cost`、`--max-turns/--max-cost`
- 测试：`tests/test_validate.py`、`tests/test_permissions.py`、`tests/test_loop.py`
