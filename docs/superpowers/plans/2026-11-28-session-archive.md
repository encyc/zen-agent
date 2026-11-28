# Session 归档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 project session 加归档：sidecar 记状态、文件不动、sidebar 隐藏、Settings→Projects 可恢复、agent 会话工具仍可见。

**Architecture:** 内核侧新增 `.archive.json` sidecar（与 pins/titles 同构）+ `list_sessions(archived=...)` 过滤；RPC 新增 host 侧 `session/archive` op 与列表 `archived` 参数；桌面新增归档 store、sidebar 菜单项与 Settings 归档面板。所有路径不变，恢复=删 sidecar 一行。

**Tech Stack:** Python (uv/pytest) + JSON-RPC over stdio（协议 codegen `scripts/gen_rpc_ts.py` → `wire.gen.ts`）+ React/zustand（node --test）。

**Spec:** `docs/superpowers/specs/2026-11-28-session-archive-design.md`

## Global Constraints

- 归档**绝不移动/改写 JSONL 文件本体**；只写 sidecar（原子替换模板，与 `_write_session_pins` 一致）。
- sidecar 缺失/损坏/形态不对 → 读作空，**绝不**让列表报错（pins/titles 同一容错约定）。
- `list_sessions` 默认 `archived="exclude"`：CLI、REPL、`--resume latest` 行为不变。
- agent 三件套工具保持只读：`cache=False`，不新增任何写路径。
- legacy 目录（`~/.zenagent/sessions`）拒绝归档（先 adopt）。
- i18n：桌面端新增键必须 zh/en 两份都加（`desktop/tests/i18nKeys.test.ts` + `scripts/check_i18n.py` 把关）。
- 协议改动必须重新生成 `desktop/src/rpc/wire.gen.ts`（`uv run python scripts/gen_rpc_ts.py`；`scripts/check.sh` 用 `--check` 门禁）。
- 每个任务以 `uv run pytest -q <相关文件>` 全绿 + 提交收尾；最终 `bash scripts/check.sh` 全绿。

## Review Focus

- **归档当前正在运行的会话**：run 进行中必须拒绝（`session_busy`），空闲时归档后桌面切到新会话。测试：Task 4 的 busy 拒绝测试 + Task 6 的切换序列。
- **损坏的 `.archive.json`**：读作空 `{}`，列表/恢复不受影响。测试：Task 1 损坏容错测试。
- **对不存在/非 jsonl 文件调 archive**：`ValueError` → RPC `session_invalid`，不产生 sidecar 脏数据。测试：Task 1 拒绝测试 + Task 4 refusal 测试。
- **归档 + 置顶叠加**：归档的置顶会话不进 sidebar（exclude 过滤在 pinned 排序前生效），恢复后置顶原样。测试：Task 3 entries 测试。
- **Settings 在项目未打开时恢复会话**：host 侧 op 不依赖 slot，无 slot 也能写 sidecar。测试：Task 4 host 测试。

---

### Task 1: 内核 — archive sidecar 与列表过滤

**Files:**
- Modify: `zenagent/agent/session.py`（sidecar 常量区 ~line 63-67、`__all__` ~line 44-75、pins 区块后 ~line 311、`SessionInfo` ~line 395、`list_sessions` ~line 825、`latest_session` ~line 888）
- Test: `tests/test_session.py`

**Interfaces:**
- Produces（后续任务依赖的精确签名）:
  - `ARCHIVE_FILENAME = ".archive.json"`
  - `read_session_archived(directory: Path | None = None) -> dict[str, float]`（文件名 → 归档 epoch 秒；缺省 `{}`）
  - `set_session_archived(session_file: Path, archived: bool) -> float`（返回归档时间戳；取消归档返回 `0.0`；非存在的 `.jsonl` 或 legacy 目录内 → `ValueError`）
  - `list_sessions(limit=20, *, scope="project", root=None, cache=True, archived: str = "exclude")`（`"exclude"|"include"|"only"`，非法值 `ValueError`；`SessionInfo` 加 `archived: bool = False`、`archived_at: float = 0.0`）
  - `latest_session()` 跳过归档文件

- [ ] **Step 1: 写失败测试**（加在 `tests/test_session.py` 的 pins 测试区块之后，风格同 `test_pins_sidecar_roundtrip_and_corruption`）:

```python
def test_archive_sidecar_roundtrip_and_corruption(tmp_path):
    from zenagent.agent.session import read_session_archived, set_session_archived

    session = tmp_path / "s.jsonl"
    session.touch()
    assert read_session_archived(tmp_path) == {}

    stamp = set_session_archived(session, True)
    assert stamp > 0
    assert read_session_archived(tmp_path) == {"s.jsonl": stamp}
    assert set_session_archived(session, False) == 0.0
    assert read_session_archived(tmp_path) == {}

    (tmp_path / ".archive.json").write_text("{broken", encoding="utf-8")
    assert read_session_archived(tmp_path) == {}
    (tmp_path / ".archive.json").write_text('{"s.jsonl": 1}', encoding="utf-8")
    assert read_session_archived(tmp_path) == {"s.jsonl": 1.0}


def test_set_session_archived_rejects_missing_or_non_jsonl(tmp_path):
    from zenagent.agent.session import set_session_archived

    with pytest.raises(ValueError):
        set_session_archived(tmp_path / "nope.jsonl", True)
    with pytest.raises(ValueError):
        set_session_archived(tmp_path / "notes.txt", True)  # 不存在
    (tmp_path / "notes.txt").touch()
    with pytest.raises(ValueError):
        set_session_archived(tmp_path / "notes.txt", True)


def test_listing_filters_archived_by_mode(tmp_path):
    from zenagent.agent.session import list_sessions, set_session_archived

    kept = tmp_path / "keep.jsonl"
    gone = tmp_path / "gone.jsonl"
    Session(kept)  # 写信封 + 内容
    Session(gone)
    set_session_archived(gone, True)

    names = lambda mode: [p.path.name for p in list_sessions(scope="project", root=tmp_path, archived=mode)]
    assert "gone.jsonl" not in names("exclude")
    assert set(names("include")) == {"keep.jsonl", "gone.jsonl"}
    assert names("only") == ["gone.jsonl"]
    only = list_sessions(scope="project", root=tmp_path, archived="only")[0]
    assert only.archived is True and only.archived_at > 0
    with pytest.raises(ValueError):
        list_sessions(scope="project", root=tmp_path, archived="sometimes")


def test_latest_session_skips_archived(tmp_path):
    from zenagent.agent.session import latest_session, set_session_archived

    old = tmp_path / "20260101-000000.jsonl"
    new = tmp_path / "20260102-000000.jsonl"
    Session(old); Session(new)
    set_session_archived(new, True)
    assert latest_session(root=tmp_path) == old
```

（`Session(...)` 构造写文件的方式与文件内现有测试保持一致；若现有测试用 `session.append`，照抄其写法。）

- [ ] **Step 2: 跑测试确认失败**: `uv run pytest tests/test_session.py -q -k archived` → FAIL（`ImportError: cannot import name 'read_session_archived'`）

- [ ] **Step 3: 实现**：
  - 常量 `ARCHIVE_FILENAME = ".archive.json"` 紧跟 `PINS_FILENAME`。
  - `read_session_archived` / `_write_session_archived` / `set_session_archived`：照 `_pins_path`/`_write_session_pins`/`set_session_pin` 的结构；校验 `target.suffix == ".jsonl" and target.is_file()`，否则 `ValueError(f"not a saved session file: {target}")`；legacy 守卫复用 `_project_label` 里的判法（`base.resolve() == project.legacy_sessions_dir().resolve()` → `ValueError("legacy sessions are read-only; adopt into a project first")`，异常吞 OSError）。sidecar 值存 `float(time.time())`，写 `json.dump` dict。
  - `list_sessions` 加参 `archived: str = "exclude"`，函数级校验（风格同 `_SCOPES` 检查）；循环里每个 directory 读一次 `read_session_archived(directory)`；过滤放在"信封空文件跳过"**之前**（`mode == "exclude" and path.name in archived → continue`；`mode == "only" and path.name not in archived → continue`）；命中时填 `info.archived/archived_at`。
  - `SessionInfo` 加两个默认字段（不破坏 `SessionIndex` 构造）。
  - `latest_session`：`scope="project"` 分支读 sidecar 后过滤文件名；`scope="all"` 分支同样按目录过滤。

- [ ] **Step 4: 跑测试确认通过**: `uv run pytest tests/test_session.py tests/test_session_index.py tests/test_app_session_meta.py -q` → PASS（全量也跑一次防回归：`uv run pytest -q -x tests/test_session.py tests/test_cli.py` 不必，直接 Task 8 收口）

- [ ] **Step 5: Commit**: `git add zenagent/agent/session.py tests/test_session.py && git commit -m "feat(session): archive sidecar and listing filter"`

### Task 2: agent 工具 — 归档会话仍可搜、可列、可读

**Files:**
- Modify: `zenagent/tools/sessions.py`（`_row` line 198、`_header` line 297、`_list_sessions` line 226、`_search_sessions` line 402、`_resolve_session` line 516）
- Test: `tests/test_tools_sessions.py`

**Interfaces:**
- Consumes: Task 1 的 `list_sessions(archived=...)` 与 `SessionInfo.archived`
- Produces: 三件套行文案对归档会话追加 ` (archived)` 后缀；行为只多不少。

- [ ] **Step 1: 写失败测试**（跟随 `tests/test_tools_sessions.py` 现有 fixture `search_home` 的构造方式）:

```python
async def test_tools_see_archived_sessions_marked(...):
    # 建 session A（保留）与 B（set_session_archived(B, True)），二者都含可搜文本
    # list_sessions 工具结果含 B 且行尾有 " (archived)"
    # search_sessions 命中 B；read_session 可读 B
```

- [ ] **Step 2: 确认失败**: `uv run pytest tests/test_tools_sessions.py -q -k archived` → FAIL

- [ ] **Step 3: 实现**：三处 `list_saved_sessions(...)` 调用各加 `archived="include"`；`_row`/`_header` 末尾按 `info.archived` 追加 ` (archived)`。

- [ ] **Step 4: 确认通过**: `uv run pytest tests/test_tools_sessions.py -q` → PASS

- [ ] **Step 5: Commit**: `git add zenagent/tools/sessions.py tests/test_tools_sessions.py && git commit -m "feat(tools): session tools keep archived sessions visible"`

### Task 3: SessionManager — entries 透传与 set_archived

**Files:**
- Modify: `zenagent/app/sessions.py`（`SessionEntry` line 105、`as_wire` line 120、`entries` line 165）
- Test: `tests/test_app_sessions.py`

**Interfaces:**
- Consumes: Task 1 的内核签名
- Produces（Task 4 RPC 依赖）:
  - `SessionEntry.archived: bool = False`、`SessionEntry.archived_at: float = 0.0`，出现在 `as_wire()`（键名 `archived`、`archived_at`）
  - `SessionManager.entries(*, scope="project", limit=None, live=(), archived: str = "exclude")`
  - `SessionManager.set_archived(path: Path, archived: bool) -> float`（委托内核 `set_session_archived`；不 materialize——归档未保存会话应被内核拒绝）

- [ ] **Step 1: 写失败测试**（跟随 `test_delete_moves_to_trash_instead_of_unlink` 的 `session_runtime` fixture 风格）:

```python
async def test_entries_exclude_archived_and_set_archived_roundtrips(...):
    # 建 session A/B；manager.set_archived(B, True)
    # entries() 默认不含 B；entries(archived="only") 恰含 B 且 entry.archived/archived_at 正确
    # wire dict 带 "archived": True、"archived_at": >0
async def test_pinned_archived_session_stays_hidden(...):
    # A 置顶 + 归档 → entries() 不含 A；恢复后 entries() 含 A 且 pinned 仍 True
```

- [ ] **Step 2: 确认失败**: `uv run pytest tests/test_app_sessions.py -q -k archived` → FAIL

- [ ] **Step 3: 实现**：`entries` 加 `archived` 参数透传 `list_sessions`；占位行/live 行/current 插入逻辑仅在 `archived == "exclude"` 时执行（include/only 是纯列表，否则 current 占位会混进归档列表）；`set_archived` 直接委托并返回时间戳。

- [ ] **Step 4: 确认通过**: `uv run pytest tests/test_app_sessions.py -q` → PASS

- [ ] **Step 5: Commit**: `git add zenagent/app/sessions.py tests/test_app_sessions.py && git commit -m "feat(app): archive/restore through SessionManager"`

### Task 4: RPC — `session/archive` op、列表 `archived` 参数、wire 重新生成

**Files:**
- Modify: `zenagent/cli/rpc/protocol/_chat.py`（`SessionScopeRequest` line 126、session OpSpec 表 line 505-540）
- Modify: `zenagent/cli/rpc/protocol/_views.py`（`SessionSummary` shape ~line 148）
- Modify: `zenagent/cli/rpc/ops_session.py`（`session_list_frame` line 14、`list` op line 250）
- Modify: `zenagent/cli/rpc/host.py`（`_handle_line` 的 session 分支 ~line 158-165、新 `_session_archive`、`_session_list` line 447 透传）
- Modify: `desktop/src/rpc/wire.gen.ts`（生成，不手写）
- Test: `tests/test_rpc_contract.py`（shape/契约处跟随现有写法）、`tests/test_rpc_session_routing.py` 或 ops 测试现有文件

**Interfaces:**
- Consumes: Task 3 的 `entries(archived=...)` / `as_wire` 字段；Task 1 内核 `set_session_archived`
- Produces（Task 6/7 桌面依赖）:
  - 请求 `{type:"session", op:"list", scope:"all", archived?:"exclude"|"include"|"only"}`（缺省 exclude）
  - 请求 `{type:"session", op:"archive", path:string, archived:boolean}` → 结果 `{path, archived, archived_at:number}`
  - `SessionSummary` 加 `archived:boolean`、`archived_at:number`
  - host 侧处理 archive：无需打开项目；目标会话被某个 slot 持有且 `slot.busy` 且 `archived=true` → `error_frame(..., "session_busy")`

- [ ] **Step 1: 写失败测试**:
  - `tests/test_rpc_contract.py`：跟随现有 shape/契约测试给 `SessionSummary` 新字段与 `session/archive` OpSpec 加断言（照现有 diff 断言风格）。
  - 新增 host 行为测试（放现有 RPC host 测试文件里，跟随其 fixture）:

```python
async def test_archive_op_writes_sidecar_without_a_slot(...):
    # 直接对 host 发 {"type":"session","op":"archive","path":str(f),"archived":True}
    # 断言 result ok、archived_at>0、.archive.json 侧车内容、SessionInfo 过滤生效
async def test_archive_refuses_busy_holder(...):
    # slot 持有 f 且 busy（用现有 busy 模拟法）→ error_frame code=session_busy
async def test_session_list_passes_archived_filter(...):
    # {"op":"list","scope":"all","archived":"only"} 只返回归档行
```

- [ ] **Step 2: 确认失败**: `uv run pytest tests/test_rpc_contract.py tests/test_rpc_session_routing.py -q -k archived` → FAIL

- [ ] **Step 3: 实现**：
  - 协议：`SessionScopeRequest` 加 `"archived": Field(STRING_LAX)`；新 `SessionArchiveRequest {path: STRING required, archived: BOOL required}`；OpSpec `"archive": OpSpec("session", SessionArchiveRequest, {"path": Field(STRING), "archived": Field(BOOL), "archived_at": Field(FLOAT)})` 插在 `delete` 之后；`SessionSummary` shape 加 `"archived": Field(BOOL), "archived_at": Field(FLOAT)`。
  - `session_list_frame` 加 `archived: str = "exclude"` 透传 `entries`；slot `list` op 与 host `_session_list` 各自解析 `request.get("archived")`（仅接受 `exclude|include|only`，其余按 exclude）。
  - host `_handle_line`：`if kind == "session" and request.get("op") == "archive": await self._session_archive(request, request_id); return True`（放 `resume` 分支旁，在 `_session_unserved` 之前）。`_session_archive`：校验 path/archived；`holder = self._session_holder(target, self._project_tag(request))`，`holder is not None and holder.busy and archived` → `Refusal("busy: cancel the run before archiving", code="session_busy")`；`await asyncio.to_thread(set_session_archived, target, archived)`；成功 `{"path": raw, "archived": archived, "archived_at": ts}`。错误分支镜像 `_session_resume` 的 Refusal/Exception 结构（`op="archive"`）。恢复（archived=False）不检查 busy。

- [ ] **Step 4: 重新生成 wire 并确认通过**: `uv run python scripts/gen_rpc_ts.py && uv run pytest tests/test_rpc_contract.py tests/test_rpc_session_routing.py tests/test_rpc_ops_layout.py -q` → PASS；`git diff desktop/src/rpc/wire.gen.ts` 只含新字段/新 op。

- [ ] **Step 5: Commit**: `git add zenagent/cli/rpc desktop/src/rpc/wire.gen.ts tests/test_rpc_*.py && git commit -m "feat(rpc): session archive op and archived listing filter"`

### Task 5: CLI `--archived` 与文档

**Files:**
- Modify: `zenagent/cli/sessionscmd.py`（`build_parser` list_parser ~line 25、`_list` line 38）
- Modify: `docs/frontend-matrix.md`（line 76 session op 表）、`CHANGELOG.md`（Unreleased/Added）

**Interfaces:**
- Consumes: Task 1 的 `list_sessions(archived="only")`
- Produces: `zen sessions list --archived` 只列归档会话（与 `--all` 正交叠加）。

- [ ] **Step 1: 写失败测试**: `tests/test_sessionscmd.py` 若存在则追加，否则并入 `tests/test_session.py` 或新建（跟随 `--json` 现测法）：`--archived` 输出恰为归档会话行。
- [ ] **Step 2: 确认失败**: `uv run pytest -q -k sessionscmd` → FAIL
- [ ] **Step 3: 实现**: `list_parser.add_argument("--archived", action="store_true", help="list only archived sessions")`；`_list` 传 `archived="only" if args.archived else "exclude"`；JSON 输出加 `"archived"`/`"archived_at"`。
- [ ] **Step 4: 确认通过** → **Step 5: Commit**: `git commit -m "feat(cli): zen sessions list --archived"`

### Task 6: 桌面 — store、动作、sidebar 菜单、i18n

**Files:**
- Modify: `desktop/src/state/stores.ts`（sessions 区块后加 `useArchivedStore`）
- Modify: `desktop/src/state/appStore.ts`（`refreshSessions` 旁加 `refreshArchivedSessions`；`deleteSession` 旁加 `archiveSession(path, archived)`；AppStore interface ~line 108/148 加签名）
- Modify: `desktop/src/ui/SidebarCoding.tsx`（菜单 line 416-539）
- Modify: `desktop/src/i18n/messages.ts`（zh ~line 41-52、en ~line 408-422 各加键）
- Test: `desktop/tests/i18nKeys.test.ts`（自动把关）、新建 `desktop/tests/archivedSessions.test.ts`

**Interfaces:**
- Consumes: Task 4 的 wire（`session/archive` op、`SessionSummary.archived/archived_at`）
- Produces（Task 7 依赖）:
  - `useArchivedStore`: `{ archived: SessionSummary[], setArchived(rows): void }`
  - `appStore.refreshArchivedSessions()`: 发 `{type:'session', op:'list', scope:'all', archived:'only'}` → `setArchived(frame.sessions)`
  - `appStore.archiveSession(path: string, archived: boolean): Promise<void>`: 发 `{type:'session', op:'archive', path, archived}`；失败用现有 `frameErrorNotice` 通道报错；成功后若 `archived===true && path===useSessionStore.getState().currentPath` → `await get().newSession()`；最后 `Promise.all([refreshSessions(), refreshArchivedSessions()])`

- [ ] **Step 1: 写失败测试** `desktop/tests/archivedSessions.test.ts`（跟随 `clientSessionTag.test.ts` 对 client 的 mock 法）：断言 `archiveSession` 发出的帧序列——归档非当前会话=单帧 archive；归档当前会话=archive 后跟一帧 `op:'new'`；恢复=单帧 `archived:false`；成功后两处 refresh 都被调用。
- [ ] **Step 2: 确认失败**: `cd desktop && pnpm test` → FAIL
- [ ] **Step 3: 实现**：
  - store + 两个动作（`refreshArchivedSessions` 在 `archiveSession`/启动时与 `refreshSessions` 一同被 Settings 页触发，Task 7 接线）。
  - sidebar 菜单：置顶项之后、Delete 之前插入 Archive 按钮，`role="menuitem"`，显示条件 `menuSession.messages > 0 || menuSession.path === currentPath`；`onClick` → `setMenu(null); void archiveSession(menuSession.path, true)`。归档是可逆操作，不做两步确认。
  - i18n 键（zh/en 对齐，en 直译）：`'session.archive'`（归档/Archive）、`'session.restore'`（恢复/Restore）。
- [ ] **Step 4: 确认通过**: `cd desktop && pnpm test` → PASS
- [ ] **Step 5: Commit**: `git commit -m "feat(desktop): archive action on sidebar session rows"`

### Task 7: Settings → Projects 归档面板

**Files:**
- Modify: `desktop/src/state/projectRows.ts`（`ProjectRowData` 加 `archived: number`；`projectRows` 加第三参 `archivedSessions: Pick<SessionSummary,'project_path'>[] = []`）
- Modify: `desktop/src/ui/pages/ProjectSettings.tsx`（`ProjectRow` 加展开归档区；`ProjectSettings` 挂载时 `refreshArchivedSessions`）
- Modify: `desktop/src/ui/pages/ProjectSettings.module.css`（跟随现有 `css.project` 结构加少量类）
- Modify: `desktop/src/i18n/messages.ts`（zh/en）
- Test: `desktop/tests/projectRows.test.ts`（追加）

**Interfaces:**
- Consumes: Task 6 的 `useArchivedStore` / `archiveSession`
- Produces: 每个项目行可展开"已归档 (N)"，行内列出标题 + 归档时间（`formatAge(archived_at)`）+ 恢复按钮。

- [ ] **Step 1: 写失败测试**（追加到 `desktop/tests/projectRows.test.ts`）:

```ts
// projectRows(sessions, entries, archived) 归档行按 project_path 计数进 row.archived
// 无归档时 row.archived === 0；归档行 project_path 为空的不计入任何行
```

- [ ] **Step 2: 确认失败**: `cd desktop && pnpm test -- projectRows` → FAIL
- [ ] **Step 3: 实现**：
  - `projectRows` 统计归档计数；`ProjectRow` 用 `useState` 展开态，归档数 > 0 时显示展开开关（复用 sidebar 项目组头样式思路，按 module.css 现有类风格定）。
  - 展开区行：标题（title || name）+ `t('settings.projectArchivedAt', { when: formatAge(archived_at, locale) })` + 恢复按钮 `onClick={() => void archiveSession(row.path, false)}`。
  - i18n：`'settings.projectArchived'`（'已归档 ({count})' / 'Archived ({count})'）、`'settings.projectArchivedAt'`（'{when} 归档' / 'archived {when}'）、空态 `'settings.projectArchivedEmpty'`（'没有已归档的会话' / 'No archived sessions'）。
  - `ProjectSettings` 的 `useEffect` 同时调 `refreshSessions()` 与 `refreshArchivedSessions()`；归档行数据来自 `useArchivedStore` 按 `project_path` 过滤到对应展开区。
- [ ] **Step 4: 确认通过**: `cd desktop && pnpm test` → PASS（`i18nKeys` 自动校验双份键）
- [ ] **Step 5: Commit**: `git commit -m "feat(desktop): archived sessions panel in project settings"`

### Task 8: 全量门禁

- [ ] **Step 1**: `bash scripts/check.sh`（uv sync → ruff → pytest → gen_rpc_ts --check）→ 全绿
- [ ] **Step 2**: `cd desktop && pnpm test && pnpm build`（tsc --noEmit 类型门禁）→ 全绿
- [ ] **Step 3**: 桌面手动冒烟（可选，Playwright e2e `session.spec.ts` 若易扩展则补一条归档-恢复流；否则记录在 PR 描述里）
- [ ] **Step 4**: Commit（若有遗留）+ `git log --oneline main..HEAD` 汇总

## Self-Review 结论

- Spec 覆盖：sidecar/过滤（T1）、工具可见（T2）、SessionManager（T3）、RPC op+参数+wire（T4）、CLI（T5）、sidebar 菜单（T6）、Settings 面板（T7）、i18n/文档/CHANGELOG（T5/T6/T7/T8）——无缺口。
- 归档当前会话的服务端 switch 语义改为**客户端两步**（T6 的 archiveSession 内嵌 newSession），与 spec "桌面端先切再归档"一致；host 侧只做 busy 拒绝。
- 类型一致性：`archived_at: float`（Python）/ `archived_at: number`（TS）两端已对齐；op 键名 `archive`、参数 `path`/`archived` 全文统一。
- Review Focus 五项均有归属测试（T1/T3/T4/T6）。
