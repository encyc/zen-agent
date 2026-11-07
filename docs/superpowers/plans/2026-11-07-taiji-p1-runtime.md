# Taiji P1 — Minimal Orchestration Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `app/taiji/` 编排层最小闭环：项目级 taiji session 单例、Router 的 worker 工具集、worker 以 `zen rpc` 子进程绑自动创建的 worktree 干活、里程碑事件注入 Router、zen 模式确定性合并。

**Architecture:** taiji 只在宿主 Runtime 的活跃会话是 taiji session（`taiji.jsonl`）时组合进去（工具按会话条件组装）。进程级单例 supervisor 持有 worker 注册表（持久化 `<project_dir>/taiji.json`），经一个仓库内自带的极简 JSONL client 拉起 `zen rpc --cwd <worktree>` 子进程，worker 回合结束把摘要经 `ChatController.submit` 注入 Router（排队即批处理）。worker 跑 zen 模式；合并门禁读 Router 会话模式：zen→安全合并、ask→注入验收问题、plan→只汇报。

**Tech Stack:** Python 3.11+ / asyncio / subprocess git / 既有 RpcServer（零新依赖）。

**Spec:** `docs/superpowers/specs/2026-11-07-taiji-design.md` — §3（分层铁律）、§4.1（生命周期）、§4.2（工具集）、§8 已定·1（(a) 生命周期 + 续命）、§9。

## Global Constraints

- 零新依赖；git 走 subprocess 带超时；注册表写盘原子（tempfile+replace，模式对齐 `.titles.json` 写法）。
- worker worktree 一律放 zenagent home：`~/.zenagent/worktrees/<project_key>/<slug>`（不污染用户 repo）；分支 `task/<slug>`，基分支默认 `main`（spawn 时记录）。
- import DAG：`app/taiji` 可 import `app/*`、`agent/*`，**禁止 import `cli/*`**（worker 是 subprocess 不是 import；对 RpcServer 的测试除外——测试代码不受限）。工具定义放 `zenagent/tools/taiji.py`（tools 包惯例）。
- (a) 生命周期：无常驻；rpc shutdown 调 `close_taiji()` 终止全部子进程；重启后从 `taiji.json` + `git worktree list` 重建，running → interrupted。
- 语义钉死：taiji session 文件名 stem 固定 `taiji`（`<sessions>/taiji.jsonl`），标题 `.titles.json` 里设「太极 Router」。
- **host 锁**（spec §8）：同一项目同时只允许一个宿主激活 taiji。`<project_dir>/taiji.lock` 用 `O_CREAT|O_EXCL` 创建并写入 pid；已存在且 pid 活着 → 拒绝激活并报错；pid 已死 → 视为陈锁覆盖。释放挂在 `close_taiji()`。

## Review Focus

1. **spawn 半途失败**（worktree 建好、子进程起不来）→ 回滚分支+worktree，注册表不留脏记录。→ Task 4 测试。
2. **Router 忙时 worker 事件到达** → ChatController 排队不丢、顺序保持。→ Task 5 测试（fake chat 记录 submit 序列）。
3. **zen 自动合并时主 worktree 脏 / 基分支偏移** → 绝不碰用户工作区，降级注入验收问题。→ Task 5 测试。
4. **宿主重启打断任务** → 重建标 interrupted；续命 = 在既有 session 上重拉。→ Task 2 + Task 4 测试。
5. **分支名撞车**（`task/<slug>` 已存在）→ `-2` 后缀，绝不复用/覆盖。→ Task 4 测试。
6. **worker 子进程中途死** → status=error、Router 收到通知、绝不触发合并。→ Task 5 测试。

---

### Task 1: taiji session 单例

**Files:**
- Create: `zenagent/app/taiji/__init__.py`（P0 已建则跳过；本任务往里加符号）
- Test: `tests/test_taiji_session.py`

**Interfaces:**
- Produces:
  - `TAIJI_SESSION_STEM = "taiji"`
  - `def taiji_session_path(sessions_dir: Path) -> Path`（= `sessions_dir / "taiji.jsonl"`）
  - `def ensure_taiji_session(sessions_dir: Path) -> Path`（不存在则创建：复用 `agent/session.py` 的头记录写法写首行；并在该目录 `.titles.json` 写入 stem→「太极 Router」，复用现有 title 写函数）
  - `def is_taiji_session(path: Path | None) -> bool`（stem 匹配）
  - `def acquire_taiji_lock(project_dir: Path) -> None`（O_CREAT|O_EXCL 写 pid；活 pid 占用 → raise `TaijiHostLocked`；死 pid → 覆盖陈锁）、`def release_taiji_lock(project_dir: Path) -> None`

- [ ] **Step 1: 失败测试** — tmp sessions 目录：首次 ensure 创建文件+头记录+标题；二次 ensure 返回同一路径不改内容；`is_taiji_session` 对普通时间戳 session 为 False；锁三态：无锁获取、活 pid（伪造当前进程 pid）拒绝、死 pid（pid 999999）覆盖。
- [ ] **Step 2: FAIL → Step 3: 实现 → Step 4: PASS → Step 5: Commit** `feat(taiji): per-project taiji session singleton`

### Task 2: worker 注册表

**Files:**
- Create: `zenagent/app/taiji/registry.py`
- Test: `tests/test_taiji_registry.py`

**Interfaces:**
- Produces:
  - `@dataclass WorkerRecord: name: str; branch: str; base: str; worktree: str; session: str = ""; status: str; created_at: float; updated_at: float`（status ∈ `running|ok|error|merged|interrupted|killed`）
  - `class TaijiRegistry: def load(self, path: Path) -> None; def save(self, path: Path) -> None`（原子）；`records: dict[str, WorkerRecord]`
  - `def reconcile(self, worktree_paths: list[str]) -> None` — load 后必调：worktree 消失 → interrupted；status==running → interrupted（冷启动语义）。

- [ ] **Step 1: 失败测试**（save/load 往返；reconcile 两个转移）→ **Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 Commit** `feat(taiji): worker registry with reconcile`

### Task 3: worker rpc client

**Files:**
- Create: `zenagent/app/taiji/worker_client.py`
- Test: `tests/test_taiji_worker_client.py`

**Interfaces:**
- Produces:
  ```
  class WorkerRpcClient:
      async def start(self, cwd: Path, *, on_event: Callable[[dict], None]) -> None   # hello 握手，断言 protocol_version==2
      async def resume(self, session_path: str) -> None
      async def prompt(self, text: str, *, timeout_s: float = 1800.0) -> dict          # 等最终 result 帧
      async def stop(self) -> None                                                      # terminate
  ```
- 实现：`asyncio.create_subprocess_exec` + 行泵任务；自增 id `w{n}` 关联 future；事件帧回调 `on_event`。
- **测试不 spawn 子进程**：对同一进程里的 `RpcServer` 直连——用其构造参数 `read_line/write_line` 接 asyncio 队列，client 的 stdin/stdout 侧用 transport stub 桥过去（进程内回环）。断言：握手、prompt→result 往返、on_event 收到 `tool_execution_start`。

- [ ] **Step 1: 失败测试（进程内回环）→ Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 Commit** `feat(taiji): in-process-testable rpc worker client`

### Task 4: supervisor（spawn/message/kill + git 操作 + 回滚）

**Files:**
- Create: `zenagent/app/taiji/supervisor.py`
- Test: `tests/test_taiji_supervisor.py`

**Interfaces:**
- Consumes: Task 2 registry、Task 3 client；`workspace.worktree_overview`、`git.git_status`。
- Produces:
  ```
  @dataclass TurnResult: status: str; summary: str; session_path: str
  class WorkerRunner(Protocol): async def run_turn(self, worktree, session_path: Path|None, prompt: str) -> TurnResult
  class RpcRunner:  # 包装 WorkerRpcClient（真实现）
  class TaijiSupervisor:
      def __init__(self, *, repo_root: Path, sessions_dir: Path, registry_path: Path,
                   runner: WorkerRunner, chat, mode_of: Callable[[], str])
      async def spawn(self, prompt: str, *, name: str | None = None) -> WorkerRecord
      async def message(self, name: str, text: str) -> WorkerRecord
      async def kill(self, name: str) -> WorkerRecord
      def list_workers(self) -> list[WorkerRecord]
      async def close(self) -> None
  def get_taiji(...) / async def close_taiji() -> None   # 进程单例，模式对齐 get_task_manager
  ```
- spawn 行为（钉死）：slug 取 name 或 prompt 前 24 字符（复用 `project._slug` 风格）；撞车 `-2`；`git branch task/<slug> main` → `git worktree add ~/.zenagent/worktrees/<project_key>/<slug> task/<slug>` → 注册 running → 后台跑 `runner.run_turn(...)`（首回合 prompt=任务原文）；回合结束更新 status/summary/session。**半途失败回滚**：删 worktree + 删分支 + 移除记录。kill：stop 子进程 + `git worktree remove --force` + status=killed（分支保留）。
- message：对既有 session 跑下一回合（resume 语义由 runner 承担，session_path 传入）。
- 测试全部用 StubRunner + tmp git repo（monkeypatch worktree 根到 tmp home），零子进程。

- [ ] **Step 1: 失败测试** — 四组：spawn 建分支+worktree+记录；撞车后缀；半途失败回滚（StubRunner.start 抛错）；kill 清理。→ **Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 Commit** `feat(taiji): supervisor with rollback`

### Task 5: 事件注入 + 确定性合并门禁

**Files:**
- Modify: `zenagent/app/taiji/supervisor.py`
- Test: `tests/test_taiji_supervisor.py`（追加）

**Interfaces:**
- Produces（在 supervisor 内）:
  - `def _summary(record, turn: TurnResult, duration_s: float) -> str` — 格式钉死：`[worker task/<name> · <status>] <summary≤200字> · <mm:ss>`
  - 回合结束：`await chat.submit(_summary(...))`；status==ok 且 `mode_of()=="zen"` → 自动 `merge(name)`。
  - `async def merge(self, name: str) -> bool`：plan → False（只汇报）；前置条件 = 基分支在主 worktree checkout 且 `git_status(main_root).is_clean` → `git -C main_root merge --no-ff task/<slug> -m "taiji: merge task/<slug>"` → 成功后 `git worktree remove` + status=merged，返回 True；zen 但条件不满足、或 ask → `chat.submit("任务 task/<slug> 完成，待验收：<summary>。请说「合并 task/<slug>」或提返工意见。")` 返回 False。
- chat 为 None（无宿主对话，纯测试）→ 跳过 submit，不炸。

- [ ] **Step 1: 失败测试** — fake chat 记录 submit 序列：ok+zen+干净 → 合并发生（main 前进、worktree 删、记录 merged）；zen+脏 → 只注入验收问题；plan → 无合并无问题；error → 通知且无合并；Router 忙语义（fake chat 内部排队断言顺序）。→ **Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 Commit** `feat(taiji): milestone injection and mode-gated merge`

### Task 6: Router 工具 + 组装门槛 + 模块入口

**Files:**
- Create: `zenagent/tools/taiji.py`
- Create: `zenagent/__main__.py`
- Modify: `zenagent/app/runtime.py`（`_recompose` 条件组装）、`zenagent/cli/rpc.py`（shutdown 处 `close_taiji()`）
- Test: `tests/test_taiji_tools.py`

**Interfaces:**
- Produces:
  - `def build_taiji_tools(supervisor_provider: Callable[[], TaijiSupervisor]) -> list[AgentTool]`：`spawn_worker(prompt, name?)` / `message_worker(name, text)` / `kill_worker(name)` / `merge_worker(name)` / `list_workers()`；全部 `approval="read"`、`execution_mode="sequential"`；schema/description 按 spec §4.2；`default_inactive=False`。
  - `def taiji_tools_for(session_path: Path | None, provider) -> list[AgentTool]`（纯门槛函数：`is_taiji_session` → tools else []）——Runtime `_recompose` 调它，supervisor_provider 惰性绑当前 cwd 的 `get_taiji`，chat/mode_of 接 Runtime 自己的 chat 与模式访问器（与 rpc `_status_result` 同源）。
  - `zenagent/__main__.py`：`from zenagent.cli.main import main` 后 `raise SystemExit(main())`（使 `python -m zenagent` 可作为 worker spawn 命令）。
- spawn 命令解析（RpcRunner 用，钉死优先级）：`shutil.which("zen")` → `[sys.executable, "-m", "zenagent"]`；args `["rpc","--cwd",wt,"--yes"]`。

- [ ] **Step 1: 失败测试** — 工具名/schema/审批级；`taiji_tools_for` 两种会话路径的门槛；`__main__` 冒烟（`uv run python -m zenagent --version` 输出版本行）。→ **Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 Commit** `feat(taiji): router worker tools with session-gated compose`

### Task 7: 集成验收环

**Files:**
- Test: `tests/test_taiji_loop.py`

**Interfaces:**
- Consumes: Task 1–6 全部。
- 场景（一个测试函数走完 zen 全自动环）：tmp git repo（main+1 commit）+ StubRunner（在 worktree 里写文件并 `git commit`，返回 ok）+ fake chat + `mode_of=lambda:"zen"`：`ensure_taiji_session` → `get_taiji(...)` → `spawn("加个 README")` → 等 turn → 断言：分支存在、worktree 在 monkeypatch 后的 home 下、chat 收到 summary、main 前进包含新 commit、worktree 已删、registry=merged。第二场景 ask 模式 → 验收问题注入、无合并。

- [ ] **Step 1: 写测试（先跑 FAIL）→ Step 2: 修跨模块问题直到 PASS → Step 3: Commit** `test(taiji): zen-mode end-to-end loop`
- [ ] **Step 4: 全量门禁** `uv run pytest -q` 全绿（含 2363 基线）。

### 收尾

两计划（P0+P1）各自门禁全绿后，向用户演示：desktop 打开太极 panel + headless 跑一遍 zen 环。
