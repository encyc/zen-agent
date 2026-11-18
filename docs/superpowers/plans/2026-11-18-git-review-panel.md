# Git 审阅面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 work rail 的 review/worktree 两个 tab 合并为一个只读 Git 审阅面板（分支区 + Changes + Graph，点文件/提交看 unified diff，可全屏），替换作者工作流里的 VSCode。

**Architecture:** 后端新增 `zenagent/app/git_review.py`（纯 shell-out，四个函数），RPC dispatch 经 `asyncio.to_thread` 包一层避免阻塞事件循环；前端新增 `gitStore` + `GitPanel` 组件族，diff 渲染复用现有 `parseDiffHunks`，Graph 用手写 SVG lane 布局。分两期交付，每期一个 PR。

**Tech Stack:** Python 3.11+（subprocess，零新依赖）· React + CSS Modules · zustand · node --test · Playwright · pytest

**Spec:** [docs/superpowers/specs/2026-11-18-git-review-panel-design.md](../specs/2026-11-18-git-review-panel-design.md)

## Global Constraints

- kernel 运行时依赖仍只有 `httpx`；后端只用标准库 `subprocess`，禁止新 Python 依赖。
- 前端禁止新增 npm 依赖（graph 手写 SVG，不用图表库）。
- 所有 git 子进程超时 5.0s、`check=False`、**全部传 `--no-renames`**（路径键稳定，rename 显示为删+增对）。
- 新 RPC op 一律 `asyncio.to_thread(...)` 执行；失败/超时回 `failure_frame(code="git_failed")`，**不返回部分数据**。
- i18n：新文案全部走 `desktop/src/i18n/messages.ts`，en + zh 双语。
- CSS 用 CSS Modules 且过 `pnpm lint:tokens`（脚本在 `../scripts/check-style-tokens.mjs`）。
- 提交信息用仓库既有 conventional 风格（`feat(desktop):` / `feat(rpc):` / `test:`）。
- 桌面测试命令：`cd desktop && pnpm test`（node --test）；e2e：`pnpm e2e`；后端：`uv run pytest tests/...`。

## Review Focus

1. **巨型 untracked 目录**（如误入的构建产物）→ `-uall` 展开有 200 文件上限，超出部分归入 `untracked_more` 计数，面板显示一行不可点提示 → Task 1 测试。
2. **二进制文件**（png 等）→ diff 返回 `binary: true`，面板显示占位文案而非乱码 → Task 2 测试 + Task 8 e2e 断言。
3. **超长 diff**（lockfile 万行级）→ 后端 2000 行截断置 `truncated: true`，前端 1200 行渲染截断提示 → Task 2 测试（后端侧）。
4. **agent 流式输出期间的刷新轮询**→ dispatch 必须走 `to_thread`，同步执行会卡住聊天流 → Task 3 测试（断言经 `to_thread` 而非直调）。
5. **已删除文件**（工作区 D 状态）→ 归入 unstaged 组、diff 为全删行、渲染器不炸 → Task 2 测试。

---

## Phase 1 · 全屏 + 融合面板 + Changes/diff

### Task 1: 后端 `changes()` — 文件级状态与计数

**Files:**
- Create: `zenagent/app/git_review.py`
- Test: `tests/test_git_review.py`

**Interfaces:**
- Produces: `changes(cwd: Path | str) -> dict`，返回
  `{"is_repo": bool, "branch": str, "files": [{"path": str, "state": "staged"|"unstaged"|"untracked", "plus": int|None, "minus": int|None}], "untracked_more": int}`；
  以及模块级 fixture 助手 `make_repo(tmp_path, *setup_cmds) -> Path`（init + 配 user.email/name，后续任务复用）。
- 约定：非仓库 → `{"is_repo": False, "branch": "", "files": [], "untracked_more": 0}`。state 判定沿用 `parse_porcelain` 的 XY 语义（X 非空 → staged；Y 非空且非 `?` → unstaged；`??` → untracked；一行同时 staged+unstaged 只归 staged 组，计数取该组）。plus/minus：staged 行取 `git diff --cached --numstat`，unstaged 行取 `git diff --numstat`，按路径查表；untracked 行两项为 `None`。

- [ ] **Step 1: Write the failing test**

```python
def test_changes_groups_and_numstat(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "a.txt", "1\n2\n"); _git(repo, "add", "a.txt")          # staged +2
    write(repo / "a.txt", "1\n2\n3\n")                                    # 再改 → staged 组
    write(repo / "b.txt", "x\n"); _git(repo, "add", "b.txt"); _git(repo, "commit", "-m", "init")
    write(repo / "b.txt", "x\ny\n")                                       # unstaged +1
    (repo / "new.txt").write_text("z\n")                                  # untracked
    result = changes(repo)
    assert result["is_repo"] is True and result["branch"] == "main"
    assert {f["path"]: f for f in result["files"]}["a.txt"] == {"path": "a.txt", "state": "staged", "plus": 2, "minus": None}
    # staged 行的 minus 取 --cached --numstat 的实际值；b.txt 是 unstaged +1
    assert {f["path"]: f for f in result["files"]}["b.txt"]["state"] == "unstaged"
    assert {f["path"]: f for f in result["files"]}["new.txt"]["plus"] is None
```

  另加 `test_changes_untracked_cap`（造 205 个 untracked 文件，断言 `files` 里 untracked 恰 200 条、`untracked_more == 5`）与 `test_changes_not_a_repo`。

- [ ] **Step 2: Run test to verify it fails**

  Run: `uv run pytest tests/test_git_review.py -v` · Expected: FAIL（`ModuleNotFoundError: zenagent.app.git_review`）

- [ ] **Step 3: Implement `changes` in `zenagent/app/git_review.py`**

  模块头注释风格仿 `app/git.py`。命令：`git status --porcelain=v1 -b -uall`（分支行解析复用 `parse_porcelain` 的头部逻辑，抽小助手或直接重写皆可）、`git diff --cached --numstat`、`git diff --numstat`（numstat 行格式 `加\t删\t路径`，`-` 表示 binary 记 0）。untracked 截断上限常量 `UNTRACKED_LIMIT = 200`。全部 `_git(cwd, *args)` 内部助手（`subprocess.run` + 5s 超时 + `--no-renames` 只对 diff 类命令）。

- [ ] **Step 4: Run test to verify it passes**

  Run: `uv run pytest tests/test_git_review.py -v` · Expected: PASS

- [ ] **Step 5: Commit**

  `git add zenagent/app/git_review.py tests/test_git_review.py && git commit -m "feat(rpc): file-level git changes with numstat and untracked cap"`

### Task 2: 后端 `diff()` — 单文件 diff 与全新增语义

**Files:**
- Modify: `zenagent/app/git_review.py`
- Test: `tests/test_git_review.py`（追加）

**Interfaces:**
- Produces: `diff(cwd: Path | str, path: str, *, staged: bool = False) -> dict`，
  返回 `{"text": str, "binary": bool, "truncated": bool, "missing": bool}`。
- 语义：staged → `git diff --cached --no-renames -- <path>`；否则若 path 在索引中 → `git diff HEAD --no-renames -- <path>`；untracked（不在索引）→ 服务端合成全新增 diff（读文件，空或二进制嗅探 `\0` 在前 8KB → `binary: true`；正文每行加 `+` 前缀，配 `@@ -0,0 +1,N @@` 头）。行数上限 `DIFF_LINE_LIMIT = 2000`，超出截断置 `truncated: true`。路径不在状态里（干净文件）→ `missing: true`、text 空。

- [ ] **Step 1: Write the failing tests**

  `test_diff_staged_and_unstaged`（对 Task 1 场景断言 staged 拿到 `--cached` 版本、unstaged 拿到 vs HEAD 版本，hunk 头存在）；`test_diff_untracked_all_additions`（断言 text 含 `@@ -0,0 +1,N @@` 且每行以 `+` 开头）；`test_diff_binary`（写含 `\0` 字节的 untracked 文件 → `binary` 为 True）；`test_diff_truncated`（写 3000 行 untracked 文件 → `truncated` 为 True 且行数 ≤ 2000）；`test_diff_deleted_file`（`rm b.txt` 后 unstaged diff 全为 `-` 行、不抛异常）。

- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/test_git_review.py -v` · FAIL

- [ ] **Step 3: Implement `diff`**

  索引判断用 `git ls-files -- <path>` 输出非空。截断按行 split/join。

- [ ] **Step 4: Run tests to verify they pass** — 同上 · PASS

- [ ] **Step 5: Commit** — `feat(rpc): per-file git diff incl. untracked all-additions`

### Task 3: RPC dispatch — `git_changes` / `git_diff`（非阻塞）

**Files:**
- Modify: `zenagent/cli/rpc/server.py`（dispatch 区，`git_worktrees` 分支旁）
- Test: `tests/test_rpc_contract.py`（追加，沿该文件既有 mock 客户端模式）

**Interfaces:**
- Produces（wire，前端 Task 4 镜像）：请求 `{"type": "git_changes"}` → 结果帧 payload 键 `changes`（Task 1 dict）；请求 `{"type": "git_diff", "path": str, "staged": bool}` → payload 键 `diff`（Task 2 dict）。两者失败均回 `failure_frame(code="git_failed")`。`path` 非 str 或为空 → 错误帧（仿 `subagent_transcript needs call_id` 的直发 error 形式）。

- [ ] **Step 1: Write the failing tests**

  沿 `test_rpc_contract.py` 既有 harness：`test_git_changes_roundtrip`（临时仓库，客户端收到 `ok: True` 且 `changes.files` 非空）；`test_git_diff_bad_path`（`path: 123` → error 帧）；`test_git_ops_run_off_loop`（monkeypatch `zenagent.app.git_review.changes` 为记录调用线程的 stub，断言执行线程 ≠ 事件循环线程，即 dispatch 经 `asyncio.to_thread`）。

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/test_rpc_contract.py -v` · FAIL（unknown type）

- [ ] **Step 3: Implement dispatch**

  两个分支，各 `try: result = await asyncio.to_thread(git_review.changes, self.cwd) ... except Exception as exc: self._send(failure_frame(request_id, code="git_failed", message="git op failed", exc=exc, op=kind))`；成功回 `{"type": "result", "id": ..., "ok": True, "changes": result}` / `..."diff": result`。`subprocess.TimeoutExpired` 一并被 except 捕获。

- [ ] **Step 4: Run to verify pass** — PASS

- [ ] **Step 5: Commit** — `feat(rpc): git_changes/git_diff ops off the event loop`

### Task 4: 前端类型 + bridge mock + `gitStore`

**Files:**
- Modify: `desktop/src/rpc/types.ts`（git 段，`GitWorktreesResult` 旁）
- Modify: `desktop/src/dev/bridge.ts`（switch 加 case）
- Create: `desktop/src/state/gitStore.ts`
- Test: `desktop/tests/gitStore.test.ts`

**Interfaces:**
- Produces: `GitChangesResult`、`GitFileChange`、`GitDiffResult`（types.ts，字段与 Task 3 wire 一一对应）；`useGitStore`：
  - state: `changes: { is_repo: boolean; branch: string; files: GitFileChange[]; untracked_more: number } | null`；`selected: { kind: 'file'; path: string; staged: boolean } | null`；`diffFor(key: string): { text: string; binary: boolean; truncated: boolean; missing: boolean } | null`（内部 Map 缓存，字段与 Task 2 wire 一致）；`failed: boolean`
  - actions: `loadChanges(): Promise<void>`（`useAppStore.getState().client().send`，沿 WorktreePanel 的调用模式，超时 8000）；`openFile(path: string, staged: boolean): Promise<void>`（选中 + 拉取 diff，缓存键 `path + ':' + (staged ? 's' : 'w')`）；`clear(): void`
  - 刷新联动：面板挂载时调 `loadChanges`；`useChatStore` 的 `nodes` 长度变化且 work panel 开着 → 500ms 去抖再 `loadChanges`（订阅写在 gitStore 的 init 或面板 effect，实现自选，行为由测试钉死）。
- Consumes: Task 3 的两个 op。

- [ ] **Step 1: Write the failing test**

```ts
test('openFile caches diff by path and stage key', async () => {
  const send = mockClientSend({ 'git_changes': changesFrame, 'git_diff': diffFrame })  // 沿 tests/ 既有 client mock 手法
  await useGitStore.getState().loadChanges()
  await useGitStore.getState().openFile('a.txt', false)
  await useGitStore.getState().openFile('a.txt', false)   // 第二次不再发 git_diff
  assert.equal(send.calls('git_diff'), 1)
})
```

  另：`test_load_failure_sets_failed`（回错误帧 → `failed` true 且旧数据保留）；`test_refresh_debounced_on_nodes_growth`（用假 timer 推进 500ms，断言恰好两次 `git_changes`）。

- [ ] **Step 2: Run to verify fail** — `cd desktop && pnpm test tests/gitStore.test.ts` · FAIL
- [ ] **Step 3: Implement types + store + bridge case**（bridge mock 数据：3 个文件三组各一 + `untracked_more: 0`）
- [ ] **Step 4: Run to verify pass** — PASS
- [ ] **Step 5: Commit** — `feat(desktop): gitStore drives changes and per-file diffs`

### Task 5: `DiffBlock` 提共享 + `GitPanel`（分支区 + Changes + 详情）

**Files:**
- Create: `desktop/src/ui/rightbar/DiffBlock.tsx`（从 ReviewPanel 原样提出，props `{ diff: string }`）
- Create: `desktop/src/ui/rightbar/GitPanel.tsx`（容器：双栏 + 窄栏退化）
- Create: `desktop/src/ui/rightbar/gitChanges.ts`（纯函数集）
- Create: `desktop/src/ui/rightbar/GitPanel.module.css`
- Test: `desktop/tests/gitChanges.test.ts`
- e2e 覆盖留给 Task 8。

**Interfaces:**
- Produces:
  - `gitChanges.ts`：`groupChanges(files: GitFileChange[]): { staged: GitFileChange[]; unstaged: GitFileChange[]; untracked: GitFileChange[] }`；`sessionPaths(nodes: ChatNode[]): Set<string>`（`collectFileChanges` 的路径经 `toWorkspacePath` 归一）；`isSessionFile(path: string, session: Set<string>): boolean`
  - `GitPanel`：挂进 RightPanel 的 `kind === 'git'` 分支（本任务先并存，Task 7 再删旧 tab）
- Consumes: `useGitStore`（Task 4）、`DiffBlock`、`useAppStore.openProject`（切换 worktree，沿 WorktreePanel 守卫：`running` 时禁用）、`useWorkPanelStore.openFile`（详情头部"在 Files 中打开"链接）。
- 布局：≥520px 双栏（左列 260px 滚动，分支区 sticky）；<520px 单栏 list→detail→back（复用 FilesPanel 交互形态）。二进制文件 → 占位文案（i18n）；`truncated` → 脚注 + "在 Files 中打开"；`untracked_more > 0` → 一行不可点提示行。

- [ ] **Step 1: Write the failing tests**（纯函数）— `groupChanges` 三组归类 + 同文件只入 staged 组；`sessionPaths`/`isSessionFile` 用绝对路径 transcript 节点匹配 git 相对路径。
- [ ] **Step 2: Run to verify fail** — `pnpm test tests/gitChanges.test.ts` · FAIL
- [ ] **Step 3: Implement `gitChanges.ts`**，然后 `DiffBlock.tsx` 提取（ReviewPanel 改 import，行为不变），再写 `GitPanel.tsx` + CSS（样式沿 `ReviewPanel.module.css` / `FilesPanel.module.css` 的 token 用法）。
- [ ] **Step 4: Run to verify pass + 旧测试不回归** — `pnpm test`（全量）· PASS
- [ ] **Step 5: Commit** — `feat(desktop): git panel with branch, grouped changes and diff detail`

### Task 6: 全屏模式

**Files:**
- Modify: `desktop/src/state/stores.ts`（workPanelStore）
- Modify: `desktop/src/ui/columns.ts`、`desktop/src/ui/AppFrame.tsx`、`desktop/src/App.tsx`
- Test: `desktop/tests/columns.test.ts`（追加）

**Interfaces:**
- Produces: `workPanelStore` 增 `fullscreen: boolean` 与 `toggleFullscreen(): void`（`closePanel` 时一并退出全屏）；`computeFullscreenColumns(viewport: number, sidebar: number): Columns`（`{sidebar: s, center: 0, rightbar: viewport - s}`，s 用现有 clamp）；`AppFrame` 增可选 prop `fullscreen?: boolean`，为真时用全屏求解、center 列不渲染；`App.tsx` Escape 链：palette（既有，最先）→ busy 取消（既有）→ **非 busy 且 fullscreen → 退出全屏**；RightPanel 头部加 ⤢ 按钮（`aria-label` i18n）。
- 消费：Task 5 的 GitPanel 是全屏主要受益者，但全屏作用于整个 rail（对所有 tab 生效）。

- [ ] **Step 1: Write the failing test** — `computeFullscreenColumns(1440, 275)` → `{sidebar: 275, center: 0, rightbar: 1165}`；小视口 clamp 断言一条。
- [ ] **Step 2: Run to verify fail** — `pnpm test tests/columns.test.ts` · FAIL
- [ ] **Step 3: Implement** store flag → columns 求解 → AppFrame 分支 → App Escape 链 → RightPanel 按钮。
- [ ] **Step 4: Run to verify pass** — `pnpm test` · PASS
- [ ] **Step 5: Commit** — `feat(desktop): work panel fullscreen with escape precedence`

### Task 7: tab 合并清理

**Files:**
- Modify: `desktop/src/state/stores.ts`（kind 联合类型 `'review'|'worktree'` 删、`'git'` 增；默认 tabs `REVIEW_TAB` → `GIT_TAB = { id: 'git', kind: 'git' }`）
- Modify: `desktop/src/ui/rightbar/RightPanel.tsx`（TOOL_TABS：git/files/memory/terminal；渲染 GitPanel）
- Delete: `desktop/src/ui/rightbar/ReviewPanel.tsx`、`ReviewPanel.module.css`、`WorktreePanel.tsx`、`WorktreePanel.module.css`
- Modify: `desktop/src/i18n/messages.ts`（删 `panel.worktree.*`、`panel.review*`；增 `panel.git.*`：标题、分组名 Staged/Changes/Untracked、binary 占位、truncated 脚注、untracked_more 行、切换中、错误/加载、在 Files 中打开、全屏开/关——en+zh）
- Modify: `desktop/e2e/app.spec.ts`（488 行 review hunks 用例与 791 行 worktree tab 用例改为面向 GitPanel 的等价断言）
- Test: 既有 `desktop/tests/bridge.test.ts`、e2e 全量为本任务回归网。

**Interfaces:**
- Consumes: Task 5 的 GitPanel（`kind === 'git'`）。
- 注意：`review.ts` 保留（`parseDiffHunks`/`collectFileChanges`/`fileBasename` 仍被 GitPanel/chat 使用）。

- [ ] **Step 1: Update e2e specs to the new panel**（先改测试表达目标行为：git tab 列出三组、点击文件出 hunk、worktree 列表在分支区）
- [ ] **Step 2: Run e2e to verify fail** — `pnpm e2e --grep "git panel|worktree"` · FAIL
- [ ] **Step 3: Implement the merge/cleanup**（stores、RightPanel、删文件、i18n）
- [ ] **Step 4: Run full checks** — `pnpm test && pnpm lint:tokens && pnpm e2e` · 全 PASS
- [ ] **Step 5: Commit** — `refactor(desktop): merge review and worktree tabs into the git panel`

### Task 8: Phase 1 e2e 与验收

**Files:**
- Modify: `desktop/e2e/app.spec.ts`（追加）

- [ ] **Step 1: Write e2e** — `git panel shows grouped changes and opens a file diff`（mock changes 帧三组各一，点 untracked 文件 → 全 `+` 行；点 staged → `--cached` 版本）；`binary files show a placeholder`；`narrow rail falls back to list-detail-back`（视口压到 rail <520px，断言单栏导航与返回按钮）；`fullscreen expands and escape restores`（⤢ 点击后 center 列消失，空闲 Escape 复原；busy 时 Escape 仍走取消——用既有 busy mock 手法）。
- [ ] **Step 2: Run** — `pnpm e2e` · FAIL→调至 PASS
- [ ] **Step 3: Phase 1 全量回归** — `uv run pytest tests/test_git_review.py tests/test_rpc_contract.py && cd desktop && pnpm test && pnpm lint:tokens && pnpm e2e` · 全 PASS
- [ ] **Step 4: Commit** — `test(desktop): git panel phase 1 e2e coverage`

---

## Phase 2 · Graph + 提交 diff

### Task 9: 后端 `log()` — DAG 数据

**Files:**
- Modify: `zenagent/app/git_review.py` · Test: `tests/test_git_review.py`（追加）

**Interfaces:**
- Produces: `log(cwd: Path | str, *, limit: int = 100, skip: int = 0) -> dict`，
  `{"commits": [{"hash": str(全), "short": str, "parents": [str(全)...], "subject": str, "author": str, "timestamp": int(unix), "refs": [str...]}]}`。
- 实现：`git log --skip=<s> -n <limit> --date=raw --pretty=format:%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%at%x1f%D%x1e`（记录分隔 `\x1e`，字段 `\x1f`；`%D` 形如 `HEAD -> main, origin/main`，按 `, ` 拆、去 `HEAD -> ` 前缀）。

- [ ] **Step 1: Write failing tests** — `test_log_parents_and_refs`（造 main + 分支 merge 回来，断言 merge 提交 `parents` 长 2、main 上提交 refs 含 `main`）；`test_log_pagination`（3 提交，limit 2 skip 2 → 1 条）。
- [ ] **Step 2: Verify fail** — `uv run pytest tests/test_git_review.py -v` · FAIL
- [ ] **Step 3: Implement** · **Step 4: Verify pass** · **Step 5: Commit** — `feat(rpc): git log with parents, refs and paging`

### Task 10: 后端 `show()` — 提交 diff

**Files:**
- Modify: `zenagent/app/git_review.py` · Test: `tests/test_git_review.py`（追加）

**Interfaces:**
- Produces: `show(cwd: Path | str, commit: str) -> dict`，
  `{"found": bool, "short": str, "subject": str, "files": [{"path": str, "additions": int, "removals": int, "diff": str}]}`（空 diff 的 merge 提交 → `files: []`；坏 hash → `found: False`）。
- 实现：`git show --no-renames --numstat --format=%H%x1f%h%x1f%s <commit>` 拿元数据+计数，`git show --no-renames -p --format= <commit>` 拿正文，按 `diff --git a/` 行切分逐文件；numstat 的 `-` 计 0。行截断沿用 `DIFF_LINE_LIMIT`（整提交总量截断即可）。

- [ ] **Step 1: Write failing tests** — `test_show_files_and_counts`；`test_show_merge_empty`（`--no-renames` 下无文本变更的 merge → `files == []`）；`test_show_bad_commit`。
- [ ] **Step 2: Verify fail** · **Step 3: Implement** · **Step 4: Verify pass**
- [ ] **Step 5: Commit** — `feat(rpc): commit-level diff for the graph`

### Task 11: RPC dispatch — `git_log` / `git_show`

**Files:**
- Modify: `zenagent/cli/rpc/server.py` · Test: `tests/test_rpc_contract.py`（追加）

**Interfaces:**
- Produces：请求 `{"type": "git_log", "limit"?: int, "skip"?: int}` → payload 键 `log`；`{"type": "git_show", "commit": str}` → payload 键 `show`。`commit` 校验 `^[0-9a-f]{4,40}$`，不过 → error 帧。其余沿 Task 3 模式（to_thread + `git_failed`）。

- [ ] **Step 1: Failing tests**（roundtrip ×2 + `test_git_show_bad_commit`）
- [ ] **Step 2: Verify fail** · **Step 3: Implement** · **Step 4: Verify pass**
- [ ] **Step 5: Commit** — `feat(rpc): git_log/git_show ops off the event loop`

### Task 12: 前端 `lanes.ts` + `mergeCommits`（纯算法）

**Files:**
- Create: `desktop/src/ui/rightbar/lanes.ts`
- Test: `desktop/tests/lanes.test.ts`

**Interfaces:**
- Produces:
  - `assignLanes(commits: GitCommit[]): LaneRow[]`，`LaneRow = { hash: string; lane: number; parents: { hash: string; lane: number }[] }`——贪心 first-fit：维护活动 lane 槽位数组（槽位存"下一个期待出现的 hash"）；提交出现在某槽 → 占该 lane，否则占第一个空槽；父提交放槽：首父优先继承当前 lane，其余父找空槽；末尾收尾。
  - `mergeCommits(loaded: GitCommit[], fresh: GitCommit[]): GitCommit[]` —— 按 `hash` 去重，fresh 中新提交按序前插，loaded 尾部原样保留。
  - 颜色：组件按 `lane % PALETTE.length` 取色，算法不管颜色。

- [ ] **Step 1: Write failing tests** — `assignLanes`：线性 3 提交全 lane 0；分叉后并行（两 lane）；merge 汇合（子占首父 lane，次父边跨 lane，之后槽位回收）；`mergeCommits`：前插去重、尾部保序。
- [ ] **Step 2: Verify fail** — `pnpm test tests/lanes.test.ts` · FAIL
- [ ] **Step 3: Implement（纯函数，无 React 依赖）** · **Step 4: Verify pass**
- [ ] **Step 5: Commit** — `feat(desktop): lane layout and keyed-merge for the commit graph`

### Task 13: `GitGraphSection` + 提交详情 + 分页

**Files:**
- Modify: `desktop/src/state/gitStore.ts`（`log`、`hasMore`、`loadLog(reset)`, `loadMoreLog()`, `openCommit(hash)`, `selected` 扩 `{kind:'commit'; hash: string}`；刷新走 `mergeCommits`）
- Create: `desktop/src/ui/rightbar/GitGraphSection.tsx` + `.module.css`
- Modify: `desktop/src/ui/rightbar/GitPanel.tsx`（Graph section + commit 详情复用 DiffBlock）、`desktop/src/dev/bridge.ts`（`git_log`/`git_show` mock）、`desktop/src/i18n/messages.ts`（graph 相关键）
- Test: `desktop/tests/gitStore.test.ts`（追加分页与 merge 行为）

**Interfaces:**
- Consumes: Task 11 op、Task 12 算法、Task 5 `DiffBlock`。store 增 `showFor(hash: string): { found: boolean; short: string; subject: string; files: { path: string; additions: number; removals: number; diff: string }[] } | null`（`openCommit` 拉取后入缓存，键为请求时的 hash）。
- 行渲染：每行 SVG（宽 = lane 宽 × 槽位数上限，行高固定）画竖线/折线/圆点 + 文本（subject、author、相对时间、refs 徽章 chips）；滚动触底 `loadMoreLog()`（`skip = commits.length`，返回 < limit → `hasMore=false`）。

- [ ] **Step 1: Write failing store tests** — `test_log_pagination_appends`；`test_refresh_prepends_new_commits`（第二次首页帧含新 hash → 前插、长度正确、滚动状态无关）。
- [ ] **Step 2: Verify fail** — `pnpm test tests/gitStore.test.ts` · FAIL
- [ ] **Step 3: Implement store 扩展 → GraphSection → GitPanel 接线 → bridge/i18n**
- [ ] **Step 4: Verify pass（全量 `pnpm test`）**
- [ ] **Step 5: Commit** — `feat(desktop): commit graph with lanes, paging and commit diffs`

### Task 14: Phase 2 e2e 与总回归

**Files:**
- Modify: `desktop/e2e/app.spec.ts`（追加）

- [ ] **Step 1: Write e2e** — `graph renders lanes and loads more`（mock 105 提交含一个 merge，断言首屏 100 行、滚动加载后 105）；`clicking a commit shows its diff`；`refresh keeps scroll position`（可选，若 e2e 难断言则以 store 测试为准并注明）。
- [ ] **Step 2: Run to green** — `pnpm e2e`
- [ ] **Step 3: 总回归** — `uv run pytest && cd desktop && pnpm test && pnpm lint:tokens && pnpm e2e` · 全 PASS
- [ ] **Step 4: Commit** — `test(desktop): commit graph e2e and full regression`
