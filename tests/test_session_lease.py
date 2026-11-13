"""One writer per session: process registry, file lock and Runtime binding."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.session_lease import (
    SessionLeaseBusy,
    acquire_session,
    release_session,
)


def _lease_path(tmp_path: Path) -> Path:
    return tmp_path / "session.jsonl"


def test_second_owner_is_refused(tmp_path):
    path = _lease_path(tmp_path)
    first = acquire_session(path, object())
    try:
        with pytest.raises(SessionLeaseBusy):
            acquire_session(path, object())
    finally:
        release_session(first)


def test_same_owner_is_reentrant(tmp_path):
    path = _lease_path(tmp_path)
    owner = object()
    first = acquire_session(path, owner)
    second = acquire_session(path, owner)
    assert second is first
    release_session(first)
    # The reentrant call did not create a second lock; one release frees it.
    acquire_session(path, object())


def test_release_allows_a_new_owner(tmp_path):
    path = _lease_path(tmp_path)
    first = acquire_session(path, object())
    release_session(first)
    second = acquire_session(path, object())
    release_session(second)


def test_busy_message_never_contains_the_path(tmp_path):
    path = tmp_path / "private-project" / "session.jsonl"
    first = acquire_session(path, object())
    try:
        with pytest.raises(SessionLeaseBusy) as caught:
            acquire_session(path, object())
        assert "private-project" not in str(caught.value)
        assert "session.jsonl" not in str(caught.value)
    finally:
        release_session(first)


async def test_runtime_refuses_a_second_writer(runtime_factory):
    from zenagent.types import UserMessage

    first = await runtime_factory()
    await first.session.append(UserMessage(content="hi", timestamp=1))
    path = first.session.path
    with pytest.raises(SessionLeaseBusy):
        await runtime_factory(resume=path)
    await first.close()
    second = await runtime_factory(resume=path)
    await second.close()


async def test_switch_to_a_busy_session_keeps_the_current_one(runtime_factory):
    first = await runtime_factory()
    second = await runtime_factory()
    current = second.session.path
    with pytest.raises(SessionLeaseBusy):
        await second.switch_session(first.session.path)
    assert second.session.path == current
    assert first.session.path != current
    await first.close()
    await second.close()


async def test_same_runtime_may_rebind_its_own_session(runtime_factory):
    runtime = await runtime_factory()
    path = runtime.session.path
    await runtime.switch_session(path)
    assert runtime.session.path == path
    await runtime.close()


@pytest.mark.skipif(os.name == "nt", reason="HOME-based lock path is POSIX-shaped")
async def test_another_process_cannot_take_the_lock_async(
    runtime_factory, tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(project, "zenagent_home", lambda: home / ".zenagent")
    monkeypatch.setenv("HOME", str(home))

    holder = await runtime_factory()
    path = str(holder.session.path)
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from zenagent.app.session_lease import SessionLeaseBusy, acquire_session\n"
        "try:\n"
        "    acquire_session(Path(sys.argv[1]), object())\n"
        "except SessionLeaseBusy:\n"
        "    raise SystemExit(17)\n"
        "raise SystemExit(0)\n"
    )
    env = {**os.environ, "HOME": str(home)}
    busy = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, path],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert busy.returncode == 17, busy.stderr

    await holder.close()
    free = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, path],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert free.returncode == 0, free.stderr


async def test_host_translates_a_busy_session_to_a_refusal(runtime_factory):
    from zenagent.app.runtime import RuntimeOptions
    from zenagent.cli.rpc.errors import Refusal
    from zenagent.cli.rpc.host import RpcHost
    from zenagent.types import UserMessage

    holder = await runtime_factory()
    await holder.session.append(UserMessage(content="hi", timestamp=1))
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=holder.root),
        read_line=lambda: None,
        write_line=writes.append,
    )
    try:
        with pytest.raises(Refusal) as caught:
            await host._open_project_slot(str(holder.root), resume=str(holder.session.path))
        assert caught.value.code == "session_lease_busy"
    finally:
        await holder.close()
