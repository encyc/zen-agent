# 太极（Taiji）功能 Brief

> 代码快照：分支 `task/taiji` @ `54d38bd`（相对 `main` 38 文件 / +3958 行）。本 brief 是那支分支的现状说明，随分支一起合并。
> 设计稿：[specs/2026-11-07-taiji-design.md](superpowers/specs/2026-11-07-taiji-design.md)（D1–D7）。
> 实现计划：`docs/superpowers/plans/2026-11-07-taiji-p0-panel.md`、`...-p1-runtime.md`（在 `task/taiji` 分支上，尚未进 `main`）。

## 1. 一句话

**太极把"同时推进 N 个任务"从人的认知负担变成系统的实现细节**：人只跟一个 Router 会话说话，系统在底下并发跑多个绑各自 git 分支/worktree 的 worker 子进程，任务图 = git 图，整个并行态在右侧栏一张图里看得见。

核心恒等式：**任务 = 分支 = worktree = worker session**。不存在第二份会漂移的"任务状态"。

## 2. 用户视角

| 环节 | 现行为 |
|---|---|
| 进入 | 打开项目的 `taiji.jsonl` 会话（桌面右栏「太极」页签 = 这张图；发消息给 Router 会话 = 进太极模式）。无独立开关，切回普通会话即退出。 |
| 下任务 | 对 Router 说"把登录超时修一下，顺手补测试"。Router 调 `spawn_worker` → 建 `task/<slug>` 分支 + `~/.zenagent/worktrees/<project>/<slug>` + 起 `zen rpc --cwd <worktree> --yes` 子进程。 |
| 干活 | worker 在自己 worktree 里自由用全部工具、自行 commit。人完全看不到它的对话。 |
| 汇报 | 每个回合结束，taiji 往 Router 注入一行里程碑：`[worker task/login-fix · ok] <摘要≤200字> · 03:12`。 |
| 收尾 | zen 模式：门禁代码自动 `--no-ff` 合并进基分支、删 worktree、状态 `merged`，Router 只收汇报。ask/plan：停在验收问题"任务 task/x 完成，待验收：… 请说「合并 task/x」或提返工意见"。 |
| 看全局 | 右栏「太极」：泳道 rail + commit 行 + 分支徽章 + 该分支上的 session 标题 + 「你在这里」。 |

Router 的五个工具：`spawn_worker` / `message_worker` / `kill_worker` / `merge_worker` / `list_workers`。全部 `approval="read"`、`sequential`，**只在活跃会话是 taiji session 时组装进工具集**（`runtime.py::_recompose` → `taiji_tools_for`）。

## 3. 不变量（写进代码的硬约束）

1. **git-native**：任务状态唯一真相是 git 分支/worktree + 注册表；`taiji.json` 是运行时态不是历史（重启对着 `git worktree list` reconcile）。
2. **单一人机信道**：人只对 Router 说话；worker 永不与人对话。panel 上任何动作 = 给 Router 发一条消息。
3. **import DAG 单向**：`cli → app.taiji → app.runtime → agent`。`app/taiji` 不 import `cli`（`test_layering` 钉住；唯一允许例外是包入口 shim `zenagent/__main__.py`）。
4. **无守护进程**：宿主拥有整棵进程树，退出即全停；转录/分支/worktree 留盘，重进在既有 session 上续命。
5. **工作区神圣**：合并前置条件 = 基分支已在主 worktree checkout 且 `git_status().is_clean`；冲突即 `merge --abort`，降级为验收问题，绝不在用户树里留 MERGE_HEAD 或冲突标记。
6. **反目标已守住**：没有第二条控制信道、没有编排逻辑进 `desktop/`、没有数据库/常驻 daemon、内核核心对 taiji 零感知。

## 4. 已落地范围

### P1 编排层 `zenagent/app/taiji/`

| 模块 | 职责 | 关键点 |
|---|---|---|
| `session.py` | 项目级 taiji session 单例 + 宿主锁 | stem 固定 `taiji`，标题「太极 Router」；`taiji.lock` 写 pid，活 pid 拒绝第二宿主，死 pid 覆盖陈锁；拒绝时不动别人的锁文件 |
| `registry.py` | worker 注册表 | 原子写（mkstemp + `os.replace`）；`reconcile()`：worktree 消失且终态 → 删记录，否则 `interrupted`；冷启动 `running` → `interrupted` |
| `worker_client.py` | `zen rpc` JSONL 客户端 | 自增 `w{n}` id 关联 future；握手断言 `protocol_version == 2`；transport 可注入 → 测试用进程内回环，不 spawn 子进程 |
| `supervisor.py` | 进程单例，管一支 worker 舰队 | spawn/message/kill/merge/wait_idle/close；git 全走带超时 subprocess；spawn 半途失败回滚分支 + worktree + 记录；回合异常是"状态"不是崩溃 |
| `tools.py` | Router 五工具 + 会话门禁 | `taiji_tools_for(session_path, provider)` 纯函数；provider 惰性绑 `get_taiji(...)`，chat/mode 取自 Runtime |
| `snapshot.py` | 只读 P0 数据源 | `commit_graph`：`git log --all --date-order` + 自写泳道分配；`session_map`：按 worktree 的 project_key 归集 session 目录；git 失败返回空快照不抛 |

### 接线与协议

- `zenagent/__main__.py`：`python -m zenagent` 成为 worker spawn 的兜底命令（`which zen` 优先）。
- `zenagent/cli/rpc.py`：新增 `{"type":"taiji","op":"snapshot"}` 只读 op；rpc 启动时 `ensure_taiji_session` + `own_taiji_lock`，退出 `close_taiji()` + 释放锁；`runtime.taiji_chat = chat`。
- `zenagent/prompts/spec.py`：五个 `tool.*` block（900 字预算，`stable` cache class），不进标准工具黄金对齐集。
- `runtime.py`：`_recompose()` 条件组装 taiji 工具；`load_session`/恢复后也要 recompose（工具集随会话变）。

### P0 面板 `desktop/`

- `rightbar/TaijiPanel.tsx`：只读渲染（rail + 提交行 + 分支徽章 + session 标题 + 「你在这里」+ 刷新/空态/错误态，8s 超时）。
- `rightbar/taijiGraph.ts`：`laneColor` / `graphEdges` / `attachSessions` 三个纯函数（可单测）。
- 右栏新增「太极」页签 + 6 条 i18n 文案。

## 5. 关键流程走查

**spawn**：`_slug(name|prompt[:24])` → `_free_name()` 查注册表 **且** `git rev-parse --verify refs/heads/task/<n>`（分支留存于历史，光看注册表会撞车）→ `git branch task/<n> main` → `git worktree add ~/.zenagent/worktrees/<key>/<n>` → 记录 `running` → 后台 `_run_turn`。

**turn**：`RpcRunner.run_turn` 每回合新起一个 `zen rpc` 子进程、`session resume` 旧转录、`prompt` 等结果、`stop`。**回合级子进程而非长驻**——宿主重启的代价是再 spawn 一次，不是丢转录。

**合并门禁（LLM 不在关键路径）**：`_emit_turn` 在 `status == ok` 且 Router 模式 `zen` 时直接 `await merge()`；成功则 `merged`，不成功则进入验收问题分支。`ask` 只验收问题，`plan` 只汇报。显式 `merge_worker` 在任何模式都能合（模式只决定"不问就合"）。

**重启**：`TaijiSupervisor.__init__` → `registry.load` → `reconcile(git worktree list)` → 中断任务标 `interrupted`；`message_worker` 对 `interrupted` 记录 resume 同一 session 继续干。

**宿主互斥**：同一项目只允许一个进程持有 `taiji.lock`；拿不到锁的宿主只 `_note("taiji: 另一个宿主已持有本项目的太极锁")`，不创建 supervisor 的第二个实例（`get_taiji` 仍是进程单例）。

## 6. 验证证据

| 项 | 结果 |
|---|---|
| taiji 专项 | `pytest tests/test_taiji_*.py` → **55 passed**（8 文件，12.3s） |
| 全量门禁 | `pytest -q` → **2418 passed**（151.8s，Python 3.14.5） |
| 桌面单测 | `desktop/tests/taijiGraph.test.ts` 4 条（泳道配色稳定性、父子边与 merge 标记、session 绑到分支 tip、无当前会话时不高亮） |
| 桌面 e2e | `desktop/e2e/app.spec.ts` 2 条（页签列出 commit/分支/session；非仓库时空态） |
| 契约测试 | `test_layering` 更新（`__main__.py` 允许 import cli）；`test_prompts_tools` 覆盖新增 tool block |

测试覆盖的关键回归点：spawn 撞名后缀、半途失败回滚、回合失败绝不合并、主 worktree 脏/基分支偏移时不合并、宿主重启 reconcile、注册表原子写、坏锁/陈锁、session 门槛（普通会话看不到这五个工具）。

## 7. 与设计稿的偏差 + 已知缺口

按严重度排（本轮代码评审发现，未修）：

1. **`kill` 有竞态**（`supervisor.py::kill`）：只删 worktree + 记 `killed`，**不终止在跑的那个 `zen rpc` 子进程**；该回合结束后 `_run_turn` 会把 `record.status` 覆盖成 `ok`/`error`，甚至可能对一个已删 worktree 的回合判 `ok` 并触发自动合并。spec §4.1 要求"终止子进程"。现有测试（`test_kill_removes_worktree_keeps_branch`）只覆盖 worker 空闲时 kill。修法：per-record 持有 in-flight task/client 句柄，kill 先 cancel 再落状态，且 `_run_turn` 结束时对 `killed` 记录不改状态。
2. **基分支硬编码 `main`**（`spawn` 里 `git branch <b> main`，`WorkerRecord.base="main"`）。spec §4.1 要求记 spawn 时的基分支（面板/仓库可能跑在别的默认分支上）。修法：spawn 时读 `git symbolic-ref refs/remotes/origin/HEAD` 或当前分支，失败回落 `main`（现有测试 `test_spawn_on_a_repo_without_main_fails_cleanly` 把这个缺陷当成预期行为钉住了，改的时候要连它一起改）。
3. **worker 永远 `--yes`（zen）**（`worker_client.worker_command`）：因此 ask 模式下 worker 的工具审批**不会**经"转问卡片"上浮，注册表也没有 spec 里的 `blocked/question` 态。D4/§4.1 的完整人审通道是 P2 的活，现在是"worker 自己全权、人在合并口验收"。
4. **里程碑注入只在 rpc 宿主生效**：`runtime.taiji_chat` 只有 `cli/rpc.py` 赋值（`tools.py:216` 用 getattr 兜 None）。TUI/REPL/headless 里 Router 能开 worker、能自动合并，但**收不到汇报文本**。要么把 chat 在 `Runtime` 里统一接上，要么把这层能力显式标注为"需 rpc 宿主"。
5. **Router 轻模型 / worker 强模型未接**（spec §9 默认决定）：`RpcRunner` 不传 `--model`，worker 用宿主默认模型。配置面需要在 P2 补 `taiji.router_model` / `taiji.worker_model`。
6. **合并是 porcelain 而非 plumbing**：`git merge --no-ff` 直接在用户主 worktree 跑，靠"基分支已 checkout + 干净 + 失败 abort"三件套保护。满足"工作区神圣"，但没到 §4.1 说的"plumbing/临时索引式合并"（那是为了在脏树下也能合）。
7. **taiji session 是急初始化**：rpc 一启动就 `ensure_taiji_session`，D2 说的是"存量项目首次触碰时懒初始化"——现在每个项目目录都会多出一个 `taiji.jsonl`。
8. **RPC 只有 `snapshot` 一个 op**：taiji ops 命名空间（worker 列表/状态/事件流）是 P2，面板仍直连 P0 只读 op（计划里写明的过渡态）。面板也还没有 worker 状态 chip 和验收卡片。
9. **无并发上限、无健康监控**：spec 说本期"仅进程级健康监控"，实际只有"回合有结果 = 活着"；`worker` 卡死只能靠 `RpcRunner` 的 1800s 超时。
10. **无事件批处理窗口**：注入靠 `ChatController.submit` 排队天然合并（P1 简版，符合计划）。

## 8. 现状边界

- **可用面**：`zen rpc` 宿主（含桌面 GUI）→ 完整闭环。TUI/REPL/headless → 工具可用、无汇报注入（见缺口 4）。
- **TUI 太极视图**：未做（P3，spec Non-goal 本期）。
- **投机执行 / 语义 watchdog / 外部信道（IM、GitHub、CI）/ 多 Router / A2A / 云端持久**：明确 Non-goal，未动。

## 9. 后续（P2/P3）

- P2：ask/plan 验收卡片 + 人审事件汇聚（panel「等你」过滤器）、taiji ops 命名空间（worker list/status/event stream）、panel 从只读 op 切到 taiji ops。
- P2 顺带修 §7 的 1–5。
- P3：TUI 太极视图。

## 10. 手工验收脚本

```bash
# 1. 内核闭环（无 GUI）
cd <repo> && zen rpc --yes          # 宿主
# 会话切到 taiji：<project_dir>/sessions/taiji.jsonl
# 对它说：把一个 README 补上，名字叫 readme
#   期望：task/readme 分支出现、~/.zenagent/worktrees/<key>/readme 出现、
#         子进程干活、回合末收到里程碑、zen 模式下自动 no-ff 合并、worktree 消失
git log --oneline --graph --all -n 10
cat ~/.zenagent/projects/<key>/taiji.json     # status 应为 merged

# 2. 面板
# 桌面 → 右栏「工作面板」→ 页签菜单「太极」→ 看到泳道图 + task/readme 徽章
```

判定标准：git 图上出现 `taiji: merge task/readme`；主 worktree `git status` 全程干净；普通会话的工具列表里看不到 `spawn_worker`。
