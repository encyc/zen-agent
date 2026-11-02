#!/usr/bin/env python
"""Minimal LSP server for the ``lsp`` tool tests (stdio, Content-Length framing).

Implements just enough for the five actions: initialize, documentSymbol,
references, definition, rename, and a publishDiagnostics notification after
didOpen.
"""

from __future__ import annotations

import json
import sys
import threading


def send(message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def read() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", "replace").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if not length:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def main() -> None:
    published: set[str] = set()
    while True:
        message = read()
        if message is None:
            return
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")

        if method == "textDocument/didOpen":
            uri = str((params.get("textDocument") or {}).get("uri", ""))
            # Simulate a real server: analysis finishes a moment after didOpen;
            # publishDiagnostics is the client-visible completion signal.
            timer = threading.Timer(
                0.3,
                lambda uri=uri: (
                    published.add(uri),
                    send(
                        {
                            "jsonrpc": "2.0",
                            "method": "textDocument/publishDiagnostics",
                            "params": {
                                "uri": uri,
                                "diagnostics": [
                                    {
                                        "range": {"start": {"line": 0, "character": 2}},
                                        "severity": 1,
                                        "message": "fake error",
                                        "source": "fake",
                                    }
                                ],
                            },
                        }
                    ),
                ),
            )
            timer.start()
            continue
        if request_id is None:
            continue

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"capabilities": {}}})
        elif method == "textDocument/documentSymbol":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "name": "foo",
                            "kind": 12,
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 1, "character": 0},
                            },
                        }
                    ],
                }
            )
        elif method == "textDocument/references":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "uri": params["textDocument"]["uri"],
                            "range": {
                                "start": {"line": 2, "character": 1},
                                "end": {"line": 2, "character": 4},
                            },
                        }
                    ],
                }
            )
        elif method == "textDocument/definition":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "uri": params["textDocument"]["uri"],
                        "range": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 3},
                        },
                    },
                }
            )
        elif method == "textDocument/rename":
            # Mirror pyright's cold-start race: before analysis completes
            # (no publishDiagnostics yet) only the open file's edits exist;
            # afterwards the full cross-file workspace edit is returned.
            uri = params["textDocument"]["uri"]
            single = {
                uri: [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": params.get("newName", "renamed"),
                    }
                ]
            }
            if uri in published:
                other = "file:///other/mod.py"
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            # pyright's real shape: TextDocumentEdit entries
                            # (no "kind") inside documentChanges
                            "documentChanges": [
                                {
                                    "textDocument": {"uri": uri, "version": None},
                                    "edits": single[uri],
                                },
                                {
                                    "textDocument": {"uri": other, "version": None},
                                    "edits": [
                                        {
                                            "range": {
                                                "start": {"line": 3, "character": 0},
                                                "end": {"line": 3, "character": 3},
                                            },
                                            "newText": params.get("newName", "renamed"),
                                        }
                                    ],
                                },
                            ]
                        },
                    }
                )
            else:
                send({"jsonrpc": "2.0", "id": request_id, "result": {"changes": single}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": None})


if __name__ == "__main__":
    main()
