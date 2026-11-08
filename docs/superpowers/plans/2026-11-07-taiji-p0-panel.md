# Taiji P0 — Read-Only Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only "太极" panel in the desktop right sidebar: the repo's commit graph (GitLens-style lane rail) with zen sessions overlaid on their branches, fed by one new read-only RPC op.

**Architecture:** Python gains `zenagent/app/taiji/snapshot.py` producing a combined snapshot (commit graph + cross-worktree session map), exposed as a new RPC kind `taiji` with op `snapshot`. Desktop adds a `TaijiPanel` registered in the existing right-panel tab system, fetching once on mount (WorktreePanel pattern). No orchestration, no taiji session, no writes.

**Tech Stack:** Python 3.11+ / asyncio / subprocess git (no new deps); React 18 + zustand + CSS Modules + design tokens; node:test units; Playwright e2e (real shell + fake Tauri bridge).

**Spec:** `docs/superpowers/specs/2026-11-07-taiji-design.md` — §4.3 (panel 数据源), §7 (P0), D2 (UI 载体)。注：P0 阶段 panel 挂在右栏供所有会话使用；"只住在 taiji session 右边栏"的门槛在 P1/P2 落地（spec D2 描述的是终态）。

## Global Constraints

- 不新增 Python 运行时依赖（httpx 仍是唯一一个）；git 全部走 subprocess 且带超时（对齐 `workspace.py:_git` 模式，超时 5.0s）。
- RPC 协议 `PROTOCOL_VERSION = 2` 不变；新 kind 必须同时加到 `zenagent/cli/rpc/` 的 `_handle_line` 和 `desktop/src/rpc/types.ts` 的 `REQUEST_KINDS`（`tests/test_rpc_contract.py` 强制同步，漏一边即红）。
- Desktop：只用 CSS Modules + design tokens（禁止写死颜色）；所有文案走 `i18n/locale.ts` 的 zh+en 两个字典；单测 node:test（`pnpm test`）；e2e 遵循 `installBridge`/`initialState`/`boot` 模式（`pnpm e2e`）。
- P0 严格只读：不写盘、不 spawn、不改会话。
- import DAG：`app/taiji` 只准 import `project.py`/`workspace.py`/`git.py`/`agent/session.py`，禁止 import cli。

## Review Focus

1. **非 repo / 零提交的空 repo** → snapshot 返回 `is_repo:false` 或空 commits；panel 渲染空态不崩。→ Task 1 测空 repo + Task 4 测空态渲染。
2. **巨型仓库（git log --all 慢）** → `-n 200` 上限 + 5s 超时；超时返回空图而不是挂死 panel。→ Task 1 monkeypatch 慢 git 测超时路径。
3. **worktree 从未在 zen 里打开过（project 目录不存在）** → 分支照常显示、`sessions: []`，不报错。→ Task 2 测缺失目录。
4. **detached HEAD / bare worktree**（branch 为 None/空）→ 绑定与渲染都容忍。→ Task 2 测 detached 条目。
5. **project 目录里 sidecar 损坏**（.titles.json 坏 / meta.json 坏）→ 优雅跳过（titles 已返回 `{}`；meta 坏时 root 回退为目录名）。→ Task 2 测坏 sidecar。

---

### Task 1: commit graph builder

**Files:**
- Create: `zenagent/app/taiji/__init__.py`（空包）
- Create: `zenagent/app/taiji/snapshot.py`
- Test: `tests/test_taiji_snapshot.py`

**Interfaces:**
- Consumes: `workspace.worktree_overview(cwd) -> dict`、`workspace._git` 风格的 subprocess 封装。
- Produces: `def commit_graph(cwd: Path | str, *, limit: int = 200) -> dict`，返回：
  ```
  {"root": str, "is_repo": bool,
   "commits": [{"sha": str, "short": str, "parents": [sha], "subject": str,
                "author": str, "ts": int, "lane": int}],
   "branches": [{"name": str, "sha": str, "is_head": bool}],  # is_head=被任一 worktree checkout
   "max_lane": int}
  ```
- git 命令（钉死）：`git log --all --date-order -n <limit> --pretty=format:%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%at`（按 `\x1f` 分列）；`git for-each-ref refs/heads --format=%(refname:short)%1f%(objectname)`（按 `\x01` 分列）；`is_head` 从 `worktree_overview(cwd)["worktrees"]` 的 branch 集合推导。空 repo（log 报 "does not have any commits"）→ `{"is_repo": True, "commits": [], "branches": [], "max_lane": 0}`；非 repo（rev-parse 失败）→ `is_repo: False`。
- lane 算法（钉死）：按输出序维护 `active_lanes: list[sha|None]`；commit 若是某 lane 的 tip → 用该 lane 并清空它，否则用第一个空 lane，否则 append 新 lane；first parent 占据该 commit 的 lane；其余 parents 各取第一个空 lane 否则新 lane。`lane` = 该 commit 被赋予的下标。

- [ ] **Step 1: 写失败测试** — fixture 用 subprocess 造 tmp git repo（`git init/commit/branch/checkout/merge`；仓库里已有测试这么干则跟随其模式），三个断言组：线性 main 全在 lane 0；分支 commit 拿到新 lane、merge commit 回到 parent lane 且 max_lane 正确；空 repo / 非 repo 两形态。再加超时用例：monkeypatch `_git` 抛超时 → 返回空图不抛。
- [ ] **Step 2: `uv run pytest tests/test_taiji_snapshot.py -v`** → FAIL（模块不存在）
- [ ] **Step 3: 实现 `commit_graph`**（解析、lane 算法如上）
- [ ] **Step 4: 同命令** → PASS
- [ ] **Step 5: Commit** — `git add zenagent/app/taiji tests/test_taiji_snapshot.py && git commit -m "feat(taiji): commit graph snapshot builder"`

### Task 2: session map builder

**Files:**
- Modify: `zenagent/app/taiji/snapshot.py`
- Test: `tests/test_taiji_snapshot.py`（追加）

**Interfaces:**
- Consumes: `project.projects_root()`、`project.project_key(root)`、`app/sessions.py` 的底层 `list_sessions`（SessionManager.entries 内部调用的那个模块级函数；若非模块公开，先小重构成可导入）。
- Produces: `def session_map(cwd: Path | str) -> dict`，返回：
  ```
  {"repo_root": str,
   "projects": [{"key": str, "root": str, "branch": str|None, "worktree": bool,
                 "sessions": [{"name": stem, "title": str, "updated_at": float, "messages": int}]}]}
  ```
- 算法：`overview = worktree_overview(cwd)`；非 repo → 空。roots = `[overview["main"]] + [wt["path"] for wt in worktrees if not bare]`；对每个 root：`key = project_key(root)`，dir = `projects_root()/key`；`sessions/` 不存在 → 条目仍输出但 `sessions: []`；存在 → `list_sessions(limit=100, scope="project")` 该目录，按 updated_at 降序；branch 取该 root 对应 worktree 的 branch（detached → None）。

- [ ] **Step 1: 失败测试** — monkeypatch `zenagent.project.projects_root` 到 tmp；造两个 project 目录（meta.json + sessions/*.jsonl 单行头记录 + .titles.json）；断言：主仓与 worktree 各一条、worktree 缺 project 目录时 `sessions: []`、detached 条目 branch 为 None、坏 .titles.json 不炸。
- [ ] **Step 2: 跑测试 FAIL → Step 3: 实现 → Step 4: PASS**
- [ ] **Step 5: Commit** — `feat(taiji): cross-worktree session map`

### Task 3: RPC kind `taiji`

**Files:**
- Modify: `zenagent/cli/rpc/`（`_handle_line` 加分支 + 模块 docstring 请求清单补一行）
- Modify: `desktop/src/rpc/types.ts`（`REQUEST_KINDS` + 快照类型）
- Test: `tests/test_taiji_rpc.py`

**Interfaces:**
- 请求：`{"type":"taiji","id":N,"op":"snapshot"}`；响应：`{"type":"result","id":N,"ok":true,"taiji":{"graph":<commit_graph>,"sessions":<session_map>}}`；未知 op → error 帧（跟随现有二级分发模式）。
- types.ts 新类型：`TaijiCommit/TaijiBranch/TaijiGraph/TaijiProjectSession/TaijiSessionMap/TaijiSnapshot/TaijiResult`（字段与 Python 侧一一对应）。

- [ ] **Step 1: 失败测试** — 参照现有 rpc 测试模式构造 RpcServer（read_line/write_line 用 asyncio queue），在 tmp repo 里发一行 `{"type":"taiji","id":1,"op":"snapshot"}`，断言 result 帧含 `taiji.graph.commits` 与 `taiji.sessions.projects`；未知 op 得 error。
- [ ] **Step 2: FAIL → Step 3: 实现（两文件）→ Step 4: PASS + `uv run pytest tests/test_rpc_contract.py -v` PASS**
- [ ] **Step 5: Commit** — `feat(rpc): taiji snapshot op`

### Task 4: TaijiPanel 骨架 + 注册 + i18n + e2e

**Files:**
- Create: `desktop/src/ui/rightbar/TaijiPanel.tsx`、`desktop/src/ui/rightbar/TaijiPanel.module.css`
- Modify: `desktop/src/state/stores.ts:205,227`（kind 联合加 `'taiji'`）、`desktop/src/ui/rightbar/RightPanel.tsx:41-45,250-262`（TOOL_TABS + 渲染分支）、`desktop/src/i18n/locale.ts`（zh/en 加 `panel.taiji` 等）、`desktop/e2e/app.spec.ts`（`BridgeState.taiji` 字段 + `respond()` case + 用例）
- Test: e2e 用例（仿 lines 1084-1128 worktree 流程）

**Interfaces:**
- Produces: `export function TaijiPanel()`（无 props，WorktreePanel 模式）；挂载时 `client().send({type:'taiji',op:'snapshot'}, 8000)`，本地 `useState<TaijiSnapshot|null>`；刷新按钮；空态（无数据/非 repo）文案 `panel.taiji.empty`。
- Consumes: Task 3 的 `TaijiResult` 类型。

- [ ] **Step 1: 写失败 e2e** — boot 时 BridgeState 注入固定 snapshot（3 commits、2 branches、1 worktree 带 2 sessions）→ 打开工作面板 → launcher 菜单点「太极」→ 断言 tab `aria-selected`、首条 commit subject 可见、空态用例（空 snapshot）也在内。
- [ ] **Step 2: `pnpm e2e` FAIL → Step 3: 实现面板骨架（fetch + subject 列表 + 空态）+ 注册 + i18n → Step 4: `pnpm e2e` 与 `pnpm test` PASS**
- [ ] **Step 5: Commit** — `feat(desktop): taiji panel skeleton`

### Task 5: 泳道渲染 + session 叠加（纯函数 + 单测）

**Files:**
- Create: `desktop/src/ui/rightbar/taijiGraph.ts`
- Modify: `TaijiPanel.tsx` / `TaijiPanel.module.css`
- Test: `desktop/tests/taijiGraph.test.ts`

**Interfaces:**
- Produces（全部纯函数）：
  - `laneColor(lane: number): string`（循环 design-token 色板数组）
  - `graphEdges(commits: TaijiCommit[]): {fromLane, toLane, merge: boolean}[]`（用 sha→lane 映射连 parent 边，供 rail SVG 画贝塞尔）
  - `attachSessions(graph: TaijiGraph, sessions: TaijiSessionMap, currentSessionPath: string | undefined): TaijiRow[]` — 绑定规则：`project.branch === row 分支名` 的 sessions 挂到该分支 tip 行；多 session 全挂、按 updated_at 降序；`currentSessionPath` 以 session name 结尾 → 该行加 `isHere: true`；无 branch 的 project 不挂。
- Consumes: Task 3/4 类型。

- [ ] **Step 1: 失败单测** — 固定小图（main + 1 分支 + merge）断言 edges 形状；attachSessions 三用例：常规绑定、多 session 排序、you-are-here 标记。
- [ ] **Step 2: `pnpm test` FAIL → Step 3: 实现 ts + 面板渲染（SVG rail + commit 行 + 分支徽章 + session chips，徽章样式对齐 WorktreePanel 的 token 用法）→ Step 4: `pnpm test` + `pnpm e2e` PASS（e2e 补断言 session chip 可见）**
- [ ] **Step 5: Commit** — `feat(desktop): taiji lane rail and session overlay`

### 收尾门禁

`uv run pytest -q`（全量）、`pnpm test`、`pnpm e2e` 全绿后，本计划完成。
