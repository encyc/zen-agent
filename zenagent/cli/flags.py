"""The shared runtime flag set: declared once here, built into both the CLI
parser (``cli/main.py``) and the ``zen rpc`` parser. A new flag lands in both
frontends in one edit; front-end-specific flags stay in their own builders.
Help text follows the CLI's fuller wording; actions and defaults are part of
the wire contract between the desktop launcher and ``zen rpc``."""

from __future__ import annotations

import argparse

from zenagent.app.modes import MODES

THINKING_CHOICES = ("off", "low", "medium", "high", "max")

#: The flags every frontend shares; ``tests/test_cli_flags.py`` pins this
#: list against both parsers.
SHARED_FLAGS = (
    "--model",
    "--yes",
    "--mode",
    "--read-only",
    "--thinking",
    "--max-turns",
    "--max-cost",
    "--retries",
    "--resume",
    "--no-mcp",
    "--mcp-file",
    "--mcp-server",
    "--no-skills",
    "--no-templates",
    "--no-auto-compact",
    "--extension",
    "--no-context-files",
    "--trace",
    "--metrics",
    "--cwd",
    "--allow-dangerous",
    "--allow-outside",
    "--allow-destructive",
    "--allow-protected",
)


def add_runtime_flags(parser: argparse.ArgumentParser) -> None:
    """Declare every flag both frontends share."""
    parser.add_argument("-m", "--model", help="model id from the catalog")
    parser.add_argument(
        "--thinking",
        choices=THINKING_CHOICES,
        help="reasoning level (default: high for reasoning models, off otherwise; max when supported)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="run everything without asking (zen mode)"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="refuse every tool that can modify the filesystem or run commands",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="runtime permission mode: plan (read-only) | ask (confirm) | "
        "zen (fully automatic, no confirmations)",
    )
    parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="also allow catastrophic shell commands (rm -rf /, mkfs, ...)",
    )
    parser.add_argument(
        "--allow-outside",
        action="append",
        dest="allow_outside",
        metavar="PATH",
        help="allow writes outside the workspace under this path (repeatable)",
    )
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="allow destructive shell commands (git reset --hard, ...)",
    )
    parser.add_argument(
        "--allow-protected",
        action="store_true",
        help="allow writes to protected credential paths",
    )
    parser.add_argument(
        "--resume", nargs="?", const="latest", help="resume a session (Task 15)"
    )
    parser.add_argument(
        "--metrics",
        metavar="PATH",
        help="append one JSON metrics line per run",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help=(
            "extra attempts for transient provider failures; each attempt is "
            "buffered, so enabling retries disables token streaming (default 0)"
        ),
    )
    parser.add_argument(
        "--max-turns", type=int, help="stop after this many provider requests"
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extension_paths",
        metavar="PATH",
        help="load an extension module (repeatable)",
    )
    parser.add_argument(
        "--no-templates",
        action="store_true",
        help="do not load prompt templates from .zenagent/commands",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="do not load skills from .zenagent/skills",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="do not connect any MCP servers",
    )
    parser.add_argument(
        "--mcp-server",
        action="append",
        dest="mcp_servers",
        metavar="NAME",
        help="only connect this MCP server (repeatable)",
    )
    parser.add_argument(
        "--mcp-file",
        metavar="PATH",
        help="MCP config file (default: ~/.zenagent/mcp.json)",
    )
    parser.add_argument(
        "--cwd",
        metavar="PATH",
        help="working directory to run in (default: current directory)",
    )
    parser.add_argument(
        "--trace",
        metavar="PATH",
        help="append a JSONL trace of turns and tool calls",
    )
    parser.add_argument(
        "--no-auto-compact",
        action="store_true",
        help="never compact automatically when the context gets full",
    )
    parser.add_argument(
        "--no-context-files",
        action="store_true",
        help="do not load AGENTS.md / CLAUDE.md into the system prompt",
    )
    parser.add_argument(
        "--max-cost", type=float, help="stop when the run has spent this many USD"
    )


def build_rpc_parser_stub() -> argparse.ArgumentParser:
    """The canonical shared-flags view: a bare parser plus exactly this set.
    Tests compare both real parsers against it."""
    parser = argparse.ArgumentParser()
    add_runtime_flags(parser)
    return parser
