"""Embed ZenAgent in a Python program with the batteries-included app layer.

    DEEPSEEK_API_KEY=... uv run python examples/embed.py "explain this repo"
"""

from __future__ import annotations

import asyncio
import sys

from zenagent.app.chat import ChatController
from zenagent.app.runtime import Runtime, RuntimeOptions


async def main(prompt: str) -> int:
    try:
        runtime = await Runtime.bootstrap(RuntimeOptions())
    except RuntimeError as exc:  # missing API key, unknown model, ...
        print(f"zenagent: {exc}", file=sys.stderr)
        return 1
    try:
        chat = ChatController(runtime)
        await chat.submit(prompt)
        await chat.wait_for_idle()
        for view in chat.transcript:
            text = getattr(view, "text", "") or getattr(view, "result_text", "")
            if text:
                print(text)
        status = chat.status
        print(f"\n[{status.model_id} · {status.input_tokens} in / "
              f"{status.output_tokens} out · ${status.cost:.4f}]", file=sys.stderr)
        return 0 if runtime.agent.termination in (None, "stop") else 1
    finally:
        await runtime.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or "say hi")))
