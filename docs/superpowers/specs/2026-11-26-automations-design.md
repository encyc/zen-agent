# 自动化任务（automations）设计

日期：2026-11-26
状态：待批准

## 意图与为什么

给 zen 加一个**常驻调度器**：到一个时间点，在一个**固定的 session** 里自己起一轮 agent，
跑完的结论就追加在那个 session 里。

为什么现在做：今天 zen 的每一次开工，起点都是用户敲的那一行字。而"到点自己干活"这个需求是
真实的——用户本机 `/Users/mac/zhizu-auto` 已经有一份 25 条的 crontab（`setup_crontab.sh`），
每条都是「cron → python 脚本 → 日志文件」，跑完没人读、没人判断"这算不算异常"、没人主动来
说一句。缺的不是定时器，是定时器后面那个会读数字的人。

为什么是常驻进程而不是交给 launchd/crontab 起一次性进程：
**冷启动贵**（每轮都要重连 MCP、重扫 skills、重装配 runtime，`app/bootstrap.py` 那一串）、
**错过要能补跑**（睡眠/重启期间该跑的那几轮，醒来要能按 last_run 判断）、
**跑的时候要能看见**（不能只在事后翻日志）。

### 考察结论：omp（oh-my-pi）里没有这个功能

用户最初的意见是"其他 harness 都有了"。实测 `/Users/mac/oh-my-pi`（二进制名就叫 `omp`）：
**没有面向用户的定时/自动化功能**，没有 `cron` 表达式解析、没有 `/schedule` 命令；`cron`
这个词只出现在 mnemopi 的同义词表和内部 idle 计时器（压缩、memory consolidation）里。

它有一个极相近的东西值得作为参考实现：`python/robomp/`——外部常驻服务（FastAPI + sqlite
事件队列 + WorkerPool），webhook 进来后 `spawn omp --mode rpc`（`cwd` = 每个 issue 独立的
worktree，持久 `session_dir`，后续事件用 `--continue` 续同一份 JSONL），工具调用全量审计。

它那套复杂度 **九成来自它的处境**：公网收 GitHub webhook，所以要 HMAC 验签、要按
`X-GitHub-Delivery` 去重（webhook 会重放）、要跨容器把 PAT 隔开、要 pool 并行对付多个 issue。
我们一条都不需要：单用户、几十条 job、触发源是自己的钟而不是可能重放的网络事件。

**真正要抄的两条**：

1. **每轮续同一个 session**（robomp 用 `--continue`，我们用 `switch_session` 从盘重读）。
2. **无人值守 fail closed**。omp 的 `docs/approval-mode.md:63`：没有可交互 UI 时，任何需要
   prompt 的调用一律被拒绝；子 agent 跑 `yolo` 时授权边界是父级那一次 `task` 调用。而即使
   在 `yolo` 下，用户的 `deny` 依然绝对生效。

## 对话里已经定下的事

| 决定 | 谁定的 |
| --- | --- |
| 常驻服务（daemon），不是系统 cron 调一次性进程 | 用户 |
| 结论追加进**一个固定 session**；别的实例不抢占；用户也能进去接着聊 | 用户 |
| 无人值守**放开权限**（不问），初期用 `auto`，不做审批闸 | 用户 |
| 一个 job = 一段指令 + 一个工作目录 + 一张时间表 | 用户 |
| **一个项目一条 daemon**；没有 automation 的项目不装、不起 | 用户 |
| 最小实现：asyncio 循环 + 一个 JSON + session 追加；**不引入** sqlite / worker pool / worktree | 用户同意 |
| 调度器住在 daemon 里，一轮 = 系统在 session 里替你打一行字（方案 A） | 用户选 |
| **要做 UI**（桌面端）：看全部项目的 automation + 开关 + 新建；与 daemon 同期交付 | 用户选 |
| UI 要跨项目视角，但**不**需要跨项目的 daemon —— 见"UI"一节 | 本设计 |

## 目标

- 定义一条 automation（指令 + 时间表；工作目录 = 项目根），到点自动跑一轮，结果落在一个固定 session。
- 常驻：macOS 登录自起，Linux 用 systemd --user。**一项目一实例**，没有 automation 的项目不装。
- 无人值守可跑完：没有人在旁边点"允许"，也没有人会回答 `ask`。
- 可审计：每一轮在项目账本里有一行（kind=automation），含成本、时长、终止原因。
- 用户随时可以打开那个 session 读，或者接着聊；下一次定时轮会看到聊过的内容。
- 桌面端有一个界面：**列出所有项目的 automation**、开关、新建；能看最近一轮的状态与结论。

## 非目标

- 不做 5 段式 cron 表达式（v1 只有 `daily HH:MM` 和 `every N[m|h]`）。
- 不做事件触发（git hook / 文件监听 / webhook），只做时间触发。
- **不做多 job 并发。** 同一时刻只跑一个 automation，其余排队。
- **不做一条 daemon 管多个项目。** 跨项目视角由 UI / `list_all` 提供，不由 daemon 提供（见"桌面端界面"）。
- 不做 job 间的依赖编排（DAG）、不做重试退避策略（失败就是失败，下个周期再跑）。
- 不做沙箱/容器隔离。隔离靠已有的路径策略 + 工具的审批层级。
- TUI 的 `/automations` 面板不在本期（桌面端优先，见"UI"一节）。

## 反目标

- **不能变成"偶尔跑一次"**：如果错过了不补、或者跑失败了没有任何痕迹，这功能就是失败的。
- **不能悄悄花钱**：一轮跑飞了不能无人知晓。
- **不能让用户想聊的时候打不开那个 session**：常驻持有写租约就违背了这一条（见"运行序列"）。

## 约束（改代码前必须守住的既有契约）

1. **一个 session 只有一个写者**：`app/session_lease.py` 已实现进程内注册表 +
   `~/.zenagent/locks/<sha256(path)>.lock` 的 flock，冲突抛 `SessionLeaseBusy`。租约是
   **Runtime 级**（session setter 里 bind），不是 turn 级。
2. **`agent/` 与 `plugins/` 不得向上 import**（`tests/test_layering.py` 实测钉的是这两条）。
   `app/` 不 import `cli/` 是**约定**，那个文件并不检查它（builder 检查发现的）。所以新增的
   `app/automations*` 要自己守住这条线：argparse、进程入口、launchd/systemd 一律留在 `cli/`。
3. **所有会话写盘走 append-only JSONL**，截断的尾巴要能容忍（`agent/journal`、`app/records`）。
4. **账本 `kind` 实际上没有校验**：`app/runs.py:RUN_KINDS` 是个导出常量，全仓库没人读；
   `RunLedger.start(kind=...)` 存什么就是什么（原设计写错成"白名单"）。所以 `kind="automation"`
   直接可用，但**没有任何东西会拦住拼错的 kind**——要写测试钉住。
5. **`ask` 工具在没人回答时不阻塞**（`tools/ask.py` 已实现：返回"没有交互 UI，请按最保守的
   方案继续"）。自动化不额外造机制。
6. **配置的 `[tools] deny` 是全局 only**，项目里的 `.zenagent/config.toml` 不能放宽权限
   （`config.py`）。
7. **`Runtime.root` / `cwd` 在装配时定死**（`runtime.py:144-148`），`switch_session` 不换工作
   目录、只在跨项目时 warn（`runtime.py:400-416`）。这是"一项目一 daemon"的根因。

## 复用的既有机制（本功能几乎全是装配）

| 需要的东西 | 已有的实现 | 位置 |
| --- | --- | --- |
| 同一 session 不允许两个写者 | `acquire_session` / `bind_session_lease` / `release_session_lease` / `SessionLeaseBusy` | `app/session_lease.py` |
| 一轮 = 一次 agent 运行 | `ChatController.submit()` / `wait_for_idle()` / `abort()` / `busy` | `app/chat.py:263,685,514` |
| 从盘重读一个 session（拿到用户中途聊的内容） | `Runtime.switch_session()` → `records.open_session` + `session.load()` + `replace_messages` + `_recompose()` | `app/runtime.py:703` |
| 无人值守的权限档 | `Runtime.bootstrap(..., assume_yes=True)`：`resolve_mode` 抬成 `auto`，broker 不再拦 | `app/modes.py:68`, `app/bootstrap.py:130,385` |
| 运行记录（成本/时长/终止） | `RunRecorder` + `RunLedger` + `update_run_scope(kind=...)`（超时需要 `relabel` 口子） | `app/runs.py`, `app/run_wiring.py` |
| 机器级状态目录 | `project.zenagent_home()` / `project.project_dir()` | `project.py:57,96` |
| 铸一个新会话路径（不写文件） | `agent.session.unique_session_path(root=...)`，文件懒创建 | `agent/session.py:109` |
| 单实例锁的写法 | 同一套 flock（`_acquire_file_lock` 的语义） | `app/session_lease.py` |

## 方案：调度器住在 daemon 里，一轮就是"系统替你打一行字"

三条候选：**A** daemon 内调度 + 复用 `ChatController.submit()`；**B** daemon 只定时、每轮起
headless 子进程；**C** 寄生在已有 `zen rpc`（只有桌面开着才跑）。

选 **A**。理由：它是唯一让"定时"和"你手打一行字"走**完全同一条路**的选择——权限门、事件流、
持久化、可打断、账本全都白拿，而"结论在同一个 session 里、还能接着聊"天然成立。B 把一条路
拆成两条，两份状态要长期对齐（robomp 用 worktree + `--continue` 填的正是这个坑）。C 与"常驻"冲突。

## 组成

### 1. 定义与状态文件：按项目分

两者都放项目的机器级目录，和账本、会话作邻居：

```
~/.zenagent/projects/<key>/automations.json
~/.zenagent/projects/<key>/automations.state.json
```

- **一个项目一条 daemon**，`automations.json` 里所有 job 的 `cwd` 必须等于（或位于）这个项目根，
  `add` 时就校验。跨项目的 job 不支持——`Runtime` 的 `root`/`cwd` 在装配时定死
  （`app/runtime.py:144-148`），工具、`GuardPolicy`、config、MCP、skills、账本全挂在 `root` 上，
  而 `switch_session` 明确不换工作目录，只会吭一声"工具仍跑在 X"（`runtime.py:400-416`）。
  所以 job 定义里**不再需要 `cwd`**：它就是项目根。
- **默认没有 daemon。** 用户没有创建过任何 automation 的项目，`~/.zenagent/projects/<key>/`
  下没有 `automations.json`，也就不会装、不会起 daemon。`zen automations add` 是唯一的
  "这个项目要一条 daemon"的开关（`rm` 掉最后一条时提示可以 `zen daemon uninstall`）。
- 定义由 CLI 写（临时文件 + `os.replace` 原子替换），daemon 只读；状态只由 daemon 写（单写者）。

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "a_7c3f9e21",
      "name": "daily-report",
      "session": "20261126-104512.jsonl",
      "prompt": "跑 auto_email/order_daily_report.py，然后读它的输出……",
      "schedule": {"kind": "daily", "at": "09:00"},
      "timeout_s": 1800,
      "enabled": true,
      "created_at": 1764118800
    }
  ]
}
```

- `session` 是**该 job 出生时铸出的文件名**（`agent.session.unique_session_path(root=cwd)` 的 basename），
  存在这个项目的 sessions 目录里，不可变。用相对名而不是绝对路径：项目目录搬家后定义仍然有效。
  已存在的 path 由项目目录 + 它算出，**不需要 Runtime、不需要 API key** 就能算。
- zen 的会话本来就是懒的（`Runtime.use_new_session()` 的注释写着"文件只在第一条消息写入时
  出现"），所以 `add` 只铸路径、不造空文件。代价是**第一次运行之前那个 session 还不存在**；
  想提前进去聊，把那次的会话名写进 `session` 即可（`--session <name|path>`）。
- 定义里不放运行时状态（不放 last_run_at / last_status）：状态是另一个文件。
- `unrestricted` 不需要了：默认就是 `auto`（见"权限"）。

### 2. 状态文件：`automations.state.json`（同目录）

只由 daemon 写（单写者），原子替换。

```json
{"version": 1,
 "jobs": {"a_7c3f9e21": {
   "session": "20261126-104512.jsonl",
   "last_run_at": 1764118800,
   "last_status": "done",
   "last_skip": "session-busy",
   "last_error": "",
   "missed": 2
 }}}
```

为什么不用账本推导：**跳过**这类事件没有 run，账本里没有行；而 catch-up 需要 `last_run_at`。
分工是清楚的——**state = 调度视角**（何时触发、是否跳过、上一次的 run id），**账本 = 运行视角**
（成本、时长、termination）。两者不重复。`session` 也在这里存一份，是为了"会话文件被删后
重铸"能落地。

### 3. `app/automations.py`：纯逻辑

不 import argparse/textual，不建 Runtime。可离线单测。

- `Schedule`：`daily HH:MM` / `every N(m|h)` → `next_due(now, last_run_at) -> int`。
  用本地时间；不做 DST 特判（v1，见"不在 v1 里的"）。
- `resolve_session(root, name) -> Path`：项目 sessions 目录 + `automations.json` 里的相对名。
- `due_list(jobs, state, now) -> list[Due]`：谁该跑、以及 catch-up 判定。
- `wrap_prompt(job, run_number, now) -> str`：提示词包装（见下）。
- `JobStore` / `StateStore`：读、原子写、坏文件的降级（跳过该条 + warn，不让 daemon 崩）。

### 4. `app/automation_runner.py`：跑一轮

一条清晰的序列，每步失败都有确定的结果：

1. `wait_for_idle()`——daemon 串行，上一条没完不可能到这里。
2. `update_run_scope(runtime, kind="automation", label=job.name)`。
3. `await runtime.switch_session(resolve_session(root, job.session))`：
   - 成功 = 拿到该 session 的写租约 **并且** 从盘重读（用户中途在这个 session 里聊过的内容
     现在进了上下文）；
   - `SessionLeaseBusy` → **跳过这一轮**，state 记 `last_skip="session-busy"`，等下一个周期。
     这是"用户正在用这个 session"的唯一正确反应：不抢、不排队堆积。
4. `chat.submit(wrap_prompt(...))`；**超时用 watchdog 实现，不要用 `asyncio.wait_for`**：
   `wait_for` 超时会取消 `wait_for_idle()` 的 gather，从而取消 `_run` task 本身（`chat.py:278`
   那个 create_task），`_run` 的 `finally` 可能走不到 `self._task = None`，`busy` 就永远为真
   ——一个卡死且无痕的 daemon。正确做法是：起一个 sleep 到 `timeout_s` 的 watchdog task，到点
   调 `chat.abort()`（协作式停止，见既有契约 `状态 取消是协议状态不是异常`），然后**照常**
   `await chat.wait_for_idle()`；谁先到就用 `asyncio.wait([...])` 收尾，watchdog 一定要 cancel 掉。
5. 收尾时按终止原因补一行账本（见"账本"一节），因为 `RunRecorder` 只会写内核那套终止词表。
6. `release_session_lease(runtime)`——**运行结束立刻放租约**，用户随时能打开这个 session。
   daemon 的 Runtime 保持热（MCP、skills、catalog 都不重连），下一轮再靠第 3 步的
   `switch_session` 重新拿租约 + 重新读盘。这是"热进程 + 冷上下文"的组合，也是这个设计
   唯一不显然的地方：**热的是装配，不是 transcript**。
6. 账本由 `RunRecorder` 自动落行（`agent_start`/`agent_end`），daemon 只补 state。

### 5. `cli/daemon.py` 与 `cli/automationscmd.py`

```
zen automations add --name daily-report --daily 09:00 \
    --prompt "跑 auto_email/order_daily_report.py，然后……" [--timeout 30m]
zen automations list [--json]      # 定义 + 最近一轮（从账本读）
zen automations show <name>
zen automations rm <name>          # 只删定义；session 文件留着（人要能回看）
zen automations enable|disable <name>
zen automations run <name>         # 请求 daemon 立刻跑一轮
zen daemon [--foreground]          # 常驻循环
zen daemon install | uninstall     # macOS launchd / Linux systemd --user
zen daemon status
```

- 所有 `automations` 子命令都作用在**当前目录所属项目**上，和 `zen sessions` 同一套口径
  （`project.project_key` / `project.project_dir`）。`add` 校验当前目录就是项目根。
- `zen daemon` 用 flock 拿 `~/.zenagent/projects/<key>/daemon.lock`，拿不到就退出并说明已有
  实例在跑。锁按项目分——两个项目可以各有一条 daemon 同时活着。
- `SIGTERM`/`SIGINT` → 不打断正在跑的那一轮（等它 settle，最多再等一次），然后退出。
- **`zen automations run <name>` 不在 CLI 里直接跑。** 它往项目的 daemon 写一个请求文件
  （`automations.request.json`，原子替换），daemon 每 30 秒（以及每轮之间）检查一次并消费。
  理由：状态是 daemon 的单写者文件，CLI 直接跑会和 daemon 抢同一个 session，也会绕开
  "用户正开着这个 session"的租约判定。daemon 不在跑 → 明确报错，并给出 `zen daemon` 提示，
  而不是偷偷跑一轮然后让 state 和现实不一致。
- 入口在 `cli/main.py:429` 的子命令分派里加两个分支（`daemon`、`automations`，与
  `sessions`/`prompts` 同构，注意 `raw[0]` 的分派在 `main()` 开头）。

### 6. 常驻安装

- macOS：写 `~/Library/LaunchAgents/com.zenagent.daemon.plist`（`RunAtLoad` + `KeepAlive`），
  `launchctl bootstrap gui/$UID`。
- Linux：写 `~/.config/systemd/user/zenagent-daemon.service`（`Restart=on-failure`），
  `systemctl --user enable --now`。
- `uninstall` 反向操作，保留 `automations.json`（不删数据）。
- 路径必须写绝对路径（launchd/systemd 都没有用户的交互式 `PATH`）；解释器用
  `sys.executable`，在 `zen daemon install` 时确定并写进单元。

### 7. 桌面端界面

**这一节是一个反直觉的结论，先写在前面：UI 要"跨项目视角"，但**不需要**跨项目的 daemon。**

理由分两层，都是实测出来的：

1. **UI 本来就没有文件系统权限。** 桌面端只开一个 `zen rpc` host 子进程
   （`desktop/src/rpc/client.ts:118`），里面放若干个 resident slot，slot 是 `project open`
   才按需有的（`cli/rpc/host.py:405`）。它读文件只能走 `fs_read` 这类 op。所以"UI 直接 glob
   `~/.zenagent/projects/*/automations.json`"是不成立的——它必须经过 rpc 或经过一个既能被
   Python 写、也能被 Rust 读的地方。
2. **但那个地方可以是文件本身。** `automations.json` 是纯数据、无进程内状态，谁写都是原子替换
   （临时文件 + `os.replace`）。daemon **本来就必须**监视它的 mtime 才能感知"用户用 CLI 改了
   时间表"——所以 UI 写它，daemon 天然就看见了，一条通信线路都不用新造。

于是分工是：

| 文件 | 谁写 | 谁读 | UI 怎么用 |
| --- | --- | --- | --- |
| `projects/<key>/automations.json` | CLI / UI（rpc op） | daemon（按 mtime 热加载） | 读 + 写（开关、新建、删） |
| `projects/<key>/automations.state.json` | **只有 daemon** | UI（轮询 1–2s） | 只读：正在跑 / 上次结果 / next_due |
| `projects/<key>/automations.request.json` | CLI / UI | **只有 daemon**（消费后删除） | 写：`run <name>` 立刻跑一轮 |
| `projects/<key>/runs.jsonl` | 既有账本 | UI | 只读：历史轮次的成本/时长/终止原因 |

**这就是为什么一项目一 daemon 不会让 UI 变复杂**：N 条 daemon 对 UI 的成本是零——它读的是一份
文件清单，不是 N 个连接。上表里没有任何一列需要"跟哪个 daemon 说话"。

反过来，"一条 daemon 管所有项目"依然要为每个项目根各养一个常驻 Runtime（`Runtime.root` /
`cwd` / config / MCP / skills / `GuardPolicy` / 账本全在装配时绑死，`app/runtime.py:144-148`），
每个都带一套 MCP 连接和一次 skills 扫描。付出这个代价只为了让 UI 少读一个 glob ——**不划算**。

**rpc 侧新增一个 `automations` 域**（`cli/rpc/protocol/_automations.py` + `ops_automations.py`，
照 `ops_skills.py` 的 mixin 写法，在 `server.py:547` 那样的 `if kind == ...` 链里加一支）：

```
automations list_all                     # 跨项目：glob projects/*/automations.json
automations list        {root}           # 一个项目
automations add         {root, name, prompt, schedule, timeout_s?, session?}
automations update      {root, name, ...}   # 开关/改时间表都走它
automations rm          {root, name}
automations run         {root, name}       # 写 request 文件
```

**这六个 op 都不建 Runtime。** 和 `zen sessions` 同一条原则（`cli/sessionscmd.py` 的 docstring
写着"Kept free of Runtime/Agent construction so it works without an API key"）——所以 UI 可以在
一个**根本没打开过**的项目里创建 automation。`root` 由 UI 传（它的 `projects` 列表来自
`localStorage`，是用户注册过的项目目录）。

跨项目清单的拼装（`list_all`）用现成的东西：`project.projects_root()` 下 glob
`*/automations.json`，项目根和名字从同目录的 `meta.json` 读（`project.load_project_meta`），
再并进 `state.json` 和账本末行。**不新增索引文件**。

新会话的路径在 `add` 时铸（`agent.session.unique_session_path(root=...)`），**不建 Runtime、不写文件**
（会话本来就是懒创建的）。注意它的防重名依赖进程内的 `_RESERVED_PATHS`：跨进程（daemon 与
rpc 各铸一个）只靠时间戳 + `exists()` 去重，同一秒内并发 `add` 两条同名 job 有极小概率撞名。
解法很简单：`add` 时如果同一个项目里已有 job 用这个 `session` 名，就直接报错让人重新加一次。

UI 的形态（React 侧，`desktop/src/`）：

- 新增一个顶层面板（侧栏入口），列表按项目分组：每行 = 名字 / 时间表 / 下次到期 / 上次结果
  （`✓ ✗ −` 用 `app/runs.py:status_marks` 同一套字形）/ 上次成本。
- 行内开关 → `automations update`；"现在跑一次" → `automations run`；新建用表单
  （项目选择器 + 指令 textarea + 时间表 `daily HH:MM` / `every N`）。
- 点"上次结果"跳到那个 session（复用现有的 session 打开路径）。
- 轮询 `automations.state.json`（经 rpc 的 `fs_read` 或一个 `automations state` op），不要
  新开 websocket 通道——这一期只需要 1–2 秒的粒度。

### 8. `app/` 与 `cli/` 的边界（新增文件的落位）

```
app/automations.py          Schedule / Job / JobStore / StateStore / wrap_prompt   ← 纯逻辑，无 UI 无 Runtime
app/automation_runner.py    跑一轮（switch_session → submit → watchdog → relabel）  ← 用 Runtime/ChatController
cli/daemon.py               asyncio 循环、信号、锁、launchd/systemd 安装             ← 进程入口
cli/automationscmd.py       argparse 子命令                                        ← CLI
cli/rpc/ops_automations.py  rpc op 处理（复用 app/automations.py）                  ← 桌面通道
```

## 关键语义

| 问题 | 决定 |
| --- | --- |
| 时间表语法 | `daily HH:MM`｜`every N[m\|h]`，本地时间。两个必须二选一。 |
| 错过补跑 | daemon 启动/唤醒时，若 `now >= due` 且只错过 1 轮 → 现在跑；错过 ≥2 轮 → **只跑最近一轮**，state 记 `missed=N`。 |
| 上一轮还没完 | 不可能（串行），但如果 `submit` 因任何原因排队，就当下一轮处理。 |
| 刚 `add` 完的第一个周期 | **不立刻补跑**：没有 `last_run_at` 时，`next_due` 一律取"从现在起的下一个时间点"。理由：`add` 在 14:00 写一条 `daily 09:00`，那一轮"错过了"没意义，而现在就跑等于一次偷偷花钱。 |
| 用户正在这个 session 里 | 拿不到租约 → 跳过 + 记 `last_skip`，不抢、不排队。 |
| 多 job 同时到期 | 串行，按到期时刻排序依次跑；第二轮的延迟记进 state。 |
| 单轮预算 | `--timeout`（默认 30 分钟）wall-clock 硬超时 → `abort()` + 记 `failed/timeout`。**不做 token 上限**（内核已无 per-run 成本闸，`config.py:_TASK_RETIRED` 里 `max_cost` 已退役）；成本事后从账本读。 |
| 提示词包装 | 见下 |
| 投递 | **只有 session**。没有邮件/通知/webhook（用户明确要"就在这个 session 里回"）。可观测性 = `zen automations list` + UI 面板 + 账本 + `RunRecord.cost`。 |
| session 文件被删 / 直接被改坏 | 下一轮用 state 里（或定义里）的名字重铸一个路径、写进 state，空会话懒创建；定义不改。不静默失败。 |

### 提示词包装

```
[automation] daily-report · 第 3 次运行 · 2026-11-27 09:00

这个 session 由定时器写入。现在没有人在看，也没有人能回答你的提问（ask 会直接
返回"无交互 UI"）。做完把结论写成一段可以直接读的汇报：先一句话结论，再列异常
（按严重程度排序）；没有异常也要用一句话说明。

<link 原文指令>
```

包装由 `wrap_prompt()` 生成，不改用户的原文——它只加一层上下文，让模型知道自己在一个
无人值守的定时运行里，而不是在对话中。

## 权限：默认全放开（`auto`），边界照实写清

用户的决定：**初期不做无人值守的审批闸，直接 `auto`。** 这里必须把 `auto` 的真实边界写清楚，
因为它的名字容易让人以为"只是不问"：

- 非交互运行**不带** `assume_yes` 时，任何非只读工具一律拒绝（"non-interactive run without
  --yes"）——所以 `assume_yes` 是必需的，不是可选的放权。
- `assume_yes` 会把解析出的 mode 抬成 `auto`（`app/modes.py:68`）。`auto` 下 `confirm_risks`
  为 false，所以**确认档全部降级为放行**：工作目录外的写、破坏性 shell（`rm -rf`、git 历史重写）、
  **凭证路径的写**（`~/.ssh`、`~/.zenagent` 都放行）。
- 仍然生效的**拒绝**档（它们在 `assume_yes` 之前，无法被放开）：
  - **灾难性命令**（`dangerous_command_reason`）：需要显式 `--allow-dangerous` 才放行。
  - **`[tools] deny` 命中**：绝对生效（`config.py` 里 `[tools]` 是项目不可放宽的全局段）。
  - **凭证路径的读**（`protected`）：连 `auto` 都拦（`assess()` 的读分支不受 `confirm_risks`
    控制），非交互下等价拒绝。

所以 v1 的实际边界是：**工作目录内外都能写、破坏性命令照跑、凭证文件能改、凭证内容读不到、
`[tools] deny` 与灾难性命令仍被拒。** 这个边界要在 `zen automations add` 的输出里原样打印，
不要用"已放开权限"这种含糊话。

装配方式：`Runtime.bootstrap(..., assume_yes=True, gate=<PermissionBroker>, allow_missing_key=False)`
—— **不要绕开 `prepare()` 去手搓 `PermissionGate`**。builder 检查里指出过一个陷阱：`prepare()`
传下去的是 `PermissionBroker`，而 broker 的 `assume_yes` 只用来决定初始 mode，它的
"需要审批"分支读的是 `_confirm` 回调而不是 `assume_yes`；所以只要 mode 是 `auto`（`assume_yes`
已经保证），broker 就不会拦任何非拒绝档的东西，`_confirm` 不需要装。走 `prepare()` 还顺带拿到
`attach_run_recorder` 和 `ChatController`，缺了它们账本一行都不会有。

## 账本：需要给 daemon 一条窄口子

`RunRecorder` 用 `status_for_termination(event.termination)` 决定 `status`，而它只认内核的词表：
`aborted → aborted`、**未知字符串一律 → `done`**（`app/runs.py:112-121`）。于是"超时"要么被记成
`aborted`（丢掉了"是我们掐的"，与用户取消混在一起），要么没人写。
`RunRecord` 又是 `RunRecorder` 私有、跑完即被清掉（`runtime.current_run_id` 由 recorder 置空）。

方案：加一个公开的 `RunRecorder.relabel(status=..., termination=..., error=...)`，由 daemon 在
released 时调；它内部用账本既有的"同一个 run id 追加一条终态行、最后一行生效"语义
（`app/runs.py:109` 的注释就是这条）。**不改 `status_for_termination`**（它是共享的、有别的
调用者），也不碰 `RUN_KINDS`（它是个没人读的常量，`kind="automation"` 直接就能用——这点原设计
写错了）。超时时 `termination="timeout"`、`status="failed"`。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 常驻 daemon 持有写租约 → 用户打不开那个 session | 运行结束立刻 `release_session_lease`；只在轮次期间持有。 |
| 热进程的 transcript 陈旧 → 下一轮基于旧上下文写盘 | 每轮 `switch_session(job.session)` 强制从盘重读（已有机制）。 |
| 一轮跑飞烧钱 | wall-clock 超时 + 账本成本 + `zen automations list` 显示。 |
| 无人值守删错东西 / 改凭证 | v1 靠 `[tools] deny`（绝对生效）+ 灾难性命令拒绝；**已知缺口**：工作目录外的写、破坏性命令、凭证路径的写都放行（见"权限"）。后续版本可以加回 broker 的 confirm 回调把这三类挡掉，不需要改本设计的其余部分。 |
| daemon 死了没人知道 | launchd `KeepAlive` / systemd `Restart=on-failure`；`zen daemon status`；账本里会出现 `running` 僵尸行，`reconcile_session` 用租约探活收尾。 |
| 两个 daemon | 每个项目一把 `daemon.lock` flock，第二个直接退出；不同项目可以各一条。 |
| 没有 job 的项目也常驻 | 不安装、不启动。`automations.json` 不存在 = 这个项目不要 daemon。 |
| 升级后 daemon 跑的是旧代码 | `zen daemon install` 写的是 `sys.executable -m zenagent`；升级后 `zen daemon status` 显示版本不一致，提示 `zen daemon install` 重装。 |
| daemon bootstrap 失败（没有 API key / 项目目录没了） | **fail fast**：进程直接退出并打印原因，交给 launchd/systemd 的重启策略；不做"降级启动但每轮都失败"（那会让 `list` 显示一堆看不懂的错）。`allow_missing_key=False`。 |

## 不在 v1 里的（明确记账）

- **TUI 的 `/automations` 面板**：桌面端优先，TUI 只留 CLI 子命令。`app/automations.py` 是纯数据，
  以后加 TUI 面板不用改它。
- **cron 表达式**：只有 `daily HH:MM` 和 `every N(m|h)`。够了再上。
- **跳过/超时也往 session 正文留痕**：v1 只在 state 和账本留（`last_skip`/`last_error`/
  `zen automations list` 与 UI）。往 transcript 里塞一条非 AgentEvent 的行会影响会话回放、
  `Journal` 快照和 restore，代价大于收益。
- **DST 语义**：本地时间字面值，跳变日不追赶。
- **实时流式看正在跑的那一轮**：UI 只轮询 state（1–2s），要看细节点进那个 session。做真正的
  实时流（把 agent 事件转发给 UI）需要给 daemon 加一条推送通道，这一期不做。
- **UI 里"跑完通知我"**：不做系统通知；结论只在 session 和列表里。

## 验收标准

离线、不花 token（`uv run pytest -q` 级别）：

1. `Schedule.next_due`：daily 的跨午夜、every 的对齐、本地时间字面值。
2. `due_list`：错过 1 轮补跑、错过 3 轮只跑 1 次且 `missed=3`、disabled 不跑、future 不跑、
   **新建的 job（无 `last_run_at`）不立刻补跑**（下次到期才算第一次）。
3. `wrap_prompt`：包含 job 名、第 N 次、时间；原文一字不改地出现。
4. runner：用 fake stream 跑一轮 → session 文件里多出 user+assistant 两条；账本多出
   `kind="automation"`、`label=<job>`、`status="done"` 的一行。
5. runner：目标 session 被另一个 owner 持租约 → 不发请求、state 记 `session-busy`、
   下一个周期仍会尝试。
6. runner：超时 → `chat.abort()` 被调用、账本被 `relabel` 成 `status="failed"` +
   `termination="timeout"`，并且 `chat.busy` 最后是 `False`（防卡死的回归测试）
7. 权限：`assume_yes=True` 下 **`--allow-dangerous` 关着时** `rm -rf` 类命令仍被拒、
   `[tools] deny` 仍生效、凭证**读**仍被拒；同时断言工作目录外的写**是被放行的**
   （把这个边界钉住，别让后人以为是 bug）。
8. `zen automations add` 在项目根的校验：`cwd` 不是项目根时报错；`daily`/`every` 二选一。
9. `daemon.lock` 竞争：第二个 `zen daemon` 直接退出，不碰 state。
10. **UI 的 rpc 契约**：`automations list_all` 在没有任何 automation 的机器上返回空列表；
    在**没打开过的项目**里 `add` 成功（证明不建 Runtime）；`add` 只铸会话路径、不写文件；
    `update` 改 `enabled` 后 daemon 凭 mtime 重新加载（用两个进程模拟，或直接断言 reload 函数）。
11. `test_layering.py` 仍然通过（`app/automations*` 不 import cli/argparse——这是**约定**，
    该文件只钉 `agent/` 与 `plugins/`，别指望它守住）。
12. `scripts/check.sh` 全绿。
13. 桌面端：新面板的组件测试（列表按项目分组、开关调用 update、新建表单校验时间表），
    以及 `wire.gen.ts` 重新生成后 `tsc` 干净。

## 留给 builder 的决定

- 默认模型：用 `~/.zenagent/config.toml` 的 `default_model`（本机现在就是 `deepseek-flash`），
  还是给 automations 一个更便宜的角色档。建议**先沿用默认**，别新造角色。
- `zen daemon status` 的输出格式（要不要含"下一次到期时刻"）。
- 状态文件损坏（手改坏了、写了半截）时的降级：建议"跳过所有 job + 打印一条明确警告"，
  而不是清空重建（清空会把 `missed` 抹掉、可能触发一次意外补跑）。
- `zen automations rm` 掉最后一条之后，是否自动 `zen daemon uninstall`。建议只提示、不自动
  （卸载常驻服务不该是删一条配置的副作用）。
- **UI 轮询用 `fs_read` 还是新增一个 `automations state` op**：前者零新增，后者能给
  "next_due 已算好" + 更稳的 schema。建议前者先跑通，schema 稳了再抽 op。
- UI 面板放在哪个入口（侧栏新分组 / 设置里的一个页签 / 独立窗口）——取决于现有导航结构，
  实现时按 `desktop/src/ui/` 的既有模式定。
- `docs/explorer/src/modesData.ts` 里"账本 kind"的取值列表要加 `automation`。
- `docs/` 正文（architecture.md 的层图、commands 参考、desktop 的 rpc op 清单）要补一节。
- `wire.gen.ts` 的生成器要不要覆盖新 op（看它的生成来源是哪里）。
