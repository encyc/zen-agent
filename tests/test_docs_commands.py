"""The slash-command reference must list every command the app registers."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    ROOT / "zenagent/app/commands.py",
    ROOT / "zenagent/cli/tui/app.py",
    ROOT / "zenagent/extensions.py",
)
DOC = ROOT / "docs/guide/commands.md"


def registered_commands() -> set[str]:
    names: set[str] = set()
    for path in SOURCES:
        names |= set(
            re.findall(r'_command\("([a-z][a-z-]*)"\)', path.read_text(encoding="utf-8"))
        )
    return names


def test_every_registered_command_is_documented():
    names = registered_commands()
    assert len(names) > 40, names  # the scrape itself must not silently break
    text = DOC.read_text(encoding="utf-8")
    missing = sorted(name for name in names if f"`/{name}" not in text)
    assert not missing, f"docs/guide/commands.md is missing {missing}"


def test_doc_never_repeats_a_command_row():
    rows = re.findall(r"^\| `/([a-z][a-z-]*)", DOC.read_text(encoding="utf-8"), re.MULTILINE)
    duplicates = sorted({name for name in rows if rows.count(name) > 1})
    assert not duplicates, f"duplicate command rows: {duplicates}"
