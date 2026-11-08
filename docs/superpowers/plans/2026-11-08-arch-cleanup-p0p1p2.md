# 架构清理实施计划 P0/P1/P2(2026-11-08)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 [2026-11-08 架构审查](../../reviews/2026-11-08-architecture-review.md) 全部修复项:LOC 门变绿、taiji 边界(rpc↔supervisor)归位、git 出事件循环、supervisor/desktop 状态层/locale 还债、rpc 包拆分收尾。

**Architecture:** 不动六层结构与进程模型。三类改动:(1) 把 taiji 的投影与策略从 `cli/rpc.py` 移回 `app/taiji`;(2) 把 supervisor 的第二关注点(`RpcRunner`/合并门/worktree)拆成模块;(3) desktop 抽 boot 共享路径、taiji 数据层出组件、locale 双通道收敛。每任务独立可合并、门保持绿。

**Tech Stack:** Python 3.11+(stdlib httpx 内核,pytest/ruff)、React+zustand(TS strict)、Tauri 2 Rust 壳、node --test / Playwright。

**Spec:** `docs/reviews/2026-11-08-architecture-review.md`(行号以审查当日 HEAD `f3f9e1e` 为准;基线:2463 py 测试 + 109 desktop 单测 + 48 e2e 全绿,LOC 门红灯)。

## Global Constraints

- 运行时第三方依赖**只允许 httpx**;CLI extra 仅 prompt-toolkit/rich/textual,不得新增。
- 分层由 `tests/test_layering.py` 钉死:上层 import 下层,`app/` 不 import `zenagent.cli`/argparse/textual/rich/prompt_toolkit。任何任务不得引入反向依赖。
- 线协议**只增不改**:既有字段/形状逐字节兼容;新增字段必须可选并同步 `desktop/src/rpc/types.ts`;`tests/test_rpc_contract.py` 是协议未破的判据。
- **门绿规则**:`python3 scripts/check_architecture.py` 在每个提交点 exit 0。需要超出某文件已登记行数时,在同一提交内调高该条目,commit message 写明债务注记与对应的还债任务号;只缩不涨是默认。
- 神文件只许变小:本计划涉及的所有既有大文件(rpc.py、appStore.ts、locale.ts、supervisor.py、TaijiPanel.tsx)的任何任务,净效果不得大于登记值。
- 不重命名公共 API;旧路径用 re-export 过渡(`zenagent.cli.rpc`、`zenagent.app.taiji.supervisor.RpcRunner` 等导入方零改动)。
- Python 任务收尾:`uv run pytest -q && uv run ruff check`;desktop 任务收尾:`pnpm build && pnpm test && pnpm e2e`(在 `desktop/` 下;首次跑 e2e 若缺浏览器先 `pnpm exec playwright install`)。
- 每任务一个 commit,沿用仓库 conventional commits(`refactor:`/`fix:`/`test:`)。

## Review Focus

- **会话 JSONL 顺序**(Task 10):`append` 改 to_thread 后乱序=数据损坏。期望:并发 append 的落盘顺序与调用顺序一致。测试:Task 10 内并发 100 条顺序断言。
- **board payload 双写漂移**(Task 2/7):改投影字段忘改 `types.ts` → 桌面静默渲染空字段。期望:键集任何一侧变动 CI 即红。测试:Task 7 契约断言 py dict 键集 == TS interface 字段集。
- **inbox 持久化窗口**(Task 6):插话 append 与 `_save()` 之间崩溃丢一条。期望:inbox 变更与所在状态变更同帧原子保存,重启后 `message` 链尾照常投递。测试:Task 6 重启保留断言。
- **allowlist 再红灯**(所有任务):任何任务让登记文件超过登记数=CI 红。期望:超限当提交内认账或先缩;每任务收尾必跑门。
- **openProject 切换竞态**(Task 5):旧子进程迟到事件写入新会话。期望:pid/退出守卫拦截,面板无串台。测试:既有 appStore pid 守卫行为 + e2e 全绿兜底。

---

## Phase P0

### Task 1: LOC 门认账——allowlist 登记至现状,门变绿

**Files:**
- Modify: `docs/architecture/allowlist.json`

**Interfaces:**
- Produces: 门 exit 0 的干净基线;后续任务的债务登记规则(见 Global Constraints 门绿规则)。

- [ ] **Step 1: 更新 allowlist 至实测值**

```json
{
  "python": {
    "zenagent/app/runtime.py": 919,
    "zenagent/app/commands.py": 866,
    "zenagent/cli/repl.py": 1023,
    "zenagent/cli/rpc.py": 2144,
    "zenagent/cli/tui/screens/modals.py": 1042
  },
  "ts": {
    "desktop/src/ui/Sidebar.tsx": 854,
    "desktop/src/state/appStore.ts": 910,
    "desktop/src/i18n/locale.ts": 962,
    "desktop/e2e/app.spec.ts": 2611
  }
}
```

- [ ] **Step 2: 验证门变绿**

Run: `python3 scripts/check_architecture.py; echo $?`
Expected: exit 0

- [ ] **Step 3: Commit**

```bash
git add docs/architecture/allowlist.json
git commit -m "chore(arch): register post-taiji file sizes in the LOC allowlist

Debt ledger (paydown tasks in 2026-11-08-arch-cleanup-p0p1p2.md):
rpc.py 2144 (taiji ops -> Task 2, split -> Task 14), runtime.py 919,
appStore.ts 910 (boot dedupe -> Task 5), locale.ts 962 (keys -> Task 8)."
```

### Task 2: taiji board 投影回 `app/taiji`,rpc 去私有访问

**Files:**
- Create: `zenagent/app/taiji/board.py`
- Modify: `zenagent/app/taiji/supervisor.py`(新增 `find_record`/`has_running` 公共访问器;`_attention_question` 改用 `board.parse_attention`)
- Modify: `zenagent/cli/rpc.py:688-879`(删 `_taiji_worker`,board/peek/message op 改走新 API)
- Test: `tests/test_taiji_board.py`(新)、`tests/test_rpc_contract.py`(追加边界断言)

**Interfaces:**
- Produces:
  - `zenagent/app/taiji/board.py`:`parse_attention(raw: str) -> dict | None`(返回 `{"question": str, "options": list[str]}` 且 options 截断 ≤4;非 dict JSON 或解析失败回退 `{"question": raw, "options": []}`);`project_worker(supervisor, record: WorkerRecord) -> dict`(键集与现 `rpc._taiji_worker` 输出逐字段一致:name/branch/base/status/summary/session/created_at/updated_at/attention/diffstat;diffstat 仅 `record.status == "ok"` 时调 `supervisor.diffstat`);`project_board(supervisor, router_busy: bool) -> dict`(键:active/router_busy/slots/workers)。
  - `TaijiSupervisor.find_record(name: str) -> WorkerRecord | None`(等价 `registry.records.get`)、`has_running(name: str) -> bool`(等价现 `rpc._taiji_action` 的 `was_running` 快照语义:记录存在且 `status == "running"`)。

- [ ] **Step 1: 写失败测试 `tests/test_taiji_board.py`**

```python
def test_parse_attention_truncates_options():
    raw = '{"question": "q", "options": ["a", 2, "c", "d", "e"]}'
    assert board.parse_attention(raw) == {"question": "q", "options": ["a", "2", "c", "d"]}

def test_parse_attention_falls_back_on_garbage():
    assert board.parse_attention("not json") == {"question": "not json", "options": []}

def test_project_worker_gates_diffstat_on_ok(...):   # ok → diffstat 非 None;error → None;键集固定 10 个
def test_project_board_inactive_and_active_shape(...): # active=False 形状在 rpc 侧,active=True 含 slots/workers
```

- [ ] **Step 2: 跑测试确认失败** — Run: `uv run pytest tests/test_taiji_board.py -q`;Expected: FAIL(模块不存在)
- [ ] **Step 3: 实现 `board.py` 三函数**(从 `rpc.py:809-840` 逐字段搬,不改任何键名/截断/门控语义);supervisor 加 `find_record`/`has_running`,`_attention_question` 改调 `parse_attention`
- [ ] **Step 4: rpc.py 切换**——board op 的 `asyncio.to_thread` 内改调 `project_board`;`peek` 用 `supervisor.find_record`;`_taiji_action` 的 `was_running` 改 `supervisor.has_running(name)`(快照仍在 await 前);删 `_taiji_worker` 与全部 `registry.records`/`._record` 引用
- [ ] **Step 5: 追加防回退断言到 `tests/test_rpc_contract.py`**

```python
def test_rpc_never_touches_supervisor_internals():
    rpc = RPC_PATH.read_text()
    assert "registry.records" not in rpc
    assert "._record(" not in rpc
```

- [ ] **Step 6: 验证** — Run: `uv run pytest tests/test_taiji_board.py tests/test_rpc_contract.py tests/test_taiji_rpc.py -q && uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS,`test_taiji_rpc.py`(332 行线协议行为)零改动通过,门绿
- [ ] **Step 7: Commit** — `refactor(taiji): board projection lives in the taiji layer; rpc stops reaching into supervisor internals`

### Task 3: supervisor 的 git 调用全路径出事件循环

**Files:**
- Modify: `zenagent/app/taiji/supervisor.py`(`_git` :44-58 保持同步;新增 `_git_async`;异步调用点切换)
- Test: `tests/test_taiji_supervisor.py`(追加)

**Interfaces:**
- Produces: `TaijiSupervisor` 内(或模块级)`async def _git_async(root: Path, *args: str) -> CompletedProcess[str] | None`(实现:`return await asyncio.to_thread(_git, root, *args)`——`_git` 经模块全局查找,可 monkeypatch)。切换调用点:spawn 的 worktree git 序列(`:307-344`)、`kill`(`:394`)、`merge` 内 `branch --show-current`/`merge`/`merge --abort`(`:649-668`)与 `git_status`(`await asyncio.to_thread(git_status, self.repo_root)`)、auto-merge 路径(`:554`)、`_retire_worktree`(`:678-683`)。boot 时的 registry 对账(`:234-235`)保持同步,加一行注释说明(进程启动期,loop 未接流量)。

- [ ] **Step 1: 写失败测试**

```python
async def test_git_async_offloads_to_worker_thread(monkeypatch):
    import threading
    from zenagent.app.taiji import supervisor as sup
    idents = []
    monkeypatch.setattr(sup, "_git", lambda root, *a: idents.append(threading.get_ident()) or None)
    await sup._git_async(Path("/tmp"), "status")
    assert idents[0] != threading.get_ident()

async def test_merge_runs_git_off_loop(monkeypatch, ...):
    # 猫板 _git 与 git_status:记录 threading.get_ident(),返回罐头 CompletedProcess
    # (show-current→record.base、status clean、merge rc=0),走通一次 merge
    # 断言:所有记录的 ident != loop 线程 ident,且 record.status == "merged"
```

- [ ] **Step 2: 跑测试确认失败** — Expected: FAIL(`_git_async` 不存在 / merge 在 loop 线程跑 git)
- [ ] **Step 3: 实现并切换调用点**(逐点改 `await self._git_async(...)`)
- [ ] **Step 4: 验证** — Run: `uv run pytest tests/test_taiji_supervisor.py tests/test_taiji_loop.py -q && uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS(taiji 8 个测试文件全绿)
- [ ] **Step 5: Commit** — `fix(taiji): every async-path git call leaves the event loop`

## Phase P1

### Task 4: supervisor 拆分——RpcRunner / 合并门 / worktree 管家各自成模块

**Files:**
- Create: `zenagent/app/taiji/rpc_runner.py`、`zenagent/app/taiji/merge_gate.py`、`zenagent/app/taiji/worktree.py`
- Modify: `zenagent/app/taiji/supervisor.py`(删迁出代码;`message()` 去重 :352-358 与 :371-377)
- Test: 既有 taiji 套件 + `tests/test_layering.py`(追加 import 兼容断言)

**Interfaces:**
- Produces:
  - `rpc_runner.py`:`class RpcRunner`(`supervisor.py:85-204` 逐字迁出,含 ask_request 拦截);`supervisor.py` 顶部 `from zenagent.app.taiji.rpc_runner import RpcRunner` 保持 `supervisor.RpcRunner` 可导入。
  - `merge_gate.py`:`AUTO_MERGE_MAX_DIFF_LINES = 200`;`diff_lines(repo_root: Path, record: WorkerRecord) -> int`;`compute_diffstat(repo_root: Path, record: WorkerRecord) -> list[dict] | None`;`block_reason(repo_root: Path, record: WorkerRecord) -> str | None`(纯计算,不碰 registry;`git_status` 由本模块 import `app.git`)。`supervisor.merge/diffstat/merge_block_reason` 方法签名不变,内部委托。
  - `worktree.py`:`worktree_paths(root: Path, name: str) -> tuple[Path, Path]`;`free_name(root: Path, slug: str) -> str`;`rollback(root: Path, branch: str, worktree: Path) -> None`;`retire_worktree(root: Path, worktree: Path, branch: str) -> None`(git 调用各自 `to_thread` 由调用方包,本模块函数保持同步、签名显式收 root/路径,不持 supervisor 引用)。
  - `message()` 去重:抽 `async def _cancel_queued_turn(self, name: str) -> None`(现 :352-358 体),删除 :371-377 重复块——语义等价(第二块对 queued 状态是死代码,首次取消后任务已 done)。

- [ ] **Step 1: 追加失败测试到 `tests/test_layering.py`**

```python
def test_taiji_modules_are_split():
    from zenagent.app.taiji import merge_gate, rpc_runner, worktree
    from zenagent.app.taiji.supervisor import RpcRunner  # re-export 兼容
    assert rpc_runner.RpcRunner is RpcRunner
    assert merge_gate.AUTO_MERGE_MAX_DIFF_LINES == 200
```

- [ ] **Step 2: 跑测试确认失败** — Expected: FAIL(模块不存在)
- [ ] **Step 3: 三次机械搬移 + message 去重**(每次搬完跑 taiji 套件;新文件各自 < 300 行;`wc -l zenagent/app/taiji/supervisor.py` 目标 ≤ 550)
- [ ] **Step 4: 验证** — Run: `uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS,8 个 taiji 测试文件零改动通过
- [ ] **Step 5: Commit** — `refactor(taiji): supervisor sheds rpc runner, merge gate, worktree housekeeping`

### Task 5: desktop boot 共享路径 + 直连收编 + switching 接线

**Files:**
- Modify: `desktop/src/state/appStore.ts`(抽共享 boot;净减行数)
- Modify: `desktop/src/ui/chat/ChatView.tsx:191-195`、`desktop/src/ui/rightbar/WorktreePanel.tsx:42-49`
- Modify: `desktop/src/App.tsx`(switching 渲染)、`desktop/src/i18n/locale.ts`(+1 键×2)
- Test: 既有 desktop 套件

**Interfaces:**
- Produces: appStore.ts 模块内 `async function bootChild(set, get, bin: string | undefined, cwd: string, args: string[]): Promise<void>`(start→pid→handshake→localStorage addProject→refreshTranscript→restorePrefs→refreshSessions,即现 `connect :222-248` 与 `openProject :469-496` 的公共体);`connect`/`openProject` 委托之,`openProject` 保留 switching set/clear 与 pid/exit 守卫(`:208-218`、`:455-461` 语义不动)。
- Consumes: 既有 `openProject(path)` action。

- [ ] **Step 1: 抽 `bootChild`,connect/openProject 改委托**;跑 `pnpm build && pnpm test`
- [ ] **Step 2: 收编直连**——`ChatView.switchProject` 体改 `await useAppStore.getState().openProject(path)`(保留 `window.confirm`);`WorktreePanel` :45-46 同改(保留按钮本地 disabled 态)
- [ ] **Step 3: switching 接线**——`App.tsx` 在 `connection === 'starting'` 分支(:187)之后加:`switching !== null` 时渲染与 starting 同款的轻量 boot 面板,文案 `t('app.switching', { path })`;`locale.ts` 增 `app.switching`(zh/en 各一条)。若本提交使 locale.ts 超登记值,按门绿规则同提交调高并注记(Task 8 还债)
- [ ] **Step 4: 验证** — Run: `pnpm build && pnpm test && pnpm e2e && cd .. && python3 scripts/check_architecture.py`;Expected: 全绿;grep 断言 `grep -rn "disconnect()" desktop/src/ui --include='*.tsx'` 仅剩 ResizeObserver 类无关命中
- [ ] **Step 5: Commit** — `refactor(desktop): one boot path; project switches go through openProject; switching renders`

### Task 6: WorkerRecord prompt 持久化 + inbox 落盘

**Files:**
- Modify: `zenagent/app/taiji/registry.py`(`WorkerRecord` +2 字段)
- Modify: `zenagent/app/taiji/supervisor.py`(`_inbox` dict 移除,改 `record.inbox`;spawn 存 prompt)
- Modify: `zenagent/cli/rpc.py` board 投影增可选字段 `prompt`(经 `board.project_worker`)
- Test: `tests/test_taiji_registry.py`、`tests/test_taiji_supervisor.py`(追加)

**Interfaces:**
- Produces:
  - `WorkerRecord` 增:`prompt: str = ""`、`inbox: list[str] = field(default_factory=list)`(dataclass 需 `from dataclasses import field`)。原子写入沿用现有 mkstemp+`os.replace` 路径。
  - spawn 在首 turn 前 `record.prompt = <初始任务文本>` 并 `_save()`。
  - inbox 语义不变、存储换家:`supervisor._inbox` 删除;`:357,:366,:389,:513,:517,:530,:536` 各触点改 `record.inbox`;**每次变更后跟一次 `self._save()`**(与所在状态变更同帧);turn 链尾投递逻辑(`:513-536`)行为逐字保持。
  - `board.project_worker` 输出增 `"prompt": record.prompt`(additive;desktop `TaijiWorker` 加 `prompt?: string` 可选字段)。

- [ ] **Step 1: 写失败测试**

```python
def test_registry_roundtrips_prompt_and_inbox(...):   # 存→读两字段不丢
async def test_inbox_survives_supervisor_restart(...):  # running worker + 排队插话 → 重建 supervisor → message 后链尾投递两条
def test_spawn_persists_prompt(...):                     # spawn 后 registry 文件含 prompt
```

- [ ] **Step 2: 跑测试确认失败** — Expected: FAIL
- [ ] **Step 3: 实现字段与迁移**(触点逐一改;`grep -n '_inbox' zenagent/app/taiji/supervisor.py` 收零)
- [ ] **Step 4: 验证** — Run: `uv run pytest tests/test_taiji_registry.py tests/test_taiji_supervisor.py tests/test_taiji_rpc.py -q && uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS
- [ ] **Step 5: Commit** — `fix(taiji): worker prompts and queued inbox survive a crash`

### Task 7: taiji payload 契约测试 + taijiStore 抽取(TaijiPanel 数据层出组件)

**Files:**
- Modify: `tests/test_rpc_contract.py`(追加 taiji 形状断言)
- Create: `desktop/src/state/taijiStore.ts`、`desktop/tests/taijiStore.test.ts`
- Modify: `desktop/src/ui/rightbar/TaijiPanel.tsx`(消费 store,净减)、`desktop/src/rpc/types.ts`(如 Task 6 未同步则补 `prompt?: string`)

**Interfaces:**
- Consumes: Task 2 的 `board.project_worker/project_board`(契约测试的 py 侧读取源)、Task 6 的 `prompt` 字段。
- Produces:
  - 契约测试:`test_taiji_worker_shape_matches_ts`(regex 提取 `project_worker` 返回 dict 的键集 vs `types.ts` `TaijiWorker` 字段集,相等断言);`test_taiji_board_shape_matches_ts`(`project_board` 键集 vs `TaijiBoard`)。
  - `desktop/src/state/taijiStore.ts`(zustand,模式对齐 `updateStore` 的可测 seam):`{ board: TaijiBoard | null; error: string | null; refresh(): Promise<void>; send(name: string, text: string): Promise<'queued' | 'started'>; merge(name: string): Promise<void>; kill(name: string): Promise<void>; peek(name: string, limit: number): Promise<{role: string; text: string}[]> }`;全部经 `useAppStore.getState().client().send(...)`;错误帧写入 `error`。轮询生命周期不变:组件 `useEffect` 依旧 2s 调 `refresh()`。
  - `TaijiPanel.tsx`:删本地 `board` useState(:360)与裸 send(:105),改订阅 store。

- [ ] **Step 1: 追加契约测试(读 `zenagent/app/taiji/board.py` 源码 regex 键集)** — Expected: 先 FAIL(prompt 字段未同步 types.ts 时)
- [ ] **Step 2: types.ts 补 `prompt?: string`**,契约测试转绿
- [ ] **Step 3: 写 `desktop/tests/taijiStore.test.ts` 失败测试**(fake client 注入,沿 updateStore seam 模式:refresh 存 board;send/merge/kill 发帧形状;错误帧置 error)
- [ ] **Step 4: 实现 taijiStore,TaijiPanel 切换消费**
- [ ] **Step 5: 验证** — Run: `uv run pytest tests/test_rpc_contract.py -q && cd desktop && pnpm build && pnpm test && pnpm e2e && cd .. && python3 scripts/check_architecture.py`;Expected: 全绿;`wc -l desktop/src/ui/rightbar/TaijiPanel.tsx` < 482
- [ ] **Step 6: Commit** — `refactor(desktop): taiji board moves into a store; payload shape pinned by contract tests`

### Task 8: locale 双通道收敛——错误码过线,面板串进字典,TS parity 测试

**Files:**
- Modify: `zenagent/cli/rpc.py` taiji 错误帧 +`code`/`params`;`zenagent/app/taiji/board.py` 如错误经投影则随移
- Modify: `desktop/src/rpc/types.ts`(ErrorFrame 可选字段)、`desktop/src/ui/rightbar/TaijiPanel.tsx`(串→键)、`desktop/src/i18n/locale.ts`(+键×2)、Create `desktop/src/i18n/errors.ts`、`desktop/tests/locale.test.ts`

**Interfaces:**
- Produces:
  - 线协议(additive):taiji 错误帧增 `"code"` 与 `"params"`(可选;`message` 保留中文原值兼容旧客户端)。码表:`taiji.inactive`、`taiji.merge_blocked`(params `{reason}`)、`taiji.worker_missing`(params `{name}`)、`taiji.bad_limit`(params `{value}`)。对应点:`rpc.py` :743(合并未执行)、:763(worker 不存在)、peek limit 非(:759 附近)、`_taiji_missing`(:842)。
  - `types.ts` 错误帧类型增 `code?: string; params?: Record<string, string>`。
  - `desktop/src/i18n/errors.ts`:`export function localizeError(frame: { message: string; code?: string; params?: Record<string, string> }): string`(code 命中字典则 `t(code, params)`,否则回退 `frame.message`)。
  - `locale.ts` 新键:`taiji.error.inactive / .mergeBlocked / .workerMissing / .badLimit` + `taiji.replyPrefix`、`taiji.bounce`、`taiji.continue` 等——**TaijiPanel.tsx 内所有 CJK 字面量**(:100 `答复：`、:102、:197 `打回：`、:322 `继续` 等)全部转 `t()` 键,双语各一。
  - 若 locale.ts 超登记值:按门绿规则同提交调高 + 注记(Task 14 收紧)。

- [ ] **Step 1: 写失败测试 `desktop/tests/locale.test.ts`**

```ts
// 两个字典 import 后:key 集合 deepEqual;无空串值;
// localizeError({message:'x', code:'taiji.worker_missing', params:{name:'w1'}}) 含 'w1' 且非 'x'
```

- [ ] **Step 2: 跑测试确认失败** — Run: `cd desktop && pnpm test`;Expected: FAIL(键/文件不存在)
- [ ] **Step 3: 实现码表 + errors.ts + 面板串迁移 + parity 值**;契约测试追加错误码枚举双端断言(读 rpc.py 码表源 vs types.ts 相邻常量或 errors.ts 键前缀)
- [ ] **Step 4: 验证** — Run: `uv run pytest tests/test_rpc_contract.py tests/test_taiji_rpc.py -q && cd desktop && pnpm build && pnpm test && pnpm e2e && cd .. && python3 scripts/check_architecture.py`;Expected: 全绿;`python3 -c "import pathlib,sys; bad=[(i,l) for i,l in enumerate(pathlib.Path('desktop/src/ui/rightbar/TaijiPanel.tsx').read_text().splitlines(),1) if any('\u4e00'<=c<='\u9fff' for c in l)]; print(*bad,sep='\n'); sys.exit(1 if bad else 0)"` 零命中 exit 0(字符串全走字典)
- [ ] **Step 5: Commit** — `feat(i18n): taiji errors cross the wire as codes; panel strings enter the dictionaries`

## Phase P2

### Task 9: rpc argparse 归一——共享旗标抽 `cli/flags.py`

**Files:**
- Create: `zenagent/cli/flags.py`
- Modify: `zenagent/cli/main.py`(`build_parser` :265-458 改委托)、`zenagent/cli/rpc.py`(`main()` :2069+ 改委托)
- Test: `tests/test_cli_flags.py`(新)

**Interfaces:**
- Produces:
  - `zenagent/cli/flags.py`:`THINKING_CHOICES`(自 `cli/main.py` 迁入,main.py re-import 保兼容)、`def add_runtime_flags(parser: argparse.ArgumentParser) -> None`。
  - 共享子集 = 两解析器旗标的交集(以 `--model/-m`、`--mode`、`--read-only`、`--thinking`、`--max-turns`、`--max-cost`、`--retries`、`--resume`、`--no-mcp`、`--mcp-file`、`--yes` 为候选,执行时以两处实际声明为准逐一核对 flag 字符串/action/default/help 一致后收编);任一侧独有的旗标留在原地。规则:同一旗标名在仓库只允许一处声明。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.parametrize("build", [main_build_parser, rpc_build_parser])
def test_shared_flags_declared_once_with_same_defaults(build):
    parser = build()
    for flag in SHARED_FLAGS:  # flags.py 导出的共享名单
        action = next(a for a in parser._actions if flag in a.option_strings)
        assert action.default == flags.DEFAULTS[flag]
```

- [ ] **Step 2: 确认失败 → 实现 → 两解析器委托**;`wc -l zenagent/cli/rpc.py` 预期 -40 左右
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS(rpc.py 登记值不再触顶)
- [ ] **Step 4: Commit** — `refactor(cli): runtime flags declared once in cli/flags.py`

### Task 10: 同步 IO 出循环——session append / trace / telemetry

**Files:**
- Modify: `zenagent/agent/session.py`(`append` :423 → async + `to_thread`)
- Modify: `zenagent/app/runtime.py`(persist 监听器 :479-486 改 async)
- Modify: `zenagent/trace.py:88`、`zenagent/telemetry.py:99-106`(异步监听器内写盘 `to_thread`)
- Test: `tests/test_session.py`(追加并发顺序用例)

**Interfaces:**
- Produces: `async def append(self, entry) -> None`(体:`await asyncio.to_thread(self._write_line, entry)`);所有调用方改 await(调用方均在异步上下文;执行时 `grep -rn '\.append(' zenagent/app zenagent/cli --include='*.py' | grep -i session` 核对站点)。监听器派发若为顺序 await,则 to_thread 保序成立。
- 约束(沿 11-07 计划 Review Focus):**逐条 await,禁止 fire-and-forget**。

- [ ] **Step 1: 写失败测试**

```python
async def test_concurrent_append_preserves_order(tmp_path):
    # gather 100 条 append,读回文件断言行序 == 提交序
```

- [ ] **Step 2: 确认失败 → 实现 session/runtime/trace/telemetry 改造**
- [ ] **Step 3: 验证** — Run: `uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS(golden 会话测试全绿)
- [ ] **Step 4: Commit** — `fix(kernel): session, trace, telemetry writes leave the event loop`

### Task 11: `.catch(() => {})` 纪律——静默吞错分流

**Files:**
- Modify: `desktop/src/state/appStore.ts`(:284, :303, :315, :323, :341, :379, :416 七处)

**Interfaces:**
- Consumes: `useNoticeStore`(`stores.ts:145`)的 `show({ kind: 'error', text })`。
- Produces: 规则——凡 `.send(...)` 链上的吞错改 `.catch((error) => useNoticeStore.getState().show({ kind: 'error', text: <具体文案或 String(error)> }))`;纯本地持久化(如 localStorage 清理)的吞错改 `.catch((error) => console.debug('prefs:', error))`。逐点判断归类,不允许留在中间态(既不通知也不日志)。

- [ ] **Step 1: 逐点归类改造**(七处,每处一行注释标注归类理由)
- [ ] **Step 2: 验证** — Run: `grep -n 'catch(() => {})' desktop/src/state/appStore.ts; echo $?`;Expected: 无命中(exit 1);`pnpm build && pnpm test && pnpm e2e` 全绿
- [ ] **Step 3: Commit** — `fix(desktop): no silent catches in appStore`

### Task 12: `prefs.ts` 补测试

**Files:**
- Create: `desktop/tests/prefs.test.ts`

**Interfaces:**
- Consumes: `desktop/src/state/prefs.ts` 的 `loadPrefs/savePref/spawnArgs/replayFrames` 纯函数。
- Produces: 用例——空/损坏 localStorage 时 `loadPrefs` 回默认;`savePref` 写后 `loadPrefs` 读回一致;`spawnArgs` 组合 model/mode/thinking 顺序;`replayFrames` 只回放匹配会话帧。

- [ ] **Step 1: 写测试** → **Step 2: `pnpm test` 全绿**(纯函数,预期直接绿;若发现缺陷先修函数再绿)
- [ ] **Step 3: Commit** — `test(desktop): cover the prefs module`

### Task 13: REPL 还债——reload 去重 + 最终渲染走 TranscriptBuilder(独立可砍)

**Files:**
- Modify: `zenagent/cli/repl.py`(:564-579 与 :706-713 重复 reload 体;最终渲染改造)
- Test: 既有 repl/golden 套件 + 新golden 对照

**Interfaces:**
- Produces:
  - (a) 抽 `async def _do_reload(self) -> ...`,两处 reload 分支委托,行为逐字不变。
  - (b) `_EventPrinter` 喂 `app.views.TranscriptBuilder`:每事件过 builder;**定稿消息/工具节点**的输出改由 builder 快照渲染(继承孤儿配对/稳定行 id/notice 修复);**流式增量打印路径保持现状不动**。验收:改前对一组回放 fixture 固化 stdout golden,改后逐字节一致。

- [ ] **Step 1: 固化 golden**(选 2-3 个会话回放 fixture,先跑改前输出存 `tests/golden/repl_*.txt`)
- [ ] **Step 2: reload 去重**(机械)→ 跑 repl 套件
- [ ] **Step 3: builder 接入**(只动定稿渲染;每步跑 golden 对照)
- [ ] **Step 4: 验证** — Run: `uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py`;Expected: 全 PASS;`wc -l zenagent/cli/repl.py` < 1023
- [ ] **Step 5: Commit** — `refactor(repl): one reload body; finalized rendering rides TranscriptBuilder`
- 注:此任务风险最高、独立成 commit;执行中若 golden 无法逐字节对齐,允许回退 (b) 仅保留 (a),并在 commit message 注明遗留。

### Task 14: rpc.py → `cli/rpc/` 包拆分 + allowlist 收紧(还债收尾)

**Files:**
- Create: `zenagent/cli/rpc/__init__.py`、`codec.py`、`server.py`、`ops_chat.py`、`ops_session.py`、`ops_config.py`、`ops_files.py`、`ops_taiji.py`、`cli.py`(删除原 `zenagent/cli/rpc.py` 文件,包目录替代)
- Modify: `docs/architecture/allowlist.json`(收紧)
- Test: 既有全部 `tests/test_*rpc*.py` 零改动通过 + 门

**Interfaces:**
- Produces:
  - `zenagent/cli/rpc/__init__.py`:re-export `main, RpcServer, PROTOCOL_VERSION, serialize_event` 及测试现存导入名(`grep -rn 'from zenagent.cli.rpc import\|cli\.rpc\.' tests/ zenagent/ --include='*.py'` 实测清单为准)——8 个测试文件零改动。
  - `codec.py`:模块级线编解码(`_message_projection`、`serialize_event`、usage 薄适配,:94-257 段)。
  - `server.py`:`RpcServer` 核心(`__init__/_send/_handle_line` 派发 + hello/approval/ask/shutdown/status);`ops_*.py`:按 `_handle_line` 的 `kind` 分组的域 mixin 类(`ChatOps/SessionOps/ConfigOps/FilesOps/TaijiOps`),`class RpcServer(ChatOps, ..., TaijiOps)` 组合。**纯搬移,零行为改动**。
  - `cli.py`:`main()`(Task 9 后已是薄壳)。
  - 硬规则:每个新文件 < 800 行(门自然验证);搬移后 `docs/architecture/allowlist.json` 删除 `zenagent/cli/rpc.py` 条目,并收紧 Task 2/5/8/13 已缩文件的登记值至实测。

- [ ] **Step 1: 建 `codec.py`(先搬模块级函数)** → 跑全部 rpc 测试
- [ ] **Step 2: 建 ops mixin 与 server 组合**(按域逐个搬,每搬一域跑一次测试)
- [ ] **Step 3: 建 `cli.py` + `__init__.py` shim,删旧文件**
- [ ] **Step 4: 收紧 allowlist**
- [ ] **Step 5: 验证** — Run: `uv run pytest -q && uv run ruff check && python3 scripts/check_architecture.py; cd desktop && pnpm build && pnpm test && pnpm e2e`;Expected: 全 PASS,门零条目触顶
- [ ] **Step 6: Commit** — `refactor(rpc): the wire module becomes a package; allowlist debt paid down`

---

## 执行顺序与依赖

```
T1 ────────────────────────────────────────────────► 门全程绿
T2 → T7 → T8(taiji 边界 → 契约 → 错误码)
T3 → T4 → T6(git 出 loop → supervisor 拆分 → 持久化)
T5 → T11(desktop boot → 吞错)
T9、T10、T12、T13 任意穿插
T14 最后(收拢 T2/T5/T8/T9/T13 的缩减,还 allowlist 债)
```

T13 标注独立可砍。全部完成后:`docs/reviews/2026-11-08-architecture-review.md` 的 P0/P1/P2 清单逐项对勾,门、2463+ 测试、e2e 为最终判据。
