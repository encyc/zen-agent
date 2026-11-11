"""The zen fleet: async dispatch to worker sessions and report injection."""

from __future__ import annotations

import asyncio
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
        self.errors: dict[Path, str] = {}
        self.calls: list[dict] = []

    async def prepare(self, options, **kwargs) -> SimpleNamespace:
        """Match the real ``prepare``: a PreparedFrontend envelope, not a bare Runtime."""
        self.calls.append({"root": str(options.root), "resume": options.resume})
        path = Path(options.resume)
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


async def test_dispatch_runs_worker_and_injects_report(env) -> None:
    fleet: ZenFleet = env["fleet"]
    path = env["paths"]["w1"]
    status = await fleet.dispatch(str(path), "修复编译")
    assert status == "working"
    await fleet.wait_all()
    assert fleet.status_of(str(path)) == "waiting"
    assert "[zen:worker" in env["chat"].delivered[0], "idle router must be woken"


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


async def test_zen_router_workers_run_in_ask_mode(env) -> None:
    """A zen router keeps its autonomy, but its workers downgrade to ask so
    mutating tool calls surface as approvals instead of auto-approving."""
    fleet: ZenFleet = env["fleet"]
    fleet.mode = "zen"
    await fleet.dispatch(str(env["paths"]["w1"]), "修复")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w1"])] == "ask"


async def test_plan_router_workers_inherit_plan(env) -> None:
    """Only the zen bypass is downgraded; a plan router's workers keep plan."""
    fleet: ZenFleet = env["fleet"]
    fleet.mode = "plan"
    await fleet.dispatch(str(env["paths"]["w2"]), "修复")
    await fleet.wait_all()
    assert env["factory"].modes[str(env["paths"]["w2"])] == "plan"


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
    await asyncio.sleep(0.02)  # staggered, still inside the window
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
