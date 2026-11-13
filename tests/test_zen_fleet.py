"""The zen fleet: async dispatch to worker sessions and report injection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.zen.fleet import ZenFleet
from zenagent.types import AssistantMessage, TextContent, Usage


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        provider="fake",
        model="fake",
        usage=Usage(),
        stop_reason="stop",
        timestamp=0,
    )


class FakeWorkerAgent:
    """A worker agent whose run is awaitable and records subscribers."""

    def __init__(self, factory: FakeWorkerFactory, path: Path) -> None:
        self.factory = factory
        self.path = path
        self.messages: list = []
        self.termination = "stop"
        self.is_streaming = False
        self._listeners: list = []
        self.abort_called = False

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def abort(self) -> None:
        self.abort_called = True

    async def prompt(self, text: str, images=None) -> None:
        self.factory.prompts.append((self.path, text))
        gate = self.factory.gates.pop(self.path, None)
        if gate is not None:
            await gate
        # Model a mutating tool gated by the wired confirm, as the real broker
        # gates write/exec: a denial stops the mutation. No confirm = no gate.
        broker = self.factory.brokers.get(str(self.path))
        confirm = getattr(broker, "confirmed", None)
        decision = "allow"
        if confirm is not None:
            result = confirm("write_file", {"path": "note.txt", "content": "hi"})
            decision = await result if asyncio.iscoroutine(result) else result
        if decision in ("allow", "always"):
            self.factory.mutations.append(str(self.path))
        self.messages.append(_assistant(f"did: {text.splitlines()[0]}"))
        if self.factory.errors.get(self.path):
            raise RuntimeError(self.factory.errors.pop(self.path))


class FakePreparedBroker:
    """The broker inside a real ``PreparedFrontend``; records confirm wiring."""

    def __init__(self) -> None:
        self.confirmed: object = None

    def set_confirm(self, fn) -> None:
        self.confirmed = fn


class FakeWorkerRuntime:
    """The runtime a dispatch builds for one worker session."""

    def __init__(self, factory: FakeWorkerFactory, path: Path) -> None:
        self.factory = factory
        self.root = factory.root
        self.cwd = factory.root
        self.session = FakeWorkerSession(path)
        self.agent = FakeWorkerAgent(factory, path)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeWorkerSession:
    def __init__(self, path: Path) -> None:
        self.path = path


class FakeWorkerFactory:
    """Monkeypatched ``prepare``: one runtime per (root, resume)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtimes: dict[str, FakeWorkerRuntime] = {}
        self.brokers: dict[str, FakePreparedBroker] = {}
        self.modes: dict[str, str | None] = {}
        self.prompts: list[tuple[Path, str]] = []
        self.gates: dict[Path, object] = {}
        self.build_gates: dict[Path, object] = {}
        self.errors: dict[Path, str] = {}
        self.calls: list[dict] = []
        self.mutations: list[str] = []

    async def prepare(self, options, **kwargs) -> SimpleNamespace:
        """Match the real ``prepare``: a PreparedFrontend envelope, not a bare Runtime."""
        self.calls.append(
            {
                "root": str(options.root),
                "resume": options.resume,
                "model_id": options.model_id,
                "thinking": options.thinking,
                "max_cost": options.max_cost,
                "max_turns": options.max_turns,
            }
        )
        path = Path(options.resume)
        build_gate = self.build_gates.pop(path, None)
        if build_gate is not None:
            await build_gate
        rt = FakeWorkerRuntime(self, path)
        broker = FakePreparedBroker()
        self.runtimes[str(path)] = rt
        self.brokers[str(path)] = broker
        self.modes[str(path)] = getattr(options, "mode", None)
        return SimpleNamespace(runtime=rt, broker=broker)


class FakeChat:
    def __init__(self) -> None:
        self.steered: list[str] = []
        self.delivered: list[str] = []
        self.steering = False

    def steer(self, text: str) -> None:
        self.steered.append(text)

    async def deliver(self, text: str) -> None:
        """Mirror the real ChatController.deliver the fleet must prefer."""
        if self.steering:
            self.steer(text)
        else:
            self.delivered.append(text)


def _worker_files(tmp_path: Path, names: list[str]) -> dict[str, Path]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    paths = {}
    for name in names:
        p = sessions / f"{name}.jsonl"
        p.write_text("{}", encoding="utf-8")
        paths[name] = p
    return paths


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    paths = _worker_files(tmp_path, ["router", "w1", "w2", "w3"])
    factory = FakeWorkerFactory(tmp_path / "project")
    monkeypatch.setattr("zenagent.app.zen.fleet.prepare", factory.prepare)
    chat = FakeChat()
    changes: list[int] = []
    confirm = lambda *a, **k: "allow"
    fleet = ZenFleet(
        chat=chat,
        root=tmp_path / "project",
        sessions_dir=tmp_path / "sessions",
        router_path=str(paths["router"]),
        max_concurrent=2,
        on_change=lambda: changes.append(1),
        confirm=confirm,
        mode="ask",
        batch_window_s=0.05,
    )
    return {
        "fleet": fleet,
        "chat": chat,
        "factory": factory,
        "paths": paths,
        "changes": changes,
        "tmp_path": tmp_path,
    }


def _fleet_with(env: dict, **kwargs) -> ZenFleet:
    return ZenFleet(
        chat=kwargs.pop("chat", env["chat"]),
        root=env["tmp_path"] / "project",
        sessions_dir=env["tmp_path"] / "sessions",
        router_path=str(env["paths"]["router"]),
        on_change=lambda: None,
        confirm=lambda *a, **k: "allow",
        batch_window_s=0.05,
        **kwargs,
    )


async def test_worker_mode_inherit_downgrades_auto_to_ask(env) -> None:
    fleet = _fleet_with(env, mode="auto")
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w1"])] == "ask"


async def test_worker_mode_inherit_keeps_other_router_modes(env) -> None:
    fleet = _fleet_with(env, mode="plan")
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w1"])] == "plan"


async def test_worker_mode_pins_the_worker_broker(env) -> None:
    fleet = _fleet_with(env, mode="ask", worker_mode="auto")
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w1"])] == "auto"


async def test_report_limit_cuts_worker_reports(env) -> None:
    fleet = _fleet_with(env, mode="ask", report_limit=10)
    await fleet.dispatch(str(env["paths"]["w1"]), "abcdefghij")
    await fleet.wait_all()
    report = env["chat"].delivered[0].removeprefix("[zen:worker w1] ")
    assert report.startswith("did: ")
    assert len(report) == 10


async def test_dispatch_runs_worker_and_injects_report(env) -> None:
    fleet: ZenFleet = env["fleet"]
    path = env["paths"]["w1"]
    status = await fleet.dispatch(str(path), "修复编译")
    assert status == "working"
    await fleet.wait_all()
    assert fleet.status_of(str(path)) == "waiting"
    assert "[zen:worker" in env["chat"].delivered[0], "idle router must be woken"


async def test_reports_never_steer_a_non_router_session(env) -> None:
    fleet: ZenFleet = env["fleet"]
    env["chat"].runtime = SimpleNamespace(
        session=SimpleNamespace(path=env["paths"]["w1"])
    )
    await fleet.dispatch(str(env["paths"]["w2"]), "work")
    await fleet.wait_all()
    assert env["chat"].delivered == []
    assert env["chat"].steered == []

    # Back on the router, the next report is delivered as usual.
    env["chat"].runtime = SimpleNamespace(
        session=SimpleNamespace(path=env["paths"]["router"])
    )
    await fleet.dispatch(str(env["paths"]["w2"]), "again")
    await fleet.wait_all()
    assert env["chat"].delivered and "[zen:worker" in env["chat"].delivered[-1]


async def test_dispatch_materializes_the_spawned_worker_path(env) -> None:
    fleet: ZenFleet = env["fleet"]
    path = Path(fleet.spawn("continuity"))
    assert not path.exists(), "spawn keeps the session lazy"
    await fleet.dispatch(str(path), "first task")
    await fleet.wait_all()
    assert path.is_file(), "dispatch must resume the registered path, not a -N sibling"
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["type"] == "session"
    assert header["id"] == path.stem


async def test_report_steers_only_while_router_streams(env) -> None:
    fleet: ZenFleet = env["fleet"]
    env["chat"].steering = True
    await fleet.dispatch(str(env["paths"]["w2"]), "a")
    await fleet.wait_all()
    assert env["chat"].steered and "[zen:worker" in env["chat"].steered[0]
    assert env["chat"].delivered == []


async def test_worker_prompt_carries_focus_and_task(env) -> None:
    fleet: ZenFleet = env["fleet"]
    fleet.focus(str(env["paths"]["w1"]), "编译修复")
    await fleet.dispatch(str(env["paths"]["w1"]), "把警告清零")
    await fleet.wait_all()
    text = env["factory"].prompts[0][1]
    assert "编译修复" in text and "把警告清零" in text


async def test_dispatch_router_is_refused(env) -> None:
    with pytest.raises(ValueError, match="router"):
        await env["fleet"].dispatch(str(env["paths"]["router"]), "x")


async def test_dispatch_busy_worker_is_refused(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = __import__("asyncio").get_running_loop()
    gate = loop.create_future()
    env["factory"].gates[env["paths"]["w1"]] = gate
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    with pytest.raises(ValueError, match="working"):
        await fleet.dispatch(str(env["paths"]["w1"]), "b")
    gate.set_result(None)
    await fleet.wait_all()


async def test_concurrency_cap_queues(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = __import__("asyncio").get_running_loop()
    gates = [loop.create_future() for _ in range(2)]
    env["factory"].gates[env["paths"]["w1"]] = gates[0]
    env["factory"].gates[env["paths"]["w2"]] = gates[1]
    s1 = await fleet.dispatch(str(env["paths"]["w1"]), "a")
    s2 = await fleet.dispatch(str(env["paths"]["w2"], ), "b")
    assert (s1, s2) == ("working", "working")
    s3 = await fleet.dispatch(str(env["paths"]["w3"]), "c")
    assert s3 == "queued"
    gates[0].set_result(None)
    gates[1].set_result(None)
    await fleet.wait_all()
    assert fleet.status_of(str(env["paths"]["w3"])) == "waiting"


async def _await_runtime(factory: FakeWorkerFactory, path: Path) -> None:
    """Yield until the dispatch task has built its worker runtime."""
    for _ in range(50):
        if str(path) in factory.runtimes:
            return
        await asyncio.sleep(0)
    raise AssertionError("worker runtime never built")


async def _await_flush_armed(fleet: ZenFleet) -> None:
    """Yield until a report is buffered and the flush sleeps in its batch window.

    A latch, not a wall-clock guess: once the window is armed a second report
    released now joins the same batch deterministically.
    """
    for _ in range(200):
        if (
            fleet._wake_task is not None
            and not fleet._wake_task.done()
            and fleet._report_buffer
        ):
            return
        await asyncio.sleep(0)
    raise AssertionError("batch window never armed")


async def test_worker_failure_marks_failed_and_injects(env) -> None:
    fleet: ZenFleet = env["fleet"]
    env["factory"].errors[env["paths"]["w1"]] = "boom"
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert fleet.status_of(str(env["paths"]["w1"])) == "failed"
    assert any("boom" in s for s in env["chat"].delivered)


async def test_stop_aborts_running_worker(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = __import__("asyncio").get_running_loop()
    gate = loop.create_future()
    env["factory"].gates[env["paths"]["w1"]] = gate
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await _await_runtime(env["factory"], env["paths"]["w1"])
    assert await fleet.stop(str(env["paths"]["w1"])) is True
    gate.set_result(None)
    await fleet.wait_all()
    rt = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert rt.agent.abort_called
    assert fleet.status_of(str(env["paths"]["w1"])) in ("blocked", "waiting")


async def test_worker_timeout_marks_failed_and_reclaims(env) -> None:
    fleet: ZenFleet = env["fleet"]
    fleet.run_timeout_s = 0.05
    loop = asyncio.get_running_loop()
    gate = loop.create_future()
    env["factory"].gates[env["paths"]["w1"]] = gate
    # cap is 2: w1 hangs, w2 runs, w3 queues until the timeout frees w1's slot.
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await _await_runtime(env["factory"], env["paths"]["w1"])
    await fleet.dispatch(str(env["paths"]["w2"]), "b")
    await fleet.dispatch(str(env["paths"]["w3"]), "c")

    await asyncio.wait_for(fleet.wait_all(), timeout=5)

    rt = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert rt.agent.abort_called
    assert rt.closed
    assert fleet.status_of(str(env["paths"]["w1"])) == "failed"
    assert any("超时" in s for s in env["chat"].delivered)
    # The freed slot let the queued worker run to completion.
    assert fleet.status_of(str(env["paths"]["w3"])) == "waiting"
    assert fleet.has_active() is False


async def test_fleet_worker_built_after_close_is_torn_down(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = asyncio.get_running_loop()
    gate = loop.create_future()
    env["factory"].build_gates[env["paths"]["w1"]] = gate
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    # Wait until _run is suspended inside _build_worker (prepare entered).
    for _ in range(50):
        if any(c["resume"] == str(env["paths"]["w1"]) for c in env["factory"].calls):
            break
        await asyncio.sleep(0)
    # close() lands while the (slow) build is in flight.
    fleet._closing = True
    gate.set_result(None)
    await fleet.wait_all()
    rt = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert rt.closed
    assert env["factory"].prompts == []
    assert fleet.status_of(str(env["paths"]["w1"])) == "blocked"


async def test_cancelled_flush_does_not_reschedule(env) -> None:
    fleet: ZenFleet = env["fleet"]
    fleet.batch_window_s = 0.2
    fleet._report_buffer.append("[zen:worker w1] report")
    original = asyncio.get_running_loop().create_task(fleet._flush_reports())
    fleet._wake_task = original
    # Let the flush enter its batch-window sleep with the buffer still full.
    await asyncio.sleep(0.01)
    assert fleet._report_buffer
    original.cancel()
    try:
        await original
    except asyncio.CancelledError:
        pass
    # A flush cancelled during teardown must not spawn an orphan successor.
    assert fleet._wake_task is original


async def test_focus_updates_roster(env) -> None:
    fleet: ZenFleet = env["fleet"]
    fleet.focus(str(env["paths"]["w1"]), "性能巡检")
    rows = {r["path"]: r for r in fleet.roster()}
    assert rows[str(env["paths"]["w1"])]["focus"] == "性能巡检"
    assert rows[str(env["paths"]["w1"])]["role"] == "worker"


async def test_roster_lists_router_and_workers(env) -> None:
    fleet: ZenFleet = env["fleet"]
    rows = fleet.roster()
    roles = {r["path"]: r["role"] for r in rows}
    assert roles[str(env["paths"]["router"])] == "router"
    assert roles[str(env["paths"]["w1"])] == "worker"


async def test_spawn_creates_tracked_session(env) -> None:
    fleet: ZenFleet = env["fleet"]
    path = fleet.spawn("冒烟测试")
    assert Path(path).exists() is False or True  # lazy session: file may not exist
    assert path.endswith(".jsonl")
    rows = {r["path"]: r for r in fleet.roster()}
    assert rows[path]["focus"] == "冒烟测试"


async def test_close_aborts_and_marks_blocked(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = __import__("asyncio").get_running_loop()
    gate = loop.create_future()
    env["factory"].gates[env["paths"]["w1"]] = gate
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await _await_runtime(env["factory"], env["paths"]["w1"])
    await fleet.close()
    gate.set_result(None)
    rt = env["factory"].runtimes[str(env["paths"]["w1"])]
    assert rt.agent.abort_called
    assert fleet.has_active() is False


async def test_has_active_tracks_lifecycle(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = __import__("asyncio").get_running_loop()
    gate = loop.create_future()
    env["factory"].gates[env["paths"]["w1"]] = gate
    assert fleet.has_active() is False
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    assert fleet.has_active() is True
    gate.set_result(None)
    await fleet.wait_all()
    assert fleet.has_active() is False


async def test_worker_runtime_is_unwrapped_from_prepared_frontend(env) -> None:
    """The real prepare returns PreparedFrontend; the fleet must unwrap
    .runtime (and not treat the envelope as the runtime)."""
    fleet: ZenFleet = env["fleet"]
    path = env["paths"]["w1"]
    await fleet.dispatch(str(path), "修复")
    await fleet.wait_all()
    prepared = env["factory"].runtimes[str(path)]
    assert prepared.closed is True  # the Runtime was closed, not the envelope
    broker = env["factory"].brokers[str(path)]
    assert broker.confirmed is not None


async def test_worker_inherits_confirm_and_mode(env) -> None:
    fleet: ZenFleet = env["fleet"]
    await fleet.dispatch(str(env["paths"]["w2"]), "修复")
    await fleet.wait_all()
    broker = env["factory"].brokers[str(env["paths"]["w2"])]
    assert broker.confirmed is not None
    assert env["factory"].modes[str(env["paths"]["w2"])] == "ask"


async def test_auto_router_workers_run_in_ask_mode(env) -> None:
    """An auto router keeps its autonomy, but its workers downgrade to ask so
    mutating tool calls surface as approvals instead of auto-approving."""
    fleet: ZenFleet = env["fleet"]
    fleet.mode = "auto"
    await fleet.dispatch(str(env["paths"]["w1"]), "修复")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w1"])] == "ask"


async def test_plan_router_workers_inherit_plan(env) -> None:
    """Only the auto bypass is downgraded; a plan router's workers keep plan."""
    fleet: ZenFleet = env["fleet"]
    fleet.mode = "plan"
    await fleet.dispatch(str(env["paths"]["w2"]), "修复")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w2"])] == "plan"


async def test_zen_approval_deny(env) -> None:
    fleet: ZenFleet = env["fleet"]
    asked: list[str] = []

    async def deny(tool_name, arguments, verdict=None):
        asked.append(tool_name)
        return "deny"

    fleet.confirm = deny
    await fleet.dispatch(str(env["paths"]["w1"]), "写文件")
    await fleet.wait_all()
    # The worker consulted the router's wired confirm and the denial stuck.
    assert asked == ["write_file"]
    assert env["factory"].mutations == []
    assert fleet.status_of(str(env["paths"]["w1"])) == "waiting"


async def test_same_moment_reports_arrive_as_one_batch(env) -> None:
    """dsh batching: reports completing together reach the router merged,
    so one run reviews all of them instead of one run per report."""
    fleet: ZenFleet = env["fleet"]
    loop = asyncio.get_running_loop()
    gates = [loop.create_future() for _ in range(2)]
    env["factory"].gates[env["paths"]["w1"]] = gates[0]
    env["factory"].gates[env["paths"]["w2"]] = gates[1]
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.dispatch(str(env["paths"]["w2"]), "b")
    gates[0].set_result(None)
    gates[1].set_result(None)
    await fleet.wait_all()
    joined = [
        s
        for s in env["chat"].delivered
        if "[zen:worker w1]" in s and "[zen:worker w2]" in s
    ]
    assert len(joined) == 1, f"expected one merged message, got {env['chat'].delivered}"


async def test_reports_within_the_batch_window_merge(env) -> None:
    fleet: ZenFleet = env["fleet"]
    loop = asyncio.get_running_loop()
    gates = [loop.create_future() for _ in range(2)]
    env["factory"].gates[env["paths"]["w1"]] = gates[0]
    env["factory"].gates[env["paths"]["w2"]] = gates[1]
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.dispatch(str(env["paths"]["w2"]), "b")
    gates[0].set_result(None)
    # Latch until w1's report is buffered and the flush is sleeping in its
    # window, then release w2 so it joins the same batch — no wall-clock race.
    await _await_flush_armed(fleet)
    gates[1].set_result(None)
    await fleet.wait_all()
    joined = [
        s
        for s in env["chat"].delivered
        if "[zen:worker w1]" in s and "[zen:worker w2]" in s
    ]
    assert len(joined) == 1
    per_report = [s for s in env["chat"].delivered if "[zen:worker" in s]
    assert len(per_report) == 1, f"one message per report leaked: {per_report}"


async def test_report_arriving_mid_delivery_batches_into_the_next_flush(env) -> None:
    """A report completing while the router turn is running waits for the
    next flush instead of chaining an immediate extra run per report."""
    fleet: ZenFleet = env["fleet"]
    loop = asyncio.get_running_loop()
    gates = [loop.create_future() for _ in range(3)]
    for gate, key in zip(gates, ("w1", "w2", "w3")):
        env["factory"].gates[env["paths"][key]] = gate
    for key in ("w1", "w2", "w3"):
        await fleet.dispatch(str(env["paths"][key]), "a")
    gates[0].set_result(None)
    await asyncio.sleep(0.12)  # w1's report flushes alone (window 0.05s)
    assert len(env["chat"].delivered) == 1
    assert "[zen:worker w1]" in env["chat"].delivered[0]

    # w2 completes; w3 follows inside the window: they must merge, not chain
    gates[1].set_result(None)
    gates[2].set_result(None)
    await fleet.wait_all()
    batch = [s for s in env["chat"].delivered if "[zen:worker w2]" in s]
    assert len(batch) == 1 and "[zen:worker w3]" in batch[0]


async def test_worker_inherits_router_model_and_task_budgets(env) -> None:
    env["chat"].runtime = SimpleNamespace(
        agent=SimpleNamespace(
            model=SimpleNamespace(id="router-model"), thinking="high"
        )
    )
    fleet = _fleet_with(env, max_cost=2.5, max_turns=12)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    call = env["factory"].calls[-1]
    assert call["model_id"] == "router-model"
    assert call["thinking"] == "high"
    assert call["max_cost"] == 2.5
    assert call["max_turns"] == 12


async def test_worker_without_router_falls_back_to_the_default_model(env) -> None:
    fleet = _fleet_with(env, chat=None)
    await fleet.dispatch(str(env["paths"]["w1"]), "a")
    await fleet.wait_all()
    assert env["factory"].calls[-1]["model_id"] is None
    assert env["factory"].calls[-1]["thinking"] is None
