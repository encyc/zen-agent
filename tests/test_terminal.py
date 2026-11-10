"""Host-level terminal wire ops: a real pty, its tagged output events, and the
refusals a stale or unknown session gets."""

import json
import time

from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost


def _term_events(writes: list[dict], kind: str) -> list[dict]:
    return [
        frame["event"]
        for frame in writes
        if frame.get("type") == "event"
        and frame["event"].get("type") == "terminal"
        and frame["event"].get("kind") == kind
    ]


def _wait_for_output(writes: list[dict], needle: str, timeout: float = 10.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = [e for e in _term_events(writes, "output") if needle in e.get("data", "")]
        if events:
            return events
        time.sleep(0.05)
    raise AssertionError(f"terminal never printed {needle!r}: {writes[-5:]}")


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


async def _request(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


async def test_terminal_start_write_resize_close(tmp_path):
    writes: list[dict] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=None,
        write_line=_collect(writes),
    )
    try:
        started = await _request(
            host,
            writes,
            {
                "type": "terminal",
                "id": "t1",
                "op": "start",
                "session": "s1",
                "cwd": str(tmp_path),
                "cols": 90,
                "rows": 30,
            },
        )
        assert started["type"] == "result" and started["ok"] is True
        assert started["cwd"] == str(tmp_path)
        assert started["pty"] > 0

        typed = await _request(
            host,
            writes,
            {
                "type": "terminal",
                "id": "t2",
                "op": "write",
                "session": "s1",
                "data": "printf 'wire-ok\\n'\n",
            },
        )
        assert typed["ok"] is True
        output = _wait_for_output(writes, "wire-ok")
        assert all(event["session"] == "s1" for event in output)

        resized = await _request(
            host,
            writes,
            {
                "type": "terminal",
                "id": "t3",
                "op": "resize",
                "session": "s1",
                "cols": 120,
                "rows": 40,
            },
        )
        assert resized["ok"] is True

        closed = await _request(
            host, writes, {"type": "terminal", "id": "t4", "op": "close", "session": "s1"}
        )
        assert closed["ok"] is True
    finally:
        await host.close()


async def test_terminal_refuses_a_stale_session(tmp_path):
    writes: list[dict] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=None,
        write_line=_collect(writes),
    )
    try:
        await _request(
            host,
            writes,
            {"type": "terminal", "id": "t1", "op": "start", "session": "s1", "cwd": str(tmp_path)},
        )
        stale = await _request(
            host,
            writes,
            {"type": "terminal", "id": "t2", "op": "write", "session": "s0", "data": "ls\n"},
        )
        assert stale["type"] == "error"
        assert stale["code"] == "terminal_failed"

        unknown = await _request(
            host,
            writes,
            {"type": "terminal", "id": "t3", "op": "explode", "session": "s1"},
        )
        assert unknown["type"] == "error"
        assert unknown["code"] == "terminal_invalid"

        missing = await _request(
            host, writes, {"type": "terminal", "id": "t4", "op": "write", "data": "ls\n"}
        )
        assert missing["type"] == "error"
        assert missing["code"] == "terminal_invalid"
    finally:
        await host.close()


def test_terminal_close_ends_the_shell(tmp_path):
    """The close op is synchronous enough to assert from a plain test."""
    from zenagent.cli.rpc.terminal import TerminalError, TerminalSession

    events: list[dict] = []
    term = TerminalSession(events.append)
    term.start("s1", str(tmp_path), 80, 24)
    term.close()
    try:
        term.write("s1", "ls\n")
    except TerminalError:
        pass
    else:  # pragma: no cover - the assertion below is the point
        raise AssertionError("a closed terminal still accepted input")
    # The pump reports the exit for the token it was serving.
    assert any(
        event["event"].get("kind") == "exit" and event["event"].get("session") == "s1"
        for event in events
    )

