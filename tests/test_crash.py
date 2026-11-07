"""Crash logs: one file per uncaught exception, never containing secrets."""

import sys

from zenagent import crash


def test_write_crash_log_captures_the_traceback(tmp_path):
    try:
        raise RuntimeError("boom-123")
    except RuntimeError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        path = crash.write_crash_log(exc_type, exc_value, exc_tb, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "zenagent crash report" in text
    assert "RuntimeError" in text and "boom-123" in text
    assert "Traceback" in text


def test_install_excepthook_is_idempotent(tmp_path, monkeypatch):
    # Other CLI tests run main(), which installs the hook for real; isolate
    # the module state so the idempotency contract is what is under test.
    monkeypatch.setattr(crash, "_installed", False)
    monkeypatch.setattr(crash, "_log_dir", None)
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
