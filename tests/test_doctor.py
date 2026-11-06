"""doctor: local checks only, exit code reflects failures."""

import zenagent.cli.main as cli_main
from zenagent.cli.doctor import Check, collect_checks, collect_report, run_doctor


def test_missing_keys_are_reported():
    checks = collect_checks(env={})
    by_name = {check.name: check for check in checks}
    assert by_name["key:deepseek"].ok is False
    assert "DEEPSEEK_API_KEY" in by_name["key:deepseek"].detail
    assert by_name["catalog"].ok is True
    assert by_name["python"].ok is True


def test_all_keys_present_passes_those_rows():
    # Set the env var of every catalog provider, so "all keys present" stays a
    # real assertion no matter how many providers the packaged catalog ships.
    from zenagent.providers.catalog import load_catalog

    env = {p.api_key_env: "x" for p in load_catalog().values()}
    checks = collect_checks(env=env)
    key_rows = [check for check in checks if check.name.startswith("key:")]
    assert key_rows and all(check.ok for check in key_rows)
    assert len(key_rows) == len(load_catalog())


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


def test_missing_keys_for_unconfigured_providers_are_advisory():
    """A fresh install must not fail doctor just because 17 providers exist."""
    checks = collect_checks(env={})
    key_rows = [check for check in checks if check.name.startswith("key:")]
    assert key_rows
    # Absent keys are advisory (never fail the run); present keys are plain ok.
    # A machine may already hold some keys in settings.json, so assert the
    # implication, not "all rows are advisory".
    for check in key_rows:
        if check.ok:
            assert not check.advisory, check.name
        else:
            assert check.advisory, check.name
            assert "is missing" in check.detail


async def test_run_doctor_exits_zero_when_missing_keys_are_only_advisory(monkeypatch):
    monkeypatch.setattr(
        "zenagent.cli.doctor.collect_checks",
        lambda env=None: [
            Check("a", True, "fine"),
            Check("key:x", False, "X_API_KEY is missing", advisory=True),
        ],
    )
    printed: list[str] = []
    assert await run_doctor(print_fn=printed.append) == 0
    assert any(line.startswith("--") for line in printed)
    assert not any("FAIL" in line for line in printed)


def test_main_routes_the_doctor_subcommand(monkeypatch):
    async def fake_doctor(print_fn=print):
        return 0

    monkeypatch.setattr(cli_main, "run_doctor", fake_doctor)
    assert cli_main.main(["doctor"]) == 0


def test_unknown_subcommand_is_rejected(capsys):
    assert cli_main.main(["nonsense"]) == 1
    assert "unknown command" in capsys.readouterr().err


def test_secrets_check_passes_on_a_clean_config(tmp_path, monkeypatch):
    from zenagent import settings
    from zenagent.cli import doctor

    config = tmp_path / "config.toml"
    config.write_text('default_model = "deepseek-flash"\n')
    store = tmp_path / "settings.json"
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(store))
    monkeypatch.setattr(doctor, "config_path", lambda: config)
    settings.clear_cache()
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-test")

    check = doctor._check_secrets()
    assert check.ok, check.detail
    assert "settings.json" in check.detail


def test_secrets_check_flags_plaintext_keys_in_config(tmp_path, monkeypatch):
    from zenagent import settings
    from zenagent.cli import doctor

    config = tmp_path / "config.toml"
    config.write_text('default_model = "x"\n\nDEEPSEEK_API_KEY="sk-leftover"\n')
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(doctor, "config_path", lambda: config)
    settings.clear_cache()

    check = doctor._check_secrets()
    assert not check.ok
    assert "plaintext API keys" in check.detail
    assert "/settings" in check.detail


def test_secrets_check_flags_a_world_readable_store(tmp_path, monkeypatch):
    import os

    from zenagent import settings
    from zenagent.cli import doctor

    config = tmp_path / "config.toml"
    config.write_text('default_model = "x"\n')
    store = tmp_path / "settings.json"
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(store))
    monkeypatch.setattr(doctor, "config_path", lambda: config)
    settings.clear_cache()
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-test")
    os.chmod(store, 0o644)

    check = doctor._check_secrets()
    assert not check.ok
    assert "chmod 600" in check.detail


def test_provider_key_checks_read_the_settings_store(tmp_path, monkeypatch):
    from zenagent import settings
    from zenagent.cli import doctor

    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(doctor, "config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text('default_model = "deepseek-flash"\n')
    settings.clear_cache()
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-test")

    checks = {check.name: check for check in doctor.collect_checks(env={})}
    assert checks["key:deepseek"].ok, "a key stored by /settings counts"


def test_report_never_leaks_credential_values(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret")
    monkeypatch.setattr(
        "zenagent.cli.doctor.collect_checks",
        lambda env=None: [
            type("C", (), {"name": "key:deepseek", "ok": True, "detail": "DEEPSEEK_API_KEY is set"})()
        ],
    )
    report = collect_report()
    assert "sk-super-secret" not in report
    assert "DEEPSEEK_API_KEY is set" in report
    assert "zenagent diagnostics" in report
