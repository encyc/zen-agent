#!/usr/bin/env python
"""An MCP server that dies on its first ``tools/call`` and works after restart.

Used to test the call-level reconnect: the first call kills the process (no
response, transport error), the manager reconnects, and the retried call runs
against the fresh process (which sees the marker file and behaves normally).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_echo_server import TOOLS, result, send  # noqa: E402

MARKER_ENV = "MCP_CRASH_MARKER"


def main() -> None:
    marker = os.environ.get(MARKER_ENV)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:  # notification
            continue
        params = message.get("params") or {}

        if method == "initialize":
            result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "crash-once", "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            result(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            if marker and not Path(marker).exists():
                Path(marker).write_text("crashed")
                # Die without answering: the client sees EOF, not a JSON-RPC error.
                sys.exit(3)
            arguments = params.get("arguments") or {}
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"echo:{arguments.get('text', '')}",
                            }
                        ],
                        "isError": False,
                    },
                }
            )
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
