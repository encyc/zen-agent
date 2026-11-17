"""Runs ledger and worktrees overview screens: read-only panels.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
The runs tests seed a real :class:`RunLedger` in tmp; the worktrees tests
build a tiny git repository with one linked worktree.
"""

import subprocess
from types import SimpleNamespace

from textual.widgets import OptionList

from zenagent.app.commands import CommandResult
from zenagent.app.runs import RunLedger
from zenagent.cli.render import render_command_result
from zenagent.cli.tui.app import ZenAgentApp


def _seed_ledger(runtime, tmp_path):
    ledger = RunLedger(tmp_path / "runs.jsonl")
    record = ledger.start(kind="coding", agent_id="main", label="fix the bug")
    ledger.finish(record, status="done", termination="stop", turns=3, cost=0.12)
    failed = ledger.start(kind="subagent", agent_id="scout", label="explore")
    ledger.finish(
        failed, status="failed", termination="max_turns", turns=10, cost=0.5
    )
    ledger.start(kind="coding", agent_id="main", label="still running")
    runtime.run_ledger = ledger
    return ledger


async def test_dispatch_runs_lists_records_newest_first(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    _seed_ledger(app.runtime, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("runs", "")
        await pilot.pause()
        screen = app.screen
        options = list(screen.query_one("#runs-list", OptionList).options)
        assert len(options) == 3
        prompts = [str(option.prompt) for option in options]
        assert "●" in prompts[0] and "still running" in prompts[0]
        assert any("✗" in prompt and "explore" in prompt for prompt in prompts)
        assert any("✓" in prompt and "fix the bug" in prompt for prompt in prompts)


async def test_run_detail_opens_from_enter(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    _seed_ledger(app.runtime, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("runs", "")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#runs-list", OptionList).highlighted = 1  # the failed run
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        text = detail.text
        assert "kind: subagent" in text
        assert "termination: max_turns" in text
        assert "cost: 0.5" in text


def test_repl_renders_runs_as_text_rows(fake_runtime, tmp_path):
    ledger = _seed_ledger(fake_runtime, tmp_path)
    lines: list[str] = []
    render_command_result(
        CommandResult(screen="runs-screen"),
        name="runs",
        services=SimpleNamespace(runtime=fake_runtime),
        print_fn=lines.append,
        keybindings={},
        set_theme=lambda spec: None,
        set_language=lambda code: None,
    )
    text = "\n".join(lines)
    assert "still running" in text
    assert "explore" in text
    assert str(ledger.path) not in text


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


async def test_dispatch_worktrees_lists_linked_worktrees(fake_runtime, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "file.txt").write_text("x", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=repo)
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=repo)

    app = ZenAgentApp(fake_runtime)
    app.runtime.root = repo
    async with app.run_test(size=(140, 40)) as pilot:
        await app._dispatch_command("worktrees", "")
        await pilot.pause()
        options = list(app.screen.query_one("#worktrees-list", OptionList).options)
        prompts = [str(option.prompt) for option in options]
        assert any("side" in prompt and "▸" not in prompt for prompt in prompts)
        assert any("▸" in prompt for prompt in prompts)  # the current worktree


async def test_dispatch_worktrees_outside_a_repo_says_so(fake_runtime, tmp_path):
    app = ZenAgentApp(fake_runtime)
    app.runtime.root = tmp_path  # not a git repository
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("worktrees", "")
        await pilot.pause()
        options = list(app.screen.query_one("#worktrees-list", OptionList).options)
        assert len(options) == 1


def test_repl_renders_worktrees_restart_note(fake_runtime, tmp_path):
    app_runtime = SimpleNamespace(root=tmp_path)
    lines: list[str] = []
    render_command_result(
        CommandResult(screen="worktrees-screen"),
        name="worktrees",
        services=SimpleNamespace(runtime=app_runtime),
        print_fn=lines.append,
        keybindings={},
        set_theme=lambda spec: None,
        set_language=lambda code: None,
    )
    # Outside a repository the overview says so instead of listing worktrees.
    assert "not inside a git repository" in "\n".join(lines)
