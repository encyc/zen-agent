# 多角色 Agent 运行时（Swarm Runtime）设计

日期：2026-11-10（2026-11-11 修订）· 状态：待审批

上游决策（对话中已定，不再讨论）：

- 宿主形态选**方案 1**：swarm 作为 RPC host 进程内的 app 层模块，零新进程、零新协议。
- 可见性选 **B**：agent 只收到 @ 自己的消息 + 自己发的消息，派活者必须把上下文写进消息。
- 任务选 **B**：任务是一等对象（注册表 + 状态机），不是"一条消息就是任务"。
- **结果路由由作者 @ 决定**：派活者要回执，就在派活消息里写明"完成后 @我"；不做自动回执机制。
- 房间先做 GUI 内聊天室，**不接外部 IM**（单向连接器原则不变）。
- sidebar 顶部三模式切换：**coding / zen / swarm**。coding = 现有单 agent 编码；swarm = 本设计；zen = 另一 worktree 的 taiji 重实现，本分支只预埋切换位与占位页。
- daemon 化的"长期在线"是第二阶段，本设计只保证宿主可替换。

## 1. 意图与背景（why）

zen core 现在是单 agent 编码 harness：一个 Agent、一份 JSONL transcript、一个 run 一次。
用户要在此基础上做一个**多角色 Agent 运行时**（产品方向）：角色可声明式特化、长期存活、
在房间式总线里互相派活、向人汇报。它是"Agent OS"的第一块拼图，不是一次性功能。

taiji（已移除）的教训直接约束本次设计：它的失败不在板子或注册表，而在
**契约只活在 prompt 里**——Router 被要求只调度不编辑，结果模型自己改了 main。
本设计把每个关键契约都落到结构上：@ 路由由注入层强制、任务状态由注册表持有、
防乒乓由链深/链预算/并发三道硬闸门执行、报告由 run 结束自动落房间，不依赖模型自觉；
唯一交给模型判断的是"结果 @ 给谁"——那也是消息内容，不是路由契约。

## 2. 目标 / 非目标 / 反目标

**目标**

- 角色 = 一份声明式配置（system prompt、skills、MCP/插件、工具白名单、模型、预算、root），
  新角色 = 新增一份配置；GUI 可管理。
- 全局单房间（GUI 聊天室），人类与 agent 同场；`@角色` 即派活。
- 任务注册表：`queued → running → done | failed | cancelled`；可取消、可重试。
- agent 可自主 `@` 其他 agent 形成协作链；链式协作受结构性闸门约束。
- 每个 agent 独立 session、独立工作目录；进程重启后可恢复（日志重放 + 在飞任务对账）。
- sidebar 顶部三模式切换器，zen 位预留。

**非目标（v1 不做）**

- 外部 IM 接入（保持 zen→外部单向原则；房间先内部，IM 是后续出口）。
- headless daemon / 关闭桌面后仍在线的形态（第二阶段，见 §15）。
- 按项目分区的多个房间；cron/定时唤醒；任务依赖图（DAG）；跨进程房间同步。
- zen 模式内部实现（taiji 重实现由另一 worktree 负责）。
- 编码任务的合并门禁（那是 zen 模式的领域，本设计不碰 git 编排）。

**反目标（做成这样即失败）**

- 关键契约靠 prompt（例如"agent 必须报告"却没有结构性兜底）。
- 房间消息或 agent 运行能绕过现有权限门禁。
- 凭据出现在房间日志、任务记录、RPC 回显。
- 一个 agent 崩溃或卡死拖垮整个运行时（run 级隔离必须成立）。
- 改动 `agent/` 内核；引入新三方依赖；产生循环 import；破坏既有门禁。

## 3. 约束（项目既有规则，逐条钉死）

1. 六层依赖只向下（`tests/test_layering.py`）；swarm 是 app 层模块，与 `app/taiji/` 曾经的
   位置同级、与 `plugins/` 同一先例（app 装配、内核零改动）。
2. 核心依赖仅 httpx；swarm 模块纯 stdlib（JSONL + asyncio）。
3. 单文件 ≤800 行（`scripts/check_architecture.py` 的 allowlist 只许降）。新逻辑全部进新文件，
   必须触碰的顶格宿主（`server.py` / `host.py` / `App.tsx` / `SettingsPage.tsx` / i18n）只留薄钩子。
4. RPC 契约双向钉住（`tests/test_rpc_contract.py` ↔ `desktop/src/rpc/types.ts`）。
5. arch-gov：新节点/边落回 `docs/arch-gov/manifest.json`，更新架构图资产，CHANGELOG 写架构小节。
6. i18n：扁平 dict、英文目录为准（`zenagent/i18n`）。
7. secrets 只进 `~/.zenagent/settings.json`（0600）；角色 manifest 只存 `${KEY}` 占位。
8. **命名冲突**：`zen` 已是运行时自治模式名（`zenagent/app/modes.py` 的 `MODES = ("plan","ask","zen")`）。
   顶层概念在代码中一律叫 `workspaceMode`（类型 `'coding' | 'zen' | 'swarm'`），RPC/存储不出现
   "mode" 歧义；UI 文案仍显示 Coding / ZEN / Swarm。
9. desktop 的帧过滤按 project/session（`frames.ts`、`frameBelongsTo`）；swarm 帧是全局的，
   必须携带独立标记并绕开该项目/会话过滤。
10. `app/safety.py` 的保护目录栅栏包含 `~/.zenagent`，且"保护优先于内部"。默认角色工作区
    因此放在 `~/.zenagent-workspaces/<id>/`（栅栏之外）；**不为 swarm 豁免任何安全门**。

## 4. 候选方案与取舍

**A. swarm 作为 RPC host 内的 app 层模块（选定）**
每个角色 = 一个常驻 agent 实例（自己的 Runtime + Agent + session），`SwarmManager` 持有
房间日志、任务注册表、@ 路由、每 agent 的 inbox 与闸门。复用多 slot 后台运行、session JSONL、
runtime 装配、审批帧路由。GUI 加 Room 页面，RPC 加 `swarm` 请求族。
代价：同进程、进程级无隔离（run 级隔离已有）；每 agent 一套 MCP 子进程，角色多了资源线性增长。
好处：零新进程模型、零新协议、第一版最快；`SwarmManager` 边界保持"宿主可替换"。

**B. 独立 swarm daemon（`zen swarm serve`，desktop 走 socket）**
"长期在线"的终局形态，但需要新建进程生命周期、IPC、重连、打包。第一版就做会把房间语义的
验证拖后，且与 GUI 优先的阶段性目标冲突。保留为第二阶段演进方向。

**C. 复用 subagents_runtime（协调者 spawn 子 agent）**
子 agent 是某轮 run 的临时孩子，生命周期挂在父 run 上；用户要的是平等、常驻、能自主派活的
对等角色。模型不匹配，弃。

用户已确认方案 A。`SwarmManager` 的对外边界（房间/任务/路由 = 纯数据 + 异步逻辑）保证
将来搬进 daemon 只是换宿主，不重写业务。

## 5. 概念模型

| 概念 | 含义 |
|---|---|
| `workspaceMode` | desktop 顶层模式：coding / zen / swarm。纯前端状态，后端无感。 |
| Role（角色） | 一份 manifest：人设 + 能力 + 模型 + 预算 + 工作目录。 |
| SwarmAgent | 角色实例：Runtime + Agent + session + inbox + 当前任务，常驻但空闲不耗 run。 |
| Room（房间） | 全局单例，append-only JSONL；人类与所有 agent 的消息流。 |
| Message | 房间消息：作者（user / agent / system）、文本、mentions、task 引用、链深。 |
| Task | 一等对象：id、负责人、状态、来源消息、结果消息、链深、链 id/累计成本、时间戳。 |
| Chain | 由 mention 形成的有向链；链深与链级预算共同构成防乒乓/防烧钱的硬闸门。 |

```
用户 ──@research──▶ Room ──router──▶ task#12(queued) ──▶ research.inbox
                                                          │
research run（自己的 session、自己的 root）◀──────────────┘
   │ 最终文本自动落房间 / 中途 swarm_post（可 @ 别人）
   ▼
Room（@coder）──router──▶ task#13（depth+1）──▶ coder.inbox ──▶ coder run
```

结果消息的读者**由作者决定**：结果对谁有价值，就在文本里 @ 谁——@ 照常生成新 task
（depth+1）。派活者想要回执，就在派活消息里写明"完成后 @我"。**不做自动回执机制**，
@ 是唯一的路由原语。

## 6. 角色配置（manifest）

```
~/.zenagent/agents/<id>/
├── manifest.json
├── prompt.md            # 人设正文（systemPromptFile）
└── skills/              # 可选：角色私有 skills
```

```json
{
  "schemaVersion": 1,
  "id": "research",
  "name": "Research",
  "description": "查资料、做对比、给结论",
  "model": "deepseek-chat",
  "thinking": "medium",
  "systemPromptFile": "prompt.md",
  "skills": ["skills"],
  "mcpServers": { "feishu": { "command": ["npx", "-y", "@larksuiteoapi/lark-mcp"], "env": {"APP_ID": "${APP_ID}"} } },
  "plugins": ["zen.feishu"],
  "tools": ["bash", "read_file", "write_file", "edit_file", "web_fetch"],
  "root": "/Users/mac/work/notes",
  "inheritProject": false,
  "limits": { "maxTurns": 30, "maxCost": 1.5 },
  "mode": "ask"
}
```

规则：

- `mcpServers` 与 `mcp.json` 的 server 形状同形（command xor url、env、headers），复用
  `zenagent/mcp/config.py` 的公开解析函数；`${KEY}` 从 settings secrets 替换，缺失即该角色的
  该 server 不启动（warning，不报错）。
- `plugins` 引用已安装插件，继承其 MCP 与 skills（合并规则：角色显式配置 > 插件贡献）。
- `tools` 白名单：缺省（不写）为 zen 标准工具集 + swarm 工具；显式列表即裁剪。
  **结构契约**：白名单在 Runtime 装配时执行，不靠提示词。
- `root`：角色工具的 cwd 绑定目录。缺省自动创建 `~/.zenagent-workspaces/<id>/` 并作为 root
  （在受保护目录栅栏之外，见 §3.10），保证工具永远有合法 cwd。
- `inheritProject`（缺省 `false`）：角色是**隔离 profile**——能力完全由 manifest 决定。
  `false` 时不继承 root 的项目层（`.zenagent/config.toml`、项目 `mcp.json`、项目记忆、
  项目 skills、AGENTS.md）；`true` 时按普通会话的项目装配规则并入，角色显式配置仍优先。
- `skills` 缺省为空：只含角色目录内 skills + 引用插件贡献的 skills；不隐式继承全局 skills，
  需要就显式放进角色目录（v1 不支持跨目录引用）。
- `limits`：映射到既有 `AgentConfig.max_turns` / `max_cost`（内核已有，零改动）。
- `mode`：沿用既有自治模式 plan/ask/zen。缺省 `ask`（审批超时 → 拒绝并失败任务）；
  无人值守角色建议设 `zen`。权限门禁本身不因 swarm 放宽。
- 角色 session 落在 `~/.zenagent/swarm/agents/<id>/sessions/`，**不进**普通项目会话列表
  （普通列表只扫项目 session 目录，天然隔离）。
- **角色生命周期**：`update` 在当前 run 结束后重建 Runtime 生效（运行中标记 pending-apply）；
  `delete` 取消其排队/运行任务（`cancelled(role_removed)`）、停 runtime，**默认保留** session
  与工作区磁盘数据，由 UI 提供显式清除。
- GUI 角色管理写回的也是这份 manifest；手工编辑文件等同管理。

## 7. 房间与任务存储

```
~/.zenagent/swarm/
├── room.jsonl           # 消息流，append-only
├── tasks.jsonl          # 任务记录；状态转换以追加实现，同一 task 最后一条胜出
└── agents/<id>/sessions/…   # 角色 session（应用自身写入，不经工具门禁）
```

`room.jsonl` 行形状：

```json
{"type":"message","version":1,"id":"m_01H…","ts":1762…,"author":{"kind":"agent","id":"research"},
 "text":"结论是 Z。","mentions":["coder"],"taskId":"t_…","chainDepth":0}
```

`tasks.jsonl` 行形状（每次状态转换追加一行）：

```json
{"type":"task","version":1,"id":"t_01H…","assignee":"coder","state":"running",
 "sourceMessageId":"m_…","createdBy":{"kind":"agent","id":"research"},"chainId":"c_…",
 "chainDepth":1,"chainCost":0.0,"createdAt":1762…,"updatedAt":1762…,
 "resultMessageId":null,"reason":null,"derivedFrom":null}
```

链的可追溯性由 `sourceMessageId` + 消息里的 mentions 派生，**不设 `parentTaskId`**：
任务由哪条消息产生、产出哪条结果，都能从房间日志还原。`chainId` 在链的第一跳生成，
后续由来源消息所属任务继承；`chainCost` 为该链至今累计花费（每次 run 结束后累加）。

不变量：

- 房间与任务日志只追加；启动时全量重放，内存索引即状态。
- 启动对账：**所有非终态任务**（`queued` / `running`）一律追加 `failed(reason="interrupted")`，
  并在房间发系统消息（taiji registry 的对账先例，绝不静默复活、绝不自动重跑）。正常退出时
  在飞的 run 同样记 `failed(interrupted)`（它不是用户主动取消）；用户可用 `retry` 重投。
- 写入用整行写 + 线程内锁（沿用 `Session._write_line` 的做法）；大重写用
  mkstemp + `os.replace`（压缩/归档时）。

## 8. 路由与运行模型

**派活**：任何一条房间消息（来自人或 agent）里的 `@role` 即一次派活：

1. router 解析 mentions（只认已配置角色 id；未知 @ 原样保留，不报错）。
2. 每个被 @ 的角色建一条 task（`queued`，`chainDepth = 父消息深度 + 1`）。
3. task 入该 agent 的 inbox；agent 空闲时由调度器取队首开跑。

**运行**：每个 agent 串行（一次一个 run），全局并发上限默认 3（常量，可后续配置）。

- 注入内容 = 发件人 + task id + 链深 + 房间协议说明 + 消息正文，作为 user 消息进 agent session；
  agent 用自己的历史（session）继续。
- run 结束的**最终文本自动作为该任务的结果发回房间**（`done` 的结构性兜底——模型忽略协议
  也一定有报告）；中途可用 `swarm_post` 工具发言/派活（带 mentions）。
- **结果路由由作者决定**：结果对谁有价值就 @ 谁，@ 照常生成新 task（depth+1）；派活者在
  派活消息里写"完成后 @我"即可拿到回执。没有自动回执——@ 是唯一路由原语。
- 终态映射：正常结束 → `done`；`error` / `aborted` / `max_turns` / `max_cost` → `failed`
  （原因写入房间系统消息）；用户取消 → `cancelled`。

**结构性闸门（不依赖提示词）**

| 闸门 | 默认 | 行为 |
|---|---|---|
| 链深 | 8 | 超限的派活拒投，房间系统消息说明；task 不创建 |
| 每 run 预算 | 角色 `limits` | 复用 AgentConfig；超限 run 以 `max_cost`/`max_turns` 结束 → failed |
| 链级预算 | $5 / 链 | `chainCost` 累计超限后拒绝新派活（房间系统消息），防链式烧钱 |
| 每 agent inbox | 16 | 满则拒绝新派活并提醒（不再静默堆积） |
| 全局并发 run | 3 | 超出排队 |
| @ 范围 | 已配置角色 | 只能派给存在的角色；@user 触发桌面通知 |

**审批**：run 内工具审批以 `agent + task` 标记的 approval 帧送 GUI，复用既有 approval 通道；
超时按角色 `mode` 处理（ask：拒绝并失败任务；zen：自动通过）。
**@user**：作为提醒送达桌面通知（复用 Notifications 机制），并留在房间历史里。

**会话压缩与重置**：长活角色复用既有 context guard 与 `app/compact.py` 的压缩逻辑（把触发
逻辑从 `ChatController` 抽成可复用函数，runner 不持有 ChatController 的前端态）；新增
`swarm op=reset` 清空角色 session 并留一条房间系统消息。

**取消 / 重试**：`cancel` 对目标任务触发对应 run 的 `AbortToken`；`retry` 以原任务来源消息
复制一条新 task（`derivedFrom` 指向旧 id），不修改历史记录。

**崩溃恢复**：见 §7 对账；房间与任务日志是唯一真相，session JSONL 由既有机制保住。

## 9. 模块归属与装配

新包 `zenagent/app/swarm/`（app 层，与 `app/taiji/` 曾经的层级一致）：

| 文件 | 职责 |
|---|---|
| `roles.py` | manifest 解析/校验、角色发现、凭据占位替换、Runtime 装配输入 |
| `room.py` | room/tasks JSONL 读写、重放、索引、mention 解析、任务状态机 |
| `router.py` | 派活路由、inbox 管理、闸门（链深/链预算/队列/并发） |
| `runner.py` | 单任务 run 执行、Agent 事件→房间/任务/RPC 帧、终态映射、压缩触发 |
| `manager.py` | SwarmManager：角色生命周期、调度循环、取消/重试、启动对账 |
| `protocol.py` | 房间协议提示块、swarm 工具（`swarm_post`）定义 |

装配：

- `cli/rpc/host.py`：按需创建 `SwarmManager`（首次 swarm 请求或存在角色时），与 slot 注册表
  并列、互不占名额；host 关闭时先停 swarm（abort 在飞 run）。
- `cli/rpc/ops_swarm.py`：`type: "swarm"` mixin；帧带 `swarm: true` 与 `agent`/`task` id。
- 内核零改动：Agent/loop/Session/Runtime 全部按既有 API 使用。

## 10. RPC 面

`type: "swarm"`，`op` 如下：

| op | 载荷 | 结果 |
|---|---|---|
| `list` | `{since?}` | 角色 + agent 状态（idle/running/task）+ 任务 + 房间消息（分页） |
| `post` | `{text}` | 人类发言（可含 @），触发的 tasks 一并返回 |
| `cancel` | `{taskId}` | 取消任务（abort run） |
| `retry` | `{taskId}` | 复制任务重投 |
| `reset` | `{agentId}` | 清空该角色 session（留房间系统消息） |
| `roles` | `{op: list/create/update/delete, id?, manifest?}` | 角色 CRUD（写 manifest 目录） |
| `reload` | — | 重读角色目录（手工改文件的逃生口） |

帧：`swarm_message`（新消息）、`swarm_task`（状态转换）、`swarm_agent`（运行态变化）、
`swarm_error`（可报告错误，稳定 code）。审批沿用 `approval`/`ask` kind + `agent` 字段。

契约：`tests/test_rpc_contract.py` 与 `desktop/src/rpc/types.ts` 双向同步；`REQUEST_KINDS`
增加 `swarm`。

## 11. 桌面端

**模式切换器**（`ModeSwitcher.tsx`，sidebar 品牌行下方）：分段控件 Coding / ZEN / Swarm；
状态为 `workspaceMode`（settingsStore，持久化 prefs）。ZEN 选中显示预留占位页
（"由另一分支实现中"）。**这是与 zen 分支唯一共享的触点，保持组件与状态独立。**

**SwarmPage**（主区，替换会话区）：

- 房间消息流：角色头像 + 状态点、作者名、task 药丸（id/状态）、系统消息弱化样式；
  用户消息右对齐。自动滚底、向上分页。
- composer：`@` 触发角色自动补全（复用 `mentions.ts` 的触发检测，新增 agent token 类型）；
  发送即 `swarm post`。
- 左/右栏（v1 放右栏或页头抽屉）：agent roster（idle/running + 当前任务）、任务条
  （运行中/排队/失败，可取消、重试）、失败与 @user 通知。
- 角色管理：Settings 新增 Agents 目的地（或房间内抽屉）：列表 + 新建/编辑表单
  （prompt、skills 多选、MCP/插件多选、工具白名单、模型、预算、root、mode）。
- i18n：en 为准，中文目录跟进。
- e2e：dev-bridge fixture（mock 流）走通「派活 → 运行 → 报告 → 链式派活 → 取消 → 重试」。

## 12. 安全与边界

- 房间消息等同外部输入；agent 的每次工具调用照常走 PermissionGate，不因 swarm 放宽。
- 凭据只在 `settings.json`；manifest 只存占位；消息/任务日志/RPC 不回显 secret。
- 每个 agent 的 root 是其工具的 cwd 边界；不做沙箱（与现状一致），角色管理 UI 明示
  "带 bash/写文件能力的角色等同于本地代码执行"。默认工作区在 `~/.zenagent-workspaces/`，
  不豁免任何保护目录栅栏。
- 自主派活链由链深/链预算/并发三道结构闸门限制；`@` 只能指向已配置角色。
- 一个 run 的异常、超时、abort 只影响该任务（failed/cancelled），不影响其他 agent；
  MCP server 崩溃走既有失败隔离。

## 13. 测试策略

- 单测：mention 解析、任务状态机（含非法转换拒绝）、链深/链级预算/队列/并发闸门、manifest
  校验与凭据替换、日志重放与全量非终态对账、终态映射、角色 update/delete 生命周期。
- 集成：SwarmManager + fake `stream_fn`，双角色链式派活、取消、重试、预算耗尽、inbox 溢出、
  压缩触发、崩溃恢复。
- 契约/门禁：RPC 双向契约、layering、800 行预算、arch-gov。
- 桌面 e2e：mock 流；真实模型手工验收一次三角色协作。

## 14. 治理交付（与代码同批）

- arch-gov：`manifest.json` 新增 `app.swarm` 节点与边（→ agent、→ app、→ mcp、→ settings），
  scaffold 重新生成 brief/nodes/deps，更新架构图资产。
- CHANGELOG：架构图小节 + 功能小节。
- 用户文档：`docs/guide/swarm.md`（角色配置、房间协议、闸门说明、与 zen 模式的区别）。
- 内核 `PROMPT_VERSION` 不动（swarm 协议块在 app 层拼装，不碰内核 prompt）。

## 15. 留给实现者的细节

- JSONL 记录字段的最终形状与版本迁移策略；RPC payload 与 TS 类型的逐字段定义。
- Runner 与 Agent 事件的桥接（哪些事件转 `swarm_*` 帧、批处理/节流）。
- 审批帧路由到对应 agent/task 的实现；ask（结构化提问）在 unattended 下的语义。
- GUI 布局/样式细节、空态、长消息折叠；角色表单校验与错误文案。
- 默认值调优（链深 8、链预算 $5、inbox 16、并发 3）与每角色覆盖方式。
- 第二阶段 daemon 化的迁移路径（宿主替换点已在 §9 标注）。

## 16. 与 ZEN 模式的边界与合并约定

- 本分支只实现 coding 与 swarm；zen 位是占位页，不实现任何编排逻辑。
- 共享触点只有三处：`ModeSwitcher` 组件、`workspaceMode` 状态、App 主区路由分支。
- zen 分支合并时：替换占位页，新增自己的状态/页面/RPC；不改 swarm 包与 swarm RPC。
- 两套编排（swarm=对等房间，zen=taiji 式多 worker）共存于不同模式，避免概念混线；
  未来若合并抽象，在两者都落地后另立设计。

## 附：与既有系统的关系

- 复用：`Agent`/`agent_loop`/`Session`/`Runtime`、多 slot 后台运行与审批路由、
  plugin manifest 的 MCP 形状与 `${KEY}` 凭据替换、skills 发现、Notification、prefs。
- 不复用：taiji 的 Router/worker、worktree 编排、dispatch board（zen 模式领域）。
- 参照先例：`docs/superpowers/specs/2026-11-10-plugin-system-design.md`（声明式打包与装配）。
