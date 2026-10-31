"""Embed ZenAgent in a Python program with the batteries-included app layer.

``Runtime`` owns the agent, session, MCP servers, skills and the permission
gate; ``ChatController`` is the frontend-agnostic view model the TUI and REPL
both drive. Together they are what you want inside a larger app -- for a
one-shot run without the app layer, use ``run_headless`` (``examples/headless.py``).

    DEEPSEEK_API_KEY=... uv run python examples/embed.py "explain this repo"
"""

from __future__ import annotations

import asyncio
import sys

from zenagent.app.chat import ChatController
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.views import MessageView, ToolCallView


def _print_transcript(chat: ChatController) -> None:
    """Render the finished transcript the way a frontend would."""
    for view in chat.transcript:
        if isinstance(view, MessageView):
            if view.kind == "assistant" and view.text:
                print(view.text)
                print()
            continue
        if isinstance(view, ToolCallView) and view.status == "error":
            print(f"[{view.name}: {view.result_text}]", file=sys.stderr)


async def main(prompt: str = "say hi") -> int:
    try:
        runtime = await Runtime.bootstrap(RuntimeOptions())
    # RuntimeError: missing API key. LookupError/KeyError: empty or unknown model.
    except (RuntimeError, LookupError, KeyError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 1
    for warning in runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)
    try:
        chat = ChatController(runtime)
        await chat.submit(prompt)
        await chat.wait_for_idle()
        _print_transcript(chat)
        status = chat.status
        print(
            f"[{status.model_id} · {status.input_tokens} in / "
            f"{status.output_tokens} out · ${status.cost:.4f}]",
            file=sys.stderr,
        )
        if status.last_error:
            print(f"zenagent: {status.last_error}", file=sys.stderr)
        return 0 if runtime.agent.termination in (None, "stop") else 1
    finally:
        await runtime.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or "say hi")))
