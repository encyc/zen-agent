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
README = ROOT / "README.md"


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
    # A whole row, closing backtick included: a substring test would accept
    # "`/new" as proof of "/new" when the doc only has "`/new-session`".
    missing = sorted(
        name for name in names if not re.search(rf"^\| `/{re.escape(name)}`", text, re.MULTILINE)
    )
    assert not missing, f"docs/guide/commands.md is missing {missing}"


def test_doc_never_repeats_a_command_row():
    rows = re.findall(r"^\| `/([a-z][a-z-]*)`", DOC.read_text(encoding="utf-8"), re.MULTILINE)
    duplicates = sorted({name for name in rows if rows.count(name) > 1})
    assert not duplicates, f"duplicate command rows: {duplicates}"


def test_readme_states_the_real_command_count():
    """The landing page advertises a count; adding a command must update it."""
    count = len(registered_commands())
    assert f"{count} commands" in README.read_text(encoding="utf-8")
