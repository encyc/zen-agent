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
