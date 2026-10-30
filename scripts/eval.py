#!/usr/bin/env python
"""Behavioral evals for zenagent: run YAML-free JSON cases against a real model.

Each case gets an isolated temp workspace, a real headless run, and a set of
assertions over the output, the tool sequence, the termination reason, the cost
and the resulting files. That is the harness-level complement to the offline
unit tests: they prove the code is correct, this proves the agent is useful.

    uv run python scripts/eval.py --list
    uv run python scripts/eval.py --filter read --repeats 1
    uv run python scripts/eval.py --model deepseek-flash --repeats 3

Case format (JSON, one file per case under ``evals/cases``):

    {
      "id": "read_file_secret",
      "prompt": "...",
      "setup": {"files": {"notes.txt": "secret: 4821"}},
      "options": {"max_turns": 6, "thinking": "off", "context_files": true},
      "expect": {
        "output_contains": ["4821"],
        "output_not_contains": ["I cannot"],
        "tools": ["read_file"],          // ordered subsequence
        "tools_exclude": ["write_file"],
        "termination": "stop",
        "max_cost": 0.02,
        "files": {"out.txt": {"contains": "hello"}}
      }
    }

Not a CI gate: it calls a real provider, so it needs a key and costs money.
`scripts/check.sh` stays offline; run this when changing agent behaviour.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from zenagent.agent import Agent
from zenagent.attachments import read_image_argument
from zenagent.cli.main import _api_key_for, resolve_model, session_id_for
from zenagent.cli.permissions import PermissionGate
from zenagent.config import ContextGuard, apply_user_config, load_user_config
from zenagent.context_files import (
    discover_context_files,
    load_context_files,
    system_prompt_with_context,
)
from zenagent.events import AgentEvent
from zenagent.extensions import load_extensions
from zenagent.mcp.config import load_mcp_config
from zenagent.mcp.manager import McpManager
from zenagent.providers.catalog import load_catalog
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from zenagent.tools import ALL_TOOLS
from zenagent.types import AssistantMessage

CASES_DIR = Path(__file__).resolve().parent.parent / "evals" / "cases"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Case loading and workspace setup
# ---------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    prompt: str
    setup: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    path: Path | None = None


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(cases_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            Case(
                id=data["id"],
                prompt=data["prompt"],
                setup=data.get("setup", {}),
                options=data.get("options", {}),
                expect=data.get("expect", {}),
                path=path,
            )
        )
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate case ids in evals/cases")
    return cases


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A minimal valid PNG (used by the image cases)."""
    import struct

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * width  # filter byte 0 + pixels
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def prepare_workspace(case: Case, root: Path) -> list[Path]:
    """Create setup files; returns image paths to attach."""
    files = case.setup.get("files", {}) or {}
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    images: list[Path] = []
    for spec in case.setup.get("images", []) or []:
        target = root / spec.get("path", "image.png")
        colors = {
            "red": (220, 40, 40),
            "green": (40, 180, 60),
            "blue": (40, 70, 200),
        }
        rgb = colors.get(spec.get("color", "red"), colors["red"])
        target.write_bytes(_solid_png(spec.get("width", 64), spec.get("height", 64), rgb))
        if spec.get("attach", True):
            images.append(target)
    return images


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    output: str
    tools: list[str]
    calls: list[dict]
    tool_results: list[str]
    tool_errors: list[str]
    termination: str | None
    error_message: str | None
    cost: float
    tokens_in: int
    tokens_out: int
    turns: int
    duration_ms: int


def _resolve(value, mapping: dict[str, str]):
    """Substitute ``{{python}}``/``{{fixtures}}``/``{{workspace}}`` in case options."""
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value
    if isinstance(value, list):
        return [_resolve(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, mapping) for key, item in value.items()}
    return value


async def run_case(
    case: Case, model, api_key: str, repeats: int = 1
) -> list[tuple[RunResult, list[str]]]:
    results: list[tuple[RunResult, list[str]]] = []
    for _ in range(repeats):
        with tempfile.TemporaryDirectory(prefix=f"zenagent-eval-{case.id}-") as tmp:
            root = Path(tmp)
            images = prepare_workspace(case, root)

            image_blocks = [read_image_argument(path) for path in images]
            options = _resolve(
                case.options or {},
                {
                    "python": sys.executable,
                    "fixtures": str(FIXTURES_DIR),
                    "workspace": str(root),
                },
            )

            prompt = (
                options.get("system_prompt_override")
                or "You are zenagent, a coding agent. Be concise."
            )
            if options.get("context_files"):
                prompt = system_prompt_with_context(
                    prompt, load_context_files(discover_context_files(root))
                )

            tool_list = list(ALL_TOOLS)
            prompt_parts: list[str] = []

            skills_wanted = options.get(
                "skills", (root / ".zenagent" / "skills").is_dir()
            )
            if skills_wanted:
                discovered, _warnings = discover_skills(default_skill_roots(root))
                if discovered:
                    tool_list.append(build_skill_tool(discovered))
                    prompt_parts.append(skills_prompt_index(discovered))

            extension_hooks: dict = {}
            extension_paths = options.get("extensions") or []
            if extension_paths:
                loaded = load_extensions(extension_paths)
                tool_list.extend(loaded.tools)
                if loaded.prompt_text:
                    prompt_parts.append(loaded.prompt_text)
                extension_hooks = loaded.hooks

            mcp_manager: McpManager | None = None
            mcp_spec = options.get("mcp")
            if mcp_spec:
                mcp_file = root / "mcp.json"
                mcp_file.write_text(
                    json.dumps({"servers": mcp_spec}), encoding="utf-8"
                )
                mcp_manager = McpManager(load_mcp_config(mcp_file))
                await mcp_manager.start()
                tool_list.extend(mcp_manager.tool_defs())

            if prompt_parts:
                prompt = f"{prompt}\n\n" + "\n\n".join(prompt_parts)

            gate = PermissionGate(
                assume_yes=options.get("assume_yes", True),
                interactive=False,
                allow_dangerous=options.get("allow_dangerous", False),
                deny=options.get("tool_deny") or [],
                read_only=bool(options.get("read_only", False)),
            )
            guard = ContextGuard(model.context_window)
            agent = Agent(
                model=model,
                system_prompt=prompt,
                tools=tool_list,
                api_key=api_key,
                thinking=options.get("thinking", "off"),
                session_id=session_id_for(model),
                before_tool_call=gate.before_tool_call,
                transform_context=guard.transform,
                max_turns=options.get("max_turns"),
                max_cost=options.get("max_cost"),
            )

            collected = {
                "text": [],
                "tools": [],
                "calls": [],
                "results": [],
                "errors": [],
                "turns": 0,
            }

            def on_event(event: AgentEvent, _collected=collected) -> None:
                if event.type == "turn_start":
                    _collected["turns"] += 1
                elif event.type == "message_update" and event.assistant_event:
                    assistant_event = event.assistant_event
                    if assistant_event.type == "text_delta" and assistant_event.delta:
                        _collected["text"].append(assistant_event.delta)
                elif event.type == "tool_execution_start":
                    _collected["tools"].append(event.tool_name or "?")
                    _collected["calls"].append(
                        {"name": event.tool_name or "?", "args": event.args, "ok": True}
                    )
                elif event.type == "tool_execution_end":
                    if _collected["calls"]:
                        _collected["calls"][-1]["ok"] = not event.is_error
                    content = getattr(event.result, "content", None) or []
                    text = getattr(content[0], "text", "") if content else ""
                    _collected["results"].append(text or "")
                    if event.is_error:
                        _collected["errors"].append(text or "")

            agent.subscribe(on_event)
            agent.subscribe(guard.on_event)
            for event_type, callbacks in extension_hooks.items():
                def hook(event: AgentEvent, _type=event_type, _callbacks=callbacks) -> None:
                    if event.type != _type:
                        return
                    for callback in _callbacks:
                        try:
                            callback(event)
                        except Exception:  # noqa: BLE001,S112 - hook failures are isolated
                            continue

                agent.subscribe(hook)

            started = time.monotonic()
            cwd = Path.cwd()
            try:
                os.chdir(root)
                await agent.prompt(case.prompt, images=image_blocks or None)
            finally:
                os.chdir(cwd)
                if mcp_manager is not None:
                    await mcp_manager.close()
            duration_ms = round((time.monotonic() - started) * 1000)

            usage = _total_usage(agent.messages)
            result = RunResult(
                output="".join(collected["text"]).strip(),
                tools=collected["tools"],
                calls=collected["calls"],
                tool_results=collected["results"],
                tool_errors=collected["errors"],
                termination=agent.termination,
                error_message=agent.error_message,
                cost=usage[2],
                tokens_in=usage[0],
                tokens_out=usage[1],
                turns=collected["turns"],
                duration_ms=duration_ms,
            )
            results.append((result, check_result(case, result, root)))
    return results


def _total_usage(messages) -> tuple[int, int, float]:
    tokens_in = tokens_out = 0
    cost = 0.0
    for message in messages:
        if isinstance(message, AssistantMessage):
            tokens_in += message.usage.input
            tokens_out += message.usage.output
            cost += message.usage.cost.total
    return tokens_in, tokens_out, cost


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _contains(haystack: str, needle: str) -> bool:
    """Case-insensitive containment: models vary capitalisation between runs."""
    return needle.lower() in haystack.lower()


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    iterator = iter(haystack)
    return all(item in iterator for item in needle)


def check_result(case: Case, result: RunResult, root: Path) -> list[str]:
    problems: list[str] = []
    expect = case.expect or {}

    for needle in expect.get("output_contains", []) or []:
        if not _contains(result.output, needle):
            problems.append(f"output missing {needle!r}")
    for needle in expect.get("output_not_contains", []) or []:
        if _contains(result.output, needle):
            problems.append(f"output unexpectedly contains {needle!r}")

    if "tools" in expect and not _is_subsequence(list(expect["tools"]), result.tools):
        problems.append(f"tool sequence {result.tools} lacks {expect['tools']}")
    for needle in expect.get("tool_results_contain", []) or []:
        if not any(_contains(text, needle) for text in result.tool_results):
            problems.append(f"no tool result contains {needle!r}")
    for needle in expect.get("tool_errors_contain", []) or []:
        if not any(_contains(text, needle) for text in result.tool_errors):
            problems.append(f"no tool error contains {needle!r}")
    for needle in expect.get("forbid_successful_commands", []) or []:
        for call in result.calls:
            if call["name"] != "bash" or not call["ok"]:
                continue
            command = str((call.get("args") or {}).get("command", ""))
            if needle.lower() in command.lower():
                problems.append(f"catastrophic command succeeded: {needle!r}")
    for forbidden in expect.get("tools_exclude", []) or []:
        if forbidden in result.tools:
            problems.append(f"forbidden tool used: {forbidden}")

    if "termination" in expect and result.termination != expect["termination"]:
        problems.append(f"termination {result.termination!r} != {expect['termination']!r}")
    if "max_cost" in expect and result.cost > float(expect["max_cost"]):
        problems.append(f"cost ${result.cost:.4f} over budget ${expect['max_cost']}")
    if "max_turns" in expect and result.turns > int(expect["max_turns"]):
        problems.append(f"took {result.turns} turns, limit {expect['max_turns']}")
    for file_name in expect.get("files_absent", []) or []:
        if (root / file_name).exists():
            problems.append(f"file {file_name!r} should not exist")
    for file_name, rule in (expect.get("files") or {}).items():
        target = root / file_name
        if not target.exists():
            problems.append(f"expected file {file_name!r} was not created")
            continue
        content = target.read_text(encoding="utf-8", errors="replace")
        if "contains" in rule and rule["contains"] not in content:
            problems.append(f"{file_name!r} missing {rule['contains']!r}")
        if "not_contains" in rule and rule["not_contains"] in content:
            problems.append(f"{file_name!r} still contains {rule['not_contains']!r}")
        if "equals" in rule and content != rule["equals"]:
            problems.append(f"{file_name!r} content mismatch")
    return problems


def classify(result: RunResult, problems: list[str]) -> str:
    if result.termination == "error":
        return "provider_error"
    if result.termination in {"max_turns", "max_cost"}:
        return result.termination
    joined = " ".join(problems)
    if "tool sequence" in joined or "forbidden tool" in joined:
        return "tool_use"
    if "output missing" in joined or "output unexpectedly" in joined:
        return "wrong_answer"
    if "no tool result contains" in joined:
        return "tool_use"
    if "cost" in joined:
        return "over_budget"
    return "assertion"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def run_cases(args) -> int:
    cases = load_cases()
    if args.filter:
        needle = args.filter.lower()
        cases = [case for case in cases if needle in case.id.lower()]
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    if args.list:
        for case in cases:
            print(f"{case.id:32s} {case.prompt[:70]}")
        return 0

    user_config = load_user_config()
    catalog = apply_user_config(load_catalog(), user_config)
    try:
        model = resolve_model(catalog, args.model or user_config.default_model)
    except KeyError as exc:
        print(f"unknown model: {exc}", file=sys.stderr)
        return 2
    api_key = _api_key_for(catalog, model)
    if not api_key:
        print(f"missing API key for {model.id}", file=sys.stderr)
        return 2

    print(f"model: {model.id} · cases: {len(cases)} · repeats: {args.repeats}")
    records = []
    total_cost = 0.0
    for case in cases:
        if args.max_total_cost and total_cost > args.max_total_cost:
            print(f"stopping: total cost ${total_cost:.4f} over budget")
            break
        checked = await run_case(case, model, api_key, repeats=args.repeats)
        results = [result for result, _ in checked]
        problems_by_run = [problems for _, problems in checked]
        passed = sum(1 for problems in problems_by_run if not problems)
        ok = passed == len(results)
        total_cost += sum(result.cost for result in results)
        first_bad = next((problems for problems in problems_by_run if problems), [])
        kind = "ok" if ok else classify(results[0], first_bad)
        records.append(
            {
                "id": case.id,
                "ok": ok,
                "passed": passed,
                "runs": len(results),
                "kind": kind,
                "problems": first_bad,
                "tokens_in": sum(r.tokens_in for r in results),
                "tokens_out": sum(r.tokens_out for r in results),
                "cost": sum(r.cost for r in results),
                "duration_ms": sum(r.duration_ms for r in results),
                "turns": [r.turns for r in results],
                "tools": results[0].tools,
                "output": results[0].output[:300],
            }
        )
        mark = "PASS" if ok else "FAIL"
        detail = "" if ok else f"  ({kind}: {first_bad[0] if first_bad else '?'})"
        print(f"{mark}  {case.id:32s}{detail}")

    passed = sum(1 for record in records if record["ok"])
    rate = passed / len(records) if records else 0.0
    print(
        f"\n{passed}/{len(records)} cases passed · pass rate {rate:.0%} · "
        f"${total_cost:.4f} · {sum(r['tokens_in'] + r['tokens_out'] for r in records)} tokens"
    )
    payload = {
        "model": model.id,
        "pass_rate": rate,
        "passed": passed,
        "total": len(records),
        "cost": total_cost,
        "records": records,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.report:
        from zenagent.evals_runner import write_report

        write_report(args.report, payload)
        print(f"report: {args.report}")

    if args.baseline:
        from zenagent.evals_runner import (
            compare_reports,
            load_report,
            render_comparison,
        )

        try:
            baseline_payload = load_report(args.baseline)
        except ValueError as exc:
            print(f"zenagent: {exc}", file=sys.stderr)
            return 1
        comparison = compare_reports(baseline_payload, payload)
        print(render_comparison(comparison))
        if comparison["regressions"]:
            return 1
    if args.min_pass_rate is not None and rate < args.min_pass_rate:
        print(f"below required pass rate {args.min_pass_rate:.0%}", file=sys.stderr)
        return 1
    return 0 if passed == len(records) or args.min_pass_rate is not None else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="model id (default: user config / catalog default)")
    parser.add_argument("--filter", help="only case ids containing this substring")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case (default 1)")
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument("--json", action="store_true", help="also print a JSON report")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        help="fail unless at least this fraction passes (e.g. 0.8)",
    )
    parser.add_argument(
        "--max-total-cost", type=float, help="stop the run after this many USD"
    )
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="compare against a previous report; exit 1 on regressions",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run_cases(args))


if __name__ == "__main__":
    raise SystemExit(main())
