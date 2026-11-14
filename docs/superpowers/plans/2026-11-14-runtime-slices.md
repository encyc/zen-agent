# Runtime §6 后续切片实施计划（bootstrap 装配体 / limits 独立 / 插件胶水）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成架构审计 §六.5 对 Runtime 上帝类的剩余三条切分线（发现/重组切片已在 Task 31 完成）：装配体并入 bootstrap 模块、限额独立成模块、插件装配胶水归位 plugin_wiring。

**Architecture:** 沿用 Task 31 确立的模式——模块函数持 `runtime` 首参、`Runtime` 方法保留一行委托（调用面不变：ops mixins、fleet wiring、`prepare()`）。`Runtime.bootstrap` 是唯一带循环 import 风险的委托（bootstrap.py 顶层 import Runtime），用方法内延迟 import 解决。

**Tech Stack:** 纯 Python 重排（无新依赖）；pytest / ruff / mypy ratchet / LOC 预算 / arch-gov 治理门禁照旧。

**Spec:** `docs/reviews/2026-11-14-architecture-audit.md`（发现 9：Runtime 五职责混装；§六.5：四条切分线另立任务）+ `docs/superpowers/plans/2026-11-14-architecture-audit-cleanup.md` Task 31 的 Scope 注记。

## Global Constraints

- 调用面零变化：`Runtime.bootstrap` / `runtime.limit_overrides()` / `runtime.apply_limits()` / `runtime.reconfigure_plugins()` 签名与语义不变（外部调用方：`bootstrap.prepare`、`conftest.runtime_factory`、`ops_secrets`、`test_bootstrap.py` 等）。
- 装配体函数体**逐字搬运**（含 try/except BaseException 回滚、全部注释），不重写逻辑。
- 严禁 runtime.py 顶层 import `zenagent.app.bootstrap`（bootstrap.py 顶层 import Runtime，顶层互引即循环）。
- 每个任务结束 `uv run ruff check <files>` 干净；LOC 门禁 `scripts/check_architecture.py` 通过；gov 治理在收尾任务统一刷新。

## Review Focus

1. **bootstrap 回滚**：装配中途失败必须仍然回滚（lease/job/LSP 引用计数）——搬运不得把 try/except 留在 Runtime 委托里。钉子：`test_app_runtime.py` 的回滚用例在 Task 2 后仍绿。
2. **外部传入 gate**：broker 作为 `gate=` 传入时仍被采纳、`set_mode` 仍生效。钉子：`test_bootstrap.py:139` 的 brokered 用例仍绿。
3. **limits 链路**：`ops_secrets._limits_result/_adopt_limits` 对 `runtime.limit_overrides()/apply_limits()` 的调用在迁出后行为不变。钉子：`test_rpc_v2.py` 的 limits 用例 + Task 1 新行为测试。
4. **无插件/无 tools 路径**：`options.tools is None` 且 `profile.plugins=False` 时装配不触碰 plugin_wiring。钉子：conftest 默认 runtime（`AgentProfile(mcp=False, skills=False, templates=False)`）全量测试绿。
5. **循环 import**：任何入口 `import zenagent.app.runtime` 不得因新委托触发部分初始化错误。钉子：Task 2 步骤里的冷进程 import 检查。

---

### Task 1: limits 独立成 `limits_apply.py`

**Files:**
- Create: `zenagent/app/limits_apply.py`
- Modify: `zenagent/app/runtime.py`（`limit_overrides` :729-747 与 `apply_limits` :749-765 改一行委托）
- Test: `tests/test_limits_apply.py`

**Interfaces:**
- Produces: `limits_apply.overrides(runtime) -> dict[str, str]`；`limits_apply.apply(runtime) -> None`（键为 `Limit` attr：max_turns/max_index_bytes/max_body_bytes/max_context_bytes/compact_threshold）。
- Runtime 委托保留原方法名（`ops_secrets.py:227,287` 不改）。

- [ ] **Step 1: 写失败测试** `tests/test_limits_apply.py`

```python
"""The limits knobs live beside Runtime, not inside it (audit finding 9)."""
from zenagent.app import limits_apply
from zenagent.app.runtime import Runtime


def test_module_functions_exist():
    assert callable(limits_apply.overrides) and callable(limits_apply.apply)


def test_runtime_methods_delegate(runtime_factory):
    src = __import__("inspect").getsource(Runtime.limit_overrides)
    assert "limits_apply." in src
    assert "limits_apply." in __import__("inspect").getsource(Runtime.apply_limits)


async def test_overrides_maps_the_cli_switches(runtime_factory):
    runtime = await runtime_factory(max_turns=20, skills=False, stream_fn=None)
    active = limits_apply.overrides(runtime)
    assert active["max_turns"] == "--max-turns 20"
    assert active["max_index_bytes"] == "--no-skills"
    assert active["max_body_bytes"] == "--no-skills"
    await runtime.close()


async def test_apply_pushes_loop_guard_threshold(runtime_factory):
    runtime = await runtime_factory(stream_fn=None)
    runtime.agent.loop_guard_threshold = -1
    limits_apply.apply(runtime)
    assert runtime.agent.loop_guard_threshold == runtime.limits.loop_guard_threshold
    await runtime.close()
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_limits_apply.py -q --timeout 30` → ModuleNotFoundError: no `limits_apply`
- [ ] **Step 3: 实现** — 新建 `limits_apply.py`：两个函数体从 runtime.py 逐字搬入（`self`→`runtime`）；runtime.py 两方法改一行委托 `return limits_apply.overrides(self)` / `limits_apply.apply(self)`；runtime.py 顶部加入 `from zenagent.app import ... limits_apply ...`（加入现有 app 包 import 行），ruff --fix 清理因此失效的 import
- [ ] **Step 4: 跑测试确认通过** — `uv run pytest tests/test_limits_apply.py tests/test_rpc_v2.py -q --timeout 90` → 全绿（rpc_v2 的 limits 用例即 Review Focus 3）
- [ ] **Step 5: Commit** — `refactor(runtime): extract the limits knobs into limits_apply.py`

### Task 2: 装配体并入 bootstrap 模块

**Files:**
- Modify: `zenagent/app/bootstrap.py`（新增 `async def assemble(runtime, options, *, stream_fn=None, assume_yes=False, gate=None, allow_missing_key=False) -> None`，装配体函数体逐字搬入；`__all__` 加 `"assemble"`）
- Modify: `zenagent/app/runtime.py:341-585`（`Runtime.bootstrap` 类方法改薄委托）
- Test: `tests/test_skills_recompose_layout.py` 追加装配委托 pin（同文件管两片，避免新文件）

**Interfaces:**
- Produces: `bootstrap.assemble(runtime, options, *, stream_fn, assume_yes, gate, allow_missing_key) -> None`（异常语义不变：装配失败先 `runtime.close()` 回滚再抛原异常）。
- `Runtime.bootstrap(cls, options, *, stream_fn, assume_yes, gate, allow_missing_key) -> Runtime` 签名不变。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_skills_recompose_layout.py`

```python
def test_bootstrap_assembly_lives_in_the_bootstrap_module():
    import inspect
    from zenagent.app import bootstrap

    assert callable(bootstrap.assemble)
    source = inspect.getsource(runtime_module.Runtime.bootstrap)
    assert "bootstrap." in source and "assemble" in source
    # 回滚留在 assemble 里，委托不得吞掉失败语义
    assert "except BaseException" not in source
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_skills_recompose_layout.py -q --timeout 30` → `bootstrap.assemble` 不存在（AttributeError/callable 断言失败）
- [ ] **Step 3: 实现**
  - bootstrap.py：新增 `assemble(runtime, options, *, ...)`，函数体 = `Runtime.bootstrap` 341-585 的 **342-585 行逐字搬入**（`runtime = cls()` 一行删除；`cls` 不再引用；`self`→`runtime` 本已如此）；补齐 bootstrap.py 所需 import（逐字搬运后以 ruff F821 清单为准）：`Path`、`contextlib`、`asyncio`、`t`、`project`、`load_user_config`/`apply_user_config`/`ContextGuard`（config）、`load_catalog`（providers.catalog）、`resolve_model`/`resolve_task_model`/`session_id_for`/`api_key_for`（app.support）、`resolve_mode`（app.modes）、`GuardPolicy`（app.safety）、`build_permission_gate`（app.permissions）、`journal_for`/`load_backup_policy`/`attach_journal`（app.journal）、`build_todo_tool`（tools.todo）、`base_tools`（tools）、`build_task_tool`（tools.task）、`SubagentLimits`（agent.subagents）、`Agent`（agent）、`session_id_for`（app.support）、`subagent_runs`（app）、`plugin_wiring`（app）、`discover_skills`/`default_skill_roots`（skills）、`discover_templates`/`default_template_roots`（templates）、`load_extensions`（extensions）、`skills_recompose`（app）、`TraceRecorder`（trace）、`MetricsSession`（app.metrics）、`last_request_usage`（app.views）、`SessionMetaRecorder`/`make_retry_recorder`/`mode_record`（app.session_meta）、`StreamFn`（providers.registry）
  - runtime.py：341-585 整段替换为薄委托——**方法内延迟 import**（禁顶层）：

```python
    @classmethod
    async def bootstrap(
        cls,
        options: RuntimeOptions,
        *,
        stream_fn: StreamFn | None = None,
        assume_yes: bool = False,
        gate: PermissionGate | None = None,
        allow_missing_key: bool = False,
    ) -> Runtime:
        from zenagent.app.bootstrap import assemble  # 循环回避：bootstrap 顶层 import 本模块

        runtime = cls()
        await assemble(
            runtime,
            options,
            stream_fn=stream_fn,
            assume_yes=assume_yes,
            gate=gate,
            allow_missing_key=allow_missing_key,
        )
        return runtime
```
  - `assemble` 内部把 `await plugin_wiring.start_plugins(runtime, options)` 一行原位替换为 `assembly = await plugin_wiring.boot_contributions(runtime, options)`（Task 3 的接口，先留到 Task 3 一起落——本任务保留原行，Task 3 再换）
  - ruff --fix 清理 runtime.py 失效 import（`F821`/`F401` 清单即搬运完整性校验）
- [ ] **Step 4: 跑测试确认通过** — `uv run pytest tests/test_skills_recompose_layout.py tests/test_app_runtime.py tests/test_bootstrap.py tests/test_project_scoped_runtime.py tests/test_tui_missing_key.py -q --timeout 120` → 全绿；冷进程检查 `uv run python -c "import zenagent.app.runtime; import zenagent.app.bootstrap"` 无循环错误
- [ ] **Step 5: Commit** — `refactor(runtime): move the bootstrap assembly into bootstrap.assemble()`

### Task 3: 插件装配胶水归位 plugin_wiring

**Files:**
- Modify: `zenagent/app/plugin_wiring.py`（新增 `async def boot_contributions(runtime, options) -> PluginAssembly | None`）
- Modify: `zenagent/app/bootstrap.py`（assemble 中插件启动行改调 `boot_contributions`）
- Test: `tests/test_mcp_trust.py` 追加 pin

**Interfaces:**
- Produces: `plugin_wiring.boot_contributions(runtime, options) -> PluginAssembly | None` — `options.tools is None and runtime.profile.plugins` 时 `await start_plugins(runtime, options)`，否则 None（条件本体随之离开 assemble）。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_mcp_trust.py`

```python
async def test_boot_contributions_respects_tools_and_profile(isolated, runtime_factory):
    from zenagent.app import plugin_wiring

    runtime = await runtime_factory(mcp=True, plugins=True, root=isolated)
    # options.tools 默认 None、plugins profile=True：返回 assembly（此处无插件 → 各容器为空）
    assembly = await plugin_wiring.boot_contributions(runtime, runtime._options)
    assert assembly is not None and assembly.configs == {}
    # custom tools 时插件装配被跳过
    from dataclasses import replace
    options = replace(runtime._options, tools=[])
    assert await plugin_wiring.boot_contributions(runtime, options) is None
    await runtime.close()
```

- [ ] **Step 2: 跑测试确认失败** — AttributeError: boot_contributions 不存在
- [ ] **Step 3: 实现** — plugin_wiring.py 新增函数（条件与 `start_plugins` 调用从 bootstrap.assemble 原样迁入）；assemble 中 `assembly = await plugin_wiring.start_plugins(...)` 连同外层 `if` 替换为 `assembly = await plugin_wiring.boot_contributions(runtime, options)`
- [ ] **Step 4: 跑测试确认通过** — `uv run pytest tests/test_mcp_trust.py tests/test_plugin_install.py tests/test_bootstrap.py -q --timeout 90` → 全绿
- [ ] **Step 5: Commit** — `refactor(plugins): bootstrap's plugin start goes through plugin_wiring.boot_contributions()`

### Task 4: 治理收尾（manifest v8 + 门禁全绿）

**Files:**
- Modify: `docs/arch-gov/manifest.json`（version 7→8；deps 刷新后的边变化处置）
- Modify: `docs/arch-gov/deltas.md`（v8 条目）
- Modify: `docs/architecture/allowlist.json`（runtime.py 预计 900→~640：**删除其条目**回归默认 800 线；核对 commands/manager 条目不变）
- Modify: `docs/arch-gov/health/*`（deps 刷新 + baseline 自动收紧 + `--update-baseline` 登记仍存量的）

- [ ] **Step 1: 刷新 deps + gov_diff 到 CLEAN** — `python3 $SKILL/extract.py . -o docs/arch-gov/deps.json && python3 $SKILL/gov_diff.py docs/arch-gov`；按输出把新文件/边处置进 manifest（`limits_apply.py` 归 app.assembly 节点；`bootstrap.assemble` 不产生新节点，边随节点聚合）；version 7→8、updated=今日
- [ ] **Step 2: gov_health** — 先裸跑（改善自动收紧基线：Runtime 上帝类应显著回落、`runtime.py` 上帝文件若 <800 应解除），再 `--update-baseline` 登记剩余存量
- [ ] **Step 3: LOC allowlist** — `uv run python scripts/check_architecture.py` 核对；runtime.py 低于 800 → 删条目；`_notes` 同步
- [ ] **Step 4: 全量验收** — `bash scripts/check.sh` 退出码 0；deltas.md 记 v8（三刀的行数变化/基线变化/偿还进度）
- [ ] **Step 5: Commit** — `docs(governance): manifest v8 — runtime slices close-out`

## Self-Review 记录

- 覆盖：§六.5 四条切分线中未做的三条 = Task 1（限额）/ Task 2（装配体）/ Task 3（插件胶水）；第四条（技能重组）= 已完成的 Task 31，不在本计划。
- 类型一致：`assemble(runtime, options, *, stream_fn, assume_yes, gate, allow_missing_key)` 与 Runtime.bootstrap 委托参数一一对应；`overrides(runtime)/apply(runtime)` 与委托一致。
- Review Focus 5 条均有归属任务与测试。
- 比例：4 任务，搬运为主，无函数体转写（Task 2 Step 3 的委托代码块是唯一完整 body，因为签名+循环回避决策不由测试决定）。
