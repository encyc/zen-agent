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

from prompt_fixtures import CONFIGS, SKILLS, TOOL_GOLDEN_NAMES, TRANSCRIPT

from zenagent.prompts import PromptContext, compose_compact, compose_system
from zenagent.skills import build_skill_tool, format_skill_entries
from zenagent.tools import ALL_TOOLS
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool

DEFAULT_OUT = ROOT / "tests" / "golden" / "prompts"


def _normalize(text: str, cwd: str) -> str:
    """Replace the machine-specific working directory with the fixture path."""
    return text.replace(cwd, "/repo")


def render_config(cfg) -> tuple[str, str]:
    """Render ``(system_only, full)`` for one config through compose_system."""
    cwd = str(Path.cwd())
    base = compose_system(
        PromptContext(
            cwd=Path.cwd(),
            tool_names=cfg.tool_names,
            system_override=cfg.system_override,
            include_context=False,
            include_skills=False,
            include_extensions=False,
            include_mode=False,
        )
    ).text
    full = compose_system(
        PromptContext(
            cwd=Path.cwd(),
            tool_names=cfg.tool_names,
            mode=cfg.mode,
            context_text=cfg.context_text,
            skill_entries=format_skill_entries(list(cfg.skills)),
            extension_fragments=cfg.extension_fragments,
            system_override=cfg.system_override,
        )
    ).text
    return _normalize(base, cwd), _normalize(full, cwd)


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
    system, user = compose_compact(TRANSCRIPT)
    return {"system": system, "user": user}


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
