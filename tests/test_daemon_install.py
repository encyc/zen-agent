# tests/test_daemon_install.py
from zenagent.cli.daemon_service import (
    plist_text,
    systemd_unit_text,
    unit_label,
    unit_path,
)


def test_plist_is_absolute_and_per_project(tmp_path):
    text = plist_text(python="/usr/bin/python3", root=tmp_path / "proj")
    assert str(tmp_path / "proj") in text
    # A plist spells the command as argv, one element per <string>.
    for arg in ("<string>-m</string>", "<string>zenagent</string>", "<string>daemon</string>"):
        assert arg in text, arg
    assert "<string>/usr/bin/python3</string>" in text
    assert "RunAtLoad" in text
    assert "KeepAlive" in text
    assert "ZENAGENT_DAEMON_VERSION" in text


def test_unit_is_per_project_and_restarts(tmp_path):
    text = systemd_unit_text(python="/usr/bin/python3", root=tmp_path / "proj")
    assert "Restart=on-failure" in text
    assert "-m zenagent daemon" in text
    assert str(tmp_path / "proj") in text
    assert "ZENAGENT_DAEMON_VERSION" in text


def test_unit_paths_differ_by_project(tmp_path):
    text_a = plist_text(python="/p", root=tmp_path / "a")
    text_b = plist_text(python="/p", root=tmp_path / "b")
    assert text_a != text_b
    labels = {
        line.strip().removeprefix("<string>").removesuffix("</string>")
        for line in text_a.splitlines()
        if "com.zenagent.daemon." in line
    }
    assert any(label.startswith("com.zenagent.daemon.") for label in labels)


def test_unit_label_carries_the_project_key(tmp_path):
    label = unit_label(tmp_path / "proj")
    assert label.startswith("com.zenagent.daemon.")
    assert label != unit_label(tmp_path / "other")


def test_unit_path_per_platform(tmp_path):
    darwin = unit_path(platform="darwin", home=tmp_path, root=tmp_path / "proj")
    assert darwin.parts[-2:] == ("LaunchAgents", darwin.name)
    assert darwin.name.endswith(".plist")
    linux = unit_path(platform="linux", home=tmp_path, root=tmp_path / "proj")
    assert linux.name.endswith(".service")
    assert "systemd" in str(linux)
