"""Run one prompt headlessly from Python (no CLI process).

    DEEPSEEK_API_KEY=... uv run python examples/headless.py "say hi"
"""

from __future__ import annotations

import asyncio
import sys

from zenagent.agent import Agent
from zenagent.cli.main import _api_key_for, resolve_model, session_id_for
from zenagent.config import apply_user_config, load_user_config
from zenagent.providers.catalog import load_catalog
from zenagent.tools import ALL_TOOLS


async def main(prompt: str) -> int:
    catalog = apply_user_config(load_catalog(), load_user_config())
    model = resolve_model(catalog, None)
    agent = Agent(
        model=model,
        system_prompt="You are a concise coding agent.",
        tools=list(ALL_TOOLS),
        api_key=_api_key_for(catalog, model),
        session_id=session_id_for(model),
    )
    await agent.prompt(prompt)
    print(agent.messages[-1].content if isinstance(agent.messages[-1].content, str) else "")
    return 0 if agent.termination in (None, "stop") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or "say hi")))
