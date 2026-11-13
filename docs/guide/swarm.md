# 多角色 Swarm

Swarm 是 zen-agent 的**团队房间模式**：若干预先配置的角色（role）各自拥有独立的
manifest、session 和工作区，在同一个房间聊天室里接收任务、自主执行，并把结果发回房间。
任务靠 `@` 派发，没有自动回执——`@` 是唯一的路由原语；角色侧由 Swarm 调度器
按并发上限与队列执行，但不会替你决定该 @ 谁。

**房间按项目隔离**：每个项目有自己的房间、任务日志、角色 session 和角色配置；
`~/.zenagent/agents/` 是全局角色库，只作模板浏览与复制来源，不直接进任何房间。

桌面端 sidebar 顶部的 Coding / ZEN / Swarm 切换器进入这个模式：
**Coding** 是普通单会话；**Swarm** 是本文；**ZEN** 是另一种多 agent 形态
（[Zen 模式](zen-mode.md)：每项目一个 Router 统筹项目内 worker）。

## 与 coding / zen 的区别

| 模式 | 单位 | 协作方式 |
|---|---|---|
| Coding | 一个会话、一个 agent | 你和它直接对话 |
| Swarm | 每项目一个房间、多个角色 | 角色之间用 `@` 派活、回报；你也是房间里的一个成员 |
| ZEN | 每项目一个 Router 统筹项目内 worker | [Zen 模式](zen-mode.md)；与 swarm 共享三态切换器，fleet 与房间互不干扰 |

Swarm 不接入 IM，也不常驻后台守护进程：房间和任务的真相都在项目内的 JSONL 日志里，
运行发生在桌面宿主进程内。

## 角色（manifest）

角色有两层：**全局角色库** `~/.zenagent/agents/<id>/`（跨项目的模板）和
**项目角色** `<项目>/.zenagent/agents/<id>/`（只有该项目的房间能看见）。项目角色
可以提交进仓库、随代码走。房间或 Settings 里的「从角色库复制」把库里的角色拷成
一份项目角色；已在项目里的角色不会重复复制。旧版编辑器曾把库的计算默认值写回
manifest（`root` 指向库工作区、`inheritProject: false`），复制时会去掉这类遗留默认，
让副本落到项目根并继承项目上下文；显式固定的 `root` 与隔离选择则原样保留。
手工编辑这些目录与 GUI 管理完全等价。

每个角色是一个目录（两层结构相同）：

```
<项目>/.zenagent/agents/<id>/      # 或 ~/.zenagent/agents/<id>/（全局库）
├── manifest.json
├── prompt.md            # 人设正文（可选，也可在 manifest 里写 systemPrompt）
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
  "mcpServers": {
    "feishu": { "command": ["npx", "-y", "@larksuiteoapi/lark-mcp"], "env": { "APP_ID": "${APP_ID}" } }
  },
  "plugins": ["zen.feishu"],
  "tools": ["bash", "read_file", "write_file", "edit_file", "web_fetch"],
  "root": "/Users/mac/work/notes",
  "inheritProject": false,
  "limits": { "maxTurns": 30, "maxCost": 1.5 },
  "mode": "ask"
}
```

| 字段 | 缺省 | 说明 |
|---|---|---|
| `id` | 必填 | `[a-z0-9][a-z0-9._-]*`；目录名、房间里的 `@id` |
| `name` / `description` | id | 显示名与一句职责 |
| `model` / `thinking` | 沿用用户配置 | 模型与思考档位 |
| `systemPromptFile` | — | 相对角色目录的正文文件；GUI 保存 `systemPrompt` 时写入 `prompt.md` |
| `skills` | 空 | 只含角色目录内 skills + 引用插件贡献的 skills，不隐式继承全局 skills |
| `mcpServers` | 空 | 与 `mcp.json` 的 server 形状相同；`${KEY}` 从 `settings.json` 替换，缺 key 只跳过该 server |
| `plugins` | 空 | 引用已安装插件，继承其 MCP 与 skills；角色显式配置优先 |
| `tools` | 标准工具集 + swarm 工具 | 显式列表即白名单裁剪，在 Runtime 装配时执行，不靠提示词 |
| `root` | 项目角色缺省为项目根；全局库角色为 `~/.zenagent-workspaces/<id>/` | 角色工具的 cwd 边界；缺省目录自动创建，在 `~/.zenagent` 保护栅栏之外 |
| `inheritProject` | 项目角色 `true`，全局库角色 `false` | `false` 是隔离 profile：不继承项目层配置、记忆、skills、AGENTS.md |
| `limits` | 无 | `maxTurns` / `maxCost`，映射到既有 `AgentConfig` |
| `mode` | `ask` | `plan` 只读；`ask` 变更操作弹审批；`zen` 无人值守自动通过（灾难性命令仍拒绝） |

角色 session 落在 `~/.zenagent/projects/<项目>/swarm/agents/<id>/sessions/`，
**不进**普通项目会话列表。

桌面端角色入口有两个，管理的是**当前项目**的角色：**Swarm 模式左侧边栏的角色
面板**（「新建角色」旁即「从角色库复制」）和 **Settings → Swarm**。两者共用同一份
名册与同一个编辑器；Settings 是集中入口，除名册外还带项目上下文、角色凭据体检、
房间运行护栏（并发 / 链路深度 / 链路花销 / 空闲实例回收，写 `[swarm]`）以及全局库复制。侧边栏的
「配置」与「新建角色」打开完整表单：id、人设、
模型/思考、skills、plugins、MCP 服务器、工具白名单、root、inheritProject、mode、
轮数/成本上限。MCP 编辑框回显 manifest 里的 `${KEY}` 占位符，解析后的凭据不会回传给
界面；JSON 不合法时停留在表单并提示。项目角色手工编辑上面的文件与 GUI 管理完全等价；
全局库在 GUI 中只读（可手工编辑，作为模板）。

## 房间协议

房间是一串追加写入的消息：`~/.zenagent/projects/<项目>/swarm/room.jsonl`。

- **派活**：任何一条消息（来自人或 agent）里出现 `@role`，就为该角色创建一条
  `queued` 任务，链深 = 来源消息深度 + 1。未知的 `@xxx` 原样保留为文本，不报错。
- **文件引用**：`@"path"` 把一个工作区路径带进消息；它只作为文字上下文，不创建任务，
  也不会被当成角色。`@` 菜单在有查询时同时搜角色与工作区文件；文件一律写成引号形式，
  带空格的路径也自然包含在内。
- **执行**：每个角色一次只跑一个任务，全局并发上限 3，其余排队。注入给角色的是
  发件人、task id、链深、房间协议和消息正文，作为 user 消息进入它自己的 session。
- **回报**：run 结束时**最终文本自动作为该任务的结果发回房间**（`done` 的结构性兜底）。
  中途可用 `swarm_post` 工具发言或派活。
- **没有自动回执**：结果对谁有价值，就在最终文本里 `@` 谁；派活时写"完成后 @我"
  就能拿到回报。每个 `@` 都是一条新任务并消耗预算，不要随手多 @。
- **@you**：留在房间历史里，并作为桌面通知提醒你；它不创建任务。
- **入口**：桌面 composer 走宿主级 `supervisor op=dispatch`（`mode=swarm`），旧的
  `swarm op=post` 请求族仍然保留；两个入口在同一个 `SwarmManager.post` 上汇合，
  使用同一套 `@` 解析、闸门和任务表。派工回执只确认“已进房间”，真正的 run 身份
  由 runner 在开始执行时写进 run 账本。

任务记录在 `~/.zenagent/projects/<项目>/swarm/tasks.jsonl`，状态每次转换追加一行，最后一条胜出：

| 状态 | 含义 |
|---|---|
| `queued` / `running` | 排队 / 执行中 |
| `done` | 正常结束，结果已发回房间 |
| `failed` | `error` / `aborted` / `max_turns` / `max_cost` / `interrupted`，原因写入房间系统消息 |
| `cancelled` | 用户取消，或角色被删除（`role_removed`） |

链的可追溯性由 `sourceMessageId` + 消息里的 mentions 派生：任务由哪条消息产生、
产出哪条结果都能从日志还原。`chainId` 在链的第一跳生成，后续继承；`chainCost`
是该链累计花费。点击 Swarm 侧边栏任务列表的任意一行，会在**中间对话栏**打开该任务的
卡片（房间消息流让位，收起卡片后恢复）：任务 ID、创建者、链深与链上花费、来源消息、
失败/取消原因以及重试来源；侧边栏该行高亮，列表本身保持紧凑，再点一次或点「收起」关闭。
每次状态转换（含 `done` / `failed` / `cancelled`）都会以 `swarm_task` 帧实时推到界面。

卡片里的**执行过程**与 coding 用同一套 `nodesFromViews` 与工具卡/助手消息组件渲染
（工具调用及可展开的输入输出、markdown 正文、思考折叠）。任务运行期间，角色的事件经
`swarm_event` 帧实时推送，步骤边发生边流入面板（打字机文本、工具卡 running→ok/error）；
中途打开一个 running 任务就从打开时刻起流。结束后面板改读角色 session 的权威全量视图
（每个任务的注入提示以 `[swarm]` 开头并带 task id，后端按这两个提示切片），对账补全
中途错过的内容；房间消息流不会因为这些步骤变吵。角色会话被 `reset` 或压缩后旧任务会
显示「执行记录不可用」——完整原文仍在该角色目录下的 session JSONL 里。

## Composer 与 coding/zen 的对照

房间 composer 和 coding/zen 共用同一个 `ComposerPlate`：外壳、自动增高/拖拽改高、
Enter/⌘Enter、附件栏、菜单、审批/提问卡片和通知条都是同一套实现。差异只在房间
特有的语义上：

| 能力 | coding / zen | swarm |
|---|---|---|
| 发送 / 插话 / 停止 | 三态；跑着时输入非空变 steer，空输入变 stop | 发送；有运行中任务且输入为空时变 stop，Esc 同样停止全部 |
| `@` | 文件/目录 | 角色 + 工作区文件（文件自动写成 `@"path"`） |
| `/` | 全量命令 + skills | `/reload` `/reset` `/stop` `/help`；`/help` 打开命令表，`/reset` 先选角色 |
| 模型 | 可在 composer 里切 | 先 `@` 目标角色，再点模型 chip；写回目标角色 manifest，后续任务生效 |
| 模式 / 思考 | 可在 composer 里切 | 由角色 manifest 决定，只展示；到角色编辑器改 |
| 上下文仪表 | 有 | 无：房间是多 session，没有单一 context |
| 引用 / 发送预览 | 无 | 房间独有：引用消息作为下一条的上下文；预览会说明任务数、排队、图片与 vision |
| 待发送 / todo | `PendingDock` / `TodoDock` | 任务队列 / 工单面板 |

没有运行中的 turn，所以没有 steer；模型 chip 改的是目标角色的 manifest，不是一次性
覆盖，运行中的任务继续用旧模型，后续任务才生效。模式与思考档位仍归 manifest。
除此之外，空盘停止、`/help`、`/reset` 角色选择、目标模型信息和 `@` 文件引用都已经
补齐。

## 结构性闸门（不依赖提示词）

| 闸门 | 默认 | 行为 |
|---|---|---|
| 链深 | 8 | 超限的派活拒投，房间系统消息说明，任务不创建 |
| 每 run 预算 | 角色 `limits` | 超限以 `max_cost` / `max_turns` 结束 → failed |
| 链级预算 | $5 / 链 | 累计超限后拒绝新派活，防链式烧钱 |
| 每角色 inbox | 16 | 满则拒绝新派活并提醒，不静默堆积 |
| 全局并发 | 3 | 超出排队 |
| `@` 范围 | 已配置角色 | 只能派给存在的角色；`@you` 只触发桌面通知 |

工具审批复用既有审批通道，帧上带 `agent` 与 `task` 标记；超时 120 秒后按角色
`mode` 处理（`ask` 拒绝并失败任务，`zen` 自动通过）。`swarm_post` 是只读工具，
不弹审批。

## 重启、更新与删除

- **重启对账**：启动时全量重放房间与任务日志；所有非终态任务（`queued` / `running`）
  一律追加 `failed(interrupted)` 并留一条房间系统消息——绝不静默复活、绝不自动重跑。
  需要继续时用 `retry`（以原任务来源消息复制一条新任务，`derivedFrom` 指向旧 id）。
- **运行账本**：角色的每轮 run 另记一行到项目 `runs.jsonl`（`kind=swarm`，带
  `workItemId` / `taskId`），可用宿主级 `runs` 查询跨模式时间线；`tasks.jsonl` 仍是
  任务状态的权威，账本只记录 run 的归属与终态。角色 runtime 下次装配时，账本里该
  session 残留的 `running` 行会被收口为 `interrupted`。
- **角色更新**：运行中保存的 manifest 标记 pending-apply，在当前 run 结束后重建
  Runtime 生效；空闲时立即生效。
- **构建失败**：某个角色的 Runtime 装配失败（如模型不存在、MCP 启动异常）只跳过
  该角色，房间留一条系统消息说明原因；桌面端在房间顶部列出这些角色问题，
  角色栏的 **重载角色** 随时重读 manifest 并清掉已修复的问题。
- **角色删除**：取消其排队/运行任务（`cancelled(role_removed)`）并停掉 runtime；
  默认**保留** session 与工作区磁盘数据。
- **reset**：清空角色 session 并留一条房间系统消息，工作区文件不动。
- **取消**：对目标任务触发对应 run 的 `AbortToken`，终态为 `cancelled`。

## 项目隔离与旧房间

- **一个项目一个房间**：进入 swarm 或切换项目时，桌面按当前项目重新拉取
  `swarm list`；每个房间帧都带 `swarm: true` 与 `project`（房间根），只会应用到
  对应项目的房间。审批/提问 id 带 `<projectKey>:swarm:` 前缀，切到别的项目后
  仍能答到原房间。
- **常驻与回收**：房间在第一次请求时装配并启动；空闲房间按 LRU 回收，超出常驻
  预算（与并发槽位数一致）才关闭，有任务在跑（含等待审批）的房间永不被回收。
  房间内的 `(role, work item)` Runtime 另有 TTL 回收：无在飞 run、无排队轮次、
  无待应用 manifest 时，空闲超过 `[swarm] runtime_idle_s`（默认 600 秒，0 关闭）
  即关闭；session 文件是事实源，下一轮按需重建。
- **旧房间归档**：旧的全局房间留在 `~/.zenagent/swarm/`，**不导入**——它的任务
  不属于任何项目。一个尚无消息的项目房间首次打开时会提示旧房间已归档；
  从全局角色库复制需要的角色即可重新开始。

## 安全与边界

- 房间消息等同外部输入；每次工具调用照常走权限门禁，swarm 不放宽任何规则。
- 角色 Runtime 只装配 manifest 声明的 skills / MCP / plugins，不加载全局插件，
  也不继承全局 skills 或模板。
- 凭据只在 `~/.zenagent/settings.json`（0600）；manifest 只存 `${KEY}` 占位，
  日志、RPC 帧和房间消息都不回显 secret。
- 角色的 `root` 是它工具的 cwd 边界；默认工作区在 `~/.zenagent-workspaces/`，
  不豁免任何保护目录栅栏。没有沙箱——带 bash/写文件的角色等同于本地代码执行。
- 一个 run 的异常、超时或 abort 只影响该任务，不影响其他角色。

## 源码对照

| 关注点 | 模块 |
|---|---|
| manifest 解析 / 发现（项目角色 + 全局库）/ 凭据替换 | `zenagent/app/swarm/roles.py` |
| 房间与任务日志、状态机、启动对账 | `zenagent/app/swarm/room.py` |
| 项目房间的装配、帧打标、审批路由、空闲回收 | `zenagent/cli/rpc/ops_swarm.py` |
| `@` 解析与三道闸门 | `zenagent/app/swarm/router.py` |
| 房间协议文本、`swarm_post` 工具 | `zenagent/app/swarm/protocol.py` |
| 任务执行与终态映射 | `zenagent/app/swarm/runner.py` |
| 派工入口与模式策略 | `zenagent/app/dispatch.py`、`zenagent/app/agent_profile.py` |
| run 归属与终态写账 | `zenagent/app/run_wiring.py` |
| 任务执行过程切片（读角色 session） | `zenagent/app/swarm/transcript.py` |
| 角色运行时装配、调度、审批、生命周期 | `zenagent/app/swarm/manager.py` |
| 角色文件写回 / 删除 | `zenagent/app/swarm/roster.py` |
| RPC 请求族 | `zenagent/cli/rpc/ops_swarm.py` |
| 桌面房间与角色管理 | `desktop/src/ui/pages/RoomPage.tsx`、`desktop/src/ui/SwarmSidebar.tsx`、`AgentSettings.tsx` |
| 房间 composer | `desktop/src/ui/composer/RoomComposer.tsx`、`desktop/src/state/roomCommands.ts`、`desktop/src/state/roomPreview.ts` |
