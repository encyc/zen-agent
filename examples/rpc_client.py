"""Talk to ``pyagent rpc`` over stdin/stdout.

    uv run python examples/rpc_client.py "say hi"
"""

from __future__ import annotations

import json
import subprocess
import sys


def main(prompt: str) -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "pyagent.cli.main", "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps({"type": "prompt", "id": "1", "text": prompt}) + "\n")
    process.stdin.flush()
    result = None
    for line in process.stdout:
        message = json.loads(line)
        if message.get("type") == "result" and message.get("id") == "1":
            result = message
            break
    process.stdin.write(json.dumps({"type": "shutdown", "id": "2"}) + "\n")
    process.stdin.flush()
    process.wait(timeout=30)
    print(result["text"] if result else "(no result)")
    return 0 if result and result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(" ".join(sys.argv[1:]) or "say hi"))
