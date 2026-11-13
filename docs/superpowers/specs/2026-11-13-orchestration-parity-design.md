# 两个编排器的分叉：诊断与处置

日期：2026-11-13 · 状态：待实施

## 背景

`zen` 与 `swarm` 都在 `Runtime` 之上独立长出来，先各写一套，后来做了一次重构。
那次重构（`4ba0066` dispatch 单入口、`ee6aa18` supervisor 跨模式登记、`f7269f8` 房间
composer 走 supervisor）统一了**派活入口与登记**，没有统一**子 agent 的构造、收尾与
生命周期**。因此分叉仍在，且有三处是行为差异，不是风格差异。

代码里也留着自白：`attach_swarm_run` 的 docstring 写着
*"Swarm builds runtimes outside `prepare`, so it owns this attachment"*。

## 现状：共享什么

`AgentProfile`、`Supervisor`、`Dispatcher`、`WorkspaceState`、`RunLedger`、
`ApprovalRouter`、`Session` + lease、权限 `mode` + `PermissionGate`。

## 现状：还在分叉的十处

| # | 维度 | zen | swarm | 证据 |
|---|---|---|---|---|
| 1 | 构造入口 | `prepare()` | `Runtime.bootstrap()` | `zen/fleet.py:319` / `swarm/manager.py:649` |
| 2 | 能力面 | profile 全默认 True | 关掉 plugins/skills/templates/mcp + `role.tools` 白名单 | `fleet.py:311` / `manager.py:620` |
| 3 | 权限继承 | auto 降级成 ask | 原样用 `role.mode` | `fleet.py:300` |
| 4 | ask | worker 无 `ask_handler` | 每 role 一个 | `manager.py:662` |
| 5 | 模型/预算 | 不传 `model_id`/`thinking`/`max_turns`/`max_cost` | 全部显式传 | `fleet.py:320` / `manager.py:634` |
| 6 | 结果回传 | 终局文本 steer 进 router | room post + task 状态机 | `fleet.py:_inject` / `runner.py` |
| 7 | 事件可见性 | 只有终局报告 | `events_tap` 实时转发 | `swarm/events_tap.py` |
| 8 | 超时收尾 | abort + 注入「运行超时」 | `abort_outcome` + 等 idle | `fleet.py:257` / `runner.py:90` |
| 9 | 生命周期 | 每次 dispatch 后 `close()` | 空闲保留到 `runtime_idle_s` | `fleet.py:_run` / `e4217ef` |
| 10 | 并发 | 信号量 + queued | 轮次调度 + 收件箱 gate | `fleet.py:222` / `swarm/schedule.py` |

## 判定

**要修（行为缺陷或维护陷阱）：1、3+4、5。**

- **5 是缺陷**：zen worker 的 `RuntimeOptions` 不带 `model_id`，落到
  `options.model_id or user_config.default_model`——**不是 router 当前的模型**。
  router 用 `/model` 切过之后，worker 仍在用配置里的默认模型。同时不传 `max_cost`，
  `Agent` 那边就是 `None`（无上限）。
- **3+4 是死路**：zen 把 auto 降级成 ask（怕 worker 悄悄放行写操作），但 worker 没有
  `ask_handler`，`ask` 工具直接返回错误——既不自动放行，也没人能问。swarm 相反：
  原样用 `role.mode`，且每个 role 有 ask 通道。同一件事两套安全性。
- **1 是维护陷阱**：`bootstrap.py` 存在的理由就是「四个前端各抄一遍已经漂移过一次」，
  现在编排器也在各抄。

**保留（有意设计）：6、7、8、9、10。**

- 6/7：zen 是「派完就算完，结果飘回来」，swarm 是「房间长期在线，谁都能围观」。
- 8/9/10：生命周期与并发模型本来就该不同。
- 2：zen worker 继承 router 的全套能力，swarm role 是受限角色——这是配置意图，
  但要在文档里写明白。

## 决策

- **D1**：zen worker 继承 router 的 `model` / `thinking`；预算取 `[task]` 的
  `task_max_turns` / `task_max_cost`（与 `task` 工具派子 agent 用的是同一组旋钮）。
- **D2**：zen worker 保留 auto→ask 降级，但**必须**接上 ask 通道；`ZenFleet` 新增
  `ask` 回调，由 `ops_zen` 用 `slot.server.ask` 注入；无 UI 时可为 `None`，此时
  `ask_handler` 不接（保持现状，工具返回错误）。
- **D3**：swarm 改为走 `prepare(options, stream_fn=..., allow_missing_key=True, views=False)`，
  保留自己 `broker.set_confirm(...)` 与 `runtime.ask_handler = self._ask_handler(role.id)`。
- **D4**：6–10 不动；在 `docs/guide/orchestration.md` 里记下分工与保留的分叉。

## 非目标

- 不合并两个编排器的调度器、结果回传与生命周期。
- 不改 kernel（`agent/`、`providers/`、`tools/`）。
- 不动 workspace mode 与权限档位的词汇（`plan` / `ask` / `auto`）。
