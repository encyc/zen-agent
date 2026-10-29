#!/usr/bin/env python
"""Minimal stdio MCP server used by the client tests.

Speaks newline-delimited JSON-RPC 2.0 and implements just enough:
initialize, tools/list, tools/call (echo / fail / sleep / crash).
"""

from __future__ import annotations

import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {"name": "fail", "description": "Always fails", "inputSchema": {"type": "object"}},
    {"name": "sleep", "description": "Sleep, then answer", "inputSchema": {"type": "object"}},
    {"name": "crash", "description": "Exit the server", "inputSchema": {"type": "object"}},
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(request_id, payload) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": payload})


def error(request_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def main() -> None:
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
        if request_id is None:  # notification (e.g. notifications/initialized)
            continue
        params = message.get("params") or {}

        if method == "initialize":
            result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo", "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            result(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"echo:{arguments.get('text', '')}"}],
                        "isError": False,
                    },
                )
            elif name == "fail":
                result(
                    request_id,
                    {"content": [{"type": "text", "text": "nope"}], "isError": True},
                )
            elif name == "sleep":
                time.sleep(5)
                result(request_id, {"content": [{"type": "text", "text": "awake"}]})
            elif name == "crash":
                sys.exit(3)
            else:
                error(request_id, -32602, f"unknown tool: {name}")
        else:
            error(request_id, -32601, f"method not found: {method}")


if __name__ == "__main__":
    main()
