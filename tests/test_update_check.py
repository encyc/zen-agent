"""Update check library: comparison, cache file and install guards."""

from __future__ import annotations

import json

import pytest

from zenagent import update_check


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("0.3.0", "0.3.1", -1),
        ("0.9.0", "0.10.0", -1),
        ("0.4.0rc1", "0.4.0", -1),
        ("0.4.0.dev1", "0.4.0rc1", -1),
        ("0.4.0", "0.4.0.post1", -1),
        ("v0.3.0", "0.3.0", 0),
        ("1.0", "1.0.0", 0),
        ("garbage", "1.0.0", -1),
    ],
)
def test_compare_versions_orders_releases(left, right, expected):
    assert update_check.compare_versions(left, right) == expected


def test_cache_round_trip(tmp_path, monkeypatch):
    cache = tmp_path / "update.json"
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(cache))
    assert update_check.read_cache() == {}
    update_check._write_cache({"checked_at": 1.0, "latest": "0.4.0"})
    assert json.loads(cache.read_text(encoding="utf-8"))["latest"] == "0.4.0"
    assert cache.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["update.json"]  # no temp file left
    assert update_check.read_cache()["latest"] == "0.4.0"


def test_corrupt_cache_reads_as_empty(tmp_path, monkeypatch):
    cache = tmp_path / "update.json"
    cache.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(cache))
    assert update_check.read_cache() == {}


def test_update_check_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ZENAGENT_NO_UPDATE_CHECK", "TRUE")
    assert update_check.is_disabled() is True
    monkeypatch.setenv("ZENAGENT_NO_UPDATE_CHECK", "0")
    assert update_check.is_disabled() is False


def test_installed_version_from_metadata(monkeypatch):
    class _Dist:
        version = "0.3.0"

        def read_text(self, name: str) -> None:
            return None

    monkeypatch.setattr(update_check.importlib.metadata, "distribution", lambda name: _Dist())
    assert update_check.installed_version() == "0.3.0"


def test_editable_install_skips(monkeypatch):
    class _Dist:
        version = "0.3.0"

        def read_text(self, name: str) -> str:
            return '{"url": "file:///repo", "dir_info": {"editable": true}}'

    monkeypatch.setattr(update_check.importlib.metadata, "distribution", lambda name: _Dist())
    assert update_check.installed_version() is None


def test_missing_metadata_skips(monkeypatch):
    def _missing(name: str):
        raise update_check.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(update_check.importlib.metadata, "distribution", _missing)
    assert update_check.installed_version() is None
