# Phase 6：Supervisor Dispatch 适配器

日期：2026-11-13 · 分支：`feat/orchestration-foundation` · 前置：Phase 1–5

## 问题

Phase 5 收敛了 `status` / `approve` / `cancel`，但“派活”仍有两套入口：

```text
zen:   router agent -> dispatch tool -> fleet.dispatch(session, task)
swarm: UI -> swarm op=post -> manager.post(text) -> tasks
coding: UI -> session prompt -> slot server -> agent.prompt
```

三条路径各自有参数、返回值和错误约定。只要派活仍按模式分叉，任何新的自动化入口都要
重新知道 zen 和 swarm 的内部形状；Supervisor 也就只是“查询面”，不是“运行面的主人”。

本阶段的目标不是合并调度器（zen 的 router 和 swarm 的 room 仍然是两种合法策略），而是：

> **一个派活入口，多种派活策略；策略可以是模式特有的，入口不允许。**

## 设计

### 1. `Dispatcher` 协议

新增 `zenagent/app/dispatch.py`：

```python
class Dispatcher(Protocol):
    mode: str
    async def dispatch(self, **payload) -> dict: ...
```

- `ZenDispatcher(fleet)`：`dispatch(prompt, session)` -> `{dispatched, session, status}`；
  直接调用 `ZenFleet.dispatch`，worker 忙碌/队列上限/关闭等 `ValueError` 原样上抛。
- `SwarmDispatcher(manager)`：`dispatch(prompt, role="")` -> `{dispatched, tasks, taskIds}`；
  有 `role` 时把它变成 `@role` 前缀，仍走 `manager.post` 的房间语义。
- coding 没有 dispatcher：单 session 不是“编排派活”，由 `session prompt` 继续承担。

### 2. `Supervisor` 持有 dispatcher 注册表

```python
supervisor.attach_dispatcher(ZenDispatcher(fleet))
await supervisor.dispatch("zen", prompt="...", session="...")
supervisor.detach_dispatcher("zen")
```

- 注册表只保存“当前活着的编排器”；fleet 关闭、room 驱逐/关闭、项目关闭时注销。
- 没有注册的 mode 抛 `DispatchUnavailable`，由 RPC 映射为 `dispatch_unavailable`。
- `Supervisor` 仍不做调度：它只把请求交给当前挂载的策略对象。

### 3. RPC

`supervisor op=dispatch`：

```json
{
  "kind": "supervisor",
  "op": "dispatch",
  "project": "/abs/project",
  "mode": "zen",
  "prompt": "inspect the failing test",
  "session": "/abs/project/.zenagent/sessions/x.jsonl",
  "agent": "zen:x",
  "role": "reviewer"
}
```

- `mode` 省略时读 `workspace.json`；`coding` 拒绝，`code=dispatch_not_orchestrated`。
- `prompt` 必填。
- zen 需要 `session` 或 `agent`（`agent` 从 Supervisor 的 live handle 解析成 session）；
  两者都有且不一致时拒绝。
- swarm 可选 `role`；工单归属仍由房间的 mention/ref 语义决定，不在这里伪造 `workItem`。
- 成功结果统一带 `mode`、`dispatched: true`，再附模式自己的字段。
- 策略拒绝（worker busy、fleet closing、room 校验失败）映射为 `dispatch_rejected`。

## 旧入口 → 新入口

| 旧入口 | 新入口 | 说明 |
|---|---|---|
| `swarm op=post` | `supervisor op=dispatch mode=swarm` | 参数同 `text` → `prompt`；返回 tasks 形状不变 |
| `swarm op=cancel` | 暂不迁移 | 取消排队/运行中的 task 是房间状态机，不是 abort runtime |
| `zen_fleet`（无直接 dispatch） | `supervisor op=dispatch mode=zen` | 必须指定 worker session/handle |
| `approval` | `supervisor op=approve` | Phase 5 已收敛 |
| `session prompt` (coding) | 不迁移 | coding 是单 session，不是编排 |

旧入口本阶段全部保留；桌面端本轮不切，先让契约和测试站住。迁移是后续 UI 的事，
不是这次的重写。

## 非目标

- 不合并 zen 与 swarm 的调度器、队列、状态机。
- 不引入自动选 worker / 自动建工单；目标必须显式。
- 不改 `session prompt` 的流式协议，也不让 dispatch 承担 UI 的 transcript 更新。
- 不删除旧 RPC。

## 测试

- `Supervisor.dispatch`：未挂载 -> `DispatchUnavailable`；attach/detach；参数透传。
- `ZenDispatcher`：调用 fleet、返回 status、把 `ValueError` 上抛。
- `SwarmDispatcher`：role 前缀、task view/taskIds、无 role 时不加前缀。
- host `dispatch`：coding 拒绝；workspace 默认 mode；`agent` 解析；zen 缺 target 拒绝；
  无 dispatcher 的 `dispatch_unavailable`；策略 `ValueError` 的 `dispatch_rejected`。
- 回归：Phase 5 的 status/approve/cancel 测试保持通过。

## 验收

1. `supervisor op=dispatch` 能从 workspace mode 或显式 mode 派活；
2. zen 与 swarm 都能通过同一个入口被调用，返回值带模式自己的结果；
3. 关掉 fleet/room 后 dispatch 返回 `dispatch_unavailable`，不会打到已关闭对象；
4. 旧 `zen_fleet` / `swarm` / `session` / `approval` 入口仍可用；
5. 全量 pytest、ruff、architecture gate 全绿。

## 交付状态

- [x] `app/dispatch.py` + `Supervisor` dispatcher 注册表
- [x] host `supervisor op=dispatch` 与挂载/注销接线
- [x] 测试与文档

实现记录：

- `Supervisor.attach_dispatcher` / `detach_dispatcher` / `dispatch`；无挂载时
  `DispatchUnavailable`。
- `ZenOps._attach_fleet` 挂 `ZenDispatcher`，`_detach_fleet` 与 router slot 驱逐时
  detach；`SwarmOps._swarm_manager` 挂 `SwarmDispatcher`，LRU 驱逐、配置重载、
  项目关闭时 detach。
- host `supervisor op=dispatch`：workspace mode 默认值、coding 拒绝、zen
  `session`/`agent` target 解析、swarm role 校验、`dispatch_unavailable` /
  `dispatch_rejected` 映射。
- 旧入口未动；桌面端未切换。
