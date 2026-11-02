# 项目记忆库（Project Memory）设计（2026-11-02）

状态：待审批
作者：Lead（与用户讨论后整理）
相关：`zenagent/project.py`、`zenagent/app/runtime.py`、`zenagent/agent/context_files.py`、`zenagent/tools/todo.py`

## 1. 意图与为什么

zenagent 每个会话都从零开始了解一个项目：AGENTS.md 提供的是**人写给 agent**的静态约定，
而 agent 在干活过程中自己发现的决策、坑、结论（"为什么弃用 requests 选 httpx"、
"测试必须走 `scripts/check.sh`"、"部署脚本是手写的别自动改"）随会话结束即丢失，
下次再撞一遍同样的墙。

目标：给每个项目一个**永久性、人类可读、可随 git 共享**的记忆库，存放一条条结构化的
notes；agent 在工作时自主读写，会话结束时异步沉淀，用户可随时查看与手动触发。
记忆不是越大越全越好——索引常驻、全文按需，是这个设计的核心取向。

## 2. 目标 / 非目标 / 反目标

**目标**
1. 记忆库位于 `<project-root>/.zenagent/memory/`；是否 commit 完全由用户 gitignore 决定，
   zenagent 不做任何共享/私有的判断或提示（用户已明确此决策）。
2. 两层结构：**current**（当前生效的条目，人可读 md）与 **journal**（append-only 变更日志，
   JSONL，主历史接口）。
3. 模型通过 `memory` 工具增删改查；工具调用本身就是"是否记忆"的判断，**不新增**
   loop 级前置判断层。
4. system prompt 注入**索引**（一行一条：id + 标题），全文靠 `memory read` 按需精读；
   索引预算恒定，不随条目数增长。
5. turn 结束后**带门的异步摘要**（subagent 提炼），绝不阻塞用户交互。
6. `/memory` 命令：查看当前条目、手动触发整理、（TUI/REPL 两个前端）。

**非目标**
- 不做跨项目记忆、不做全局用户记忆（一期）。
- 不做记忆的自动过期/衰减算法；条目由模型/用户显式增删。
- 不引入 sqlite（见 §3）；不做 embedding/向量检索。
- 不做记忆的 UI 管理面板（`/memory` 文本视图即可）。

**反目标（出现即失败）**
- 记忆写操作污染 prompt（把全文塞进 system prompt）。
- 每次 turn 结束都产生"为写而写"的噪音记忆或可感知的卡顿。
- 记忆文件格式只有 zenagent 能读（必须是 `cat`/`grep`/git diff 友好的纯文本）。

## 3. 方案取舍（已决策）

### sqlite vs md/JSONL → **md + JSONL**
`sqlite3` 虽是标准库（pip 依赖零增加），但：二进制文件毁掉 git diff/PR review/手工合并，
而用户明确要 git 共享（gitignore 自决）；不可 `cat`/`grep` 违背"查看方便"。
sqlite 在本仓的诚实候选是**跨会话 FTS5 搜索索引**与 **usage 聚合**——均为机器本地、
不进 git 的场景，等真实痛点出现再立项。**本功能不引入。**

### 历史层：journal（主）+ git（附赠）
- journal（`memory/journal.jsonl`，append-only）：每行一个事件
  `{ts, op, id, title?, reason?, session?}`，自包含、不依赖 git、字段化可读。
- git 历史：随 commit 免费获得持久化与团队审计（`git log -p -- .zenagent/memory/` 按
  path 过滤，与仓库整体大小无关），但**不做查询入口**：未提交变更、非 git 项目、
  "为什么改"的语义都在 journal 里，diff 只回答"改了什么"。

### 文件粒度：一条一文件
`memory/entries/<id>.md`（id 为短随机串，如 `a3f2`），frontmatter 存
`id/title/created/updated/source`，正文为自由 markdown。目录即 current 层的 source of
truth，**不维护单独的索引文件**（prompt 注入时现扫目录，避免缓存失效问题）；
单条删除=删文件，git diff 最干净。全库条目格式统一便于 search 扫描。

### 注入策略：索引式（从第一天）
对齐 `context_files` 的做法：`PromptContext` 新增 `memory_index` 字段 + 新 prompt block；
条目为空时块整体消失（该行模板消失，同 `context_files` 行为）。索引上限
**64 条**（超出按 `updated` 最新优先截断，并在块尾提示模型用 `memory search` 检索更多）；
单条标题行超长截断。正文**永不**进 prompt。

### 三个触发点（全部要做，分三期，见 §6）
1. 模型自主调用 `memory` 工具（一期）。
2. turn 结束带门异步摘要（二期）："强制每轮"软化为带门——本轮打过 checkpoint、或
   assistant 写过 ≥3 个文件、或本轮用户显式要求记住时才触发；摘要由 subagent 完成，
   **subagent 保持只读**（复用现有 task 子代理机制），产出候选条目（结构化输出），
   **由宿主侧 runtime 校验后应用到存储层**——子代理不持有写工具，落库发生在
   `agent_end` 事件订阅里（与 transcript persist 订阅同机制），天然异步、零阻塞。
   提供设置项整体关闭（kill switch）。
3. `/memory` 命令（三期；其中"手动触发整理"依赖二期落地）。

## 4. `memory` 工具

- 动作：`read`（id → 全文）、`search`（关键词 → id+标题+命中行摘录，stdlib 子串/正则，
  兜底与维护用：写入前去重、正文匹配、索引溢出后的长尾触达）、`add`（标题+正文 → 新
  id）、`update`（id + 新标题/正文）、`delete`（id）。`list` 不设——索引已常驻 prompt。
- 路径安全：所有写操作限定在 `.zenagent/memory/` 内（拒绝逃逸路径），与 bash 工具的
  cwd 围栏风格一致；`add/update/delete` 走 **write 审批档**，`read/search` 走 read 档。
- journal：每个变更动作追加一行事件（含 reason 参数，模型可留空）。
- description 写明使用政策：先 search 去重再 add；宁缺毋滥；决策记"为什么"而不只是"是什么"。
- 注册方式：常驻注册（同 `lsp` 的无条件注册），不因目录不存在而隐藏；目录不存在时
  动作**自动创建**（`read/search` 对空库返回空结果，`add` 建目录后写入）。

## 5. 存储布局

```
<project-root>/.zenagent/memory/
  journal.jsonl          # append-only 事件流（主历史接口）
  entries/
    a3f2.md              # frontmatter: id/title/created/updated/source + 自由正文
```

已知取舍：团队共享时两人并发写靠 git 合并语义解决（journal 行级冲突罕见；条目文件
冲突由用户在 git 中解决）——一期不做任何锁或 CRDT。

## 6. 里程碑

- **M1 核心闭环**：存储层（entries+journal 读写、原子写、frontmatter 解析）→ `memory`
  工具 → prompt 索引注入（PromptContext + block + PROMPT_VERSION bump + prompts
  CHANGELOG 条目）→ 单测 + 真 prompt goldens。验收：新会话能带着上会话记的条目工作。
- **M2 异步摘要**：turn 结束钩子（门控）→ subagent 提炼 → 落库 + journal + 轻量 UI
  提示。验收：无感（零阻塞）、有门（无噪音轮）、可关（设置项）。
- **M3 `/memory` 命令**：REPL + TUI 文本视图（列表/单条/触发整理）。

## 7. Builder check 结论（2026-11-02，Lead 逐条核对源码后回填）

核对属实的声明：context_files 32KB 上限与块条件渲染（`compose_system` 中
`include_context and context_text.strip()` 才追加块——memory 索引块照此模式，空则整块
消失）；`_transform_context`/`_recompose` 挂点；todo sidecar 原子替换；registry
hidden/default_inactive；PROMPT_VERSION + prompts/CHANGELOG 流程；`zen` 入口；
`github` 条件注册 / `lsp` 常驻注册的对照成立。

核对中修正/拍板的点：
- ~~常驻注册"同 lsp"~~ → 表述修正：lsp 注册无条件，但**动作在 server 缺失时报错**；
  memory 工具同样常驻注册，动作在目录缺失时自动创建（首次 add）而非报错——比原稿
  "报错并提示"更顺（修正已写入 §4）。
- **审批档**（已核对 fs.py/lsp.py/bash.py）：read/write/exec 三档真实存在，
  read/search→`read`、add/update/delete→`write` 与机制一致，无需改动。
- **turn-end 钩子**（已核对 runtime）：不存在现成的 after-turn 抽象；但有
  `agent.subscribe(persist)` 在 `agent_end` 事件上落 transcript 的先例——二期摘要
  挂同一事件的第二个订阅，属于新增订阅点而非新抽象，风险可控。
- **frontmatter**：拍板为 stdlib 手写极简解析器（只支持 `key: value` 行，遇到空行即
  停），不引第三方库；解析失败视为正文处理并告警。
- **索引块缓存类**：拍板 `cache_class="session"`（同 extensions 块），标题变更每会话
  至多重算一次 prompt 块，成本可忽略。
