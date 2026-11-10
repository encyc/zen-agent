# Zen Mode 设计：单项目单交互会话 + Router 统筹（2026-11-10）

## 意图与动机

参考 DeepSeek dsh Super-agent 的双层架构（Router 层 + Worker 层），在 zen-agent 现有
单进程 Runtime/Slot 架构上新增一种交互形态：**Zen 模式**。切换开关位于 GUI sidebar
顶部。进入 Zen 模式后，每个项目只保留**一个交互会话**（Router session）；项目下其余
session 各自持有任务焦点（领域/事件），由 Router **异步**派工、收报告、汇总进度。
用户只面对一个会话，把多会话切换与上下文记忆的认知负担交还给系统。

核心纪律（承 dsh orchestrator）：Router 只做中枢——分派、协调、向用户汇报；不亲自
改代码；worker 结果到达时不等待、不轮询。

## 目标 / 非目标 / 反目标

**目标**

1. sidebar 顶部切换按钮：Coding ↔ Zen，全局持久化（`zen.ui.mode`）。
2. Zen 模式下：交互面收敛为每项目一个 Router session；项目其余 session 渲染为
   worker 名册（状态徽标 + 焦点标签）。
3. Router 通过工具异步管理 worker：派工、查状态、停止、设焦点、新建 worker。
4. worker 运行在同进程内（独立 Runtime/Agent/Session，transcript 落盘为普通
   session JSONL），结果完成后自动注入 Router 会话（steer / 排队）。
5. 用户消息始终进入 Router；点击 worker 行可只读查看其 transcript。

**非目标**（后续版本）

- 外部 Trigger 源（IM/GitHub webhook）、多信道输出、订阅直通、Watchdog、存算分离。
- worker 事件的全量实时流式转发到前端（MVP 用名册状态 + 按需 transcript）。

**反目标**

- Router 亲自执行实现任务（违背双层架构分工，dsh 消融实验证明劣化）。
- 出现"两个可写主会话"——Zen 开启期间项目唯一可交互会话是 Router。

## 方案取舍

| 方案 | 描述 | 结论 |
|---|---|---|
| **A. 进程内 Fleet Manager（选定）** | ZenFleet 挂在 RuntimeSlot 上；worker = 独立 `prepare()` 出的 Runtime（完整工具/技能/MCP），`switch_session` 到既有 session 后驱动 `agent.prompt`； fleet 自持并发队列 | 与 slot 生命周期一致、复用持久化订阅者、审批经 slot confirm 上浮 UI |
| B. 经 RPC Host 复用其他 slot 当 worker | Router 通过 host 内部路由驱动别的 slot | 拒绝：slot 上限 4 + LRU 驱逐 + busy 语义面向前端而非编排；审批路由易死锁 |
| C. 提示词约束的单 session + task 工具 | 纯 prompt 让模型自觉委派 | 拒绝为主方案：正是 dsh 文章消融实验中被证伪的形态；保留 task 工具给 Router 做一次性子任务 |

## 架构

```
RuntimeSlot (project P, zen enabled)
├── runtime.session = Router session（项目唯一交互会话）
├── runtime.zen_fleet = ZenFleet
│   ├── roster：sessions/.zen.json 侧车（stem → {role: router|worker, focus}）
│   ├── workers: dict[stem → WorkerHandle]
│   │     └── WorkerHandle{ runtime(懒建), status, last_report, asyncio.Task }
│   ├── dispatch 队列（并发上限 max_concurrent，默认 3）
│   └── report 注入：slot.chat.steer("[worker:<name>] ...")
├── router 工具：fleet_list / fleet_dispatch / fleet_status / fleet_stop /
│                fleet_focus / fleet_spawn（懒解析 runtime.zen_fleet，同 ask 工具模式）
└── prompt：_prompt_with_mode() 追加 zen router 纪律段
```

**会话与名册**

- Router session：普通 session JSONL；启用 Zen 时若无历史 Router 则新建，并把
  `router` 角色写入 `.zen.json`；下次启用复用最新 Router（延续全局记忆）。
- Worker focus：默认取 session 标题；Router 可用 `fleet_focus` 改写，写入 `.zen.json`。
- `.zen.json` 与 `.titles.json` 同目录同风格（`zenagent/agent/session.py` 侧车模式）。

**worker 状态机**：`idle → queued → working → waiting`（回合结束交报告）；
异常/abort → `blocked`；派工失败 → `failed`。报告为最后一次 assistant 文本的
有界摘要（≤ 800 字符）。

**worker 运行时**：`prepare(views=False)` + `switch_session(worker_session_path)`；
沿用其自带 persist 订阅者落盘；审批/ask 经 `broker.set_confirm(slot.server.confirm)`
上浮到 UI（帧带 router 的 project/session 标签）。slot 关闭或 Zen 关闭时 abort
所有在跑 worker（报告注入"host 关闭"说明，延续 session）。

**slot 语义**：Zen 开启且 fleet 有在跑/排队 worker 时 slot `pinned`（不被 LRU
驱逐）；开启/关闭 Zen 要求 slot 非 busy（否则拒绝，`zen_busy` 错误码）。

## RPC 契约（PROTOCOL_VERSION 3）

新增 request kind **`zen_fleet`**（避开既有权限模式值 `zen` 的命名空间）：

| op | 参数 | 语义 |
|---|---|---|
| `enable` | — | slot 切到 Router session + 建 fleet；返回 roster |
| `disable` | — | 恢复原 session + 关 fleet；返回 roster |
| `state` | — | `{enabled, router, roster: [{path,title,focus,status,last_report}]}` |
| `transcript` | `path` | 指定 worker 的只读 transcript（复用 views 投影） |

服务端主动帧 `{"type":"zen_fleet", "project", "session", "roster": [...]}`——fleet
状态变化即发，前端据此刷新名册。`desktop/src/rpc/types.ts REQUEST_KINDS` 与
Python dispatcher、`tests/test_rpc_contract.py` 三方同步钉住。

## 前端（desktop）

- `useSettingsStore.zenMode: 'coding' | 'zen'`（localStorage `zen.ui.mode`，重连后
  经 replay 对每个已开项目重新 `zen_fleet op=enable`）。
- Sidebar header（⌘B 按钮旁）加切换按钮（icon + tooltip，仿主题按钮模式）。
- Zen 开启时：sessions 区渲染 Router 行（强调样式）+ 项目内 worker 名册行
  （状态点 + focus）；点击 worker 打开只读抽屉（zen_fleet transcript）。
- `handleFrame` 增加对 `zen_fleet` 主动帧的处理（`frameBelongsTo` 过滤后入
  `useZenStore`）。
- i18n：`zen.*` 键，zh/en 双字典齐平（locale.test 钉住）。

## 采用与舍弃（dsh → zen-agent）

| dsh 机制 | 处置 |
|---|---|
| 双层 Router/Worker | 采用（本设计主干） |
| Ticket/Mandate/Policy 授权域 | 舍弃——单用户桌面场景，用户即授权方 |
| Trigger/Thread/Eval 事件驱动 | 舍弃（非目标）；MVP 的 Trigger 就是用户在 Router 的输入与派工 |
| 订阅直通 | 舍弃；用户消息全走 Router |
| 异步结果投递、批处理 | 采用：`chat.steer` 注入 + Router 纪律提示词 |
| worker slot 并发上限 | 采用：fleet `max_concurrent` |
| Report 结构化状态 | 简化为状态机 + 文本报告 |

## 交给实现者的默认决定

- 并发上限默认 3，`config.toml [zen] max_concurrent` 可调。
- Router 系统提示词以 Python 常量置于 `zenagent/app/zen/prompts.py`。
- 契约测试、fleet 单测、前端 store/组件单测随实现落地；`scripts/check.sh` 与
  desktop `pnpm test && pnpm build` 为验收门禁。
- CHANGELOG 按治理规则写架构图小节（app 层新增 zen 节点、cli/rpc 新增 zen_fleet
  op 边、desktop 新增 zenStore/roster 渲染）。
