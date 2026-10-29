"""Crash logs and the redacted diagnostics report."""

import sys

from pyagent import crash


def test_write_crash_log_captures_the_traceback(tmp_path):
    try:
        raise RuntimeError("boom-123")
    except RuntimeError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        path = crash.write_crash_log(exc_type, exc_value, exc_tb, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "pyagent crash report" in text
    assert "RuntimeError" in text and "boom-123" in text
    assert "Traceback" in text


def test_install_excepthook_is_idempotent(tmp_path):
    original = sys.excepthook
    try:
        first = crash.install_excepthook(tmp_path)
        second = crash.install_excepthook(tmp_path / "other")
        assert first == tmp_path
        assert second == first  # already installed: keeps the first directory
        assert sys.excepthook is not original
    finally:
        sys.excepthook = original
        crash._installed = False


def test_report_never_leaks_credential_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret")
    monkeypatch.setattr(
        "pyagent.cli.doctor.collect_checks",
        lambda env=None: [
            type("C", (), {"name": "key:deepseek", "ok": True, "detail": "DEEPSEEK_API_KEY is set"})()
        ],
    )
    report = crash.collect_report()
    assert "sk-super-secret" not in report
    assert "DEEPSEEK_API_KEY is set" in report
    assert "pyagent diagnostics" in report
