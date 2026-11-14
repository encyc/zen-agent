# 架构审计清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 P0→P1→P2 顺序清掉 2026-11-14 架构审计（docs/reviews/2026-11-14-architecture-audit.md）的全部 3 P0 / 10 P1 / 24 P2 问题点。

**Architecture:** 三个 P0 各自独立可先做；P1 里先把共享设施立起来（`app/jsonl.py` 统一 fsync+残尾修复、`effective_worker_mode` 权限归一、Runtime 工具工厂反转），再把消费方逐一迁过来；P2 按「一行修复」→「拆文件」→「治理重基线」收尾。每个任务独立可测、独立提交。

**Tech Stack:** Python 3.11+ stdlib（唯一运行时依赖 httpx）、pytest（filterwarnings=error）、ruff、uv；桌面侧 TypeScript/Tauri 仅 Task 21/22 触及。

**Spec:** `docs/reviews/2026-11-14-architecture-audit.md`（随本分支提交，行号锚点以它为准）

## Global Constraints

- 运行时零第三方依赖（仅 httpx）：新模块只用 stdlib；mypy/pytest-timeout 只进 dev 依赖
- `filterwarnings=error`：任何新告警都会红测试；新日志走 `_log.warning`，不走 `warnings`
- 用户可见文案走 i18n（`zenagent/i18n/`，英文目录为准）
- RPC 帧契约改动必须双向同步：`tests/test_rpc_contract.py` + `desktop/src/rpc/types.ts`
- LOC 门禁 `scripts/check_architecture.py`（allowlist 只许缩不许涨）：拆分任务做完再重基线
- 治理账本：改完结构后 manifest version+1 + deltas.md 记账（收尾任务统一做）
- 每任务：相关测试绿 → `git commit`；每完成一个 Phase 跑一次 `scripts/check.sh`

## Review Focus

审计暗示了但单条任务测不全的五类失败模式（每条都已在对应任务里钉了测试）：

1. **撕裂残尾**：`repair_tail` 只许截断无换行结尾的尾行，完整行一个字节都不能少 — Task 7 三处断言
2. **flock 不可用的 FS**：降级不得误杀活跑（`session_is_live` 必须保守返回 True）— Task 15
3. **auto 旁路**：任何 worker/角色运行时都不得继承 auto；protected 读在任何 mode 下都要审批 — Task 1/4
4. **RPC 帧向后兼容**：新字段必须 optional、PROTOCOL_VERSION 不变，旧 desktop 能读新 server — Task 21
5. **import 重排炸循环**：PEP 562 改造后 `from zenagent.app import X` 六种用法必须仍可用 — Task 2 + Task 3 的分层测试

---

## Phase A — P0

### Task 1: 堵住「read 任意读 + fetch 外传」密钥窃取链（P0-1）

**Files:**
- Modify: `zenagent/app/safety.py`（在 `classify_write_path` 附近加读分类，复用 `_PROTECTED_HOME_DIRS`）
- Modify: `zenagent/app/permissions.py:398-401`（read-only 自动放行前插入 protected 读检查）
- Modify: `zenagent/tools/web/tools.py:221`（fetch `approval="read"` → `"ask"`）
- Test: `tests/test_permissions.py`（新增 3 个用例）

**Interfaces:**
- Produces: `safety.classify_read_path(raw: str, root: Path) -> str`，返回 `"protected" | "project" | "outside"`；protected 判定 = 展开用户目录后位于 `_PROTECTED_HOME_DIRS` 内
- Produces: 权限门行为：read-only 工具若 `classify_read_path == "protected"` → 走 `_ask`（**auto 模式也 ask**）；fetch 的 URL 参数不参与 protected 判定

- [ ] **Step 1: 写失败测试**（`tests/test_permissions.py` 追加）：
  - `test_read_protected_dir_requires_approval_even_in_auto`：gate mode=auto，read_file `~/.zenagent/settings.json` → 返回 False 且 hint 非空（要求审批）
  - `test_read_project_file_still_free`：read_file 项目内路径 → True
  - `test_fetch_requires_approval`：fetch（approval=ask）在默认 ask 模式 → False 待审批
- [ ] **Step 2: 跑测试确认失败**：`uv run pytest tests/test_permissions.py -k "protected or fetch_requires" -v` → FAIL
- [ ] **Step 3: 实现**：`classify_read_path` 放 safety.py（写法对照 `classify_write_path:199-264`，resolve 后前缀匹配）；permissions.py 在 `if tool_is_read_only(...)` 之前插入 protected 检查（参数取 args 里的 path 类字段，read_file/list_dir 的参数名从 tool schema 读）；web/tools.py 改 approval 字段
- [ ] **Step 4: 跑测试通过** + `uv run pytest tests/test_permissions.py tests/test_tools_fs.py tests/test_web_tools.py -q` 全绿（存量用例若依赖 fetch 免审批，按新语义改断言，逐条注明）
- [ ] **Step 5: Commit** `fix(security): protect credential reads and gate fetch behind approval`

### Task 2: app/__init__.py 惰性化，拆掉 15 节点大环的锚（P0-2）

**Files:**
- Modify: `zenagent/app/__init__.py:7-28`（全部顶层 import 改 PEP 562 `__getattr__`）
- Test: `tests/test_layering.py`（新建，本任务先放 app 包冒烟用例）

**Interfaces:**
- Produces: `from zenagent.app import ChatController/CommandRegistry/ModelManager/PermissionBroker/Runtime/RuntimeOptions/SessionManager/views 各成员` 全部仍可用（经 `__getattr__` 惰性导入）；`from zenagent.app import plugin_wiring, records, subagent_runs, clipboard` 子模块用法不变（import 系统原生支持）

- [ ] **Step 1: 写失败测试**：`tests/test_layering.py::test_app_pkg_has_no_eager_reexport` — 读 `zenagent/app/__init__.py` 源码，断言不含 `from zenagent.app.` 与 `import zenagent.app.` 的顶层语句（模块级）；另加 `test_from_app_import_still_works`：六种 re-export 名逐一 `from zenagent.app import X` 后属性可用
- [ ] **Step 2: 确认失败**：`uv run pytest tests/test_layering.py -v` → 第一条 FAIL
- [ ] **Step 3: 实现**：`__init__.py` 改为 docstring + `__all__` + `_LAZY = {"ChatController": "zenagent.app.chat", ...}` 映射 + 模块级 `def __getattr__(name): importlib.import_module(_LAZY[name]); return getattr(...)`（标准 PEP 562 写法）；子模块名不进映射
- [ ] **Step 4: 验证**：`uv run pytest tests/test_layering.py tests/test_bootstrap.py tests/test_app_chat.py -q` 绿；冒烟 `uv run python -c "from zenagent.app import Runtime, ChatController, views"` 无异常
- [ ] **Step 5: Commit** `refactor(app): lazy re-exports via PEP 562; cut the package-reentry cycle`

### Task 3: fleet 工具依赖倒置，runtime 不再点名 zen 包（P0-3）

**Files:**
- Modify: `zenagent/app/runtime.py:705-712`（删 zen.tools 延迟导入）+ `Runtime.__init__`（加 `tool_providers`）
- Modify: zen 侧注册点（`rg -n "zen_fleet\s*=" zenagent/` 找到赋值处，通常 `app/zen/fleet.py` 或 `cli/rpc/ops_zen.py`：赋值后追加 provider 注册）
- Test: `tests/test_layering.py`（追加）、`tests/test_zen_tools.py`（存量应全绿）

**Interfaces:**
- Produces: `Runtime.tool_providers: list[Callable[[Runtime], list[AgentTool]]]`；`_recompose` 末尾改为 `for provider in self.tool_providers: tools.extend(provider(self))`
- Produces: `zenagent/app/zen/tools.py` 新增 `def register_fleet_tools(runtime: Runtime) -> None`（内部 import fleet_tools/router_fleet，闭包读 `runtime.zen_fleet`/`runtime.session.path`）；zen 侧赋值 `runtime.zen_fleet = fleet` 之后调用它，再触发一次 recompose（沿用现有刷新路径）
- 消费: Task 2 的 tests/test_layering.py

- [ ] **Step 1: 写失败测试**：`tests/test_layering.py::test_runtime_does_not_import_zen` — 读 runtime.py 源码断言不含 `zenagent.app.zen`；`::test_agent_never_imports_app_or_cli`、`::test_plugins_never_import_upward`（遍历目录源码断言，把审计确认的三条分层不变量钉死成测试）
- [ ] **Step 2: 确认失败** → runtime 源码含 zen 导入，FAIL
- [ ] **Step 3: 实现**：按 Interfaces 三处改动；`zen_fleet` 属性保留、类型注解进 TYPE_CHECKING 块
- [ ] **Step 4: 验证**：`uv run pytest tests/test_layering.py tests/test_zen_tools.py tests/test_zen_fleet.py tests/test_rpc_zen.py -q` 绿；`uv run python -c "import zenagent.app.runtime, sys; assert 'zenagent.app.zen' not in sys.modules"`
- [ ] **Step 5: Commit** `refactor(zen): inject fleet tools via Runtime.tool_providers; drop the runtime→zen cycle`

**Phase A 收口：`scripts/check.sh` 全绿后进 Phase B。**

---

## Phase B — P1

### Task 4: worker 权限归一，swarm 角色不再继承 auto 旁路（P1-2）

**Files:**
- Modify: `zenagent/app/agent_profile.py`（新增归一函数）
- Modify: `zenagent/app/zen/fleet.py:371-377`、`zenagent/app/swarm/manager.py:627-633`
- Test: `tests/test_swarm_manager.py`、`tests/test_zen_fleet.py`（各追加 1 条）

**Interfaces:**
- Produces: `agent_profile.effective_worker_mode(configured: str) -> str` — `return "ask" if configured == "auto" else configured`；两侧产线统一调用：fleet `mode = effective_worker_mode(self.mode)`（worker_mode 非 inherit 分支不变），swarm `mode=effective_worker_mode(role.mode)`

- [ ] **Step 1: 写失败测试**：`test_swarm_manager.py::test_role_auto_is_downgraded_to_ask` — manifest 配 `mode: "auto"` 的角色执行 write_file → **产生** approval frame（对照 `mode: "ask"` 同样产生；修前 auto 不产生）；`test_zen_fleet.py::test_worker_mode_helper_matches_fleet_semantics` — fleet 侧现行为不回归
- [ ] **Step 2: 确认失败**（swarm 新用例 FAIL）→ **Step 3: 实现**（helper + 两处调用；fleet 原地注释保留一句指向 helper）
- [ ] **Step 4:** `uv run pytest tests/test_swarm_manager.py tests/test_zen_fleet.py tests/test_agent_profile.py -q` 绿 → **Step 5: Commit** `fix(orchestration): swarm roles no longer inherit the auto bypass (shared helper)`

### Task 5: bootstrap 失败回滚 lease 与引用计数（P1-3）

**Files:**
- Modify: `zenagent/app/runtime.py`（`bootstrap` 内 session setter 之后的主体包 try/except）
- Test: `tests/test_bootstrap.py`（追加 1 条）

**Interfaces:**
- Consumes: `runtime.close()`（须容忍半装配字段；实现时逐字段核对，None 跳过）
- 行为：`Runtime.bootstrap` 主体抛异常 → `with contextlib.suppress(Exception): await runtime.close()` → 原异常 re-raise

- [ ] **Step 1: 失败测试** `test_bootstrap_failure_releases_lease_and_refs`：monkeypatch `zenagent.app.runtime.load_extensions` 抛 RuntimeError → `pytest.raises(RuntimeError)` 调 prepare → 随后同一路径 `acquire_session(...)` 成功（lease 已释放）、job manager 引用计数回落（经 `acquire_job_manager()` 可再 acquire/release 一轮无残留）
- [ ] **Step 2: 确认失败**（acquire 抛 SessionLeaseBusy）→ **Step 3: 实现** try/except → **Step 4:** `uv run pytest tests/test_bootstrap.py tests/test_session_lease.py -q` 绿 → **Step 5: Commit** `fix(runtime): roll back lease and refcounts when bootstrap fails midway`

### Task 6: ZenFleet.close() 等待 worker 任务收尾（P1-4）

**Files:**
- Modify: `zenagent/app/zen/fleet.py:572-582`
- Test: `tests/test_zen_fleet.py`（追加 1 条）

- [ ] **Step 1: 失败测试** `test_close_awaits_worker_tasks`：起 fleet、派一个 stub worker 任务、`await fleet.close()` → 断言所有 `handle.task.done()` 且 `_wake_task` 已结束（`filterwarnings=error` 下若有 pending-destroy 告警会红）
- [ ] **Step 2: 确认失败** → **Step 3: 实现**：close() 在 abort 后 `await asyncio.wait([h.task for h in ... if not done] + [self._wake_task], timeout=10)`，异常 suppress、超时只 `_log.warning`
- [ ] **Step 4:** `uv run pytest tests/test_zen_fleet.py tests/test_rpc_zen.py -q` 绿 → **Step 5: Commit** `fix(zen): close() awaits worker tasks so teardown actually runs`

### Task 7: app/jsonl.py —— fsync + 残尾修复下沉为共享设施（P1-5、P1-6、P2-work_items 一并）

**Files:**
- Create: `zenagent/app/jsonl.py`
- Modify: `zenagent/agent/session.py:434-439,506-565`（`_write_line` 加 flush_fsync；`load` 入口先 `repair_tail`）
- Modify: `zenagent/app/runs.py:245-255`（同上）+ load 入口
- Modify: `zenagent/app/swarm/workitems.py:128-136`（load 先 `repair_tail`）
- Modify: `zenagent/app/swarm/room.py:121-131,269-292`（`_repair_tail`/fsync 改为 import 共享实现，行为不变）
- Test: `tests/test_jsonl.py`（新建）

**Interfaces:**
- Produces: `jsonl.repair_tail(path: Path) -> None` — 文件不以 `\n` 结尾时截到最后一个 `\n`（或清空）；不存在/为空直接返回
- Produces: `jsonl.flush_fsync(handle: IO[str]) -> None` — `handle.flush(); os.fsync(handle.fileno())`
- Produces: `jsonl.append_line(path: Path, line: str) -> None` — 单次 `open("a")` 写 `line + "\n"` → flush_fsync → close（workitems/room 迁到它；session/runs 用长句柄 + flush_fsync）

- [ ] **Step 1: 失败测试** `tests/test_jsonl.py`：`test_repair_tail_truncates_only_unterminated_line`（完整两行 + 半行 → repair 后恰两行）；`test_repair_tail_ignores_complete_file/empty/missing`；`test_append_line_fsyncs`（monkeypatch os.fsync 计数 ≥1）
- [ ] **Step 2: 确认失败** → **Step 3: 实现 jsonl.py** → **Step 4: 迁四个消费方**
- [ ] **Step 5: 迁移后行为测试**（每处一条）：session 写两轮 → 人为截尾 → 再写一条 → load 断言三条全在（修前第二条丢）；runs、work_items 同型；`uv run pytest tests/test_jsonl.py tests/test_session.py tests/test_run_ledger.py tests/test_swarm_workitems.py tests/test_swarm_protocol.py -q` 绿
- [ ] **Step 6: Commit** `fix(durability): shared fsync+torn-tail repair for session/runs/work-items JSONL`

### Task 8: swarm 跨进程单写者锁（P1-7）

**Files:**
- Modify: `zenagent/app/swarm/manager.py`（`SwarmLocked` 异常 + start/close 持锁）
- Modify: `zenagent/cli/rpc/ops_swarm.py`（`SwarmLocked` → Refusal 文案）
- Test: `tests/test_swarm_manager.py`（追加）

**Interfaces:**
- Produces: 锁文件 `<machine 项目状态目录>/swarm.lock`（与 runs.jsonl 同目录），`fcntl.flock(LOCK_EX | LOCK_NB)`；占用 → `manager.SwarmLocked(RuntimeError)`；`start()` 失败即抛、不进 reconcile；`close()` 释放
- ops_swarm 捕获 → Refusal `"swarm room is open in another process"`（i18n key `swarm.locked`）

- [ ] **Step 1: 失败测试**：子进程 `fcntl.flock` 占住锁 → `manager.start()` raises SwarmLocked 且 tasks.jsonl 未被改动（断言 0 次状态改写）；释放后 start 成功
- [ ] **Step 2: 确认失败** → **Step 3: 实现** → **Step 4:** `uv run pytest tests/test_swarm_manager.py tests/test_rpc_swarm.py -q` 绿 → **Step 5: Commit** `fix(swarm): one-writer flock per project; reconcile only under the lock`

### Task 9: 会话写盘失败不再静默（P1-8）

**Files:**
- Modify: `zenagent/app/runtime.py:538-547`（persist 监听器捕获 OSError）
- Test: `tests/test_app_runtime.py`（追加）

**Interfaces:**
- Produces: persist 失败 → `runtime.error_notice(...)`（读 `error_notice.py` 现有 API）+ `runtime.persist_failures: int` 计数 + `_log.warning`；不再依赖 `agent.py:262-266` 的 stderr 兜底

- [ ] **Step 1: 失败测试**：monkeypatch `session.append` 抛 OSError → 跑一回合 → `runtime.persist_failures == 1` 且 notice 已记录（终止仍为 stop，回合不炸）
- [ ] **Step 2-5:** 确认失败 → 实现 → `uv run pytest tests/test_app_runtime.py tests/test_agent_class.py -q` 绿 → Commit `fix(runtime): surface session persist failures via error notice instead of stderr`

### Task 10: 项目层 mcp.json 需信任才 spawn（P1-1）

**Files:**
- Modify: `zenagent/mcp/config.py`（新增信任查询/写入函数）
- Modify: `zenagent/app/plugin_wiring.py:80-84`（bootstrap 过滤未信任的 project 层 stdio server + warning）
- Modify: `zenagent/cli/main.py`（mcp 子命令加 `trust <name>`）
- Test: `tests/test_mcp_trust.py`（新建）

**Interfaces:**
- Produces: 信任文件 `<machine 项目状态目录>/mcp_trust.json`（`{"trusted": ["name", ...]}`）；`config.project_stdio_untrusted(cwd, servers) -> list[str]`；`config.trust_project_server(cwd, name) -> None`（只记 project 层、带 command 的 server 名）
- 行为：未信任的 project stdio server **不连接**、warning `"MCP server '<name>' from project mcp.json is not trusted; run: zen mcp trust <name>"`；http 型不拦截

- [ ] **Step 1: 失败测试** ×3（未信任不连接；trust 后连接；user 层 server 不受影响）→ **Step 2** 确认失败 → **Step 3** 实现 → **Step 4:** `uv run pytest tests/test_mcp_trust.py tests/test_mcp_config.py tests/test_plugin_wiring.py -q` 绿 → **Step 5: Commit** `feat(mcp): project-layer stdio servers require explicit trust`

**Phase B 收口：`scripts/check.sh` 全绿。**

---

## Phase C — P1 工程面 + 文件拆分

### Task 11: 原子写三处补齐（P2 持久化组）

**Files:** Modify `zenagent/app/swarm/roster.py:52-58`（mkstemp+fsync+replace；损坏 manifest → 拒绝 merge 并报错，不当 `{}`）；`zenagent/app/zen/roster.py:60-65`、`zenagent/app/workspace_state.py:86-90`（replace 前 flush_fsync，用 `jsonl.flush_fsync`）
**Test:** `tests/test_swarm_roster.py::test_role_manifest_atomic_write`（写中途模拟失败 → 旧文件完好）、`tests/test_zen_roster.py::test_roster_write_fsyncs`
**Steps:** 失败测试 → 实现 → 相关文件测试绿 → Commit `fix(durability): atomic writes for role manifests and zen/workspace sidecars`

### Task 12: 台账 sweep 可重试 + finish 失败告警（P2）

**Files:** Modify `zenagent/app/run_wiring.py:44-47,79-88`（sweep 抛异常 → 从 `_SWEPT` 移除该 key，下次 attach 重试）；`zenagent/app/runs.py:245-255`（`_append` OSError → `_log.warning` + 计数暴露 `RunLedger.write_failures`）
**Test:** `tests/test_run_ledger.py` 追加两条（sweep 失败后二次 attach 重试；append 失败计数）
**Steps:** 失败测试 → 实现 → `uv run pytest tests/test_run_ledger.py tests/test_run_scopes.py -q` 绿 → Commit `fix(runs): retryable sweep and surfaced ledger write failures`

### Task 13: RECORD_VERSION 读取 + 重写保留未知记录（P2）

**Files:** Modify `zenagent/agent/session.py`（load 读 header version，文件版本 > 程序版本 → warning「文件比程序新」）；`zenagent/app/records.py:210-226`（`keep_for_rewrite` 保留未知 kind 的原行）
**Test:** `tests/test_session.py::test_newer_version_warns`、`tests/test_app_records.py` 或就近文件 `::test_rewrite_keeps_unknown_kinds`
**Steps:** 失败测试 → 实现 → session/records 相关测试绿 → Commit `fix(records): warn on newer files and preserve unknown record kinds on rewrite`

### Task 14: 备份 blob fsync（P2）

**Files:** Modify `zenagent/app/journal.py:184-200`（blob replace 前 flush_fsync）；`:246-254` manifest rewrite 同样补
**Test:** `tests/test_journal.py::test_blob_write_fsyncs`（monkeypatch os.fsync）
**Steps:** 失败测试 → 实现 → 测试绿 → Commit `fix(journal): fsync blobs before replace`

### Task 15: flock 不可用时保守降级（P2）

**Files:** Modify `zenagent/app/session_lease.py:111-141`（降级路径 `_log.warning` 一次性）；`:187-202`（lock 不可用 → `session_is_live` 返回 True）
**Test:** `tests/test_session_lease.py::test_is_live_conservative_without_lock`（monkeypatch flock 抛 OSError → True）
**Steps:** 失败测试 → 实现 → 测试绿 → Commit `fix(lease): conservative liveness when flock is unavailable`

### Task 16: swarm 取消竞态（P2）

**Files:** Modify `zenagent/app/swarm/runner.py:72-73`（record running 前重读 store，任务已终态 → 直接收尾该 unit 不记 running）
**Test:** `tests/test_swarm_manager.py::test_cancelled_queued_task_never_records_running`（修前：非法转移 ValueError / 假 failed 消息；修后：安静收尾）
**Steps:** 失败测试 → 实现 → swarm 相关测试绿 → Commit `fix(swarm): runner re-checks task state before recording running`

### Task 17: close 链异常隔离（P2）

**Files:** Modify `zenagent/app/swarm/manager.py:226-228,518-523`（逐 agent `contextlib.suppress(Exception)` + 收集 warnings，对齐 `agent_profile.py:176-184` 的写法）
**Test:** `tests/test_swarm_manager.py::test_close_isolates_failing_runtime`（一个 runtime.close 抛错，其余仍被关闭）
**Steps:** 失败测试 → 实现 → 测试绿 → Commit `fix(swarm): isolate runtime.close failures during teardown`

### Task 18: switch_session 先锁后 abort（P2）

**Files:** Modify `zenagent/app/runtime.py:1005-1025`（`ensure_session_free` 提前到 `before_switch`/`wait_for_idle`/abort 之前；busy 即返回失败，当前 run 不 abort）
**Test:** `tests/test_app_runtime.py::test_switch_refuses_before_aborting_current_run`（目标被占 → 切换失败且当前 run 仍存活）
**Steps:** 失败测试 → 实现 → runtime 测试绿 → Commit `fix(runtime): check target lease before aborting the current run on switch`

### Task 19: _safe_deliver 不再吞报告（P2）

**Files:** Modify `zenagent/app/zen/fleet.py:510,532-534`（deliver 失败 → `_log.warning` + 把该批消息塞回 `_report_buffer` 队首）
**Test:** `tests/test_zen_fleet.py::test_failed_delivery_retains_reports`（deliver 抛错 → buffer 未丢、有 warning）
**Steps:** 失败测试 → 实现 → 测试绿 → Commit `fix(zen): failed report delivery logs and retains the buffer`

### Task 20: fleet dispatch 状态统一 queued（P2）

**Files:** Modify `zenagent/app/zen/fleet.py:301-303`（dispatch 一律置 `"queued"`；`_run` 拿到信号量后翻 `"working"`）
**Test:** `tests/test_zen_fleet.py::test_dispatch_over_capacity_shows_queued`（超过 max_concurrent 派工 → status queued 而非 working）
**Steps:** 失败测试 → 实现 → 测试绿 → Commit `fix(zen): dispatch marks queued; working is set after the semaphore`

### Task 21: 审批卡片截断显式化（P2，跨 RPC 契约）

**Files:** Modify `zenagent/cli/rpc/server.py:234-240`（截断时 ApprovalEvent 加 `"args_truncated": true` 并在展示串尾加 `"…(+N chars)"`）；`tests/test_rpc_contract.py`（契约断言新可选字段）；`desktop/src/rpc/types.ts`（ApprovalEvent 加 `args_truncated?: boolean`）；`desktop/src/ui/`（审批卡片显示「已截断」标记）
**Test:** `tests/test_rpc_contract.py` 契约绿；`desktop/tests/` 相关用例绿；PROTOCOL_VERSION 不变
**Steps:** 契约测试先行 → 双端实现 → `uv run pytest tests/test_rpc_contract.py tests/test_rpc_v2.py -q` + `cd desktop && pnpm test` 绿 → Commit `feat(approval): explicit truncation marker on oversized approval args`

### Task 22: markdown 外链走系统浏览器（P2，桌面侧）

**Files:** Modify `desktop/src/ui/chat/AssistantNodeView.tsx:47-58`（`onMarkdownClick` 拦截 `a[href]` http(s) → `preventDefault` + Tauri opener 打开系统浏览器；非 http(s) 一律忽略）；`desktop/src-tauri`（加 `tauri-plugin-opener`，capabilities 只授 openurl）
**Test:** `desktop/tests/` 组件测试：外链点击不导航、调用 opener；相对/文件引用行为不回归
**Steps:** 失败测试 → 实现 → `cd desktop && pnpm test && pnpm build` 绿 → Commit `fix(desktop): model markdown links open in the system browser, never navigate the webview`

### Task 23: 插件扩展哈希记录 + 启用告示（P2，缩 scope：不做签名）

**Files:** Modify `zenagent/plugins/install.py:34-68`（安装时记录 extension 文件 sha256 到 state）；`zenagent/extensions.py:102-107`（加载时哈希不符 → `_log.warning` 明示「代码与安装时不一致」）；插件安装输出加一句「将执行本机 Python 代码」
**Test:** `tests/test_plugin_install.py::test_extension_hash_recorded_and_checked`（改文件 → 加载出 warning）
**Steps:** 失败测试 → 实现 → plugin 测试绿 → Commit `feat(plugins): pin extension hashes at install and warn on drift`

### Task 24: CI 加固（P2）

**Files:** Modify `.github/workflows/ci.yml`（每个 job `timeout-minutes: 30`；矩阵加 `"3.13"`）；`pyproject.toml` dev 依赖加 `pytest-timeout`；pytest 配置加 `timeout = 120`
**Test:** 不适用；验证 = 本地 `uv run pytest -q --timeout 120` 全绿 + workflow YAML lint（若仓库有）
**Steps:** 改配置 → 本地验证 → Commit `ci: job timeouts, pytest-timeout, and the 3.13 matrix leg`

### Task 25: 墙钟 sleep 测试 latch 化（P2）

**Files:** Modify `tests/test_tui_sidebar.py:359,387`（对齐 `tests/test_zen_fleet.py:379-393` 的 `_await_flush_armed` latch 模式，或 monkeypatch 定时器周期）
**Test:** 两条用例改造后仍绿且总耗时明显下降
**Steps:** 改造 → `uv run pytest tests/test_tui_sidebar.py -q` 连跑 3 次稳定绿 → Commit `test(tui): replace wall-clock sleeps with latches`

### Task 26: 删孤儿 shim + 测试改直连（P2）

**Files:** Delete `zenagent/cli/permissions.py`；Modify `tests/test_cli.py:15`、`tests/test_permissions.py:7,127`（改 `from zenagent.app.permissions import ...`）
**Steps:** `rg -n "cli.permissions|cli\.permissions" --type py` 确认仅测试引用 → 改 import → 删文件 → `uv run pytest tests/test_cli.py tests/test_permissions.py -q` 绿 → Commit `chore: drop the cli.permissions compatibility shim`

### Task 27: ApprovalRouter 单测改生产前缀（P2）

**Files:** Modify `tests/test_approval_router.py:18`（id 用生产同款 `project_key(...) + ":swarm:a1"` 构造）
**Steps:** 改 → `uv run pytest tests/test_approval_router.py -q` 绿 → Commit `test(approval): pin the production id-prefix format in router unit tests`

---

## Phase D — 文件拆分（P1-9 + P2 结构组；机械移动，无行为变更）

### Task 28: 拆 modals.py（P2）

**Files:** Create `zenagent/cli/tui/screens/dialogs.py`（165-373：DialogScreen/DialogChoice/preview_diff）、`pickers.py`（49-163：ModelPicker/SessionPicker）、`approvals.py`（374-609：Confirm/PlanReview/ToolApproval/AskQuestion）、`editors.py`（其余）；Modify `modals.py` 改为 re-export（保外部 import 不断）
**Steps:** 按行段移动 + import 修正 → `rg -n "screens.modals import" --type py` 确认外部引用仍走 modals → `uv run pytest tests/test_tui* -q` 绿 + ruff 绿 → Commit `refactor(tui): split modals.py by responsibility`

### Task 29: 拆 repl.py（P2）

**Files:** Create `zenagent/cli/interaction.py`（ask_user/confirm_via_stdin 等 stdin 交互闭包，改模块级函数、参数显式传状态）；Modify `zenagent/cli/render.py`（并入 `_render_sessions/_render_todo/render_command_result/render_help`，91-279）；repl() 只留主循环 + dispatch
**Steps:** 移动 → import 修正 → `uv run pytest tests/test_repl_logic.py tests/test_cli.py -q` 绿 → Commit `refactor(cli): extract interaction and rendering from repl()`

### Task 30: 拆 ops_config.py（P2）

**Files:** Create `zenagent/cli/rpc/ops_secrets.py`（secrets+limits，68-238 与 453-537）、`ops_skills.py`（skills+mcp，238-601）、保留 ops_config.py（models/commands/project/分发路由）
**Steps:** 移动 → `host.py` import 更新 → `uv run pytest tests/test_rpc_v2.py tests/test_rpc_host_e2e.py -q` 绿 → Commit `refactor(rpc): split ops_config by domain`

### Task 31: Runtime 技能重组切片（P1-9 第一步）

**Files:** Create `zenagent/app/skills_recompose.py`（`_recompose/refresh_skills/rediscover/reconcile` 的函数体搬出：`def recompose(runtime) -> None` 等签名，runtime 作参数）；Modify `zenagent/app/runtime.py:667-876`（方法改一行委托）
**Scope 注记：** 只做这一刀；装配体并入 bootstrap、插件刷新并入 plugin_wiring、限额独立——留待后续 plan（审计 §六.4）
**Steps:** 搬函数体 → 委托 → `uv run pytest tests/test_app_runtime.py tests/test_skills.py tests/test_mcp_lifecycle.py -q` 绿 → gov_health 里 Runtime 上帝类应显著回落 → Commit `refactor(runtime): extract skill/tool recomposition into skills_recompose.py`

### Task 32: mypy ratchet 门禁（P1-10）

**Files:** Modify `pyproject.toml`（dev 依赖 mypy；`[tool.mypy] python_version="3.11", ignore_missing_imports=true` + 为当前报错模块生成 `[[tool.mypy.overrides]] ignore_errors=true` 清单）；Modify `scripts/check.sh`（ruff 之后加 `uv run mypy zenagent`）
**Steps:** 装依赖 → 跑 mypy 收集报错模块 → 生成 overrides（脚本一行：报错模块 → override 条目，注释注明「ratchet：新模块不许加进来」）→ check.sh 绿 → Commit `chore(types): mypy gate with a frozen per-module ratchet baseline`

---

## Phase E — 治理收尾

### Task 33: LOC allowlist 重基线（P2）

**Files:** Modify `docs/architecture/allowlist.json`（desktop 失真条目降到现值；repl/modals/ops_config 按 Task 28-30 拆分后的新值；零余量条目留出 10 行余量并注释偿还期限）
**Steps:** `python3 scripts/check_architecture.py` 当前值核对 → 更新 → 门禁绿 → Commit `chore(gate): re-baseline the LOC allowlist after the splits`

### Task 34: 治理账本 v8 + 全量验收（收尾）

**Files:** Modify `docs/arch-gov/manifest.json`（version 8；`exempt.hub` 登记契约枢纽：types/events/i18n/包根/project/config/views/providers.catalog/agent.loop；意外枢纽 app.runtime/app.agent_profile 留在违规里盯着）；`docs/arch-gov/deltas.md`（v8 条目：本计划的边变化/文件移动/熵增处置）
**Steps:**
- [ ] `python3 $SKILL/extract.py` 刷新 deps → `gov_diff` → 处置到 CLEAN
- [ ] `gov_health`：Runtime/ChatController 等应改善（基线自动收紧）；`--update-baseline` 登记仍存量的
- [ ] `scripts/check.sh` 全绿；`uv run pytest -q` 全绿
- [ ] Commit `docs(governance): manifest v8 — hub re-baseline and audit-cleanup ledger`

## 自查记录（Self-Review）

- 覆盖：审计 §二 P0×3 → Task 1/2/3；§三 P1×10 → Task 3/4/5/6/7/8/9/10/31/32；§四 P2×24 → Task 7(残尾)/11-27/28-30/33，无遗漏；hub 政策脱节 → Task 34
- 类型一致性：`jsonl.repair_tail/flush_fsync/append_line`、`effective_worker_mode`、`classify_read_path`、`SwarmLocked`、`Runtime.tool_providers` 各 Task 引用一致
- Review Focus 5 条均有归属任务与测试
- 比例：34 个任务 ≈ 审计 37 条发现，计划只含决策与锚点，无函数体转写
