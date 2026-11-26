# tests/test_automations_cli.py
import json

from zenagent.cli.automationscmd import main


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_add_list_enable_disable_rm(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"]) == 0
    out = capsys.readouterr().out
    assert "r" in out
    assert "09:00" in out
    assert "auto" in out                              # prints the permission boundary

    assert main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["jobs"][0]["name"] == "r"
    assert payload["jobs"][0]["session"].endswith(".jsonl")
    assert payload["jobs"][0]["next_due"] > 0          # the schedule was resolved

    assert main(["disable", "r"]) == 0
    capsys.readouterr()                                # drop the toggle's own line
    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["enabled"] is False
    assert main(["enable", "r"]) == 0
    assert main(["rm", "r"]) == 0
    capsys.readouterr()
    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == []


def test_add_requires_the_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    (tmp_path / "deep").mkdir()
    monkeypatch.chdir(tmp_path / "deep")
    assert main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"]) == 1


def test_daily_and_every_are_mutually_exclusive(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    assert (
        main(["add", "--name", "r", "--daily", "09:00", "--every", "15m", "--prompt", "go"])
        == 2
    )


def test_run_without_a_daemon_fails_loudly(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"])
    capsys.readouterr()
    assert main(["run", "r"]) == 1
    assert "daemon" in capsys.readouterr().err


def test_a_bad_schedule_is_a_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert main(["add", "--name", "r", "--every", "5s", "--prompt", "go"]) == 2
    err = capsys.readouterr().err
    assert "daily" in err or "every" in err
    assert "Traceback" not in err


def test_a_bad_timeout_is_refused(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert main(["add", "--name", "r", "--daily", "09:00", "--timeout", "0",
                 "--prompt", "go"]) == 1
    assert "timeout" in capsys.readouterr().err


def test_duplicate_session_name_is_refused(tmp_path, monkeypatch, capsys):
    """`_RESERVED_PATHS` is per-process, so uniqueness has to be checked here."""
    _project(tmp_path, monkeypatch)
    assert main(["add", "--name", "a", "--daily", "09:00", "--session", "same.jsonl",
                 "--prompt", "go"]) == 0
    capsys.readouterr()
    assert main(["add", "--name", "b", "--daily", "10:00", "--session", "same.jsonl",
                 "--prompt", "go"]) == 1
    assert "session" in capsys.readouterr().err


def test_add_says_how_to_start_the_daemon(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"]) == 0
    assert "zen daemon" in capsys.readouterr().out


def test_read_only_commands_work_from_a_subdirectory(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"])
    (tmp_path / "deep").mkdir()
    monkeypatch.chdir(tmp_path / "deep")
    assert main(["list", "--json"]) == 0


def test_the_cost_column_shows_the_most_recent_run(tmp_path, monkeypatch, capsys):
    """`RunLedger.list()` is newest-first; the newest cost is the first row."""
    _project(tmp_path, monkeypatch)
    main(["add", "--name", "r", "--daily", "09:00", "--prompt", "go"])
    capsys.readouterr()
    from zenagent import project
    from zenagent.app.runs import RunLedger

    ledger = RunLedger(project.project_dir(root=tmp_path) / "runs.jsonl")
    old = ledger.start(kind="automation", label="r")
    ledger.finish(old, status="done", termination="stop", cost=0.01)
    new = ledger.start(kind="automation", label="r")
    ledger.finish(new, status="done", termination="stop", cost=9.99)

    assert main(["show", "r", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cost"] == 9.99
