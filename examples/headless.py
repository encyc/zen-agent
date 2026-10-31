"""Run one prompt headlessly -- the Python equivalent of ``zenagent -p``.

``run_headless`` is the entry point the CLI itself calls, so the run gets
everything the CLI wires up: the permission gate, the tool-catalogue prompt,
context files (AGENTS.md), MCP servers, skills, and a persisted session.

Drive the bare ``Agent`` yourself only when you really need to;
``examples/custom_tool.py`` shows that shape (fake stream, no API key).

    DEEPSEEK_API_KEY=... uv run python examples/headless.py "say hi"
    DEEPSEEK_API_KEY=... uv run python examples/headless.py --json "say hi"
"""

from __future__ import annotations

import asyncio
import sys

from zenagent.cli.main import run_headless


async def main(prompt: str = "say hi", *, json_output: bool = False) -> int:
    """Stream the answer to stdout; return the process exit code."""
    return await run_headless(prompt, json_output=json_output)


if __name__ == "__main__":
    argv = sys.argv[1:]
    as_json = "--json" in argv
    words = [word for word in argv if word != "--json"]
    raise SystemExit(asyncio.run(main(" ".join(words) or "say hi", json_output=as_json)))
