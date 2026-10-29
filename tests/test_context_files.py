"""Project instruction files: discovery boundaries, size cap, prompt wiring."""

from pathlib import Path

from pyagent.context_files import (
    MAX_CONTEXT_BYTES,
    discover_context_files,
    load_context_files,
    system_prompt_with_context,
)


def make_repo(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rules", encoding="utf-8")
    sub = root / "pkg"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("pkg rules", encoding="utf-8")
    return sub


def test_discovers_root_to_leaf_in_order(tmp_path):
    start = make_repo(tmp_path)
    found = discover_context_files(start)
    assert [p.parent for p in found] == [tmp_path, start]
    assert [p.name for p in found] == ["AGENTS.md", "AGENTS.md"]


def test_stops_at_the_git_boundary(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "AGENTS.md").write_text("outside the repo", encoding="utf-8")
    repo = outer / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("inside", encoding="utf-8")
    found = discover_context_files(repo)
    assert [p.read_text() for p in found] == ["inside"]


def test_without_a_repo_marker_only_the_cwd_counts(tmp_path):
    (tmp_path / "AGENTS.md").write_text("here", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    found = discover_context_files(child)
    assert found == []  # walks to the filesystem root, never sees a marker


def test_first_matching_name_per_directory_wins(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    found = discover_context_files(tmp_path)
    assert found[0].name == "AGENTS.md"


def test_load_renders_sections_and_caps_size(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("A" * 100, encoding="utf-8")
    b.write_text("B" * 100, encoding="utf-8")
    text = load_context_files([a, b], max_bytes=150)
    assert "### " in text
    assert "truncated" in text
    assert len(text) < MAX_CONTEXT_BYTES


def test_load_skips_unreadable_files(tmp_path):
    missing = tmp_path / "nope.md"
    good = tmp_path / "good.md"
    good.write_text("ok", encoding="utf-8")
    text = load_context_files([missing, good])
    assert "ok" in text and "nope.md" not in text


def test_system_prompt_appends_context_only_when_present():
    assert system_prompt_with_context("base", "  ") == "base"
    merged = system_prompt_with_context("base\n", "rules")
    assert merged == "base\n\n## Project instructions\nrules"


def test_build_agent_injects_agents_md(tmp_path, monkeypatch):
    import pyagent.cli.main as cli_main
    from pyagent.cli.permissions import PermissionGate
    from pyagent.providers.catalog import find_model, load_catalog

    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "pkg")
    model = find_model(load_catalog(), "deepseek-flash")
    gate = PermissionGate(assume_yes=True)

    with_context = cli_main._build_agent(model, api_key="k", thinking=None, gate=gate)
    assert "root rules" in with_context.system_prompt
    assert "pkg rules" in with_context.system_prompt

    without = cli_main._build_agent(
        model, api_key="k", thinking=None, gate=gate, context_files=False
    )
    assert "root rules" not in without.system_prompt
