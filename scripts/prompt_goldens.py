#!/usr/bin/env python
"""Capture or check the v0.4.1 prompt golden baseline.

    uv run python scripts/prompt_goldens.py            # write tests/golden/prompts/
    uv run python scripts/prompt_goldens.py --check    # compare (exit 1 on drift)

The capture path deliberately calls the *legacy* functions (``system_prompt``,
``mode_prompt_note``, ...), so the files record pre-refactor behavior. Tasks 3-7
of the P0 plan compare the new ``compose()`` output against these files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from prompt_fixtures import (  # noqa: E402
    CONFIGS,
    SKILLS,
    TOOL_GOLDEN_NAMES,
    TRANSCRIPT,
)

from zenagent.app.compact import SUMMARIZE_PROMPT, _SUMMARIZE_SYSTEM  # noqa: E402
from zenagent.app.modes import mode_prompt_note  # noqa: E402
from zenagent.app.support import system_prompt  # noqa: E402
from zenagent.context_files import system_prompt_with_context  # noqa: E402
from zenagent.skills import build_skill_tool, skills_prompt_index  # noqa: E402
from zenagent.tools import ALL_TOOLS  # noqa: E402
from zenagent.tools.task import build_task_tool  # noqa: E402
from zenagent.tools.todo import build_todo_tool  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "golden" / "prompts"


def _normalize(text: str, cwd: str) -> str:
    """Replace the machine-specific working directory with the fixture path."""
    return text.replace(cwd, "/repo")


def render_config(cfg) -> tuple[str, str]:
    """Render ``(system_only, full)`` for one config using the legacy assembly order.

    ``system_only`` is the bare system prompt (override or ``system_prompt``);
    ``full`` applies the context wrapper, skills index, extension fragments and
    plan note in the order the frontends use.
    """
    cwd = str(Path.cwd())
    system_text = (
        cfg.system_override
        if cfg.system_override is not None
        else system_prompt(list(cfg.tool_names))
    )
    text = system_text
    if cfg.context_text:
        text = system_prompt_with_context(text, cfg.context_text)
    parts: list[str] = []
    if cfg.skills:
        parts.append(skills_prompt_index(list(cfg.skills)))
    if cfg.extension_fragments:
        parts.append("\n\n".join(cfg.extension_fragments))
    if parts:
        text = f"{text}\n\n" + "\n\n".join(parts)
    if cfg.mode == "plan":
        text = f"{text}\n\n" + mode_prompt_note("plan")
    return _normalize(system_text, cwd), _normalize(text, cwd)


def tool_descriptions() -> dict[str, dict]:
    """Capture the 14 tool descriptions exactly as the model sees them."""
    tools = list(ALL_TOOLS)
    tools.append(build_todo_tool(None))
    tools.append(build_task_tool(model=None, base_tools=[]))
    tools.append(build_skill_tool(list(SKILLS)))
    captured = {
        tool.name: {"chars": len(tool.description), "text": tool.description}
        for tool in tools
    }
    missing = [name for name in TOOL_GOLDEN_NAMES if name not in captured]
    extra = [name for name in captured if name not in TOOL_GOLDEN_NAMES]
    if missing or extra:
        raise SystemExit(f"tool set drifted: missing={missing} extra={extra}")
    return captured


def compact_prompts() -> dict[str, str]:
    return {
        "system": _SUMMARIZE_SYSTEM,
        "user": SUMMARIZE_PROMPT.format(transcript=TRANSCRIPT),
    }


def render_all() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, cfg in CONFIGS.items():
        if name == "compact":
            continue
        base, text = render_config(cfg)
        files[f"{name}.txt"] = text
        files[f"{name}.base.txt"] = base
    files["tools.json"] = json.dumps(
        tool_descriptions(), ensure_ascii=False, indent=2, sort_keys=True
    )
    files["compact.json"] = json.dumps(
        compact_prompts(), ensure_ascii=False, indent=2, sort_keys=True
    )
    return files


def write_all(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    files = render_all()
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    print(f"wrote {len(files)} golden files to {out}")


def check(out: Path) -> int:
    problems: list[str] = []
    for name, expected in render_all().items():
        target = out / name
        if not target.is_file():
            problems.append(f"missing {name}")
            continue
        if target.read_text(encoding="utf-8") != expected:
            problems.append(f"drift {name}")
    if problems:
        print("golden check failed: " + ", ".join(problems), file=sys.stderr)
        return 1
    print("golden check ok: all files match")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="compare against the golden files"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="golden directory"
    )
    args = parser.parse_args(argv)
    if args.check:
        return check(args.out)
    write_all(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
