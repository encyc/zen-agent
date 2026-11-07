# 太极（Taiji）—— 多任务编排层与超视角 panel 设计文档

- 日期：2026-11-07
- 状态：待批准（brainstorm 已完成，builder check 已过）
- 参考文献语境：DeepSeek Harness《Super-agent 设计探索》（双层架构 / Router-Worker / "Session 应是实现细节而非认知负担"）；交互原型见 `.superpowers/brainstorm/14103-*/content/`（taiji-panel-v3、taiji-layering-v2 等 HTML 稿）

## 1. 意图与为什么

一个人在一个仓库里并行推进任务时，能同时跟住的活跃 session 大约 3–5 个；超过之后，上下文切换成本压倒收益。zen-agent 现状是"单 Runtime 进程、单活跃会话"，多任务只能靠人肉开多个进程/窗口并记住各自上下文。

太极要解决的是：**把多 session 管理从人的认知负担变成系统的实现细节**。用户面对一个常驻对话（Router），系统在底下并发跑多个上下文隔离的 worker，每个 worker 绑定一条 git 分支；整个并行态用一张 git 图可视化。

核心命题（git-native）：**任务 = 分支 = worktree = worker session**。开任务即开分支，完成任务即合并，废弃任务即删分支。Router 的调度语言、git 操作、panel 图形三者说同一件事，不存在第二份会漂移的"任务状态"。

## 2. 已定的核心决定（对话中逐条确认）

| # | 决定 | 内容 |
|---|------|------|
| D1 | git-native 强耦合 | 任务=分支=worktree=worker session，git tree 即任务图 |
| D2 | UI 载体 | 太极可视化是右侧栏 panel（GitLens 视觉语言：泳道 rail + commit 行 + ref 徽章），非全屏。**每个项目只有一个 taiji session（即 Router 会话），项目创建时初始化（存量项目首次触碰时懒初始化）；太极 panel 只住在该 session 的右边栏**。打开它 = 进入太极模式，切普通会话 = 回普通模式，无独立开关 |
| D3 | 交互模式 B | 人只对 Router 说话；worker 永不与人直接对话；panel 一切按钮 = 向 Router 发消息的快捷方式 |
| D4 | 验收流 | 合并门禁对照 Router 会话的现有权限模式：zen=自动合并+事后汇报；ask=验收卡片等人一句话；plan=只建议不动手。panel 的"等你"过滤器 = 全系统所有模式下冒上来的人审事件总和（经 Router 汇聚） |
| D5 | 分层 | taiji 不是第五个表面、不是新入口。它是 `app/` 里对 Runtime 的组合封装层（编排层）。四个面（TUI/REPL/headless/RPC）不变，均可双消费：直连 Runtime（普通会话，现状）或消费 taiji（太极模式） |
| D6 | 无守护进程 | 持久化靠文件不靠进程：Router/worker session 全是 JSONL；宿主拥有整棵进程树，宿主退出 = Router 与 worker 全部终止（与 GUI 退出杀掉 rpc 子进程的现状同构）；重进后 taiji 从磁盘重建注册表并可续命任务 |
| D7 | 路线 | 内核编排层（本方案）。Tauri 当 supervisor 与单进程 subagent 复用两条路线已否（见 §6） |

## 3. 分层与依赖规则（防循环依赖的三条铁律）

```
TUI · REPL · headless(-p) · RPC ← GUI(desktop/) 经 JSONL 驱动 RPC 面
        │ consume                │ consume
        ▼                        ▼
   app/taiji/  ──组合持有──▶  app/runtime.py ──▶  agent/ · tools/
        │
        └──spawn + JSONL──▶  zen rpc 子进程 ×N（各绑一个 worktree）
```

1. **import 只许向下**，DAG 无环：`cli → app.taiji → app.runtime → agent`。内核核心对 taiji 零感知。
2. **环断在"taiji 永不向上"**：taiji 不 import cli、不渲染 UI，只产出状态与事件。TUI 的太极视图（P3）是 TUI 向下消费 taiji，合法。
3. **跨进程只认协议**：GUI 是 JSONL 客户端（Python 零耦合）；worker 是 JSONL 子进程。RPC 协议新增 **taiji ops 命名空间**（worker 列表/状态/事件流/panel 快照），与现有 session ops 并列、互不渗透，老客户端不受影响。

taiji 层内含：Router Runtime（进程内组合持有）、Worker 注册表（worktree↔session↔分支绑定）、事件注入/批处理、panel 状态源。

## 4. 关键机制

### 4.1 Worker 生命周期（状态机）

`spawn → running → (blocked→question) → finished→pending-accept → merged / rework / killed → archived`

- **spawn**：Router 调用 worker 工具 → 建 `task/<slug>` 分支 + worktree + 拉起一个 `zen rpc` 子进程（cwd=worktree）+ 新 session。任务来源（用户哪句话）记入 session 元数据，panel 的 `⑂` 事件行用它。
- **running**：worker 在自己 worktree 里干活、commit；taiji 把里程碑事件（启动/提交/提问/完成/失败）注入 Router 上下文，忙碌时积压合并（批处理）。
- **blocked/question**：worker 需要人决策时，taiji 生成"转问卡片"（panel 内可快捷作答，答案作为消息注入 Router 转达）。worker 的工具审批请求（ask 模式）同样经此通道上浮。
- **finished/pending-accept**：验收卡片（diff 摘要 + 测试结果 + worker 自评），动作=合并/看 diff/返工，都是发给 Router 的消息。
- **merge**：合并目标 = spawn 时记录的基分支（不盲合"当前 checkout"）。执行不得弄脏人的工作区：优先 plumbing/临时索引式合并；目标 worktree 脏或基分支已偏移 → 降级为 pending-accept 卡片等人。zen 模式下合并是 taiji 的**确定性系统动作**（LLM 不在关键路径，Router 只收汇报；对话中仍可显式让 Router 合并）；ask/plan 按 D4 卡门。完成后 worktree 清理、session 转录归档、panel 历史组可回看。
- **rework**：验收意见作为消息注入该 worker。
- **killed**：Router 终止子进程 + 删 worktree（分支保留还是删除由 Router 按上下文决定）。

### 4.2 Router 工具集（名称实现时可调）

`spawn_worker` / `message_worker` / `merge_task` / `kill_task` / `list_workers`（读态用）。Router 不配重执行工具也可以干活——执行都在 worker。注意：zen 自动合并不依赖模型调用 `merge_task` 的意愿——门禁由 taiji 确定性执行（见 §4.1），`merge_task` 是对话式触发合并的入口。**实验佐证**：文章中去掉程序化直通后 Router 决定数 +30%、读缓存 token +37%——能确定性处理的事件不喂模型，省的不只是延迟。

### 4.3 panel 数据源（P0 就要验证的绑定模型）

- git 图：主仓库 commit graph（含各 worktree 的 HEAD）。
- session 归集：`~/.zenagent/projects/` 下所有由同一 git 仓库派生的 project 目录（不同 worktree 的 git root hash 不同，天然可区分），session→worktree→branch 三元组绑定。
- 状态叠加：注册表里的 worker 状态 chip、"你在这里"标记（当前活跃普通会话）、历史归档。

### 4.4 与现有机制的复用

- 权限模式：ask/plan/zen 直接复用，无新概念。
- compact：Router 长会话的上下文治理复用现有 compact。
- subagents_runtime：worker 的 watchdog/转录保留策略可借鉴，但 taiji worker 是进程级隔离（子进程），不走进程内 subagent。
- WorktreePanel（desktop 现有只读组件）：P0 panel 的进化起点。

## 5. Goals / Non-goals / Anti-goals

**Goals**
- 一个仓库内并行 N 个任务，人只维护一个对话。
- git 是唯一真相：任务状态不与 git 漂移。
- 四个面同等受益（太极住在内核层，不锁死在 GUI）。
- 普通会话完全不受影响，可共存于图上。

**Non-goals（本期不做）**
- 外部信道事件驱动（IM/GitHub comment/CI Trigger 接入）——文章的事件体系留待后续。
- 投机执行（Router 依环境信号后台提前开工）——将来可作为 Router 的行为策略（prompt 层）引入，非架构必需。
- 语义 Watchdog（模型判断 worker 是否有实质进展/方向偏离）——任务长时运行或接入外部信道时再考虑；本期仅进程级健康监控。
- 云端持久在线、存算分离、跨设备。
- Router 水平切分/多 Router、A2A。
- TUI 太极视图（P3 再做；TUI 用户经 RPC/REPL 仍可用编排能力）。

**Anti-goals（做了就算失败）**
- 出现绕过 Router 的第二条人机控制信道。
- 编排逻辑写进 desktop/（违反 "GUI is a client, not a rewrite"）。
- 内核核心反向感知 taiji（破坏 DAG）。
- 引入数据库或常驻 daemon 替代文件式会话存储。

## 6. 方案对比（为什么是这条路）

| 路线 | 结论 | 理由 |
|------|------|------|
| **A. 内核编排层（选定）** | ✅ | 四面受益；对齐 "GUI is a client" 哲学；纯文件持久化延续内核无 daemon 设计 |
| B. Tauri 当 supervisor | ❌ | 编排锁死在 GUI，CLI 拿不到；内核与桌面两栈将来重复实现 |
| C. 复用 task 工具进程内跑 worker | ❌ | 单进程单 cwd，worker 各绑 worktree 需 per-subagent cwd 虚拟化，比子进程更伤内核；且 worker 不是"session"，panel 模型断裂。**实验佐证**：文章消融显示单 Session + Subagent 工具即使配"优先委派"提示，5 次运行仍有 1 次主会话亲自执行形成长尾、整体慢于双层架构——提示词级委派不如进程级分层可靠 |

交互与验收的 A/B/C 备选（接管式 / Router 中转 / 阶梯式；自动合并 / 验收卡片 / 一律 PR）已在对话中比较并选定 D3、D4，理由见 D3/D4 行。

## 7. 分期

- **P0 只读 panel**：Python 侧新增**只读 RPC ops**（commit-graph 快照 + 跨 worktree session 归集），desktop 只做渲染。"零新运行时" = 不引入 taiji 编排层，不等于 desktop 自己长 git 子系统（那违反 client-not-rewrite）。P2 切 taiji ops 时这批只读 ops 退役或由 taiji 提供同形状数据。先验证 §4.3 绑定模型。可与 P1 并行。
- **P1 最小闭环**：`app/taiji/` 编排层 + Router 工具集（spawn/message/merge/kill）+ worker rpc 子进程 + 事件注入；zen 模式跑通全自动环。
- **P2 模式对照 + 协议**：ask/plan 的验收卡片与人审事件汇聚；RPC taiji ops 命名空间；panel 从直连数据源切换到 taiji ops。
- **P3 TUI 太极视图**。

## 8. 留给 builder 的决定

**【已定·1】worker 与宿主的生命周期 = (a) 随宿主死，文件续命**：宿主退出（含桌面切 worktree 导致的 rpc 子进程重启）时 Router 与全部 worker 一并终止；转录、分支、worktree 留盘。重进时：taiji 重建注册表（`git worktree list` + 各 project 目录 session 扫描），并向 Router 注入系统状态同步（哪些任务中断在哪个 commit）；**续命 = 在既有 worker session 上重新拉起 rpc 子进程接着干，而非新建任务**。不引入 per-worker 事件文件或 socket 会合点（若将来要无人值守再升级）。

**【已定·2】宿主单活跃会话语义**：taiji session 是项目级单例（D2），打开它即太极模式、切走即普通模式——复用现有会话切换机制，宿主始终单活跃会话，无多槽 RpcState。图上"你在这里"= 最近活跃的普通会话；点它 = 切回该普通会话续聊。宿主锁：同一项目同时只允许一个宿主激活 taiji（锁文件，具体机制 builder 定）。

---

- taiji ops 的具体协议形状（沿用现有 JSONL 风格与 request-id 关联）。
- worker 子进程的具体驱动方式：随宿主生命周期的普通 stdio 子进程（复用现有 rpc client 代码路径）。
- 事件注入 Router 的消息模板与批处理窗口大小。
- 同一仓库的 taiji 宿主互斥（host 锁文件机制）——必须处理双开冲突。
- worker 并发上限、worktree 命名与清理策略、失败重试。
- 绑定规则细节：同一 worktree 多 session 时取最近活跃；孤儿 worktree（无 session）的展示。
- Router 上下文里 worker 消息的 Inline/Spill 粒度（摘要 vs 全文可召回）。

## 9. 我的默认决定（用户可否决）

- 任务分支前缀统一 `task/`。
- Router 默认配轻量模型（provider 体系可配）；worker 用能力强的模型。
- Router Runtime 由 taiji 进程内组合持有（非独立子进程）；宿主崩溃重启即可，session 在盘上。
- 交互原型（HTML mockups）存档于 `.superpowers/brainstorm/`，不进 git。

## 附录 A：与《Super-agent 设计探索》的模块对照

设计渊源与裁剪依据（文章 = DeepSeek Harness 团队的 Super-agent 架构文，场景为团队 IM+GitHub 持久在线；taiji 场景为本机 solo 并行开发）。

| 文章模块 | 文章要点 | taiji 对应 | 状态 |
|---|---|---|---|
| 双层架构 §2 | Router 常驻只分派（可不配 Bash）、轻模型；Worker 隔离、全工具、强模型 | Router Runtime（轻模型、仅编排工具）+ worker rpc 子进程（强模型、绑 worktree） | 采纳；D3 交互模式 B 比文章更严格（worker 永不与人对话） |
| Router 批处理 §2.1 | 积压 Trigger 合并进一次推理，prefill 并行摊薄 | worker 里程碑事件积压合并注入（P1 简版） | 采纳简版 |
| 事件驱动 §3 | Trigger 来自群聊/邮件/GitHub/CI | Trigger 仅两源：人（经 Router 面）+ worker 里程碑 | 裁剪：solo 桌面无外部信道 |
| 两级过滤 §3.1 | 程序 Fast Path + 语义 Slow Path | worker 是受信内部事件源，无需语义过滤；接入外部信道时再引入 | 裁剪 |
| 订阅与直通 §3.2 | 同 Thread 后续事件直通订阅 Worker，免过 Router | worker 冲突协调改由 git 分支物理隔离 + 合并时解决；Router→worker 走 message_worker | 等价替换（git-native） |
| Inline & Spill / 链式召回 §3.3 | 上下文当 Cache 管理；对象带 ID 构成引用图逐跳召回 | worker 汇报摘要 Inline，全文转录随时召回（细则 §8） | 采纳简版；session JSONL 即天然召回源 |
| 投机执行 §3.4 | 依环境信号后台提前尝试 | 无 | Non-goal（将来 prompt 层可加） |
| 异步 ReAct §4.1 | start()/stop() 消除工具阻塞、全双工对话 | 多进程天然解耦：worker 是独立进程，Router 会话从不被阻塞 | 等价替换（进程边界替异步工具调用） |
| 多信道 §4.2 | IM 轻量交互 / GitHub 邮件结构化交付 | Router 对话（轻量决策）+ panel 卡片（结构化快捷应答）+ git 分支（结构化交付物） | 双信道雏形 |
| 存算分离 §5 | 云端无状态 Loop + 端侧 daemon 接环境 | D6：无守护进程、纯文件、同机；环境隔离手段是 worktree 而非分离 | 刻意不采纳：文章解决"云端持久在线"，taiji 解决"本机 solo 并行" |
| Watchdog §6 | 独立 Watchdog Session，外部 Control Loop + 语义进展监控 | 进程级健康监控归 taiji 注册表 | 部分采纳；语义 Watchdog 为 Non-goal |
| 开放问题 §8.2 | 脱离 Session 后的人机协作界面在哪 | taiji session 单例 + git 图 panel 即该问题的一个具体答案 | 定位本身 |

两处文章实验数据支持本文决定：消融实验（单 Session+提示词委派不稳定）佐证 §6 否掉路线 C；去直通后 Router 决定数 +30%/token +37% 佐证 §4.1/§4.2 的确定性门禁。
