"""Talk to ``zenagent rpc`` over stdin/stdout, the way an editor would.

The wire is JSON lines: one request per stdin line, and on stdout zero or more
``{"type": "event"}`` frames followed by exactly one ``{"type": "result"}`` or
``{"type": "error"}`` carrying the request ``id``. Logs stay on stderr, so
stdout is safe to parse. This file is the client half; the server is
``zenagent/cli/rpc.py``.

    DEEPSEEK_API_KEY=... uv run python examples/rpc_client.py "say hi"
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable

PROMPT_ID = "1"
SHUTDOWN_ID = "2"


def collect(
    lines: Iterable[str],
    request_id: str,
    on_text: Callable[[str], None] | None = None,
) -> tuple[dict | None, dict | None]:
    """Read frames until this request settles.

    Returns ``(result, error)``; both are ``None`` when the server went away
    without answering (crash, EOF) -- a failure, not an empty answer.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            continue  # never crash on a stray line: stdout is a wire, not a log
        if not isinstance(frame, dict):
            continue
        kind = frame.get("type")
        if kind == "event":
            assistant = (frame.get("event") or {}).get("assistant_event") or {}
            delta = assistant.get("delta")
            if assistant.get("type") == "text_delta" and delta and on_text is not None:
                on_text(delta)
        elif kind == "result" and frame.get("id") == request_id:
            return frame, None
        elif kind == "error" and frame.get("id") == request_id:
            return None, frame
    return None, None


def main(prompt: str = "say hi", *, timeout_s: float = 120.0) -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "zenagent.cli.main", "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None and process.stdout is not None

    def send(payload: dict) -> None:
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    streamed = False

    def on_text(delta: str) -> None:
        nonlocal streamed
        streamed = True
        print(delta, end="", flush=True)

    # A provider that never answers must not hang the client forever: kill the
    # child, which closes stdout and settles the read loop.
    watchdog = threading.Timer(timeout_s, process.kill)
    watchdog.start()
    try:
        send({"type": "prompt", "id": PROMPT_ID, "text": prompt})
        result, error = collect(process.stdout, PROMPT_ID, on_text=on_text)
        if streamed:
            print()
        if error is not None:
            print(f"rpc error: {error.get('message')}", file=sys.stderr)
            return 1
        if result is None:
            print(f"rpc: no answer (exit code {process.poll()})", file=sys.stderr)
            return 1
        send({"type": "shutdown", "id": SHUTDOWN_ID})
        return 0 if result.get("ok") else 1
    finally:
        watchdog.cancel()
        process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main(" ".join(sys.argv[1:]) or "say hi"))
