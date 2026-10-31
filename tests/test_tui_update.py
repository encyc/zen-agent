"""TUI update notice: cached value on mount, one notice per session."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from zenagent import update_check
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.rows import NoticeRow


def _cache_file() -> Path:
    return Path(os.environ["ZENAGENT_UPDATE_CACHE_PATH"])


def _update_notices(app) -> list[str]:
    return [row.plain for row in app.query(NoticeRow) if row.plain.startswith("Update available")]


async def test_cached_newer_version_shows_one_notice(runtime_factory, monkeypatch):
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)
    _cache_file().write_text(
        json.dumps({"checked_at": time.time(), "latest": "99.0.0", "notified": ""}),
        encoding="utf-8",
    )
    monkeypatch.setattr(update_check, "installed_version", lambda: "0.3.0")
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        notices = _update_notices(app)
        assert len(notices) == 1
        assert notices[0].startswith("Update available: v99.0.0 (you have v0.3.0) · ")
        assert "zenagent-py" in notices[0]
        assert json.loads(_cache_file().read_text(encoding="utf-8"))["notified"] == "99.0.0"


async def test_refresh_result_is_shown_when_cache_is_stale(runtime_factory, monkeypatch):
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)
    _cache_file().write_text(json.dumps({"checked_at": 0.0}), encoding="utf-8")
    monkeypatch.setattr(update_check, "installed_version", lambda: "0.3.0")

    async def fake_refresh(**kwargs: object) -> str:
        return "0.4.0"

    monkeypatch.setattr(update_check, "refresh", fake_refresh)
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._refresh_update_check()
        await app._refresh_update_check()
        await pilot.pause()
        notices = _update_notices(app)
        assert len(notices) == 1
        assert "v0.4.0" in notices[0]


async def test_disabled_check_skips_cache_and_network(runtime_factory):
    # conftest's autouse fixture leaves ZENAGENT_NO_UPDATE_CHECK=1 set.
    _cache_file().write_text(
        json.dumps({"checked_at": time.time(), "latest": "99.0.0"}),
        encoding="utf-8",
    )
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert _update_notices(app) == []


async def test_editable_install_does_not_network(runtime_factory, monkeypatch):
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)
    _cache_file().write_text(json.dumps({"checked_at": 0.0}), encoding="utf-8")
    monkeypatch.setattr(update_check, "installed_version", lambda: None)
    calls: list[int] = []

    async def fake_fetch() -> str:
        calls.append(1)
        return "0.9.0"

    monkeypatch.setattr(update_check, "fetch_latest", fake_fetch)
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert calls == []
        assert _update_notices(app) == []


async def test_startup_survives_a_broken_update_check(runtime_factory, monkeypatch):
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)

    def boom() -> str:
        raise RuntimeError("broken metadata")

    monkeypatch.setattr(update_check, "installed_version", boom)
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert _update_notices(app) == []


async def test_newer_notice_after_cached_one_is_suppressed(runtime_factory, monkeypatch):
    monkeypatch.delenv("ZENAGENT_NO_UPDATE_CHECK", raising=False)
    _cache_file().write_text(
        json.dumps({"checked_at": time.time(), "latest": "0.3.1", "notified": ""}),
        encoding="utf-8",
    )
    monkeypatch.setattr(update_check, "installed_version", lambda: "0.3.0")

    async def fake_refresh(**kwargs: object) -> str:
        return "0.4.0"

    monkeypatch.setattr(update_check, "refresh", fake_refresh)
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        notices = _update_notices(app)
        assert len(notices) == 1
        assert "v0.3.1" in notices[0]
        await app._refresh_update_check()
        await pilot.pause()
        assert len(_update_notices(app)) == 1
        assert json.loads(_cache_file().read_text(encoding="utf-8"))["notified"] == "0.3.1"


async def test_disabled_check_never_refreshes(runtime_factory, monkeypatch):
    # the autouse fixture keeps ZENAGENT_NO_UPDATE_CHECK=1 set
    _cache_file().write_text(
        json.dumps({"checked_at": 0.0, "latest": "99.0.0"}), encoding="utf-8"
    )

    async def boom(**kwargs: object) -> str:
        raise AssertionError("disabled checks must not refresh")

    def no_installed() -> None:
        raise AssertionError("disabled checks must not resolve the installed version")

    monkeypatch.setattr(update_check, "refresh", boom)
    monkeypatch.setattr(update_check, "installed_version", no_installed)
    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert _update_notices(app) == []
