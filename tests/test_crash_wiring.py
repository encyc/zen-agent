"""The CLI entry installs the crash-log excepthook before anything else runs."""

import sys

import pytest


@pytest.fixture()
def clean_excepthook():
    """Keep the process hook pristine even if a test installs a real one."""
    previous = sys.excepthook
    yield
    sys.excepthook = previous


def test_main_installs_crash_hook_before_parsing(monkeypatch):
    import zenagent.cli.main as main_mod

    calls = []
    monkeypatch.setattr(main_mod, "_install_crash_hook", lambda: calls.append(1))
    with pytest.raises(SystemExit):
        main_mod.main(["--version"])
    assert calls == [1]


def test_hook_failure_never_breaks_startup(monkeypatch):
    import zenagent.cli.main as main_mod

    def boom() -> None:
        raise RuntimeError("no crash dir")

    monkeypatch.setattr(main_mod, "_install_crash_hook", boom)
    with pytest.raises(SystemExit):
        main_mod.main(["--version"])


def test_install_excepthook_creates_directory(tmp_path, clean_excepthook):
    from zenagent.crash import install_excepthook

    path = install_excepthook(tmp_path)
    assert path.exists()
    assert path == tmp_path
