"""The release workflow's updater fragment: which platform key a matrix leg
feeds, and which bundle file that platform's update actually points at.

The aggregation job merges one fragment per leg into `latest.json`; getting the
platform key or the artifact wrong means a platform silently never updates.
"""

from __future__ import annotations

import importlib.util
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

    names = ["ZenAgent_0.9.0_x64-setup.nsis.zip", "ZenAgent_0.9.0_x64-setup.nsis.zip.sig", "ZenAgent_0.9.0_x64-setup.exe"]
    assert fragment().pick_artifact(names) == "ZenAgent_0.9.0_x64-setup.nsis.zip"

    names = ["ZenAgent_0.9.0_amd64.AppImage.tar.gz", "ZenAgent_0.9.0_amd64.AppImage.tar.gz.sig", "ZenAgent_0.9.0_amd64.AppImage"]
    assert fragment().pick_artifact(names) == "ZenAgent_0.9.0_amd64.AppImage.tar.gz"


def test_pick_artifact_returns_none_when_the_bundle_has_no_updater_archive():
    assert fragment().pick_artifact(["ZenAgent_0.9.0_aarch64.dmg"]) is None


def test_fragment_url_points_at_the_release_asset():
    assert (
        fragment().asset_url("encyc/zen-agent", "v0.9.0", "ZenAgent_0.9.0_aarch64.app.tar.gz")
        == "https://github.com/encyc/zen-agent/releases/download/v0.9.0/ZenAgent_0.9.0_aarch64.app.tar.gz"
    )
