"""Plugin discovery and install: strict, atomic, and hostile-zip safe."""

from __future__ import annotations

import json
import zipfile

import pytest

from zenagent import settings
from zenagent.plugins import install
from zenagent.plugins.discovery import discover_plugins
from zenagent.plugins.install import install_plugin, uninstall_plugin
from zenagent.plugins.state import credentials_for, set_credentials


def _manifest(plugin_id="zen.demo"):
    return {
        "schemaVersion": 1,
        "id": plugin_id,
        "name": "Demo",
        "version": "0.1.0",
        "description": "demo plugin",
        "contributes": {"mcpServers": {}, "skills": "skills"},
        "credentials": [{"key": "APP_ID", "label": "App ID"}],
        "setup": {},
    }


def _make_plugin(dirpath, plugin_id="zen.demo"):
    dirpath.mkdir(parents=True)
    (dirpath / "manifest.json").write_text(json.dumps(_manifest(plugin_id)), encoding="utf-8")
    guide = dirpath / "skills" / "demo-guide"
    guide.mkdir(parents=True)
    (guide / "SKILL.md").write_text("demo", encoding="utf-8")
    return dirpath


def _write_zip(target, members: dict[str, bytes]):
    with zipfile.ZipFile(target, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return target


def test_discover_skips_broken_manifest(tmp_path):
    _make_plugin(tmp_path / "a")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "manifest.json").write_text("{not json", encoding="utf-8")
    manifests, warnings = discover_plugins(tmp_path)
    assert [m.id for m in manifests] == ["zen.demo"]
    assert len(warnings) == 1
    assert warnings[0].startswith("plugin b:")


def test_install_directory_and_reinstall(tmp_path):
    src = tmp_path / "src"
    _make_plugin(src)
    manifest = install_plugin(src, root=tmp_path / "installed")
    assert manifest.id == "zen.demo"
    assert (tmp_path / "installed" / "zen.demo" / "manifest.json").is_file()
    (src / "setup.md").write_text("v2", encoding="utf-8")
    install_plugin(src, root=tmp_path / "installed")
    assert (tmp_path / "installed" / "zen.demo" / "setup.md").read_text() == "v2"


def test_install_requires_manifest(tmp_path):
    with pytest.raises(ValueError):
        install_plugin(tmp_path, root=tmp_path / "installed")
    assert not (tmp_path / "installed").exists()


def test_install_rejects_invalid_manifest(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    data = _manifest("Zen.Demo")
    (src / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        install_plugin(src, root=tmp_path / "installed")


def test_zip_traversal_rejected_and_nothing_written(tmp_path):
    archive = _write_zip(
        tmp_path / "bad.zip",
        {
            "manifest.json": json.dumps(_manifest()).encode(),
            "../evil.txt": b"pwned",
        },
    )
    with pytest.raises(ValueError):
        install_plugin(archive, root=tmp_path / "installed")
    assert not (tmp_path / "installed").exists()


def test_zip_absolute_member_rejected(tmp_path):
    archive = _write_zip(
        tmp_path / "bad.zip",
        {"/tmp/evil.txt": b"pwned"},
    )
    with pytest.raises(ValueError):
        install_plugin(archive, root=tmp_path / "installed")


def test_zip_symlink_member_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps(_manifest()))
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        handle.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError):
        install_plugin(archive, root=tmp_path / "installed")


def test_zip_size_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "MAX_PLUGIN_BYTES", 10)
    archive = _write_zip(
        tmp_path / "big.zip",
        {"manifest.json": json.dumps(_manifest()).encode()},
    )
    with pytest.raises(ValueError):
        install_plugin(archive, root=tmp_path / "installed")


def test_zip_member_count_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "MAX_ZIP_MEMBERS", 1)
    archive = _write_zip(
        tmp_path / "many.zip",
        {
            "manifest.json": json.dumps(_manifest()).encode(),
            "setup.md": b"hi",
        },
    )
    with pytest.raises(ValueError):
        install_plugin(archive, root=tmp_path / "installed")


def test_zip_single_toplevel_directory_installs(tmp_path):
    archive = _write_zip(
        tmp_path / "ok.zip",
        {
            "zen.demo/manifest.json": json.dumps(_manifest()).encode(),
            "zen.demo/skills/demo-guide/SKILL.md": b"demo",
        },
    )
    manifest = install_plugin(archive, root=tmp_path / "installed")
    assert manifest.id == "zen.demo"
    assert (tmp_path / "installed" / "zen.demo" / "manifest.json").is_file()
    assert (tmp_path / "installed" / "zen.demo" / "skills" / "demo-guide" / "SKILL.md").is_file()


def test_uninstall_removes_dir_and_credentials(tmp_path):
    src = tmp_path / "src"
    _make_plugin(src)
    root = tmp_path / "installed"
    install_plugin(src, root=root)
    set_credentials("zen.demo", {"APP_ID": "cli_1"})
    assert credentials_for("zen.demo") == {"APP_ID": "cli_1"}
    uninstall_plugin("zen.demo", root=root)
    assert not (root / "zen.demo").exists()
    assert "plugin:zen.demo:APP_ID" not in settings.load().api_keys


def test_uninstall_unknown_plugin_rejected(tmp_path):
    with pytest.raises(ValueError):
        uninstall_plugin("zen.missing", root=tmp_path / "installed")
