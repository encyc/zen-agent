"""Source gate: fixed prompt prose lives in the block files, nowhere else.

The only exceptions are data-formatting strings that are not prompt prose
(transcript markers, the skills truncation sentinel, the eval dev prompt until
P1 moves it into a variant). Anything else must move to
``zenagent/prompts/blocks/``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "zenagent" / "prompts" / "blocks"

MARKERS = (
    "You are zenagent",
    "Tool policy:",
    "## Project instructions",
    "## Skills",
    "PLAN MODE:",
    "请把下面这段编码会话压缩",
    "You compress coding-session",
    "# 此前会话摘要",
    "Answer concisely. Explain",
    "## {role}",
    "[tool_call",
    "- ... more skills omitted",
    "You are zenagent, a coding agent. Be concise.",
)

#: (relative file, marker) pairs that are allowed outside the block files.
ALLOWLIST = {
    ("zenagent/app/compact.py", "## {role}"),
    ("zenagent/app/compact.py", "[tool_call"),
    ("zenagent/skills.py", "- ... more skills omitted"),
    ("scripts/eval.py", "You are zenagent"),
    ("scripts/eval.py", "You are zenagent, a coding agent. Be concise."),
}


def _python_files() -> list[Path]:
    files = sorted((ROOT / "zenagent").rglob("*.py"))
    files.extend(sorted((ROOT / "scripts").glob("*.py")))
    return files


def test_prompt_prose_lives_only_in_blocks() -> None:
    violations: list[str] = []
    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for marker in MARKERS:
            if marker in text and (relative, marker) not in ALLOWLIST:
                violations.append(f"{relative}: {marker!r}")
    assert not violations, "prompt prose outside blocks: " + "; ".join(violations)


def test_blocks_directory_has_no_python() -> None:
    assert not list(BLOCKS.rglob("*.py"))
