"""Headless CLI: one prompt in, streamed plain text out, exit code out.

``pyagent -p "..."`` assembles an :class:`~pyagent.agent.agent.Agent`, prints the
assistant text as it streams (no ANSI, so it can be piped), summarises tool
results on one line each, and exits 0 on a clean run or 1 when the provider
failed. The interactive REPL lives in ``repl.py`` (Task 17).

Model resolution order: ``-m/--model``, then ``~/.pyagent/config.toml``'s
default (Task 21), then the catalog entry marked ``default: true``. The API key
comes from the environment variable named by the model's provider.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pyagent.agent import Agent
from pyagent.cli.permissions import PermissionGate
from pyagent.events import AgentEvent
from pyagent.providers.catalog import Model, ProviderConfig, find_model, load_catalog
from pyagent.providers.registry import StreamFn
from pyagent.tools import ALL_TOOLS
from pyagent.types import AssistantMessage, Message

__all__ = ["build_parser", "main", "resolve_model", "run_headless"]

THINKING_CHOICES = ("off", "low", "medium", "high")


def _system_prompt(tool_names: list[str]) -> str:
    tools = ", ".join(tool_names) if tool_names else "(none available yet)"
    return (
        "You are pyagent, a coding agent working in the user's repository.\n"
        f"Working directory: {Path.cwd()}\n"
        f"Available tools: {tools}\n"
        "Answer concisely. Prefer using tools to inspect the code instead of "
        "guessing. Explain what you did when you finish."
    )


def resolve_model(catalog: dict[str, ProviderConfig], model_id: str | None) -> Model:
    """Pick the model to run: explicit id, else the catalog's ``default: true``."""
    if model_id:
        return find_model(catalog, model_id)
    for provider in catalog.values():
        for model in provider.models:
            if model.default:
                return model
    for provider in catalog.values():
        if provider.models:
            return provider.models[0]
    raise LookupError("the model catalog is empty")


def _api_key_for(catalog: dict[str, ProviderConfig], model: Model) -> str | None:
    provider = catalog.get(model.provider)
    if provider is None:
        return None
    return os.environ.get(provider.api_key_env) or None


def _build_agent(
    model: Model,
    api_key: str,
    thinking: str | None,
    gate: PermissionGate,
    stream_fn: StreamFn | None = None,
    tools: list | None = None,
) -> Agent:
    """Construct the run's Agent (a seam for tests to inject a fake stream)."""
    tool_list = list(ALL_TOOLS if tools is None else tools)
    return Agent(
        model=model,
        system_prompt=_system_prompt([tool.name for tool in tool_list]),
        tools=tool_list,
        api_key=api_key,
        thinking=thinking,
        stream_fn=stream_fn,
        before_tool_call=gate.before_tool_call,
    )


@dataclass
class _PlainOutput:
    """Streams assistant text to stdout and prints one line per tool result."""

    out: object = None  # type: ignore[assignment]
    collected: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.out is None:  # resolved at construction, not import time
            self.out = sys.stdout

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "message_update" and event.assistant_event is not None:
            assistant_event = event.assistant_event
            if assistant_event.type == "text_delta" and assistant_event.delta:
                self.collected.append(assistant_event.delta)
                self.out.write(assistant_event.delta)
                self.out.flush()
            return
        if event.type == "tool_execution_end":
            status = "error" if event.is_error else "ok"
            self.out.write(f"\n[{event.tool_name}: {status}]\n")
            self.out.flush()
            return
        if event.type == "message_end" and isinstance(event.message, AssistantMessage):
            self.out.write("\n")
            self.out.flush()

    @property
    def text(self) -> str:
        return "".join(self.collected).strip()


def _usage_and_counts(messages: list[Message]) -> tuple[dict, int]:
    usage: dict = {"input": 0, "output": 0, "cost": 0.0}
    for message in messages:
        if isinstance(message, AssistantMessage):
            usage["input"] += message.usage.input
            usage["output"] += message.usage.output
            usage["cost"] += message.usage.cost.total
    return usage, len(messages)


async def run_headless(
    prompt: str,
    model_id: str | None = None,
    assume_yes: bool = False,
    thinking: str | None = None,
    *,
    json_output: bool = False,
    stream_fn: StreamFn | None = None,
    tools: list | None = None,
) -> int:
    """Run one prompt to completion; returns the process exit code."""
    catalog = load_catalog()
    try:
        model = resolve_model(catalog, model_id)
    except KeyError as exc:
        print(f"pyagent: unknown model: {exc}", file=sys.stderr)
        return 1
    api_key = _api_key_for(catalog, model)
    if not api_key:
        provider = catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        print(f"pyagent: missing API key for {model.id} (set {env_name})", file=sys.stderr)
        return 1

    if thinking is None:
        thinking = "high" if model.reasoning else "off"
    gate = PermissionGate(assume_yes=assume_yes, interactive=False)
    agent = _build_agent(model, api_key, thinking, gate, stream_fn=stream_fn, tools=tools)

    # --json must not stream plain text; collect it into a buffer instead.
    printer = _PlainOutput(out=io.StringIO() if json_output else None)
    agent.subscribe(printer)
    await agent.prompt(prompt)

    if json_output:
        usage, count = _usage_and_counts(agent.messages)
        print(
            json.dumps(
                {
                    "ok": agent.error_message is None,
                    "text": printer.text,
                    "usage": usage,
                    "messages": count,
                    "error": agent.error_message,
                },
                ensure_ascii=False,
            )
        )
    elif agent.error_message is not None:
        print(f"pyagent: {agent.error_message}", file=sys.stderr)
    return 0 if agent.error_message is None else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyagent", description="A minimal coding agent (streaming, tool-using)."
    )
    parser.add_argument("-p", "--prompt", help="run one prompt headlessly and exit")
    parser.add_argument("-m", "--model", help="model id from the catalog")
    parser.add_argument(
        "--thinking",
        choices=THINKING_CHOICES,
        help="reasoning level (default: high for reasoning models, off otherwise)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="allow write/execute tools without asking"
    )
    parser.add_argument(
        "--resume", nargs="?", const="latest", help="resume a session (Task 15)"
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.prompt:
        print(
            "pyagent: no prompt given; use -p/--prompt (the interactive REPL "
            "arrives in a later milestone)",
            file=sys.stderr,
        )
        return 1
    if args.resume:
        print("pyagent: --resume is not available yet (Task 15)", file=sys.stderr)
    try:
        return asyncio.run(
            run_headless(
                args.prompt,
                model_id=args.model,
                assume_yes=args.yes,
                thinking=args.thinking,
                json_output=args.json,
            )
        )
    except KeyboardInterrupt:
        print("\npyagent: interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
