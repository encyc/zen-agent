"""One bootstrap sequence for every frontend (repl/tui/rpc/headless).

The 2026-11-07 review found the broker → RuntimeOptions → Runtime.bootstrap
ritual copy-pasted four times, and it had already drifted once (only RPC
forwarded ``read_only``). These tests pin the contract: whatever the caller
puts in ``RuntimeOptions`` reaches BOTH the permission broker and the runtime,
in every mode.
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest

import zenagent.app.bootstrap as bootstrap_mod
from zenagent.app.runtime import RuntimeOptions

MODES = ["repl", "tui", "rpc", "headless"]


@pytest.fixture()
def fake_stack(monkeypatch):
    seen = {}

    class FakeRuntime:
        warnings: ClassVar[list] = []

        async def close(self) -> None: ...

    async def fake_bootstrap(options, **kwargs):
        seen["options"] = options
        seen["bootstrap_kwargs"] = kwargs
        return FakeRuntime()

    monkeypatch.setattr(
        bootstrap_mod.Runtime, "bootstrap", staticmethod(fake_bootstrap)
    )
    monkeypatch.setattr(
        bootstrap_mod,
        "build_broker_for",
        lambda **kw: seen.update(broker_kwargs=kw) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        bootstrap_mod, "ChatController", lambda runtime: SimpleNamespace(runtime=runtime)
    )
    monkeypatch.setattr(bootstrap_mod, "ModelManager", lambda runtime: SimpleNamespace())
    monkeypatch.setattr(
        bootstrap_mod, "SessionManager", lambda runtime: SimpleNamespace()
    )
    monkeypatch.setattr(
        bootstrap_mod, "AppServices", lambda **kw: SimpleNamespace(**kw)
    )
    monkeypatch.setattr(
        bootstrap_mod,
        "build_default_commands",
        lambda services: SimpleNamespace(services=services),
    )
    monkeypatch.setattr(
        bootstrap_mod, "load_user_config", lambda *args, **kwargs: SimpleNamespace()
    )
    return seen


@pytest.mark.parametrize("mode", MODES)
async def test_read_only_reaches_broker_and_runtime_in_every_mode(
    mode, fake_stack, monkeypatch
):
    options = RuntimeOptions(mode=mode, read_only=True)
    prepared = await bootstrap_mod.prepare(
        options,
        allow_missing_key=(mode == "tui"),
        assume_yes=(mode == "rpc"),
        views=(mode in ("repl", "rpc")),
    )
    assert fake_stack["options"].read_only is True
    assert fake_stack["broker_kwargs"]["read_only"] is True
    assert fake_stack["bootstrap_kwargs"]["gate"] is prepared.broker
    assert fake_stack["broker_kwargs"]["mode"] == mode


async def test_views_false_builds_no_view_objects(fake_stack):
    options = RuntimeOptions(mode="headless")
    prepared = await bootstrap_mod.prepare(options, views=False)
    assert prepared.runtime is not None
    assert prepared.chat is None
    assert prepared.commands is None
    assert prepared.services is None
    assert "AppServices" not in str(fake_stack["bootstrap_kwargs"])


async def test_prepared_wiring_shares_one_chat(fake_stack):
    options = RuntimeOptions(mode="repl")
    prepared = await bootstrap_mod.prepare(options)
    assert prepared.services.chat is prepared.chat
    assert prepared.commands.services is prepared.services


async def test_bootstrap_failure_releases_lease_and_refs(monkeypatch, tmp_path):
    """P1-3: a crash after the session is bound must not pin lease or refcounts.

    bootstrap binds the session lease, then builds the plugin/task surface;
    anything raising there used to leave the lease held forever, so the same
    worker session could never be rebuilt in-process (audit P1-3).
    """
    import zenagent.tools.jobs as jobs_mod
    from zenagent.app import session_lease as leases
    from zenagent.app.agent_profile import AgentProfile
    from zenagent.app.runtime import Runtime
    from zenagent.config import UserConfig

    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
    )
    refs_before = jobs_mod._refs

    seen: dict = {}
    orig_open = Runtime._open_session

    def spy(self, resume):
        session = orig_open(self, resume)
        seen["path"] = session.path
        return session

    monkeypatch.setattr(Runtime, "_open_session", spy)

    def boom(*args, **kwargs):
        raise RuntimeError("plugin wiring exploded")

    # build_task_tool is the first unconditional call after the session
    # (and therefore the lease) is bound; extension loading is skipped when
    # no extension files exist, so it is not a reliable injection point.
    monkeypatch.setattr("zenagent.app.runtime.build_task_tool", boom)

    with pytest.raises(RuntimeError, match="plugin wiring exploded"):
        await Runtime.bootstrap(
            RuntimeOptions(
                root=root,
                profile=AgentProfile(mcp=False, skills=False, templates=False, context_files=False),
            )
        )

    # The lease must be back: claiming the same session path succeeds.
    leases.acquire_session(seen["path"], owner=object())
    assert jobs_mod._refs == refs_before
