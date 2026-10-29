"""doctor: local checks only, exit code reflects failures."""

import zenagent.cli.main as cli_main
from zenagent.cli.doctor import Check, collect_checks, run_doctor


def test_missing_keys_are_reported():
    checks = collect_checks(env={})
    by_name = {check.name: check for check in checks}
    assert by_name["key:deepseek"].ok is False
    assert "DEEPSEEK_API_KEY" in by_name["key:deepseek"].detail
    assert by_name["catalog"].ok is True
    assert by_name["python"].ok is True


def test_all_keys_present_passes_those_rows():
    checks = collect_checks(
        env={
            "ZAI_CODING_CN_API_KEY": "x",
            "DEEPSEEK_API_KEY": "x",
            "OPENCODE_API_KEY": "x",
        }
    )
    key_rows = [check for check in checks if check.name.startswith("key:")]
    assert key_rows and all(check.ok for check in key_rows)


def test_broken_config_is_reported(monkeypatch):
    def broken():
        raise ValueError("config is broken")

    monkeypatch.setattr("zenagent.cli.doctor.load_user_config", broken)
    checks = collect_checks(env={})
    config_row = next(check for check in checks if check.name == "config")
    assert config_row.ok is False
    assert "broken" in config_row.detail


async def test_run_doctor_exit_code_follows_failures(monkeypatch):
    monkeypatch.setattr(
        "zenagent.cli.doctor.collect_checks",
        lambda env=None: [Check("a", True, "fine"), Check("b", False, "bad")],
    )
    printed: list[str] = []
    assert await run_doctor(print_fn=printed.append) == 1
    assert any("FAIL" in line for line in printed)


async def test_run_doctor_success(monkeypatch):
    monkeypatch.setattr(
        "zenagent.cli.doctor.collect_checks",
        lambda env=None: [Check("a", True, "fine")],
    )
    assert await run_doctor(print_fn=lambda *_: None) == 0


def test_main_routes_the_doctor_subcommand(monkeypatch):
    async def fake_doctor(print_fn=print):
        return 0

    monkeypatch.setattr(cli_main, "run_doctor", fake_doctor)
    assert cli_main.main(["doctor"]) == 0


def test_unknown_subcommand_is_rejected(capsys):
    assert cli_main.main(["nonsense"]) == 1
    assert "unknown command" in capsys.readouterr().err
