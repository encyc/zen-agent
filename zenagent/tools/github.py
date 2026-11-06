"""GitHub CLI bridge: a small, read-only subset of ``gh``.

Deliberately conditional: the tool is only registered when ``gh`` is on PATH,
so a machine without the CLI never sees a tool it cannot use. Every op is
read-only (``approval="read"``), output goes through the kernel's result budget,
and the child has a wall-clock deadline.

Ops:

* ``pr_view``   — PR metadata as text (number/title/state/branches/review).
* ``pr_diff``   — the PR diff.
* ``pr_checks`` — CI check status for the PR.
* ``run_logs``  — failed-job logs of a workflow run (``--log-failed``).

No network code of our own: ``gh`` owns auth and transport, matching the
"external binary, absent means unregistered" policy in the v0.5 plan.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.tools._result import text_result

__all__ = ["GITHUB", "build_github_tool", "github_available"]

#: Hard cap on text returned to the model (the kernel budget is the safety net).
MAX_OUTPUT_CHARS = 30_000

#: Wall-clock budget for one ``gh`` invocation.
_TIMEOUT_S = 60.0

_DESCRIPTION = (
    "Read GitHub pull requests and CI runs through the `gh` CLI (read-only). "
    "op=pr_view|pr_diff|pr_checks|run_logs; `pr` selects a PR number/URL "
    "(default: the current branch's PR), `run` selects a workflow run id for "
    "run_logs, `repo` selects owner/name. Returns text; large output is "
    "truncated by the tool budget."
)

_OPERATIONS = ("pr_view", "pr_diff", "pr_checks", "run_logs")


def github_available() -> bool:
    """Whether the ``gh`` binary is on PATH (the tool is registered only then)."""
    return shutil.which("gh") is not None


def _command(op: str, args: dict) -> list[str] | str:
    """Build the ``gh`` argv for one op; returns an error string on bad input."""
    repo = str(args.get("repo") or "").strip()
    pr = str(args.get("pr") or "").strip()
    run = str(args.get("run") or "").strip()
    repo_args = ["--repo", repo] if repo else []
    if op == "pr_view":
        command = [
            "pr",
            "view",
            *([pr] if pr else []),
            "--json",
            (
                "number,title,state,url,author,baseRefName,headRefName,"
                "mergeable,reviewDecision,isDraft"
            ),
            *repo_args,
        ]
        return command
    if op == "pr_diff":
        return ["pr", "diff", *([pr] if pr else []), *repo_args]
    if op == "pr_checks":
        return ["pr", "checks", *([pr] if pr else []), *repo_args]
    if op == "run_logs":
        if not run:
            return "run_logs needs a `run` id (from `gh run list`)"
        return ["run", "view", run, "--log-failed", *repo_args]
    return f"unknown op {op!r}; expected one of {', '.join(_OPERATIONS)}"


def _format_pr_view(text: str) -> str:
    """Render ``gh pr view --json`` as a compact key/value block."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    author = data.get("author") or {}
    rows = [
        f"PR #{data.get('number', '?')}: {data.get('title', '')}",
        f"state: {data.get('state', '?')}"
        + (" (draft)" if data.get("isDraft") else ""),
        f"url: {data.get('url', '')}",
        f"author: {author.get('login', '?') if isinstance(author, dict) else author}",
        f"base: {data.get('baseRefName', '?')} ← head: {data.get('headRefName', '?')}",
        f"mergeable: {data.get('mergeable', '?')}",
        f"review: {data.get('reviewDecision') or 'none'}",
    ]
    return "\n".join(row for row in rows if row.strip())


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + "\n… (truncated)", True


async def _github(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    op = str(args.get("op") or "").strip()
    command = _command(op, args)
    if isinstance(command, str):
        return text_result(command, is_error=True)
    gh = shutil.which("gh")
    if gh is None:
        return text_result(
            "the `gh` CLI is not installed; install it from https://cli.github.com",
            is_error=True,
        )
    try:
        process = await asyncio.create_subprocess_exec(
            gh,
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return text_result(f"cannot run gh: {exc}", is_error=True)
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_TIMEOUT_S
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return text_result(f"gh {op} timed out after {_TIMEOUT_S:g}s", is_error=True)
    out = stdout.decode("utf-8", "replace").strip()
    err = stderr.decode("utf-8", "replace").strip()
    if process.returncode != 0:
        detail = err or out or "no output"
        return text_result(
            f"gh {op} failed (exit {process.returncode}): {detail[:2000]}",
            is_error=True,
            op=op,
        )
    body = _format_pr_view(out) if op == "pr_view" else out
    body, truncated = _clip(body or "(no output)")
    return text_result(body, op=op, truncated=truncated)


GITHUB = AgentTool(
    name="github",
    description=_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": list(_OPERATIONS),
                "description": "Which read-only operation to run.",
            },
            "pr": {
                "type": "string",
                "description": "PR number or URL (default: current branch's PR).",
            },
            "run": {
                "type": "string",
                "description": "Workflow run id for run_logs.",
            },
            "repo": {
                "type": "string",
                "description": "Repository as owner/name (default: current repo).",
            },
        },
        "required": ["op"],
    },
    execute=_github,
    approval="read",
    timeout_s=_TIMEOUT_S + 10.0,
)


def build_github_tool() -> AgentTool | None:
    """The tool when ``gh`` exists, else ``None`` (absence means unregistered)."""
    return GITHUB if github_available() else None
