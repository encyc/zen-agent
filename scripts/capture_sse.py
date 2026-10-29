#!/usr/bin/env python
"""Capture raw SSE from a real provider into a fixture file.

The adapter tests replay hand-written SSE fixtures; this script captures the
real thing so the parser can be checked against actual wire behaviour (the spec
is explicit that captured traffic beats source-code inference).

It deliberately does **not** parse: the only job is to build the same request
the adapter would send, POST it, and write the response lines verbatim into
``zenagent/providers/fixtures/<model>-live-<scenario>.txt`` (same shape as the
hand-written fixtures: ``data: {...}`` lines separated by blank lines).

Usage::

    uv run python scripts/capture_sse.py \
        --model deepseek-flash --scenario text --thinking high --env-file .env

The API key is read from the environment variable named by the model's provider
(``DEEPSEEK_API_KEY``, ``ZAI_CODING_CN_API_KEY``, ``OPENCODE_API_KEY``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

from zenagent.providers.catalog import Model, find_model, load_catalog
from zenagent.providers.openai_completions import (
    StreamOptions,
    build_params,
    to_request_messages,
)
from zenagent.types import ToolDef, UserMessage

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "zenagent" / "providers" / "fixtures"

WEATHER_TOOL = ToolDef(
    name="get_weather",
    description="Get the current weather for a city",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)

SCENARIOS: dict[str, dict] = {
    "text": {
        "prompt": "用一句话介绍你自己。",
        "tools": [],
    },
    "thinking": {
        "prompt": "9.11 和 9.9 哪个大？简要说明推理过程，然后给出结论。",
        "tools": [],
    },
    "toolcall": {
        "prompt": "北京今天天气怎么样？必须调用 get_weather 工具。",
        "tools": [WEATHER_TOOL],
    },
    "parallel": {
        "prompt": "同时查询北京和上海的天气，必须调用两次 get_weather 工具。",
        "tools": [WEATHER_TOOL],
    },
}


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines without overwriting already-set variables."""
    if not path.is_file():
        raise SystemExit(f"env file not found: {path}")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _display(path: Path) -> str:
    """Repo-relative path when possible, absolute otherwise."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def api_key_for(model: Model) -> str:
    provider = load_catalog().get(model.provider)
    env_name = provider.api_key_env if provider else ""
    key = os.environ.get(env_name) if env_name else None
    if not key:
        raise SystemExit(f"missing API key: set {env_name or 'the provider env var'}")
    return key


def build_request(model: Model, scenario: str, thinking: str | None) -> tuple[dict, dict]:
    spec = SCENARIOS[scenario]
    messages = [UserMessage(content=spec["prompt"], timestamp=int(time.time()))]
    options = StreamOptions(api_key=api_key_for(model), thinking=thinking)
    params = build_params(model, spec["tools"], options, model.compat)
    params["messages"] = to_request_messages(messages, model.compat)
    headers = {
        "Authorization": f"Bearer {options.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    return params, headers


async def capture(model: Model, scenario: str, thinking: str | None, out: Path) -> int:
    params, headers = build_request(model, scenario, thinking)
    url = f"{model.base_url.rstrip('/')}/chat/completions"
    out.parent.mkdir(parents=True, exist_ok=True)
    request_dump = out.with_suffix(".request.json")
    request_dump.write_text(
        json.dumps(
            {
                "url": url,
                "scenario": scenario,
                "thinking": thinking,
                "params": {**params, "messages": params["messages"]},
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    lines = 0
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream("POST", url, headers=headers, json=params) as response,
    ):
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", "replace")[:500]
            print(f"HTTP {response.status_code}: {body}", file=sys.stderr)
            return 1
        with out.open("w", encoding="utf-8") as handle:
            async for line in response.aiter_lines():
                handle.write(line + "\n")
                lines += 1
    print(f"captured {lines} lines -> {_display(out)}")
    print(f"request  -> {_display(request_dump)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="catalog model id, e.g. deepseek-reasoner")
    parser.add_argument(
        "--scenario", choices=sorted(SCENARIOS), default="text", help="what to ask for"
    )
    parser.add_argument("--thinking", choices=("off", "low", "medium", "high"), default=None)
    parser.add_argument("--env-file", type=Path, help="load KEY=VALUE pairs from this file")
    parser.add_argument("--out", type=Path, help="output fixture path (default: auto)")
    args = parser.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)
    try:
        model = find_model(load_catalog(), args.model)
    except KeyError as exc:
        print(f"unknown model: {exc}", file=sys.stderr)
        return 2

    out = args.out or FIXTURE_DIR / f"{model.id}-live-{args.scenario}.txt"
    try:
        return asyncio.run(capture(model, args.scenario, args.thinking, out))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
