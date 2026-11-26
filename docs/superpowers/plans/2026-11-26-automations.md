# 自动化任务（automations）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 到点自动在固定的 session 里替用户跑一轮 agent（常驻调度器，一项目一实例），并给出 CLI 与桌面端的管理界面。

**Architecture:** 调度逻辑是纯函数（`app/automations.py`：时间表、到期判定、提示词包装，无 Runtime 无 UI）；一轮由 `app/automation_runner.py` 跑：`switch_session` 拿租约并从盘重读 → `chat.submit()` → watchdog 超时调 `abort()` → 释放租约。常驻进程 `cli/daemon.py` 只有 asyncio 循环、锁和 launchd/systemd 安装。控制面全是文件：`automations.json`（CLI/UI 写、daemon 按 mtime 热加载）、`automations.state.json`（只有 daemon 写）、`automations.request.json`（UI/CLI 写、daemon 消费）。桌面端经新增的 `automations` host op 读写这些文件，**六个 op 都不建 Runtime**。

**Tech Stack:** Python 3.11+，asyncio，标准库 `tomllib`/`json`/`fcntl`；pytest（全离线，fake stream）；Tauri + React（测试 `node --test`）。

**Spec:** [docs/superpowers/specs/2026-11-26-automations-design.md](../specs/2026-11-26-automations-design.md)

## Global Constraints

- **门禁只有一条**：`bash scripts/check.sh`（sync + ruff + mypy + 架构预算 + 线上类型新鲜度 + worktree + pytest）。每个任务结束时跑它。
- **单文件 ≤ 800 行**（`scripts/check_architecture.py`）。`app/chat.py` 已在 allowlist 里且**不得**再加行数；本计划不要求改它。
- **mypy ratchet**：新增模块必须**零类型错误**。**不要**把新模块加进 `pyproject.toml` 的 `[[tool.mypy.overrides]]` 白名单。
- **`app/` 不 import `cli/`、不 import argparse/textual**（约定；`tests/test_layering.py` 只钉 `agent/` 与 `plugins/`，所以这条要自觉守）。
- **权限档**：`Runtime.bootstrap(..., assume_yes=True)`，mode 解析成 `auto`。不做 confirm 回调（spec 的"权限"一节）。
- **状态文件只有一个写者**：`automations.state.json` 只由 daemon 写；`automations.json` 只由 CLI/rpc 写；daemon 对定义**只读**。
- **所有 JSON 写入都是原子的**：同目录临时文件 + `os.replace`。
- **每轮结束必须 `release_session_lease(runtime)`**，让用户随时能打开那个 session。
- **超时一律用 watchdog 调 `chat.abort()`**，不用 `asyncio.wait_for(chat.wait_for_idle())`（会取消 run task，把 `busy` 永久钉死）。
- **不新增依赖**；时间表解析只用标准库。
- **新增用户可见文案进 `zenagent/i18n/{en,zh}.py`**，不写在 Python 字符串里。
- **rpc 新 op 走 registry**：`zenagent/cli/rpc/protocol/_host.py` 声明，`scripts/gen_rpc_ts.py` 重生成 `desktop/src/rpc/wire.gen.ts`，`desktop/src/rpc/types.ts` 的 `REQUEST_KINDS` 加 `'automations'`（`tests/test_rpc_contract.py` 会对齐）。
- 时间语义：本地时间字面值；`now` 全部作为参数传入，**不要**在逻辑里直接 `time.time()`（否则无法离线测试）。

## Review Focus

1. **常驻持有写租约 → 用户打不开那个 session。** 期望：daemon 空闲时 `zen --resume <该 session>` 能直接进去聊；跑完那一轮后租约立刻释放。测试在 Task 5、Task 6。
2. **超时把 daemon 卡死。** `chat.busy` 若永远为真，之后所有轮次都不再执行且无任何提示。期望：abort 之后 `busy` 为 `False`，下一轮照常。测试在 Task 5。
3. **`automations.json` 被手改坏 / 写到一半。** 期望：daemon 不崩、保留上一份好定义、记一条 warning；绝不清空状态。测试在 Task 2、Task 6。
4. **两个写者（第二个 daemon，或 daemon 与 CLI 同时动 state）。** 期望：第二个 daemon 直接退出；state 只由 daemon 写，CLI 只发请求文件。测试在 Task 6、Task 8。
5. **job 的 session 文件被删掉（或从没创建过）。** 期望：重铸一个路径继续跑，不是每轮抛异常。测试在 Task 2、Task 5。

---

### Task 1: 时间表解析与到期计算

**Files:**
- Create: `zenagent/app/automations.py`
- Test: `tests/test_automations_schedule.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Schedule`：`kind: str`（`"daily" | "every"`）、`at: str = "09:00"`、`seconds: int = 0`
  - `Schedule.parse(raw: str) -> Schedule` —— 接受 `"daily 09:00"` 与 `"every 15m" | "every 2h"`；其它形式抛 `ValueError`
  - `Schedule.next_due(now: float, last_run_at: float | None) -> float`
  - `Schedule.to_json() -> dict`、`Schedule.from_json(raw: object) -> Schedule`
- Consumes: 无

**语义（写测试时照抄）**：`last_run_at is None` 时 `next_due` 返回"从 now 起的下一个时间点"（`daily 09:00` 在 14:00 建的 → 明天 09:00；`every` → `now + seconds`）。`daily` 用 `time.localtime` 的本地日期，`at` 解析成 HH:MM 后按本地时区组合；`every` 返回 `max(now, last_run_at) + seconds`，但**不小于 now**（时钟回拨时不要连发）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automations_schedule.py
import time

import pytest

from zenagent.app.automations import Schedule


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def test_parse_daily_and_every():
    assert Schedule.parse("daily 09:00") == Schedule(kind="daily", at="09:00")
    assert Schedule.parse("every 15m") == Schedule(kind="every", seconds=900)
    assert Schedule.parse("every 2h") == Schedule(kind="every", seconds=7200)


@pytest.mark.parametrize("raw", ["", "daily", "daily 9", "hourly", "every 0m", "every 5s"])
def test_parse_rejects_unknown_forms(raw):
    with pytest.raises(ValueError):
        Schedule.parse(raw)


def test_daily_without_history_starts_at_next_occurrence():
    now = at(2026, 11, 26, 14, 0)          # 14:00, schedule is 09:00
    due = Schedule.parse("daily 09:00").next_due(now, None)
    assert due == at(2026, 11, 27, 9, 0)


def test_daily_rolls_over_midnight():
    now = at(2026, 11, 26, 23, 30)
    due = Schedule.parse("daily 00:10").next_due(now, None)
    assert due == at(2026, 11, 27, 0, 10)


def test_every_without_history_is_one_interval_out():
    now = at(2026, 11, 26, 9, 0)
    assert Schedule.parse("every 10m").next_due(now, None) == now + 600


def test_every_is_clamped_to_now_so_a_backwards_clock_cannot_burst():
    now = 1000.0
    # last run an hour "in the future" relative to now (sleep/DST): never due
    # before now, so at most one catch-up.
    assert Schedule.parse("every 10m").next_due(now, 4600.0) == now + 600


def test_json_round_trip():
    for raw in ("daily 07:45", "every 90m"):
        parsed = Schedule.parse(raw)
        assert Schedule.from_json(parsed.to_json()) == parsed
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automations_schedule.py -q`
Expected: FAIL — `ModuleNotFoundError: zenagent.app.automations`

- [ ] **Step 3: 实现 `zenagent/app/automations.py`**

模块 docstring 写明"纯逻辑：不建 Runtime、不 import cli/argparse/textual，`now` 永远是参数"。`_DAILY_RE = re.compile(r"^daily ([01]?\d|2[0-3]):([0-5]\d)$")`、`_EVERY_RE = re.compile(r"^every (\d+)([mh])$")`；`every` 的秒数必须 > 0。`next_due` 的 `daily` 分支：用 `time.localtime(now)` 拿年月日，`time.mktime` 组合出今天的 `at`，若 `<= now` 则加一天（用 `time.mktime` 的 day+1 组合，让平台自己处理月末，**不要**用 `now + 86400`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automations_schedule.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/automations.py tests/test_automations_schedule.py
git commit -m "feat(automations): schedule parsing and next-due arithmetic"
```

---

### Task 2: job / state 存储与原子写

**Files:**
- Modify: `zenagent/app/automations.py`（追加）
- Test: `tests/test_automations_store.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Job`：`id: str`、`name: str`、`prompt: str`、`schedule: Schedule`、`session: str`、`timeout_s: int = 1800`、`enabled: bool = True`、`created_at: int = 0`；`to_json() -> dict`、`from_json(raw: object) -> Job`
  - `@dataclass class JobState`：`session: str = ""`、`last_run_at: float | None = None`、`last_status: str = ""`、`last_skip: str = ""`、`last_error: str = ""`、`last_run_id: str = ""`、`missed: int = 0`、`run_count: int = 0`；`to_json() -> dict`、`from_json(raw: object) -> JobState`
  - `class AutomationStore`：`__init__(root: Path)`、`path: Path`、`load() -> list[Job]`、`reload_if_changed() -> bool`、`get(name: str) -> Job | None`、`add(job: Job) -> Job`、`update(name: str, **changes) -> Job`、`remove(name: str) -> bool`、`warnings: list[str]`
  - `class StateStore`：`__init__(root: Path)`、`path: Path`、`load() -> dict[str, JobState]`（键是 `job.id`）、`save(states: dict[str, JobState]) -> None`
  - `def resolve_session(root: Path, name: str) -> Path`
  - `def state_for(states: dict[str, JobState], job: Job) -> JobState`（缺失时给默认值，并把 state 里的 `session` 落到 `job.session` 上）
- Consumes: Task 1 的 `Schedule`

**语义**：`AutomationStore` 读写 `project.project_dir(root=root)/"automations.json"`，文件不存在 = 空列表（**不**创建文件）；`add`/`update`/`remove` 都是"改内存 → 原子写回"，`add` 时 `id` 若为空则生成 `a_<8 hex>`，名字重复抛 `ValueError`。`update` 的 `changes` 只允许 `enabled`/`schedule`/`prompt`/`timeout_s`，其它键抛 `ValueError`。`resolve_session` 返回 `project.project_sessions_dir(root=root)/name`。坏文件：`load` 返回最后一次成功的内存快照（首次就坏 → 空列表）并往 `warnings` 追加一条，**不抛**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automations_store.py
import json

import pytest

from zenagent.app.automations import (
    AutomationStore,
    Job,
    JobState,
    Schedule,
    StateStore,
    resolve_session,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / "proj"


def job(name="daily-report", **extra):
    return Job(
        id="",
        name=name,
        prompt="go",
        schedule=Schedule.parse("daily 09:00"),
        session="s1.jsonl",
        **extra,
    )


def test_missing_file_is_an_empty_store_and_is_not_created(root):
    store = AutomationStore(root)
    assert store.load() == []
    assert not store.path.exists()


def test_add_assigns_id_and_persists_atomically(root):
    store = AutomationStore(root)
    saved = store.add(job())
    assert saved.id.startswith("a_")
    raw = json.loads(store.path.read_text())
    assert raw["version"] == 1
    assert raw["jobs"][0]["name"] == "daily-report"
    assert AutomationStore(root).load()[0].id == saved.id


def test_duplicate_name_is_refused(root):
    store = AutomationStore(root)
    store.add(job())
    with pytest.raises(ValueError):
        store.add(job())


def test_update_only_accepts_known_fields(root):
    store = AutomationStore(root)
    store.add(job())
    assert store.update("daily-report", enabled=False).enabled is False
    with pytest.raises(ValueError):
        store.update("daily-report", cwd="/etc")


def test_remove(root):
    store = AutomationStore(root)
    store.add(job())
    assert store.remove("daily-report") is True
    assert store.remove("daily-report") is False
    assert store.load() == []


def test_corrupt_file_keeps_the_last_good_snapshot(root):
    store = AutomationStore(root)
    store.add(job("keep"))                       # good snapshot in memory
    store.path.write_text('{"version": 1, "jobs": [{"na')   # torn write on disk
    assert [j.name for j in store.load()] == ["keep"]
    assert store.warnings


def test_a_store_that_never_loaded_good_data_returns_empty(root):
    store = AutomationStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")
    assert store.load() == []
    assert store.warnings


def test_state_round_trip_and_missing_default(root):
    store = StateStore(root)
    states = {"a_1": JobState(session="s1.jsonl", last_run_at=5.0, run_count=2)}
    store.save(states)
    assert store.load()["a_1"].run_count == 2


def test_resolve_session_under_project_dir(root):
    path = resolve_session(root, "s1.jsonl")
    assert path.name == "s1.jsonl"
    assert path.parent.name == "sessions"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automations_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'AutomationStore'`

- [ ] **Step 3: 实现存储**

`_atomic_write_json(path: Path, payload: dict) -> None`：`path.parent.mkdir(parents=True, exist_ok=True)`，`tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` 写入 + `os.replace`。`reload_if_changed` 用 `path.stat().st_mtime_ns` 与内存里记的值比较，变了就重读并返回 `True`；文件消失也返回 `True`（job 被删空）。`load` 里 JSON 形状不合法（`jobs` 不是 list、单条 `from_json` 抛 `ValueError`）都归为"坏文件"分支：保留快照 + warning。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automations_store.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/automations.py tests/test_automations_store.py
git commit -m "feat(automations): job and state stores with atomic writes"
```

---

### Task 3: 到期判定与提示词包装

**Files:**
- Modify: `zenagent/app/automations.py`（追加）
- Test: `tests/test_automations_due.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Due`：`job: Job`、`missed: int`、`run_number: int`
  - `def due_list(jobs: list[Job], states: dict[str, JobState], now: float) -> list[Due]` —— 按 `next_due` 升序
  - `def wrap_prompt(job: Job, run_number: int, now: float) -> str`
- Consumes: Task 1 的 `Schedule.next_due`、Task 2 的 `Job`/`JobState`

**语义**：跳过 `enabled is False`。对每个 job 算 `due = schedule.next_due(now, state.last_run_at)`；`due > now` → 不在列表里。`due <= now` → 到期。**`last_run_at is None` 时永远不到期**（Task 1 已保证 `next_due(None)` 在未来，这里只需不特殊处理）。`missed` = 从 `last_run_at` 起按周期数出来的落后轮数（`every`：`int((now - last_run_at) // seconds) - 1`，负值归 0；`daily`：本地日期差 - 1，负值归 0）。`run_number = state.run_count + 1`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automations_due.py
import time

from zenagent.app.automations import (
    Due,
    Job,
    JobState,
    Schedule,
    due_list,
    wrap_prompt,
)


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def job(name="r", schedule="daily 09:00", **extra):
    return Job(id="a_1", name=name, prompt="跑取数脚本", schedule=Schedule.parse(schedule),
               session="s.jsonl", **extra)


def test_job_without_history_is_not_due():
    now = at(2026, 11, 26, 23, 0)
    assert due_list([job()], {}, now) == []


def test_one_missed_round_is_due_with_missed_zero():
    j = job("r", "every 1h")
    now = at(2026, 11, 26, 11, 0)
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0))}
    due = due_list([j], states, now)
    assert [d.job.name for d in due] == ["r"]
    assert due[0].missed == 1
    assert due[0].run_number == 1


def test_many_missed_rounds_report_the_count():
    j = job("r", "every 1h")
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 6, 0), run_count=3)}
    due = due_list([j], states, at(2026, 11, 26, 11, 0))
    assert due[0].missed == 4
    assert due[0].run_number == 4


def test_disabled_and_future_jobs_are_skipped():
    now = at(2026, 11, 26, 11, 0)
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0))}
    assert due_list([job(enabled=False)], states, now) == []
    assert due_list([job("r", "every 5h")], states, now) == []


def test_due_list_is_ordered_by_due_time():
    a = job("a", "every 1h")
    b = job("b", "every 10m")
    b = Job(id="a_2", name="b", prompt="x", schedule=b.schedule, session="t.jsonl")
    states = {
        "a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0)),
        "a_2": JobState(last_run_at=at(2026, 11, 26, 10, 50)),
    }
    now = at(2026, 11, 26, 11, 30)
    assert [d.job.name for d in due_list([a, b], states, now)] == ["b", "a"]


def test_wrap_prompt_states_the_unattended_context_and_keeps_the_original():
    text = wrap_prompt(job(), run_number=3, now=at(2026, 11, 27, 9, 0))
    assert "第 3 次" in text
    assert "2026-11-27 09:00" in text
    assert "[automation] r" in text
    assert "ask" in text                     # tells the model nobody can answer
    assert "跑取数脚本" in text               # the user's prompt, verbatim
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automations_due.py -q`
Expected: FAIL — `ImportError: cannot import name 'due_list'`

- [ ] **Step 3: 实现 `due_list` 与 `wrap_prompt`**

`wrap_prompt` 的三段（模板的准确措辞见 spec 的"提示词包装"）：

```
[automation] {name} · 第 {n} 次运行 · {YYYY-MM-DD HH:MM}

这个 session 由定时器写入。现在没有人在看，也没有人能回答你的提问（ask 会直接
返回"无交互 UI"）。做完把结论写成一段可以直接读的汇报：先一句话结论，再列异常
（按严重程度排序）；没有异常也要用一句话说明。

{prompt}
```

时间格式 `time.strftime("%Y-%m-%d %H:%M", time.localtime(now))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automations_due.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/automations.py tests/test_automations_due.py
git commit -m "feat(automations): due detection with catch-up and prompt wrapping"
```

---

### Task 4: 账本的 `relabel` 口子

**Files:**
- Modify: `zenagent/app/run_wiring.py`（`RunRecorder` 增加方法）
- Test: `tests/test_run_ledger.py`（追加用例）

**Interfaces:**
- Produces: `RunRecorder.relabel(*, status: str, termination: str | None = None, error: str | None = None, run_id: str | None = None) -> None`
- Consumes: `RunLedger.finish` 的既有"同 id 追加终态行、最后一行生效"语义（`app/runs.py:109`）

**语义**：用 `run_id`（默认 `self._runtime.current_run_id`）在账本里查那条 run；查不到就返回（best-effort，不抛）。查到时调 `ledger.finish(...)` 写一条 `duration_ms` 保持原值的终态行。**不改** `status_for_termination`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_run_ledger.py` 末尾追加。**先跑 `uv run pytest tests/test_run_ledger.py -q -k relabel` 看它因缺 `relabel` 而红**，然后按 Step 3 实现。该文件已有的 helper 直接用；缺的补在这个测试文件里：

```python
def test_relabel_rewrites_the_terminal_row(tmp_path, monkeypatch):
    from zenagent.app.run_wiring import RunRecorder, attach_run_recorder

    runtime = _fake_runtime(tmp_path)          # 见下方说明：root/session/agent
    ledger = attach_run_recorder(runtime)
    recorder = RunRecorder(runtime)
    recorder(_event("agent_start"))
    recorder(_event("agent_end", termination="stop"))
    run_id = ledger.load()[-1].id
    assert ledger.get(run_id).status == "done"

    recorder.relabel(status="failed", termination="timeout", run_id=run_id)
    row = ledger.get(run_id)
    assert (row.status, row.termination) == ("failed", "timeout")


def test_relabel_of_an_unknown_run_is_a_no_op(tmp_path):
    runtime = _fake_runtime(tmp_path)
    attach_run_recorder(runtime)
    RunRecorder(runtime).relabel(status="failed", run_id="r_nope")   # no raise
```

`_fake_runtime` / `_event` 用该测试文件里已有的 helper；若没有，最小实现是 `SimpleNamespace(root=tmp_path, session=SimpleNamespace(path=tmp_path/"s.jsonl"), agent=SimpleNamespace(), run_ledger=None)` 与 `SimpleNamespace(type="agent_start", termination=None, turns=1, new_messages=[])`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_run_ledger.py -q -k relabel`
Expected: FAIL — `AttributeError: 'RunRecorder' object has no attribute 'relabel'`

- [ ] **Step 3: 实现 `RunRecorder.relabel`**

查行用 `ledger.get(run_id)`；写回用 `ledger.finish(row, status=status, termination=termination, error=error, duration_ms=row.duration_ms)`。整个方法体包在 `try/except Exception` 里（`# noqa: BLE001`），注释说明"观测性永远不许弄崩一轮"。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_run_ledger.py -q`
Expected: PASS（含原有全部用例）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/run_wiring.py tests/test_run_ledger.py
git commit -m "feat(runs): let the owning layer relabel a run's terminal row"
```

---

### Task 5: 跑一轮

**Files:**
- Create: `zenagent/app/automation_runner.py`
- Test: `tests/test_automation_runner.py`

**Interfaces:**
- Produces:
  - `ROUND_STATUSES: tuple[str, ...] = ("done", "failed", "timeout", "skipped", "error")`
  - `@dataclass(frozen=True) class RoundResult`：`status: str`、`skip_reason: str = ""`、`error: str = ""`、`session: str = ""`（这一轮实际用的 session basename，可能与 `job.session` 不同）
  - `async def run_round(runtime, chat, job: Job, *, run_number: int, now: float, session_root: Path) -> RoundResult`
- Consumes: Task 2 的 `resolve_session`、Task 3 的 `wrap_prompt`、Task 4 的 `RunRecorder.relabel`；既有的 `Runtime.switch_session` / `ChatController.submit` / `wait_for_idle` / `abort` / `busy` / `release_session_lease`

**执行序列（照此实现，每步失败都有确定结果）**：
1. `update_run_scope(runtime, kind="automation", label=job.name)`。
2. `path = resolve_session(session_root, job.session)`。文件不存在时用
   `agent.session.unique_session_path(root=runtime.root)` 铸一个新路径，并把它的 `name` 记进
   `RoundResult.session`（state 由调用方写，`run_round` 不碰 state）。日志里 warn 一次。
3. `await runtime.switch_session(path)`；捕 `SessionLeaseBusy` →
   `return RoundResult(status="skipped", skip_reason="session-busy", session=path.name)`，**不 submit**。
4. 订阅一个 `_RunIdSpy` 抓本轮 run id（`runtime.current_run_id` 在本轮结束后会被 recorder 置空，
   所以必须在 `agent_start` 时就记下来）；然后
   `chat.submit(wrap_prompt(job, run_number, now))`。
5. watchdog：`watchdog = asyncio.create_task(_sleep_then_abort(chat, job.timeout_s))`，
   同时 `idle = asyncio.create_task(chat.wait_for_idle())`，
   `await asyncio.wait([idle, watchdog], return_when=asyncio.FIRST_COMPLETED)`。
   watchdog 先完成 → `status = "timeout"`，**仍然**再 `await chat.wait_for_idle()`；
   最后 `watchdog.cancel()`（必须，否则它会在 30 分钟后对下一个 job 开火）。
   收尾断言 `chat.busy is False`，否则把 `status = "error"` 记进 `RoundResult.error`。
6. `status == "timeout"` 时 `RunRecorder(runtime).relabel(status="failed", termination="timeout", run_id=spy.run_id)`；
   `"done"` 不动（recorder 已写好）。取消订阅 spy。
7. `finally: release_session_lease(runtime)` —— 无论成功、跳过还是超时都放。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automation_runner.py
import asyncio
import time

import pytest

from zenagent.app.automations import Job, Schedule
from zenagent.app.automation_runner import RoundResult, run_round
from zenagent.app.session_lease import acquire_session, release_session


def job(timeout_s=5, session="s.jsonl"):
    return Job(id="a_1", name="r", prompt="go", schedule=Schedule.parse("daily 09:00"),
               session=session, timeout_s=timeout_s)


async def test_round_writes_the_session_and_the_ledger(prepared_runtime):
    runtime, chat = prepared_runtime()   # fake stream runtime + ChatController
    result = await run_round(runtime, chat, job(), run_number=1,
                             now=time.time(), session_root=runtime.root)
    assert result.status == "done"
    rows = runtime.run_ledger.load()
    assert rows[-1].kind == "automation"
    assert rows[-1].label == "r"
    assert rows[-1].status == "done"
    assert not chat.busy


async def test_busy_session_is_skipped_without_a_run(prepared_runtime):
    runtime, chat = prepared_runtime()
    target = runtime.session.path                 # the round will target this path
    lease = acquire_session(target, object())     # someone else owns it
    try:
        result = await run_round(runtime, chat, job(session=target.name), run_number=1,
                                 now=time.time(), session_root=target.parent)
    finally:
        release_session(lease)
    assert result.status == "skipped"
    assert result.skip_reason == "session-busy"
    assert runtime.run_ledger.load() == []


async def test_timeout_aborts_and_leaves_the_controller_idle(prepared_runtime):
    runtime, chat = prepared_runtime(hang=True)   # fake stream never finishes
    result = await run_round(runtime, chat, job(timeout_s=0.05), run_number=1,
                             now=time.time(), session_root=runtime.root)
    assert result.status == "timeout"
    assert chat.busy is False
    assert runtime.run_ledger.load()[-1].status == "failed"
    assert runtime.run_ledger.load()[-1].termination == "timeout"


async def test_missing_session_file_is_minted_not_fatal(prepared_runtime):
    runtime, chat = prepared_runtime()
    result = await run_round(runtime, chat, job(session="gone.jsonl"), run_number=1,
                             now=time.time(), session_root=runtime.root)
    assert result.status == "done"
    assert result.session and result.session != "gone.jsonl"


async def test_the_lease_is_released_after_the_round(prepared_runtime):
    runtime, chat = prepared_runtime()
    await run_round(runtime, chat, job(), run_number=1, now=time.time(),
                    session_root=runtime.root)
    # a fresh owner can take the session: nothing is holding it
    lease = acquire_session(runtime.session.path, object())
    release_session(lease)
```

这些 `async def` 测试不需要 `pytest.mark.asyncio`：`pyproject.toml` 的
`asyncio_mode = "auto"` 已经接管。另外 fixture 必须真的把挂住的 fake stream 收掉——
`pyproject.toml` 里 `timeout = 120` 会把一个泄漏的挂起任务烧成整个 CI job 的失败。

`prepared_runtime` fixture：复用 `tests/` 里既有的 fake-stream Runtime 构造（见 `tests/conftest.py` 与 `tests/test_chat.py`；若没有现成 helper，用 `zenagent.app.bootstrap.prepare(RuntimeOptions(root=...), stream_fn=<scripted>, assume_yes=True)`，fake stream 用一个 async generator 发一条 assistant 文本 + `done`；`hang=True` 版本在发出文本后 `await asyncio.sleep(3600)`）。把这个 fixture 写在 `tests/conftest.py` 里，命名 `prepared_runtime`，其它测试文件也能用。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automation_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: zenagent.app.automation_runner`

- [ ] **Step 3: 实现 `run_round`**

关键点全部写进注释：为什么不用 `asyncio.wait_for`、为什么在 `finally` 释放租约、为什么超时后还要再 `await chat.wait_for_idle()`（防止 abort 还没落地就把租约放掉，下一个进程会读到半截 transcript）。`SessionLeaseBusy` 从 `zenagent.app.session_lease` import。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automation_runner.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/app/automation_runner.py tests/test_automation_runner.py tests/conftest.py
git commit -m "feat(automations): run one round with lease, watchdog and relabel"
```

---

### Task 6: daemon 循环

**Files:**
- Create: `zenagent/cli/daemon.py`
- Modify: `zenagent/cli/main.py`（`raw[0] == "daemon"` 分支）
- Test: `tests/test_daemon_loop.py`

**Interfaces:**
- Produces:
  - `def next_wake(jobs: list[Job], states: dict[str, JobState], now: float) -> float` —— 最早的 `next_due`，没有 job 时返回 `now + 60`
  - `async def tick(runtime, chat, *, root: Path, now: float, request_path: Path) -> list[RoundResult]` —— 消费一次请求文件、跑 `due_list` 里的每一个、**就地写 state**（`StateStore.save`），失败也写（`last_status`/`last_error`/`last_skip`）
  - `async def run_daemon(root: Path, *, foreground: bool = False) -> int` —— 拿锁、bootstrap、循环
  - `def daemon_lock(root: Path) -> Path`
  - `def _take_daemon_lock(root: Path) -> int | None`、`def _drop_daemon_lock(fd: int) -> None`
    —— 薄封装，复用 `app/session_lease.py` 里同款的 `flock` 语义；`None` 表示"锁不住/已被占"
  - `def daemon_running(root: Path) -> bool` —— 试着拿一次锁立刻放开（在读租约同款的"探活"语义下用）
  - `def status_text(root: Path, *, now: float) -> str` —— `zen daemon status` 的输出，
    自己从 `root` 读 `AutomationStore`/`StateStore`（**不接收** jobs/states 参数，也不建 Runtime）：
    一行 `running|stopped`、一行 `jobs: N`、一行 `next wake: <YYYY-MM-DD HH:MM|none>`、
    以及**版本不一致时的一行提示**（`zenagent.__version__` 对比单元里写的
    `ZENAGENT_DAEMON_VERSION=<version>` 注释行）
- Consumes: Task 1/2/3 的 `Schedule`/`AutomationStore`/`StateStore`/`due_list`/`JobState`、Task 5 的 `run_round`

**语义**：
- 锁：`project.project_dir(root=root)/"daemon.lock"`，用 `zenagent.app.session_lease` 里同款的 flock 语义；拿不到就打印一行"already running"并 `return 1`（不抛）。
- 循环：`while not self._stop` → 消费请求文件 → `tick` → `sleep(min(next_wake(now) - now, 30.0))`。**30 秒上限**同时承担"检查请求文件"和"发现 mtime 变更"。
- 每轮开始前 `store.reload_if_changed()`；变了就重算 `states`（新 job 没有 state）。
- 请求文件 `automations.request.json`：`{"jobs": [{"name": "...", "at": 1764118800}]}`；消费 = 读出、删文件、把命中的 job 强制当到期（`Due(job, missed=0, run_number=state.run_count+1)`）。
- state 写在**每轮之后**（不是每 tick 之后），保证崩溃最多丢一轮的记录。
- `SIGINT`/`SIGTERM`：置 `_stop`，**不打断**正在跑的那一轮（`await chat.wait_for_idle()` 之后再返回）。
- `zen daemon status` 用 `daemon_running` + `next_wake` 拼出 `status_text`，**不常驻、不建 Runtime**。
  单元文件里写一个 `ZENAGENT_DAEMON_VERSION=<version>` 的注释行，`status` 读出它来比对，
  不一致就提示 `zen daemon install` 重装（这是 spec"升级后 daemon 跑的是旧代码"那条风险的落点）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_daemon_loop.py
import json
import time

import pytest

from zenagent.app.automations import AutomationStore, Job, JobState, Schedule, StateStore
from zenagent.cli.daemon import (
    _drop_daemon_lock,
    _take_daemon_lock,
    daemon_running,
    next_wake,
    status_text,
    tick,
)


def at(h, mi=0):
    return time.mktime((2026, 11, 26, h, mi, 0, 0, 0, -1))


def test_next_wake_picks_the_earliest_and_never_stalls():
    now = at(10)
    job = Job(id="a_1", name="r", prompt="p", schedule=Schedule.parse("every 1h"),
              session="s.jsonl")
    assert next_wake([job], {"a_1": JobState(last_run_at=at(9))}, now) == at(10)
    assert next_wake([], {}, now) == now + 60


async def test_tick_runs_a_due_job_and_writes_state(prepared_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))        # isolate ~/.zenagent for every tick test
    runtime, chat = prepared_runtime()
    store = AutomationStore(runtime.root)
    job = store.add(Job(id="", name="r", prompt="go",
                        schedule=Schedule.parse("every 1h"), session="s.jsonl"))
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6), run_count=2)})

    results = await tick(runtime, chat, root=runtime.root, now=at(10),
                         request_path=tmp_path / "req.json")
    assert [r.status for r in results] == ["done"]
    assert runtime.run_ledger.load()[-1].label == "r"
    # relabelled state under the SAME job id
    states = StateStore(runtime.root).load()
    assert states[job.id].last_status == "done"
    assert states[job.id].run_count == 3
    assert states[job.id].last_run_at == at(10)


async def test_tick_records_missed_count(prepared_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime, chat = prepared_runtime()
    store = AutomationStore(runtime.root)
    job = store.add(Job(id="", name="r", prompt="go",
                        schedule=Schedule.parse("every 1h"), session="s.jsonl"))
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6))})
    await tick(runtime, chat, root=runtime.root, now=at(10),
               request_path=tmp_path / "req.json")
    assert StateStore(runtime.root).load()[job.id].missed == 3


async def test_request_file_forces_a_run_and_is_consumed(prepared_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime, chat = prepared_runtime()
    store = AutomationStore(runtime.root)
    job = store.add(Job(id="", name="r", prompt="go",
                        schedule=Schedule.parse("daily 23:00"), session="s.jsonl"))
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"jobs": [{"name": "r", "at": 1}]}))
    results = await tick(runtime, chat, root=runtime.root, now=at(10), request_path=req)
    assert [r.status for r in results] == ["done"]
    assert not req.exists()


def test_daemon_running_reflects_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "proj"; (root / ".git").mkdir(parents=True)
    assert daemon_running(root) is False
    lease_fd = _take_daemon_lock(root)          # 测试内自己开一个，模拟常驻实例
    try:
        assert daemon_running(root) is True
        assert "running" in status_text(root, now=at(10))
    finally:
        _drop_daemon_lock(lease_fd)
    assert daemon_running(root) is False


def test_status_reports_stopped_and_the_next_wake(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "proj"; (root / ".git").mkdir(parents=True)
    text = status_text(root, now=at(10))
    assert "stopped" in text
    assert "jobs: 0" in text
    assert "next wake: none" in text


async def test_a_corrupt_definition_does_not_stop_the_tick(prepared_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime, chat = prepared_runtime()
    store = AutomationStore(runtime.root)
    store.add(Job(id="", name="r", prompt="go",
                  schedule=Schedule.parse("every 1h"), session="s.jsonl"))
    store.path.write_text("{torn")
    results = await tick(runtime, chat, root=runtime.root, now=at(10),
                         request_path=tmp_path / "req.json")
    assert results == []          # nothing ran, nothing raised
    assert store.warnings
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_daemon_loop.py -q`
Expected: FAIL — `ModuleNotFoundError: zenagent.cli.daemon`

- [ ] **Step 3: 实现 `tick` 与 `next_wake`，再接上 `run_daemon`**

`run_daemon` 用 `bootstrap.prepare(RuntimeOptions(root=root), assume_yes=True, allow_missing_key=False)` 拿 `runtime`/`broker`，自己 `ChatController(runtime)`（`prepare(views=False)` 不给 chat）。bootstrap 抛异常 → 打印原因、`return 2`（fail fast，不做降级）。`cli/main.py` 在 `raw[0] == "prompts"` 那一串旁边加 `if raw and raw[0] == "daemon": return asyncio.run(run_daemon(...))`，参数解析用 `cli/daemon.py` 自己的 argparse（`--foreground`、`--cwd`），保持和 `sessions`/`prompts` 同构。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_daemon_loop.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/cli/daemon.py zenagent/cli/main.py tests/test_daemon_loop.py
git commit -m "feat(daemon): tick loop over the project's automations"
```

---

### Task 7: 常驻安装（launchd / systemd）

**Files:**
- Modify: `zenagent/cli/daemon.py`（追加）
- Test: `tests/test_daemon_install.py`

**Interfaces:**
- Produces:
  - `def plist_text(*, python: str, root: Path, label: str = "com.zenagent.daemon") -> str`
  - `def systemd_unit_text(*, python: str, root: Path) -> str`
  - `def install(root: Path, *, platform: str = sys.platform, home: Path | None = None) -> Path` —— 写单元文件并调用 `launchctl`/`systemctl`（子进程失败时把错误原文抛出）
  - `def uninstall(*, platform: str = sys.platform, home: Path | None = None) -> bool`
  - `def unit_path(*, platform: str = sys.platform, home: Path | None = None, root: Path | None = None) -> Path`
  - `def unit_label(root: Path, *, base: str = "com.zenagent.daemon") -> str` —— `f"{base}.{key8}"`，`key8 = project_key(root)[-8:]`
- Consumes: Task 6 的 `daemon_lock`

**语义**：单元里的可执行路径必须是绝对路径（`python -m zenagent daemon --cwd <root>`），`WorkingDirectory`/`StandardOutPath` 都写绝对路径。label 带上项目 key 的后 8 位（`com.zenagent.daemon.<key8>`），否则多项目会互相覆盖。`uninstall` 对不存在的单元返回 `False`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_daemon_install.py
from zenagent.cli.daemon import plist_text, systemd_unit_text, unit_path


def test_plist_is_absolute_and_per_project(tmp_path):
    text = plist_text(python="/usr/bin/python3", root=tmp_path)
    assert f"{tmp_path}" in text
    assert "-m zenagent daemon" in text
    assert "<string>/usr/bin/python3</string>" in text
    assert "RunAtLoad" in text and "KeepAlive" in text
    assert unit_path(platform="darwin", home=tmp_path, root=tmp_path).name.startswith(
        "com.zenagent.daemon."
    )


def test_unit_is_per_project_and_restarts(tmp_path):
    text = systemd_unit_text(python="/usr/bin/python3", root=tmp_path)
    assert "Restart=on-failure" in text
    assert "-m zenagent daemon" in text
    assert str(tmp_path) in text


def test_label_is_per_project(tmp_path):
    text_a = plist_text(python="/p", root=tmp_path / "a")
    text_b = plist_text(python="/p", root=tmp_path / "b")
    assert text_a != text_b
    labels = {
        line.strip().removeprefix("<string>").removesuffix("</string>")
        for line in text_a.splitlines()
        if "com.zenagent.daemon." in line
    }
    assert any(label.endswith("a") or label.endswith("b") for label in labels)
```

两条不同 root 的单元文件必须字节不同（label 带项目 key 后 8 位），否则多项目会互相覆盖。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_daemon_install.py -q`
Expected: FAIL — `ImportError: cannot import name 'plist_text'`

- [ ] **Step 3: 实现四个函数**

`unit_path`：darwin → `home/Library/LaunchAgents/<label>.plist`；linux → `home/.config/systemd/user/zenagent-daemon-<key8>.service`；其它平台抛 `RuntimeError("unsupported platform")`。`install` 用 `subprocess.run(["launchctl","bootstrap",f"gui/{os.getuid()}", str(path)], check=True)` / `["systemctl","--user","enable","--now", name]`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_daemon_install.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/cli/daemon.py tests/test_daemon_install.py
git commit -m "feat(daemon): launchd and systemd unit generation plus install"
```

---

### Task 8: `zen automations` CLI

**Files:**
- Create: `zenagent/cli/automationscmd.py`
- Modify: `zenagent/cli/main.py`（`raw[0] == "automations"` 分支）
- Test: `tests/test_automations_cli.py`

**Interfaces:**
- Produces: `def build_parser() -> argparse.ArgumentParser`、`def main(argv: list[str] | None = None) -> int`（照 `cli/sessionscmd.py` 的形状）；子命令 `add`/`list`/`show`/`rm`/`enable`/`disable`/`run`
- Consumes: Task 2 的 `AutomationStore`/`StateStore`/`resolve_session`、Task 3 的 `due_list`、Task 6 的 `daemon_lock`

**语义**：root 取 `project.find_project_root()`（当前目录），`add` 校验当前目录就是项目根（`find_project_root() == Path.cwd().resolve()`），否则退出码 1 + 明确提示。`add` 铸 session 名用 `agent.session.unique_session_path(root=root).name`；**不创建文件**。`run` 写请求文件；daemon 锁拿不到时打印"daemon not running — start it with `zen daemon`"并返回 1。`list` 输出列：`name  schedule  next-due  last-status  last-skip  cost`（cost 从 `RunLedger.list(kind="automation", ...)` 的最后一条取）。`--json` 输出 `{"jobs": [...], "warnings": [...]}`，形状要与 Task 9 的 rpc op 一致（同构）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automations_cli.py
import json

import pytest

from zenagent.cli.automationscmd import main


def test_add_list_enable_disable_rm(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()                      # make it a project root
    assert main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"]) == 0
    out = capsys.readouterr().out
    assert "r" in out and "09:00" in out
    assert "auto" in out                              # prints the permission boundary

    assert main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["jobs"][0]["name"] == "r"
    assert payload["jobs"][0]["session"].endswith(".jsonl")

    assert main(["disable", "r"]) == 0
    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["enabled"] is False
    assert main(["enable", "r"]) == 0
    assert main(["rm", "r"]) == 0
    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == []


def test_add_requires_the_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "deep").mkdir()
    monkeypatch.chdir(tmp_path / "deep")
    (tmp_path / ".git").mkdir()
    assert main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"]) == 1


def test_daily_and_every_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    assert main(["add", "--name", "r", "--daily", "09:00", "--every", "15m",
                 "--prompt", "go"]) == 2


def test_run_without_a_daemon_fails_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"])
    capsys.readouterr()
    assert main(["run", "r"]) == 1
    assert "daemon" in capsys.readouterr().err
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automations_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: zenagent.cli.automationscmd`

- [ ] **Step 3: 实现 CLI**

argparse 用 `subparsers(dest="action", required=True)`。`add` 的 `--daily`/`--every` 用 `add_mutually_exclusive_group(required=True)`。用户可见文案走 `zenagent/i18n`。`main.py` 里照 `sessions` 的样子加分支。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automations_cli.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add zenagent/cli/automationscmd.py zenagent/cli/main.py tests/test_automations_cli.py
git commit -m "feat(cli): zen automations add/list/show/rm/enable/disable/run"
```

---

### Task 9: rpc `automations` 域

**Files:**
- Create: `zenagent/cli/rpc/ops_automations.py`
- Modify: `zenagent/cli/rpc/protocol/_host.py`（`HOST_OPS` 加 `automations`）、`zenagent/cli/rpc/host.py`（mixin + 分发）
- Modify: `desktop/src/rpc/types.ts`（`REQUEST_KINDS` 加 `'automations'`）
- Regenerate: `desktop/src/rpc/wire.gen.ts`
- Test: `tests/test_automations_rpc.py`

**Interfaces:**
- Produces（wire）：`kind="automations"`，subs `list_all` / `list` / `add` / `update` / `rm` / `run`，结果统一 `{"jobs": [AutomationRow], "warnings": [str]}`；`AutomationRow` 是扁平 dict：`project`、`name`、`prompt`、`schedule`（原始字符串）、`enabled`、`timeout_s`、`session`、`next_due`（float｜0）、`last_status`、`last_skip`、`last_error`、`last_run_at`、`missed`、`run_count`、`cost`
- Consumes: Task 2/3 的 store 与 `due_list`

**语义**：全部 host 级（`RpcHost` 的 mixin，照 `RunsOps`），`project` 字段缺失时用 foreground 项目。**任何 op 都不建 Runtime。** `add`/`update`/`rm` 之后复查 daemon 是否存在（`daemon_lock` 能拿到 = 没在跑），在 `warnings` 里追加一条 `"daemon not running"`，让 UI 能提示。`run` 写请求文件，daemon 不在跑时在 `warnings` 里说明而不是报错。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_automations_rpc.py
import json

import pytest

from zenagent.cli.rpc.host import RpcHost


async def test_add_then_list_all_across_projects(rpc_host, tmp_path, monkeypatch):
    # rpc_host: 既有的 RpcHost 测试 helper（见 tests/test_rpc_host.py）
    a = tmp_path / "a"; (a / ".git").mkdir(parents=True)
    b = tmp_path / "b"; (b / ".git").mkdir(parents=True)
    await rpc_host.request({"type": "automations", "op": "add", "id": "1",
                            "project": str(a), "name": "r", "prompt": "go",
                            "schedule": "daily 09:00"})
    await rpc_host.request({"type": "automations", "op": "add", "id": "2",
                            "project": str(b), "name": "s", "prompt": "go",
                            "schedule": "every 15m"})
    reply = await rpc_host.request({"type": "automations", "op": "list_all", "id": "3"})
    names = sorted(row["name"] for row in reply["result"]["jobs"])
    assert names == ["r", "s"]


async def test_add_does_not_construct_a_runtime_or_create_a_session_file(rpc_host, tmp_path):
    root = tmp_path / "p"; (root / ".git").mkdir(parents=True)
    await rpc_host.request({"type": "automations", "op": "add", "id": "1",
                            "project": str(root), "name": "r", "prompt": "go",
                            "schedule": "daily 09:00"})
    sessions = list((root / "sessions").glob("*.jsonl")) if (root / "sessions").exists() else []
    assert sessions == []
    # and the project is NOT resident in the host
    assert all(slot["root"] != str(root) for slot in rpc_host.resident)


async def test_update_toggles_and_rm_removes(rpc_host, tmp_path):
    root = tmp_path / "p"; (root / ".git").mkdir(parents=True)
    await rpc_host.request({"type": "automations", "op": "add", "id": "1",
                            "project": str(root), "name": "r", "prompt": "go",
                            "schedule": "daily 09:00"})
    await rpc_host.request({"type": "automations", "op": "update", "id": "2",
                            "project": str(root), "name": "r", "enabled": False})
    reply = await rpc_host.request({"type": "automations", "op": "list", "id": "3",
                                   "project": str(root)})
    assert reply["result"]["jobs"][0]["enabled"] is False
    await rpc_host.request({"type": "automations", "op": "rm", "id": "4",
                            "project": str(root), "name": "r"})
    reply = await rpc_host.request({"type": "automations", "op": "list", "id": "5",
                                   "project": str(root)})
    assert reply["result"]["jobs"] == []


async def test_bad_schedule_is_a_refusal_not_a_crash(rpc_host, tmp_path):
    root = tmp_path / "p"; (root / ".git").mkdir(parents=True)
    reply = await rpc_host.request({"type": "automations", "op": "add", "id": "1",
                                    "project": str(root), "name": "r", "prompt": "go",
                                    "schedule": "hourly"})
    assert reply["ok"] is False or reply["type"] == "error"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_automations_rpc.py -q`
Expected: FAIL — 未知 type `automations`

- [ ] **Step 3: 声明 registry、实现 mixin、接分发、重生成 TS**

`protocol/_host.py` 的 `HOST_OPS` 里加 `"automations": OpSpec("automations", NoFields, {}, subs={...})`，每个 sub 用 `record(ANY)`/`list_of(record(ANY))` 描述 `jobs`（扁平 row 不逐个声明字段，和 `ResidentSlot` 用 `record(ANY)` 的做法一致）。`host.py`：`class RpcHost(RunsOps, AutomationsOps)`，分发里加 `if kind == "automations": await self._automations_op(request, request_id); return True`。然后：

```bash
uv run python scripts/gen_rpc_ts.py
```

`desktop/src/rpc/types.ts` 的 `REQUEST_KINDS` 加 `'automations'`（`tests/test_rpc_contract.py` 要求与 py 侧 `kind == "..."` 集合完全相等）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_automations_rpc.py tests/test_rpc_contract.py tests/test_rpc_host.py -q`
Expected: PASS（4 passed 新增 + 既有全绿）

- [ ] **Step 5: 提交**

```bash
git add zenagent/cli/rpc/ops_automations.py zenagent/cli/rpc/protocol/_host.py \
        zenagent/cli/rpc/host.py desktop/src/rpc/wire.gen.ts desktop/src/rpc/types.ts \
        tests/test_automations_rpc.py
git commit -m "feat(rpc): automations ops for the desktop (no runtime construction)"
```

---

### Task 10: 桌面端面板

**Files:**
- Create: `desktop/src/state/automations.ts`、`desktop/src/ui/AutomationsPanel.tsx`、`desktop/tests/automations.test.ts`
- Modify: `desktop/src/rpc/client.ts`（照 `runs`/`skills` 的既有形状加 `automations` 调用）、侧栏入口（按 `desktop/src/ui/SidebarCoding.tsx` 的既有模式）

**Interfaces:**
- Produces: `export interface AutomationRow { project, name, prompt, schedule, enabled, timeout_s, session, next_due, last_status, last_skip, last_error, last_run_at, missed, run_count, cost }`、`export function groupByProject(rows: AutomationRow[]): Map<string, AutomationRow[]>`、`export function formatNextDue(next_due: number, now: number): string`
- Consumes: Task 9 的 wire 形状

**语义**：面板 = 按项目分组的列表 + 行内开关 + "现在跑一次" + 新建表单（项目选择器来自既有的 `useProjects()`）。轮询 `list_all` 每 2 秒（`setInterval`，组件卸载时清掉）。纯函数（`groupByProject`/`formatNextDue`）单独放 `state/automations.ts` 以便 `node --test`。

- [ ] **Step 1: 写失败测试**

```ts
// desktop/tests/automations.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { formatNextDue, groupByProject } from '../src/state/automations.ts'

test('groupByProject keeps projects together, most urgent first', () => {
  const rows = [
    { project: '/a', name: 'r', next_due: 200 },
    { project: '/b', name: 's', next_due: 100 },
    { project: '/a', name: 't', next_due: 300 },
  ] as never
  const groups = groupByProject(rows)
  assert.deepEqual([...groups.keys()], ['/b', '/a'])
  assert.deepEqual(groups.get('/a')!.map((r) => r.name), ['r', 't'])
})

test('formatNextDue says "now" for due-or-past and a short delta otherwise', () => {
  assert.equal(formatNextDue(0, 1000), '—')
  assert.equal(formatNextDue(900, 1000), 'now')
  assert.equal(formatNextDue(1000 + 90, 1000), '1m')
  assert.equal(formatNextDue(1000 + 7200, 1000), '2h')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && node --test tests/automations.test.ts`
Expected: FAIL — 找不到模块 `../src/state/automations.ts`

- [ ] **Step 3: 实现纯函数与面板**

`groupByProject` 用 `Map`（插入序 = 每组最小 `next_due` 升序）。`formatNextDue`：`0 → "—"`，`<= now → "now"`，`< 3600 → "{分钟}m"`，否则 `"{小时}h"`。面板组件在开关/新建后立刻重拉一次，不乐观更新。

- [ ] **Step 4: 跑测试与类型检查**

Run: `cd desktop && node --test tests/automations.test.ts && npx tsc --noEmit`
Expected: 2 passed，tsc 干净（`node --test tests/*.test.ts` 跑全量留给 Task 11 的门禁）

**注意**：仓库的 `node --test` 直跑 `.ts` 依赖 Node 的类型剥离（本机已如此，`package.json`
的 `test` 脚本就是 `node --test tests/*.test.ts`）。若本机 Node 不支持，退回在该测试里
`import` 已编译产物是错的——改用 `npx tsx --test` 并在计划里备注，不要降低测试覆盖。

- [ ] **Step 5: 提交**

```bash
git add desktop/src/state/automations.ts desktop/src/ui/AutomationsPanel.tsx \
        desktop/src/rpc/client.ts desktop/tests/automations.test.ts
git commit -m "feat(desktop): automations panel (list, toggle, create, run now)"
```

---

### Task 11: 文档、explorer 与 CHANGELOG

**Files:**
- Create: `docs/automations.md`
- Modify: `docs/architecture.md`（层图里 `app/` 与 `cli/` 的职责各加一句）、`docs/explorer/src/modesData.ts`（账本 kind 列表加 `automation`）、`CHANGELOG.md`
- Test: `tests/test_docs_automations.py`（钉住文档与命令的一致性）

**Interfaces:**
- Consumes: Task 8 的子命令名、Task 9 的 op 名

- [ ] **Step 1: 写失败测试**

```python
# tests/test_docs_automations.py
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_docs_automations_documents_every_subcommand():
    text = (REPO / "docs" / "automations.md").read_text()
    for sub in ("add", "list", "show", "rm", "enable", "disable", "run"):
        assert f"zen automations {sub}" in text, sub
    for word in ("daemon", "session", "权限", "timeout", "state", "automations.request.json"):
        assert word in text, word


def test_explorer_lists_the_automation_kind():
    text = (REPO / "docs" / "explorer" / "src" / "modesData.ts").read_text()
    assert "automation" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_docs_automations.py -q`
Expected: FAIL — `FileNotFoundError: docs/automations.md`

- [ ] **Step 3: 写文档**

`docs/automations.md`：它是什么、一条 job 的五个字段、`automations.json`/`state.json`/`request.json` 三个文件的分工表、一项目一 daemon 与安装/卸载、权限边界（**照 spec 的"权限"一节原文**：工作目录内外都能写、破坏性命令照跑、凭证能改但读不到、`[tools] deny` 与灾难性命令仍被拒）、错过补跑与跳过的语义、桌面面板的入口。`CHANGELOG.md` 按仓库既有格式加一条。

- [ ] **Step 4: 跑全门禁**

Run: `bash scripts/check.sh`
Expected: 全绿（sync / ruff / mypy / 架构预算 / wire 新鲜度 / worktree / pytest）

- [ ] **Step 5: 提交**

```bash
git add docs/automations.md docs/architecture.md docs/explorer/src/modesData.ts \
        CHANGELOG.md tests/test_docs_automations.py
git commit -m "docs: automations guide, explorer kind, changelog"
```

---

## 自审记录

- **spec 覆盖**：定义/状态/请求三文件（T2/T6/T8）、时间表（T1）、补跑与 missed（T3/T6）、提示词包装（T3）、跑一轮与租约/超时/relabel（T4/T5）、daemon 与锁与信号（T6）、安装（T7）、CLI（T8）、rpc 六个 op（T9）、桌面面板（T10）、文档（T11）。spec 的"留给 builder 的决定"里除"模型档"外都已落成具体选择（模型沿用 `config.toml` 默认；`zen daemon status` 由 T6 的 `status_text`/`daemon_running` 提供；损坏降级在 T2；`rm` 最后一条只提示不卸载；UI 轮询走 `list_all`）。
- **类型一致**：`Job`/`JobState`/`Schedule`/`RoundResult`/`Due` 的字段在各任务的 Interfaces 块里只定义一次，后续任务只引用。
- **Review Focus 落点**：租约（T5 的最后一个用例 + T6 的 tick）、超时卡死（T5 第三个用例）、坏 JSON（T2、T6 最后一个用例）、双写者（T6 的锁、T8 的 run、T9 的 warnings）、session 被删（T5 第四个用例）。
- **比例**：本计划不重写实现代码，只在签名有歧义处给出关键函数名的形态；每个任务的测试与命令是主体。
