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
    monkeypatch.setattr(bootstrap_mod, "load_user_config", lambda: SimpleNamespace())
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
