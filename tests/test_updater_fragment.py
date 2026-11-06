"""The release workflow's updater fragment: which platform key a matrix leg
feeds, and which bundle file that platform's update actually points at.

The aggregation job merges one fragment per leg into `latest.json`; getting the
platform key or the artifact wrong means a platform silently never updates.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def fragment():
    script = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "updater-fragment.py"
    spec = importlib.util.spec_from_file_location("zenagent_updater_fragment", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_platform_key_covers_the_six_matrix_targets():
    assert fragment().platform_key("aarch64-apple-darwin") == "darwin-aarch64"
    assert fragment().platform_key("x86_64-apple-darwin") == "darwin-x86_64"
    assert fragment().platform_key("x86_64-unknown-linux-gnu") == "linux-x86_64"
    assert fragment().platform_key("aarch64-unknown-linux-gnu") == "linux-aarch64"
    assert fragment().platform_key("x86_64-pc-windows-msvc") == "windows-x86_64"
    assert fragment().platform_key("aarch64-pc-windows-msvc") == "windows-aarch64"


def test_pick_artifact_takes_the_updater_archive_not_the_signer_file():
    names = ["ZenAgent_0.9.0_aarch64.app.tar.gz", "ZenAgent_0.9.0_aarch64.app.tar.gz.sig", "ZenAgent_0.9.0_aarch64.dmg"]
    assert fragment().pick_artifact(names) == "ZenAgent_0.9.0_aarch64.app.tar.gz"


def test_pick_artifact_returns_none_when_the_bundle_has_no_updater_archive():
    assert fragment().pick_artifact(["ZenAgent_0.9.0_aarch64.dmg"]) is None


def test_fragment_url_points_at_the_release_asset():
    assert (
        fragment().asset_url("encyc/zen-agent", "v0.9.0", "ZenAgent_0.9.0_aarch64.app.tar.gz")
        == "https://github.com/encyc/zen-agent/releases/download/v0.9.0/ZenAgent_0.9.0_aarch64.app.tar.gz"
    )


def test_pick_artifact_uses_the_file_the_updater_downloads(tmp_path):
    """The updater downloads the AppImage / NSIS installer itself (with a
    sibling `.sig`), not a tar.gz or zip wrapper — the release run proved it."""
    linux = ["ZenAgent_0.8.2_amd64.AppImage", "ZenAgent_0.8.2_amd64.AppImage.sig", "ZenAgent_0.8.2_amd64.deb"]
    assert fragment().pick_artifact(linux) == "ZenAgent_0.8.2_amd64.AppImage"

    windows = ["ZenAgent_0.8.2_x64-setup.exe", "ZenAgent_0.8.2_x64-setup.exe.sig"]
    assert fragment().pick_artifact(windows) == "ZenAgent_0.8.2_x64-setup.exe"

    mac = ["ZenAgent_x64.app.tar.gz", "ZenAgent_x64.app.tar.gz.sig", "ZenAgent_0.8.2_x64.dmg"]
    assert fragment().pick_artifact(mac) == "ZenAgent_x64.app.tar.gz"


def test_write_fragment_reads_the_signature_beside_the_artifact(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "appimage").mkdir(parents=True)
    artifact = bundle / "appimage" / "ZenAgent_0.8.2_amd64.AppImage"
    artifact.write_text("binary", encoding="utf-8")
    (bundle / "appimage" / "ZenAgent_0.8.2_amd64.AppImage.sig").write_text("sig-text\n", encoding="utf-8")
    out = tmp_path / "fragment.json"

    wrote = fragment().write_fragment(
        target="x86_64-unknown-linux-gnu",
        tag="v0.8.2",
        bundle_dir=bundle,
        repo="encyc/zen-agent",
        out=out,
    )
    assert wrote is True
    payload = json.loads(out.read_text())
    assert payload["platform"] == "linux-x86_64"
    assert payload["signature"] == "sig-text"
    assert payload["url"].endswith("/v0.8.2/ZenAgent_0.8.2_amd64.AppImage")


def test_write_fragment_reports_a_bundle_without_a_signature(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "appimage").mkdir(parents=True)
    (bundle / "appimage" / "ZenAgent_0.8.2_amd64.AppImage").write_text("binary", encoding="utf-8")
    assert (
        fragment().write_fragment(
            target="x86_64-unknown-linux-gnu",
            tag="v0.8.2",
            bundle_dir=bundle,
            repo="encyc/zen-agent",
            out=tmp_path / "f.json",
        )
        is False
    )
