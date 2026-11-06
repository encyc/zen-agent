"""Every ``zenagent/*.py`` path a reference doc cites must exist.

Prose drifts silently: a refactor moves a file and the docs keep pointing at the
old one. This gate only checks existence (not behaviour), so it stays cheap and
truthful.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The docs whose job is to say where code lives.
DOCS = [
    ROOT / "docs" / "architecture.md",
    ROOT / "docs" / "testing.md",
    *sorted((ROOT / "docs" / "reference").glob("*.md")),
    *sorted((ROOT / "docs" / "internals").glob("*.md")),
    ROOT / "docs" / "feature-map.md",
]

#: Source paths inside backticks. ``zenagent/...`` is absolute and a bare
#: ``providers/...`` means the same thing (docs shorten it); brace/star forms
#: are skipped because they name a family of files, not one path.
TOKEN = re.compile(r"`((?:zenagent/)?(?:agent|app|cli|tools|providers|mcp|i18n|prompts)/[A-Za-z0-9_/.-]+\.py)`")


def test_cited_source_paths_exist():
    missing = []
    for doc in DOCS:
        for line_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for path in TOKEN.findall(line):
                resolved = ROOT / path
                if not path.startswith("zenagent/"):
                    resolved = ROOT / "zenagent" / path
                if not resolved.is_file():
                    missing.append(f"{doc.relative_to(ROOT)}:{line_no}: {path}")
    assert missing == []
