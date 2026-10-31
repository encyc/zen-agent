"""Update check library: comparison, cache file and install guards."""

from __future__ import annotations

import json
import time

import httpx
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


def _write(payload: dict, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(tmp_path / "update.json"))
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)
    update_check._write_cache(payload)


def test_pending_notice_reports_newer_cached_version(tmp_path, monkeypatch):
    _write({"checked_at": time.time(), "latest": "0.4.0", "notified": ""}, tmp_path, monkeypatch)
    assert update_check.pending_notice(installed="0.3.0") == "0.4.0"
    update_check.mark_notified("0.4.0")
    assert update_check.pending_notice(installed="0.3.0") is None


def test_pending_notice_ignores_equal_or_older(tmp_path, monkeypatch):
    _write({"checked_at": time.time(), "latest": "0.3.0"}, tmp_path, monkeypatch)
    assert update_check.pending_notice(installed="0.3.0") is None
    assert update_check.pending_notice(installed="0.4.0") is None


async def test_refresh_skips_fresh_cache(tmp_path, monkeypatch):
    _write({"checked_at": time.time(), "latest": "0.4.0"}, tmp_path, monkeypatch)
    calls: list[int] = []

    async def fake_fetch() -> str:
        calls.append(1)
        return "0.5.0"

    assert await update_check.refresh(installed="0.3.0", fetch=fake_fetch) is None
    assert calls == []


async def test_refresh_fetches_updates_cache_and_returns_candidate(tmp_path, monkeypatch):
    _write({"checked_at": 0.0, "latest": "0.3.0"}, tmp_path, monkeypatch)

    async def fake_fetch() -> str:
        return "0.4.0"

    assert await update_check.refresh(installed="0.3.0", fetch=fake_fetch) == "0.4.0"
    cache = update_check.read_cache()
    assert cache["latest"] == "0.4.0"
    assert cache["checked_at"] > 0


async def test_refresh_ignores_older_candidate(tmp_path, monkeypatch):
    _write({"checked_at": 0.0, "latest": "0.4.0"}, tmp_path, monkeypatch)

    async def fake_fetch() -> str:
        return "0.3.5"

    assert await update_check.refresh(installed="0.4.0", fetch=fake_fetch) is None
    assert update_check.read_cache()["latest"] == "0.3.5"


async def test_refresh_failure_backs_off_without_new_version(tmp_path, monkeypatch):
    _write({"checked_at": 0.0, "latest": "0.3.1", "notified": "0.3.1"}, tmp_path, monkeypatch)

    async def failing_fetch() -> None:
        return None

    assert await update_check.refresh(installed="0.3.0", fetch=failing_fetch) is None
    cache = update_check.read_cache()
    assert cache["latest"] == "0.3.1"
    assert cache["checked_at"] > 0


async def test_refresh_swallows_fetch_exceptions(tmp_path, monkeypatch):
    _write({"checked_at": 0.0}, tmp_path, monkeypatch)

    async def boom() -> str:
        raise RuntimeError("network down")

    assert await update_check.refresh(installed="0.3.0", fetch=boom) is None


def test_mark_notified_survives_unwritable_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(tmp_path / "nope" / "update.json"))
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)

    def _boom(payload: dict) -> None:
        raise OSError("read-only fs")

    monkeypatch.setattr(update_check, "_write_cache", _boom)
    update_check.mark_notified("0.4.0")  # must not raise


async def test_fetch_latest_reads_pypi_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "pypi.org"
        return httpx.Response(200, json={"info": {"version": "0.4.0"}})

    transport = httpx.MockTransport(handler)
    assert await update_check.fetch_latest(transport=transport) == "0.4.0"


async def test_fetch_latest_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    assert await update_check.fetch_latest(transport=transport) is None


async def test_fetch_latest_returns_none_on_bad_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"info": {}})

    transport = httpx.MockTransport(handler)
    assert await update_check.fetch_latest(transport=transport) is None


def test_upgrade_command_matches_installer(monkeypatch):
    monkeypatch.setattr(update_check.sys, "prefix", "/home/u/.local/share/uv/tools/zenagent-py")
    assert update_check.upgrade_command() == "uv tool upgrade zenagent-py"
    monkeypatch.setattr(update_check.sys, "prefix", "/home/u/.local/pipx/venvs/zenagent-py")
    assert update_check.upgrade_command() == "pipx upgrade zenagent-py"
    monkeypatch.setattr(update_check.sys, "prefix", "/repo/.venv")
    assert update_check.upgrade_command() == 'python -m pip install -U "zenagent-py[cli]"'


def test_format_notice_pins_the_copy(monkeypatch):
    monkeypatch.setattr(update_check.sys, "prefix", "/repo/.venv")
    assert update_check.format_notice("0.4.0", "0.3.0") == (
        'Update available: v0.4.0 (you have v0.3.0) · '
        'python -m pip install -U "zenagent-py[cli]"'
    )
