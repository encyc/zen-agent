# 自动化任务：让 Agent 自己操作（automations tool）设计

日期：2026-11-26
状态：待批准
前置：`docs/superpowers/specs/2026-11-26-automations-design.md`（已实现的 daemon/CLI/rpc/UI）

## 意图与为什么

现在的入口都是**人去填表**：`zen automations add --daily 09:00 --prompt "…"`，或者桌面端那个
表单。但真实的使用顺序几乎总是反过来的——**你先在会话里跟 agent 聊，聊明白要做什么，
再说一句"那就每天早上九点跑一次"**。让用户从对话里跳出来、去另一个界面把刚才说清楚的事
重抄一遍成 CLI 参数，是这个功能目前最大的摩擦，而且抄的过程会丢掉对话里已经积累的上下文。

所以：给 Agent 一个 tool，让它在会话里**直接创建/查看/开关/删除/立刻运行**自动化任务。
人只负责说一句"就这样，每天九点"。

## 目标

- Agent 能在当前项目里创建 automation，指令直接用对话里已经谈定的内容。
- Agent 能查（列表 / 单条详情 / 上次结果 / 成本），能开能关，能删，能请求立刻跑一轮。
- 写操作在**有人看着的会话里**需要一次确认；无人值守路径（本来就是 `auto`）不受影响。
- 不需要新造任何存储或调度：tool 只是 `AutomationStore` 的另一个调用者。

## 非目标

- **不是** `zen automations` CLI 的替代品。CLI 继续存在（脚本、CI、无会话场景）。
- **不做**"task 子代理也能创建 automation"。子代理跑在 headless 下，是 `yolo`，让它静默
  创建定时任务等于把自己变成持久化后门。tool 只在**有一个人在场的 Runtime** 里装配。
- 不做跨项目的创建（只能操作当前 Runtime 的 `root`；这正是它不需要 `path` 参数的原因）。
- 不做 `install`/`uninstall`（写 launchd/systemd 单元是机器级副作用，留在 CLI）。
- 不改 daemon、不改 rpc、不改桌面端。

## 约束（改代码前必须守住的既有契约）

1. **`~/.zenagent` 是权限门的 `protected` 路径。** 所以 tool **不能有 `path`/`file` 参数**，
   也不能让模型指定写到哪个项目——全部从 builder 闭包进来的 `runtime.root` 推。
   （`tools/sessions.py` 的模块 docstring 已经把这条件写死了，照抄它的理由。）
2. **工具只声明审批层级，策略在内核之外。** 写操作声明 `approval="write"`：`ask` 模式下
   弹一次确认（显示要创建什么），`auto` 模式下直接放行，非交互且非 `auto` 时被拒并可读地
   告诉模型为什么。这是既有机制，不新造。
3. **`app/` 不认识 `cli/`。** tool 在 `tools/`（能力层），只能往下用
   `app/automations.py` 的纯逻辑；**不能 import `cli/daemon.py`**。需要的两件事——"daemon 在跑吗"
   和"请求立刻跑一轮"——要么下沉到 `app/`，要么 tool 里不依赖它们（见"设计"）。
4. **一个 session 一个 writer、原子写、单写者状态**：全部沿用，tool 不新增写者。
5. **tool 描述 ≤ 900 字符**（`TOOL_BUDGET_CHARS`），描述走 `zenagent/prompts/blocks/tools/*.md`，
   不写在 Python 字符串里。
6. **prompt golden 会变**：新增一个 builtin tool 会改动 `scripts/prompt_goldens.py` 的快照，
   必须一起更新（这是仓库既有的门禁惯例）。

## 设计

### 一个 tool，多个 action

不新增六个工具。模型面对的工具越少，选错的概率越低；而 automations 的所有操作共享同一个
对象（一条 job），天然适合一个 `action` 参数——和 `todo`、`memory` 的既有风格一致。

```
automations
  action: list | show | create | update | delete | run       (required)
  name: str              # update / delete / run / show 必填
  prompt: str            # create 必填；update 可选
  schedule: str          # create 必填；update 可选。 "daily 09:00" | "every 15m"
  timeout_s: int         # create/update 可选，默认 1800
  enabled: bool          # update 可选
```

- `approval="write"`：`create`/`update`/`delete`/`run` 都会改变机器状态。
  `list`/`show` 是读，但**同一个工具只能声明一个层级**，而把 `run`/`create` 标成 `read`
  等于绕开确认门。所以整个工具是 `write`——代价是 `list` 也要一次确认，这不可接受。
  → **拆两个工具**：
  - `automations`（`approval="read"`）：`action: list | show`
  - `automation_edit`（`approval="write"`）：`action: create | update | delete | run`
- 名字刻意的不同：模型在无确认模式下只会看到第一个可用。

### `run` 不跨层

tool **不**直接运行一轮，也不 import daemon。理由：daemon 持有 state 的单写者身份，
tool 绕过它去写 `automations.state.json` 就破坏了这个不变量。所以：

- daemon 在跑 → tool 写 `automations.request.json`（`app/automations.py` 里已有的
  `_atomic_write_json`），daemon 下一 tick 消费并跑一轮。
- daemon 没在跑 → tool 返回 `is_error=False` 的结果，正文里明确说"没有调度器在跑，
  已保存但不会自动执行；让用户跑 `zen daemon`"，**而不是**假装跑了一轮。

"daemon 在跑吗"的判断：`~/.zenagent/projects/<key>/daemon.lock` 存在且能 flock 到就说明
没在跑。这段逻辑目前只在 `cli/daemon.py::daemon_running`。按约束 3，tool 不能 import 它 →
**下沉**：把 `daemon_lock` / `daemon_running` / `_take_daemon_lock` / `_drop_daemon_lock`
移到 `app/automations.py`（纯文件+flock，本来就不属于 CLI），`cli/daemon.py` 从那里 import。
这是本设计唯一的搬迁，且不改变行为（现有测试跟着换 import 路径即可）。

### 创建时的 session

`create` 铸一个 `unique_session_path(root=runtime.root).name`，和 CLI 一致：只铸名字，不写文件。
会话是懒创建的，第一次真正跑起来时出现。tool 返回结果里带上这个 session 名，这样模型可以告诉
用户"结果会落在 <名字>"。

### 返回什么

人和模型都需要同一份事实，所以正文是给人读的短句列表，`details` 里是结构化行
（复用 `app/automations.py` 的 `Job`/`JobState`，以及账本里 `kind="automation"`、
`label=<name>` 的最新一行拿上次结果与成本）。

`create` 的正文必须包含三件足以让人判断的话：
1. 建成了什么（名字 + 时间表 + 下次大约什么时候跑）；
2. **无人值守时的权限边界**（照 CLI 的 `PERMISSION_NOTICE` 原文，不能只写"已放开"）；
3. **调度器是否在跑**（不在跑就说清楚要 `zen daemon`）。

### 装配位置

`tools/__init__.py::base_tools` 的 `session_root` 之后，用同一个 `root` 装配。和三个
session 工具一样是 per-Runtime 的（根是数据来源），所以**不进 `ALL_TOOLS`**，默认 `None`
时静态名册与既有的 prompt golden 不变（这条是 `base_tools` docstring 已经写明的模式）。

## 风险

| 风险 | 缓解 |
| --- | --- |
| 模型把用户的随口一说变成永久的定时任务 | `create` 是 `write` 层：交互会话里弹一次确认，显示的正是要创建的指令和时间表。 |
| 子代理偷偷创建 | 只在有人在场的 Runtime 装配；子代理的 `base_tools` 调用点不传 `root`（与 session 工具同一条路径）。 |
| 模型删错/停错 | `delete` 只删定义、**保留 session 文件**（与 CLI 一致），并在正文里说明"历史还在"。 |
| 模型声称"已经跑了一轮" | 没有 daemon 时明确报"未执行"，绝不假装。 |
| 描述超预算 | 两个描述各 ≤ 350 字符，合计远低于 900。 |
| prompt golden 破坏门禁 | 同步更新快照（Task 里作为独立一步）。 |

## 验收标准

1. `automations`（read）能 list 与 show，含上次状态与成本；没有 daemon 时也能用。
2. `automation_edit`（write）能 create / update / delete / run；四者都落到 `automations.json`。
3. **plan 模式下 `automation_edit` 被拒**（它是 write），`automations` 放行——用现有
   `PermissionGate` 断言，不经过模型。
4. `create` 返回正文含权限边界与"调度器未运行"提示；session 名只铸不写文件。
5. daemon 在跑时 `run` 写出请求文件；不在跑时**不写**且正文说明未执行。
6. 跨项目不可达：tool 没有 path 参数，且 `create` 只写 `runtime.root` 的那个项目。
7. 既有 3077 个 Python 测试 + prompt golden 全绿。

## 留给 builder 的决定

- `run` 的正文措辞（"已请求，下一 tick 执行" vs 带上预计延迟）。
- `list` 的默认条数与是否分页（一个项目几十条以内，本期不分页）。
- 是否在 `show` 里附带最近 3 条运行记录（本期只给最新一条）。
