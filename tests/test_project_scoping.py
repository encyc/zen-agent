"""Project-scoped frontends: labels, scope toggles, CLI, warnings, discovery."""

from pathlib import Path

from zenagent import project
from zenagent.agent.session import SessionInfo


def repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root


def test_skill_roots_use_the_git_root(tmp_path):
    from zenagent.skills import default_skill_roots

    root = repo(tmp_path, "app")
    (root / ".zenagent" / "skills").mkdir(parents=True)
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    assert default_skill_roots(nested)[0] == root / ".zenagent" / "skills"


def test_template_roots_use_the_git_root(tmp_path):
    from zenagent.templates import default_template_roots

    root = repo(tmp_path, "app")
    (root / ".zenagent" / "commands").mkdir(parents=True)
    nested = root / "src"
    nested.mkdir()
    assert default_template_roots(nested)[0] == root / ".zenagent" / "commands"


def test_runtime_warns_on_a_legacy_session():
    from zenagent.app.runtime import Runtime

    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True, exist_ok=True)
    path = legacy / "20260101-000000.jsonl"
    path.write_text("", encoding="utf-8")

    runtime = Runtime()
    runtime._note_session_scope(path)
    assert any("legacy" in warning for warning in runtime.warnings)


def test_runtime_warns_on_a_cross_project_session(tmp_path, monkeypatch):
    from zenagent.app.runtime import Runtime

    here = repo(tmp_path, "here")
    other = repo(tmp_path, "other")
    monkeypatch.chdir(here)
    sessions = project.project_sessions_dir(root=other)
    sessions.mkdir(parents=True)
    path = sessions / "20260101-000000.jsonl"
    path.write_text("", encoding="utf-8")

    runtime = Runtime()
    runtime._note_session_scope(path)
    assert any("belongs to project" in warning for warning in runtime.warnings)


async def test_repl_sessions_all_labels_each_project(monkeypatch):
    import zenagent.cli.repl as repl_module
    from zenagent.cli.repl import handle_slash

    info = SessionInfo(
        path=Path("/tmp/s.jsonl"), mtime=1.0, messages=2, title="fix bug", project="app"
    )
    captured: dict = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return [info]

    monkeypatch.setattr(repl_module, "list_sessions", fake_list)
    printed: list[str] = []

    await handle_slash(None, "sessions", "all", print_fn=printed.append)

    assert captured.get("scope") == "all"
    assert "[app]" in printed[0]
    assert "fix bug" in printed[0]


async def test_tui_sessions_all_opens_the_all_scope_picker(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import SessionPicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("sessions", "all")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        assert app.screen.scope == "all"


def test_cli_list_sessions_all_projects_labels(monkeypatch, capsys):
    import zenagent.cli.main as cli_main

    info = SessionInfo(
        path=Path("/tmp/s.jsonl"), mtime=1.0, messages=2, title="fix bug", project="app"
    )
    monkeypatch.setattr(cli_main, "list_sessions", lambda **kwargs: [info])

    assert cli_main.main(["--list-sessions", "--all-projects"]) == 0
    out = capsys.readouterr().out
    assert "[app]" in out and "fix bug" in out


def test_sessions_subcommand_lists_all_projects(monkeypatch, capsys):
    import zenagent.agent.session as session_module
    from zenagent.cli import sessionscmd

    info = SessionInfo(
        path=Path("/tmp/s.jsonl"), mtime=1.0, messages=2, title="fix bug", project="app"
    )
    captured: dict = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return [info]

    monkeypatch.setattr(session_module, "list_sessions", fake_list)

    assert sessionscmd.main(["list", "--all"]) == 0
    out = capsys.readouterr().out
    assert captured.get("scope") == "all"
    assert "[app]" in out


def test_sessions_subcommand_adopts_a_legacy_file(monkeypatch, capsys, tmp_path):
    from zenagent.app import sessions as app_sessions
    from zenagent.cli import sessionscmd

    legacy = tmp_path / "old.jsonl"
    legacy.write_text("", encoding="utf-8")
    adopted = tmp_path / "project" / "old.jsonl"
    monkeypatch.setattr(
        app_sessions, "adopt_legacy_session", lambda path: adopted
    )

    assert sessionscmd.main(["adopt", str(legacy)]) == 0
    assert "adopted:" in capsys.readouterr().out


def test_usage_scans_every_project(tmp_path, monkeypatch):
    from zenagent.usage import session_paths

    here = repo(tmp_path, "here")
    other = repo(tmp_path, "other")
    monkeypatch.chdir(here)
    for root in (here, other):
        sessions = project.project_sessions_dir(root=root)
        sessions.mkdir(parents=True)
        (sessions / "20260101-000000.jsonl").write_text("", encoding="utf-8")

    paths = session_paths()
    assert project.project_sessions_dir(root=here) / "20260101-000000.jsonl" in paths
    assert project.project_sessions_dir(root=other) / "20260101-000000.jsonl" in paths
