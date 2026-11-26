# tests/test_docs_automations.py
"""The automations guide must describe the CLI and the files that exist.

Docs drift silently; these two greps are the cheap tripwire. They are
deliberately about *presence*, not wording — the guide can be rewritten freely
as long as every subcommand and every file it owns stays documented.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_docs_automations_documents_every_subcommand():
    text = (REPO / "docs" / "guide" / "automations.md").read_text(encoding="utf-8")
    for sub in ("add", "list", "show", "rm", "enable", "disable", "run"):
        assert f"zen automations {sub}" in text, sub
    for word in (
        "daemon",
        "session",
        "权限",
        "timeout",
        "state",
        "automations.request.json",
    ):
        assert word in text, word


def test_ledger_kind_is_documented_where_the_guide_index_points():
    index = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    assert "guide/automations.md" in index
