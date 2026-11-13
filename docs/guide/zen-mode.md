# Zen 模式：单会话统筹整个项目

Zen 模式是 zen-agent 的第二种交互形态：每个项目只保留**一个交互会话**
（Router），项目下其余会话成为持有任务焦点的 worker，由 Router 异步派工、
收报告、汇总进度。你只面对一个对话窗口，多会话的上下文切换交给系统。

设计依据见
[`docs/superpowers/specs/2026-11-10-zen-mode-design.md`](../superpowers/specs/2026-11-10-zen-mode-design.md)
（参考 DeepSeek dsh super-agent 原型的双层架构）；与
[多角色 Swarm](swarm.md) 是同框架下的两种多 agent 形态（Swarm 每项目一个
房间 + 项目角色，Zen 每项目一个 Router + 项目内 worker）。

## 打开与关闭

sidebar 顶部的三态切换器（**Coding / Zen / Swarm**）切到 **Zen**；模式由后端
`workspace.json` 持久化，桌面通过 `workspace op=get/set` 校准，重启或切项目后以
后端值为准。`localStorage` 里的 `zen.workspaceMode` 只是离线缓存，不再是唯一真相
（zen 分支旧 key `zen.ui.mode` 仍会迁移到本地缓存）。

- **进入 Zen**：为项目打开（或复用）一个**专用 Router slot**，Router 会话
  （有历史就复用上次的 Router，延续全局记忆；没有则新建）与 fleet 都长在那
  个 slot 里。当前 coding 会话所在的 slot **不被动**——正在跑的一轮继续跑完，
  不会被中断，也不会因为“忙”被拒绝。
- **离开 Zen**：停掉 fleet、把手里的前台交回进入 Zen 之前的那个 slot（或该项
  目其它 slot）；Router slot 若没在跑就关闭，跑着则留驻到回合结束、由 LRU 回收。
  Router 只存在于 Zen 模式；离开后就是普通 coding 会话，不再有 fleet 工具、
  Router 提示词或 worker 报告。
- **显式停止**：名册 Router 行右侧的停止按钮同样停掉 fleet 并交回前台。

> 架构：Zen 是**项目级**概念，由 `RpcHost`（`zenagent/cli/rpc/ops_zen.py`）
> 持有，和 swarm 房间同层；每个进入 Zen 的项目因此多一个常驻 runtime
> （受 `MAX_RESIDENT_SLOTS` 约束，LRU 回收时会推 `zen_fleet enabled=false`）。


## 在 Router 会话里工作

Router 拥有普通会话的全部能力，外加六个 fleet 工具：

| 工具 | 作用 |
|---|---|
| `fleet_list` | 列出项目名册：Router + 全部 worker（焦点、状态、最近报告） |
| `fleet_dispatch` | 把一个具体任务派给某个 worker，立即返回 |
| `fleet_status` | 查单个（或全部）worker 的状态与最近报告 |
| `fleet_stop` | 停止某个正在运行的 worker |
| `fleet_focus` | 设定/修改 worker 的任务焦点 |
| `fleet_spawn` | 新建一个带焦点的 worker（可带首个任务直接派工） |

Router 的纪律（系统提示词强制）：只做中枢，不亲自实现；派工后立即结束回合，
**结果会以 `[zen:worker …]` 消息自动回到会话并唤醒一个新回合**（正在输出
时则在下一步边界注入；同一时间窗内完成的多个 worker 报告会**合并成一条
消息**，Router 一个回合统一点评），永远不要等待或轮询。

## Sidebar 名册与 Worker 抽屉

Zen 开启时，会话区渲染当前项目的名册：

- **Router 行**：当前交互会话（高亮徽标）。worker 报告到达即唤醒 Router
  开新回合，无需用户介入。
- **Worker 行**：状态点（绿=执行中/排队，红=受阻/失败，灰=空闲/待命）+
  焦点标签 + 状态词。点击打开**只读抽屉**查看该 worker 的 transcript；
  输入永远只在 Router 会话。

状态词表：`idle 空闲 · queued 排队 · working 执行中 · waiting 待命 ·
blocked 受阻 · failed 失败`。

## 持久化与边界

- 名册（谁 Router、谁的焦点是什么）存在项目 sessions 目录的 `.zen.json`
  侧车里，与会话文件同寿命；worker 的 transcript 就是普通 session JSONL，
  退出 Zen 后仍可按普通会话浏览。
- fleet 参数在 **Settings → Zen** 里调，写进全局 `config.toml` 的 `[zen]`（仅
  全局层生效）：`max_concurrent`（默认 3）并发上限、`batch_window_s`（默认 0.5）
  报告合批窗口、`report_limit`（默认 800）单条报告回传字符数、`worker_mode`
  worker 权限档位（`inherit` 默认跟随主会话，auto 主会话降为 ask；`ask` / `plan` /
  `auto` 为固定档；旧值 `zen` 仍兼容）。同页还放 `task.timeout_s`（单次运行超时）。改动在下次启用
  fleet 时生效。超过并发的派工排队。
- Zen 是**每项目**的：切换项目后由前端按持久化模式自动重新启用；一个项目
  的 fleet 不影响另一个。
- **运行账本**：Router 的每轮 run 记 `kind=zen_router`（agent `router`），worker 的
  每轮 run 记 `kind=zen_worker`，`parentRunId` 指向派工时 Router 的当前 run，`label`
  是该 worker 当时的目标。它们和 coding/swarm 的 run 落在同一份项目 `runs.jsonl`，
  可用宿主级 `runs` 查询；Zen 自己仍以名册为 worker 状态的权威。详见
  [控制面](orchestration.md)。
- **Worker 抽屉是只读的**：打开抽屉只读 transcript，不占用会话写者；但同一个 worker
  会话不能同时被第二个 runtime 恢复（[单写者租约](sessions.md#单写者租约)）。
