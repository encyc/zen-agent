# Swarm Agent 定位 · 工单 · 自管理 · KPI 设计

日期：2026-11-12 · 状态：待审批 · 前置：`2026-11-11-project-scoped-swarm-design.md`（项目化房间）已落地

## 1. 意图与动因（why）

swarm 的产品主张是"一个人管理一堆专业化 Agent"。今天这个主张只兑现了一半：Agent 能按
manifest 特化、能在一个房间里互相 @ 干活，但它不是一个**可以被管理、被考核的对象**——
房间是一串互不相关的任务，没有"这件事办完了没有"，也没有"这个 Agent 这一年干得怎么样"。
人管人靠两件事：给他配资源，然后考核他。Agent 缺的正是后一件。

本设计把 Agent 定为与 human 同级的**被管理主体**，给它三样东西：

1. **名下的资产**：id、skills、knowbase、tools/MCP、预算。资产可被它自己升级。
2. **可续跑的活（工单）**：同一件事的多轮对话共享一份上下文，是上下文隔离单位和交付单位。
3. **绩效档案（KPI）**：跨工单、跨 session 的长期趋势线，只给人看。

工单不是新概念系统，它是把现有 `TaskRecord`（一次性运行记录）升级为"可续跑、拥有 session、
可打分"的对象，任务降级为工单里的一"轮"。上下文隔离、验收挂载点、KPI 样本单位，三者是同一
个对象的三个侧面，不需要三套机制。

## 2. 目标 / 非目标 / 反目标

**目标**

- 工单：一个工单一份上下文；B 被 @ 问一句回一句，仍在同一工单内，A 回来后接着干。
- 并行：同一 Agent 可同时参与多个工单，各自独立上下文（`(agent, 工单)` 是运行单元）。
- 工单可开、可续、可请求关闭、可打分、可归档；人与 Agent 都能开。
- Agent 自管理：ask 模式一律走提案（skill / knowbase / MCP / tools / 预算），zen 模式直接生效
  但留显式回执。**门禁由模式定，不由内容类型定**。
- knowbase：per-agent 知识库，跨项目随角色目录走。
- KPI：按 Agent 聚合的长期趋势（完成率、返工率、单工单成本、时长、人工分），**只给人看**。
- 管理页：工单面板（列表 + 详情 + 统计条）与 Agent 档案页（资产 + KPI）。

**非目标（v1 不做）**

- 工单依赖图（DAG）、优先级排序、跨工单依赖。
- Agent 读取自己的 KPI（见 §6.4 约束）。
- KPI 加权总分。
- 工单级 git 编排 / 合并门禁（zen 模式的领域）。
- 跨进程工单同步（宿主可替换性保留，daemon 仍是第二阶段）。

**反目标（做成这样即失败）**

- 工单只能由 Agent 隐式判定，人看不见它把两条活并成了一条。
- 门禁靠 prompt 自律：Agent 一次 `write_file` 就能改自己 manifest。
- KPI 变成门槛：撞到 `maxCost` 就记入档案、或按月配额停活。
- 引入第二套与房间平行的"工单对象"存储，导致消息流与工单流两处不一致。
- 改 `agent/` 内核、引入三方依赖、破坏既有门禁或层依赖。

## 3. 约束（既有规则，逐条钉死）

1. **`@` 仍是唯一路由原语**；`room.jsonl` / `tasks.jsonl` 仍是 append-only、同一 id 最后一条胜出的
   唯一事实源。新表沿用同一约定，不引入可变存储。
2. **不碰 `agent/` 内核**；swarm 全在 `app/swarm/`。
3. **单文件 ≤800 行，allowlist 只许降**。`app/swarm/manager.py` 已在 allowlist 顶格（851 行），
   本次新增逻辑**一律进新文件**；manager 的改动只做索引替换，并尽量把可移出的代码移出。
4. RPC 契约双向钉住：`tests/test_rpc_contract.py` ↔ `desktop/src/rpc/types.ts` + `types-swarm.ts`。
5. 每个 swarm 帧必须带 `swarm: true` 与 `project`；新帧同样。
6. secrets 只进 `~/.zenagent/settings.json`（0600）；manifest / 提案 / 回执只存 `${KEY}` 占位。
7. 凭据、KPI、打分永不出现在 Agent 可读的上下文里。
8. arch-gov：新节点/边回写 `docs/arch-gov/manifest.json`，CHANGELOG 写架构小节。

## 4. 方案选型（已在对话中定，不再讨论）

工单的承载方式，三条路里选**第三条**：

| 方案 | 做法 | 为什么不行 / 为什么行 |
|---|---|---|
| A. 工单是独立一等对象 | 新 `work_items.jsonl`，任务挂在工单下 | 语义最干净，改动面最大；等真需要跨工单依赖再上 |
| B. 把委派链升格为工单 | 复用 `chain_id` | **否决**：链的成本/深度是门禁（默认 5 美元、8 层），工单会咬自己的门禁 |
| **C. 工单是房间里的线程** | 复用现有 `ref` 引用成线程 + 一张很薄的侧表 | **选定**：复用已验证的引用与房间流，新增只有一个薄表 + 一个 session 键 |

否决的备选还有"只采集打回信号、不建工单"：它无法隔离上下文（本次需求的另一半）。

## 5. 设计

### 5.1 Agent 的资产（manifest 是唯一配置源）

位置不变：项目本地 `<root>/.zenagent/agents/<id>/`，全局模板库 `~/.zenagent/agents/<id>/`。

```
<root>/.zenagent/agents/<id>/
├── manifest.json     # 资产清单（唯一配置源）
├── prompt.md         # 人设
├── skills/           # "怎么做"：可执行手册，带 index，按需 read_skill
├── knowbase/         # "学到了什么"：事实与规则（新增）
├── proposals.jsonl   # 待批的自我升级提案（新增，机器本地，见 §5.4）
└── receipts.jsonl    # zen 模式自我升级回执（新增）
```

manifest 字段**全部沿用**，不新增必填键：

- `id` / `name` / `description` / `model` / `thinking` / `systemPromptFile`
- `skills`（角色目录内相对子目录）/ `plugins`
- `mcpServers`（`${KEY}` 占位，从机器 settings 解析，写盘永远留占位）
- `tools`（白名单，`null` = 全部）/ `mode`（plan / ask / zen）
- `root`（cwd）/ `inheritProject`（是否并入项目层上下文）
- `limits{maxTurns, maxCost}`

**`maxCost` 的语义钉死：单次任务的止损护栏，不是额度。** 一个 Agent 撞到它只是这次 run 被截断，
**不计入 KPI，也不导致它"不能干活"**。长期监控由 §5.6 的 KPI 承担，两者不共用数字。

**knowbase 与 skills 的边界**（两者走同样的写入门槛，见 §5.4）：

| | skills | knowbase |
|---|---|---|
| 内容 | 可执行的步骤/手册（"怎么做"） | 事实、约定、踩过的坑（"学到了什么"） |
| 形态 | 目录 + `SKILL.md`（frontmatter `name`/`description`） | `knowbase/*.md`（同 frontmatter，`frontmatter.py` 复用） |
| 注入 | index 进 system prompt，正文靠 `read_skill` | index 进 system prompt（同 12 KB 上限），正文靠 `read_knowbase` |
| 发现 | 需要在 manifest 的 `skills` 里显式列出子目录 | **按约定**：`knowbase/` 存在即启用，manifest 不新增键 |
| 归属 | 角色私有 + 插件贡献 | 角色私有，随角色目录跨项目 |

与现有项目 memory（`<root>/.zenagent/memory/`）的区别：memory 是**项目共享、人可见**的；
knowbase 是**单 Agent 私有**的，不参与项目记忆 UI，也不被别的角色读到。

### 5.2 工单（work item）

**身份**：工单是房间里的**线程**。根消息 + 所有引用它的消息（现有 `ref` 引用机制）构成线程；
工单自身的可变属性（标题、状态、分数）落一张薄表。这样工单既能被 UI 聚合成一条线，又不需要
在房间流之外再造一条平行的消息流。

新存储 `work_items.jsonl`（项目状态目录，append-only，同 id 最后一条胜出，与 tasks 同一套重放器）：

```json
{"type":"work_item","version":1,"id":"w_01H…","rootMessageId":"m_…","title":"修好上传超时",
 "openedBy":{"kind":"user","id":"user"},"openedAt":1762…,"state":"open",
 "score":null,"scoreNote":null,"closedAt":null,"mergedInto":null,"updatedAt":1762…}
```

- `state`：`open` → `review`（Agent 请求关闭）→ `closed`（人归档）。`review` 可被
  `open` 打回（人再加一轮），`closed` 允许后补 `score`（见 §5.6）。
- `title` 可变（追加一条同 id 记录即可），**解决 `ref` 只存文本快照带来的"工单不能改名"限制**。
- `mergedInto`：合并只影响记账与面板，**不影响上下文**（见下）。

**谁开工单**

- **人**：`@某角色` 且不引用任何消息 → 开新工单；引用某条消息 → 加入该消息所属工单。
- **Agent**：`@别人` **默认加入当前工单**（那是委派，不是新活）。要开新工单必须显式调用
  `work_open(reason)`，房间落一张 `work_opened` 卡片（谁开的、为什么算新活），人可一键
  `merge` 回去。

理由（记录决策动机）：合并错了的代价只有 Agent 自己看得见——它污染的是自己接下来要用的上下文，
而你只看到它答得怪。所以合并的决定必须暴露在人眼前。

**轮（round）**：`TaskRecord` 新增必填字段 `workItemId`。任务本身的状态机不变
（`queued → running → done | failed | cancelled`），它现在表示"工单里的一轮"。`derived_from`
（重试来源）继续用于计算返工。

**并行**：同一 Agent 可同时参与多个工单，`(agent, 工单)` 是运行单元。房间级并发仍由
`[swarm] max_concurrent` 的信号量兜住。

**`workItemId` 的可空性与新写入的强约束**：`TaskRecord.workItemId` 类型上是 `str | None`，因为旧
记录没有它（§5.8 不回写）。但**新写入一律带值**，唯一例外是"源任务本身没有工单"的延续：

- 人的 `@`（不引用）→ 开新工单，任务带该工单。
- 人的 `@` 引用某条消息 → 加入该消息所属工单；若该消息属于 ad-hoc 历史（无工单），任务的
  `workItemId` 也为 `None`，落回 ad-hoc 分组（ad-hoc 因此是"可继续追加、永不可打分"的分组）。
- `retry` 继承源任务的 `workItemId`（含 `None`）。
- Agent 的 `@` 继承其当前实例绑定的工单。

**链在工单边界处重基**（builder check 纠正）：今天的链继承是"任务继承来源消息所属任务的
`chain_id`"，若 Agent 在一条已花到 4.50 美元的链上开了新工单，新工单的第一轮就带着 depth+1 和
接近上限的 chainCost——正是 §4 否决方案 B 的理由。所以规则改成：

- **同一工单内**的 `@`：照旧继承 `chain_id`，深度/成本闸门继续生效（防乒乓、防烧钱），这是链的
  本意。
- **跨工单**（新工单的第一轮）：**重基**——新 `chain_id`、`chain_depth = 1`、`chain_cost = 0`。
- `TaskRecord` 增加 `runCost`（本轮自身花费，`runner._run_cost` 已有这个值），因为 `chainCost` 是
  累计快照，按轮求和会把所有祖先重复计入（builder check 抓到的算术错误）。

**工单的消息集合由记录派生，不靠 `ref` 闭包**（builder check 纠正）：今天 `_post_from` 从不写
`ref`，只有人的 composer 引用会写，所以"根消息 + 引用它的消息"作为线程来源在生产路径上几乎为空。
改为：工单的消息集合 = `tasks where workItemId == W` 的 `sourceMessageId` ∪ `resultMessageId`，
再加上根消息与几张卡片；`ref` 仍只表示"人引用了哪条消息"，用于人的续话与渲染，不承担线程归属。
好处是房间日志不加字段，面板是纯投影。

**`work_open(reason)` 的机械语义**：调用即开一个工单，根消息就是那张 `work_opened` 卡片本身
（卡片文本 = reason），并记录 `openedFrom`（调用者当前实例的工单 id，可为 `None`）。
工单在第一次有轮次之前处于 `open` 且零轮；连开两次返回同一个未使用的工单（幂等，避免空壳）；
零轮工单在面板上显示为"空工单"，可被 `merge` 或由人删除。一条消息 mention 多个角色时，
它们的任务都进这个新工单。

**已关闭的工单不能新增轮次**：引用已 `closed` 工单里的消息再 `@`，开一个**新**工单，记
`continuedFrom = 旧工单`；旧工单与它的分数都不动。这样 KPI 样本一旦定分就不再漂移。

**合并**：`merge(a → b)` 的默认目标是 `a.openedFrom`（Agent 开错时"并回去"就是并回它当时所在的
工单）；`openedFrom` 为 `None` 时由人在面板上选。

**合并的诚实边界**：`merge(a → b)` 只重指向记账，**不合并 session**——两条上下文已经分叉，
硬合等于伪造历史。被并掉的工单 session 归档只读。

### 5.3 每个 `(agent, 工单)` 一份 session

现在 `role_session_path(role_id)` 取该角色最新的一个 session 文件，所有工单共享——上下文不是
不连续，是"连续过头"。改为按 `(role_id, work_item_id)` 取：

```
~/.zenagent/projects/<key>/swarm/workitems/<wi>/agents/<id>/sessions/<stamp>.jsonl
```

- 同一工单内多轮复用同一文件（`resume`），所以"B 答完 A 回来接着干"天然连续。
- 不同工单互不可见，A 工单的上下文不会漏进 B 工单。
- `reset`（原有能力）改为按 `(agent, 工单)` 重置，只在工单内换新 session；档案（§5.6）不清。

**运行单元索引改造**（这是本设计代码改动的主体，全部是索引替换，不改语义）：

| 现有 | 改为 |
|---|---|
| `SwarmManager.agents: dict[str, SwarmAgent]` | `dict[tuple[str, str], SwarmAgent]`（`(role_id, work_item_id)`） |
| `self._running: dict[str, asyncio.Task]` | 键同上 |
| `_next_entry` 的 busy 判定（`task.assignee in busy`） | `(task.assignee, task.work_item_id) in busy` |
| `_inbox_sizes` 按 role 计 | 按 `(role, 工单)` 计 |
| `SwarmAgent.current_task` 单个 | 保持单个（一个实例只跑一轮） |
| `agents_state()` / `_emit_agent` | 多一个 `workItem` 字段 |
| approval 请求的 `agent` 字段 | 多一个 `workItem` 字段（回执要说清是哪个工单） |
| `_post_from` 找 `current_task` 推来源 | 实例已绑定工单，直接读实例上的 `work_item_id` |
| `_apply_role` / `delete_role` / `reload` | 对该 role 的**所有**实例生效 |

**成本控制（必须做，否则并行即爆炸）**：每个实例是一套 Runtime + 自己的 MCP 子进程，线性增长。
所以实例**懒建**（首轮任务时构建）、**空闲回收**（无在飞 run 后闲置 N 分钟关闭 runtime，
session 文件是事实源，下次任务 resume 重建）。N 走 `[swarm]` 全局段（新增键，缺省 10 分钟）。

**角色名册必须与运行实例解耦**（builder check 抓到的启动死锁）：今天 `roles()` 和 `role_ids` 都从
`self.agents`（已构建实例）派生，`start()` 还把所有角色预先建好。一旦改成懒建，启动时
`role_ids` 为空 → Router 静默丢掉每一个 `@role` → 工单永远开不起来。所以：

- `roles()` 与 `role_ids` 一律来自 `discover_roles(root)`（配置），与实例无关。@ 列表、Settings
  页、`supportsVision` 全部回到配置事实源。
- `agents_state()` 每个**已构建实例**一行（含 `workItem` 字段）；桌面侧栏按 `role.id` 分组渲染，
  一个角色一行，状态取其所有实例的聚合（任一 running 即 running），展开看各工单的实例。
  这样 React key 不重复，Settings 不出现重复条目。

### 5.4 门禁：按模式的自我升级

**规则只有一条**：`mode` 决定门槛，不由内容类型决定。

- `ask`：一切自我升级走**提案**。Agent 调用工具提出变更 → 落 `proposals.jsonl` → 房间落一张
  待批卡片（工具名 + 参数 + diff 预览）→ 人点批准才写盘 → 该角色重建 runtime 生效。
  未批准的提案一直挂着，可弃。
- `zen`：直接生效，但**必须留显式回执**：写盘 + 追加 `receipts.jsonl` + 房间落一张回执卡片
  （谁、改了什么、为什么、何时重建）+ 管理页时间线。zen 的定义是"风险分级全部执行"，
  所以这里不设拦截点；要拦就用 ask，这是模式语义的原意。
- `plan`：只读，提案工具直接拒绝（与 mutating 工具同等对待）。

**可自我升级的范围**（同一套工具，两档模式只是生效时机不同）：

| 目标 | 工具 | ask | zen |
|---|---|---|---|
| manifest 字段（skills 列表/plugins/mcpServers/tools/limits/model/thinking/mode） | `agent_config_edit(patch, reason)` | 提案 | 直接 + 回执 |
| 角色内 skill 文件（新增/改写/删除 `SKILL.md`） | `agent_skill_write(path, body, reason)` | 提案 | 直接 + 回执 |
| knowbase 条目 | `agent_knowbase_write(name, description, body, reason)` | 提案 | 直接 + 回执 |

**结构性前置：角色配置目录必须从角色可写范围里摘出去。** 今天不成立：
`role.root` 缺省是项目根，而配置在 `<root>/.zenagent/agents/<id>/`，`write_file` 一次就改完
manifest，门禁形同摆设。

**围栏范围按"配置目录"定，不按 `role.root` 定**（builder check 纠正）：`role.root` 可被 manifest
指到任意目录（`parse_role` 的 `root` 显式分支，缺省才是项目根/`~/.zenagent-workspaces/<id>`），
若按 `role.root` 锚定，自定义 root 的角色围栏就成了空转。所以围栏的受保护集合是：

1. `<项目>/.zenagent/agents/**` —— 当前项目的所有角色，不只写者自己（A 不能改 B 的 manifest）。
2. `~/.zenagent/agents/**` —— 全局模板库。**这条特别重要**：`zen` 模式会把 protected tier 降级为
   allow，于是一次 `write_file` 就能改掉"未来所有项目 copy 的模板"。

修法挂在**既有机制**上，不新造语义：`app/modes.py` 已明确 `zen` 下"explicit deny patterns win"，
但现有 `deny` 只 fnmatch **工具名**，所以要新增一种规则类型：**路径拒绝**（swarm run 专用，
`write_file` / `edit_file` 与常见 shell 写模式：`>`、`>>`、`cp`、`mv`、`sed -i` 指向上述目录）。
权限侧落点：`build_broker_for` 增加一个 swarm 专用的 path-deny 参数，由 `manager._build_agent`
注入，规则本体放新文件（`app/swarm/fence.py`）。

**诚实的残余风险与对账口径**（builder check 纠正了两处）：`role_signature` 只哈希 manifest 字段与
`skill_roots` 的**路径**，改写 `skills/x/SKILL.md` 或新增 `knowbase/*.md` 不会移动签名，所以"无回执
即报警"只对 manifest 有效。口径分开写：

- **manifest 级对账**：`reload` 时签名变化且无对应 proposal/receipt → 系统警告"配置被改过，无回执"。
- **文件级对账**：为每个角色在状态目录存一份**内容指纹**（`skills/**`、`knowbase/**` 的路径+内容
  摘要，只在 reload 时算），变化且无回执 → 同类警告。成本可接受（reload 不是热路径）；若后续
  觉得贵，降级为"只在打开档案页时算"。

两者合起来才对得上 §5.4 表里的三个目标。这是**篡改可见性**，不是安全边界——真正的边界是围栏，
而围栏对 shell 只能尽力。文档不宣称更强的东西。

**工具审批层级**（避免一次改动被问两遍）：三个自升级工具与 `work_open` / `work_request_close`
一律声明 `approval="read"`（它们写的是 zen 自己的提案/回执/工单表，不直接写用户工作区），
由 §5.4 的模式门禁单独决定"提案还是直接生效"。代价是 `plan` 模式不会自动拒绝它们——所以
`app/modes.py` 的 `assess` 需要一条新规则：**plan 模式下这三个自升级工具一律拒绝**（与 mutating
工具同等对待）。这是一条明确的、可测试的规则，不是靠 prompt。

**自升级补丁的白名单**（防自我提权）：`agent_config_edit` 只接受
`skills ⊆ 角色目录内的既有子目录`、`plugins`、`knowbase` 无关字段、`model`、`thinking`、
`mcpServers`（占位符形状校验）、`description`。**不接受** `mode`、`tools`、`limits`、`root`、
`inheritProject`、`id`——这四项会改变它自己的约束边界（尤其 `mode: ask → zen` 等于一次性
批准未来所有升级），只能由人在管理页改。工具对越界字段直接拒绝并说明原因。

### 5.5 工单生命周期与人的动作

- 开：人的 `@`（或 Agent 的 `work_open`）。
- 续：工单内任何 `@` 都是一轮；引用工单内消息 = 明确续在该工单（仅当该工单仍 `open`/`review`）。
- 请求关闭：Agent 调用 `work_request_close(note)` → 工单 `open → review`，房间落卡片
  "请求关闭，待验收"。
- 归档：人在面板上**打分 + 关闭**。**分数只有人能打，Agent 无任何打分/关闭工单的工具。**
- **不打分也能关**：`review → closed` 且 `score: null`，记为"未评"。趋势线只用已评样本并显示
  样本数，所以人偷懒不会把系统弄坏。
- 关闭之后仍可后补分数（append 一条同 id 记录）。

### 5.6 KPI（只给人看的趋势线）

**样本单位 = 工单**，全部来自 append-only 记录，不新增人工采集动作（除分数）：

| 指标 | 来源 | 口径 |
|---|---|---|
| 人工分 | `work_items.score` | 1–5 或空 |
| 轮数 | 该工单的 task 数 | 越少越顺（附注，不做主信号） |
| 返工率 | `derived_from` 非空的 task / 总 task | 主信号 |
| 完成率 | 终态 `done` / 全部终态 | 主信号 |
| 失败/取消 | `failed` / `cancelled` 计数 | 附注 |
| 单工单成本 | 该工单各轮 `runCost` 求和 | 附注（**不是** `maxCost`，也不能用 `chainCost` 求和——那是累计快照，会重复计入祖先） |
| 工单时长 | `openedAt → closedAt` | 附注（跨自然月不可比的活不混合统计） |

**ad-hoc 不进 KPI**：没有 `workItemId` 的历史任务在面板里单独一个分组可查，但不计入任何
Agent 的档案——它们早于工单概念，混进趋势线会把基线污染掉。这比"尽力折算"诚实。

**Agent 档案聚合**（跨工单、跨 session，**不受 `reset` 影响**）：

- 参与工单数、完成率、返工率、单工单中位 `runCost` 与时长。
- **平均分（仅已评样本）+ 已评样本数**。样本数永远与平均分同时显示。
- 趋势：按周/月分桶的折线（分数、返工率）。

**口径纪律**：主信号只有"人工分 + 返工率"，成本与时长降为附注，**不做加权总分**。加权出来的
分数看着专业但是假的：一页重构和一行修复的成本本来就不可比。

**Agent 不可见**：KPI 只走 RPC（面板/档案页），**不注册为 Agent 工具**，也不注入任何 prompt。

### 5.7 管理页

两处，都在现有壳里加，不新开窗口：

1. **工单面板**：swarm 侧栏新增一个 tab，与 roster 并列。左列工单列表（时间序，带状态徽标：
   进行中 / 待验收 / 已归档 / 未评）；选中后右侧是详情：轮次流（复用现有任务详情的事件流）、
   以及上表的数字；顶部一行跨工单统计条。
2. **Agent 档案页**：Settings → Swarm 的每个角色从"配置表单"扩展为"档案页"——上半资产清单
   （manifest 投影，可编辑）、下半 KPI 趋势、提案/回执时间线。

RPC 新增（名称为草案，builder 可调整细节）：`works` 请求族
`list | detail | score | close | reopen | merge | rename`；帧 `swarm_work`（工单记录变更）。
`swarm_task` 增 `workItemId` 与 `runCost`，`swarm_agent` 增 `workItem`。桌面 `types-swarm.ts` 同步。

**已关闭工单的轮次详情必须能离线还原**（builder check 抓到的最实际的一条）：今天的
`task_transcript` 依赖活的 agent（`agent.session.path`），而懒建 + 空闲回收意味着一份已归档
工单没有任何实例——详情面板会对它的每一轮都显示"记录已丢失"，正好废掉这个面板的主要用途。
所以新增一个**磁盘解析器**：给定 `(role_id, workItemId)` 与 task id，扫描该实例的 sessions 目录，
找到包含该任务的记录（`reset` 过的工单会有多个文件，必须逐个找，不能只取最新）。活的实例仍走
事件流；离线的走解析器，两者对 UI 同形。

### 5.8 迁移（不重写历史）

- `tasks.jsonl` / `room.jsonl` 旧记录没有 `workItemId` / `runCost`：**不回写**。投影时无
  `workItemId` 的 task 归入"临时活（ad-hoc）"分组，面板可看、可继续追加、**不可打分、不进 KPI**。
- 旧角色 session：保留为 ad-hoc 的 session；新工单一律新 session。旧上下文不搬。
- 新写入的记录一律带 `workItemId` / `work_item`；`runCost` 缺失时按 0 处理（旧记录）。

## 6. 我做的判断（复核这些，不必再问我）

1. knowbase 按约定启用（`knowbase/` 存在即用），不新增 manifest 键。
2. `proposals.jsonl` / `receipts.jsonl` 独立于 `work_items.jsonl`（一个是配置治理，一个是交付）。
3. zen 的回执 = 房间卡片 + 面板时间线 + `receipts.jsonl`，不改 zen 的模式语义。
4. Runtime 懒建 + 空闲回收（缺省 10 分钟），session 文件是事实源。
5. 未评分也能关闭工单，记为"未评"，趋势线只在已评样本上算并显示样本数。
6. KPI 不做加权总分；ad-hoc 不进 KPI。
7. `merge` 只重指向记账，不合并 session。
8. 已关闭工单不再接受新轮次：续话开新工单并记 `continuedFrom`。
9. 链在工单边界重基（新工单第一轮 = 新链），工单内照旧继承。
10. `workItemId` 类型可空，但新写入只有"ad-hoc 延续"一种情况为空。
11. 自升级补丁白名单排除 `mode` / `tools` / `limits` / `root` / `inheritProject` / `id`（防自我提权）。
12. 三个自升级工具声明 `approval="read"`，由模式门禁单独管；`plan` 模式在 `assess` 里显式拒绝。
13. 工单的消息集合由任务记录派生（`sourceMessageId ∪ resultMessageId`），不靠 `ref` 闭包。
14. `roles()` / `role_ids` 改由 `discover_roles` 供源，实例只影响 `agents_state()`。
15. 围栏按"配置目录"定（项目 agents 目录 + 全局库），不按 `role.root` 定。
16. 对账分 manifest 级与文件级（内容指纹），不宣称能覆盖任意 shell 写盘。

## 7. 留给 builder 的

- 新文件划分与命名（`app/swarm/workitems.py`、`fence.py`、离线 transcript 解析器、manager 的索引
  重构边界），并保证 `manager.py` ≤ 851 行（allowlist 只许降）。
- fence 在 `build_broker_for` 的确切形状（参数 vs 拒绝规则注入），以及 shell 写模式的匹配清单。
- 提案/回执/工单的 RPC op 与 frame 命名，桌面类型镜像。
- 空闲回收的计时器放哪（manager 的调度循环 vs 实例自己）。
- `Workshop`/面板的具体视觉分层与 i18n 文案键。
- 测试切分：新 `tests/test_swarm_workitems.py`、`tests/test_swarm_fence.py`，扩展 `test_swarm_*`
  与 `test_rpc_contract.py`，桌面 e2e。
