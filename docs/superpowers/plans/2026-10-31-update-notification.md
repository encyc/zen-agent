# Update Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The interactive TUI tells the user, once per new release and at most once a day over the network, that a newer `zenagent-py` exists on PyPI and how to upgrade it.

**Architecture:** One new library module `zenagent/update_check.py` owns version comparison, an atomically-written cache file, the PyPI lookup and the notice copy. `ZenAgentApp.on_mount` reads the cache synchronously (instant) and starts one async Textual worker that refreshes the cache only when it is stale, then appends a single `NoticeRow` to the transcript. No other mode (headless, JSON, REPL, RPC) is touched.

**Tech Stack:** Python 3.11+, `httpx.AsyncClient` (already a core dependency), Textual worker API, stdlib `importlib.metadata` / `json` / `regex` (no new dependencies), pytest + Textual `run_test`.

**Spec:** `docs/superpowers/specs/2026-10-31-update-notification-design.md`

## Global Constraints

- Kernel runtime dependencies stay `httpx` only — do not add `packaging`, do not import Textual/Rich from `zenagent/update_check.py`.
- Version source is exactly `https://pypi.org/pypi/zenagent-py/json`, field `info.version`; request timeout `3.0` s.
- Cache TTL is exactly `24 * 60 * 60` seconds; cache file `~/.zenagent/update.json`, mode `0600`, directory `0700`, atomic replace.
- Env overrides: `ZENAGENT_NO_UPDATE_CHECK` (`1`/`true`/`yes`, case-insensitive) disables everything; `ZENAGENT_UPDATE_CACHE_PATH` overrides the cache path.
- Notice copy is exactly `Update available: v{latest} (you have v{installed}) · {command}`.
- Upgrade command: `uv tool upgrade zenagent-py` when `"/uv/tools/" in sys.prefix`; `pipx upgrade zenagent-py` when `"/pipx/venvs/" in sys.prefix`; otherwise `python -m pip install -U "zenagent-py[cli]"`. Normalize `os.sep` to `/` before matching.
- Only the TUI shows the notice. `-p`, `--json`, REPL, RPC must not read the cache, print, or request anything.
- Every network/parse/cache failure is silent: no exception may escape `refresh`, `pending_notice`, `mark_notified`.
- The worktree has unrelated uncommitted changes — each commit step stages only the files that task lists.

## Review Focus

- Cache file corrupt / not a JSON object / parent directory unwritable: must behave as "never checked", never crash the TUI. Pinned by Task 1 cache tests and Task 2's `mark_notified` write-failure test.
- Editable install (`uv sync`, `uv run zen`) or missing distribution metadata: skip quietly, no network, no notice. Pinned by Task 1 `test_editable_install_skips` / `test_missing_metadata_skips`.
- PyPI returns a prerelease, post-release or unparsable version: comparator must never raise and must order dev < pre < final < post; junk parses as `0`. Pinned by Task 1 parametrized comparator tests.
- Slow/dead network, HTTP 500, or a JSON body without `info.version`: the worker gives up within the 3 s timeout, refreshes `checked_at` so the next 24 h stay offline, and shows nothing. Pinned by Task 2 `fetch_latest` and `test_refresh_failure_backs_off_without_new_version`.
- Same version must not produce two notices in one session (cached value then refreshed value, or mount plus a second refresh). Pinned by Task 3 `test_refresh_result_is_shown_when_cache_is_stale` calling the refresh twice.

---

### Task 1: Version comparison, cache file and install guards

**Files:**
- Create: `zenagent/update_check.py`
- Test: `tests/test_update_check.py`

**Interfaces:**
- Consumes: nothing.
- Produces (later tasks and the TUI rely on these exact names):
  - `PYPI_URL: str`, `CACHE_TTL_S: int`, `REQUEST_TIMEOUT_S: float`
  - `cache_path() -> Path`
  - `is_disabled() -> bool`
  - `read_cache() -> dict`
  - `_write_cache(payload: dict) -> None` (raises `OSError`; callers decide)
  - `compare_versions(a: str, b: str) -> int`
  - `installed_version() -> str | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update_check.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_update_check.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'zenagent.update_check'`.

- [ ] **Step 3: Implement the module skeleton in `zenagent/update_check.py`**

Create the module with a docstring explaining it is TUI-only and offline-safe, `from __future__ import annotations`, imports (`importlib.metadata`, `json`, `os`, `re`, `from pathlib import Path` — no `httpx` yet), the three constants, and these functions:

- `cache_path() -> Path`: `ZENAGENT_UPDATE_CACHE_PATH` when set, else `Path.home() / ".zenagent" / "update.json"`.
- `is_disabled() -> bool`: env value lowercased in `{"1", "true", "yes"}`.
- `read_cache() -> dict`: read `cache_path()`; return `{}` on `OSError` / `json.JSONDecodeError` / non-dict payload.
- `_write_cache(payload: dict) -> None`: `path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)`; write to a sibling temp file, `os.chmod(tmp, 0o600)`, `os.replace(tmp, path)`. Let `OSError` propagate; public callers swallow it.
- `compare_versions(a: str, b: str) -> int`: never raises, returns `-1 | 0 | 1`. Strip one leading `v`/`V` and any `+local` suffix, compare dot-separated release numbers as integers (missing segments count as `0`, so `1.0 == 1.0.0`), then compare suffixes with the order dev-only < pre (`a`/`alpha`, `b`/`beta`, `rc`/`c`/`pre`) < final < post (`post`/`rev`/`r`), with a dev segment attached to a pre/post sorting below the same pre/post without it; an unrecognized suffix counts as release `0`.
- `installed_version() -> str | None`: `importlib.metadata.distribution("zenagent-py")`; return `None` on `PackageNotFoundError`; return `None` when `direct_url.json` parses to `{"dir_info": {"editable": true}}` (catch parse errors and treat as regular); otherwise `dist.version`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update_check.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add zenagent/update_check.py tests/test_update_check.py
git commit -m "feat(update): version compare, cache file and install guards"
```

---

### Task 2: PyPI lookup, decision and notice copy

**Files:**
- Modify: `zenagent/update_check.py`
- Test: `tests/test_update_check.py` (append)

**Interfaces:**
- Consumes: Task 1's constants, `cache_path`, `is_disabled`, `read_cache`, `_write_cache`, `compare_versions`, `installed_version`.
- Produces:
  - `async def fetch_latest(*, timeout_s: float = REQUEST_TIMEOUT_S, transport: httpx.AsyncBaseTransport | None = None) -> str | None`
  - `def pending_notice(*, installed: str | None = None) -> str | None`
  - `async def refresh(*, installed: str | None = None, fetch: Callable[[], Awaitable[str | None]] | None = None, now: float | None = None) -> str | None`
  - `def mark_notified(version: str) -> None`
  - `def format_notice(version: str, installed: str) -> str`
  - `def upgrade_command() -> str`

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_update_check.py`:

```python
import time

import httpx


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

    assert await update_check.refresh(installed="0.3.0", fetch=fake_fetch) is None
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_update_check.py -q`
Expected: failures / `AttributeError: module 'zenagent.update_check' has no attribute 'pending_notice'` (and the missing async functions).

- [ ] **Step 3: Implement the decision layer**

Add to `zenagent/update_check.py` (signatures as in Interfaces). Extend the imports with `import sys`, `import time`, `from collections.abc import Awaitable, Callable`, and `import httpx`:

- `fetch_latest`: inside `try`, `async with httpx.AsyncClient(timeout=timeout_s, transport=transport, headers={"User-Agent": "zenagent update-check"}) as client: response = await client.get(PYPI_URL); response.raise_for_status(); data = response.json()`. Return `data["info"]["version"]` only when it is a non-empty `str`; on any `Exception` return `None`.
- `pending_notice`: `None` when disabled, when no installed version is available (`installed` parameter or `installed_version()`), when the cached `latest` is missing or not newer (`compare_versions(latest, installed) <= 0`), or when `cache.get("notified") == latest`. Otherwise return `latest`.
- `refresh`: `None` when disabled. `now = time.time()` when not given. Read cache; when `checked_at` is a number and `now - checked_at < CACHE_TTL_S`, return `None` without calling `fetch`. Otherwise `latest = await (fetch or fetch_latest)()` inside `try/except Exception: latest = None`. Write `checked_at=now` (keeping the previous `latest`) and return `None` when `latest` is not a non-empty string. Otherwise write `checked_at=now, latest=<latest>`; then return `None` unless an installed version exists, `compare_versions(latest, installed) > 0`, and `cache.get("notified") != latest`; return `latest`. All cache writes go through a small helper that merges into the current cache and swallows `OSError`.
- `mark_notified(version)`: merge `notified=version` into the cache and swallow `OSError`.
- `format_notice(version, installed)`: `f"Update available: v{version} (you have v{installed}) · {upgrade_command()}"`.
- `upgrade_command()`: read `sys.prefix.replace(os.sep, "/")` and return the three commands from Global Constraints.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update_check.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add zenagent/update_check.py tests/test_update_check.py
git commit -m "feat(update): cached PyPI lookup, notice copy and upgrade hint"
```

---

### Task 3: TUI mount hook and worker

**Files:**
- Modify: `zenagent/cli/tui/app.py` (imports block, `__init__`, `on_mount`, new methods next to `_refresh_status`)
- Modify: `tests/conftest.py` (new autouse fixture)
- Test: `tests/test_tui_update.py` (create)

**Interfaces:**
- Consumes: `update_check.is_disabled`, `pending_notice`, `refresh`, `installed_version`, `format_notice`, `mark_notified` (Task 2).
- Produces: `ZenAgentApp._maybe_show_cached_update() -> None`, `ZenAgentApp._show_update_notice(version: str) -> None`, `async ZenAgentApp._refresh_update_check() -> None`; `tests/conftest.py::isolated_update_check` autouse fixture.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_update.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tui_update.py -q`
Expected: failures — `AttributeError: 'ZenAgentApp' object has no attribute '_refresh_update_check'`, and the first test shows no notice.

- [ ] **Step 3: Add the conftest fixture and wire the app**

In `tests/conftest.py`, add after `isolated_tui_history`:

```python
@pytest.fixture(autouse=True)
def isolated_update_check(tmp_path, monkeypatch):
    """Update checks never run or touch the real cache file in tests."""
    monkeypatch.setenv("ZENAGENT_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(tmp_path / "update.json"))
```

In `zenagent/cli/tui/app.py`:

1. Import the module with the other `zenagent` imports: `from zenagent import update_check`.
2. In `__init__`, next to `self._refresh_pending = False`, add `self._update_notice_shown = False`.
3. At the end of `on_mount`, after `self.query_one(PromptInput).focus()`:

```python
        self._maybe_show_cached_update()
        if not update_check.is_disabled():
            self.run_worker(self._refresh_update_check(), exclusive=False)
```

4. Add these methods right after `on_mount` (if Textual rejects mounting during `on_mount`, wrap the `_maybe_show_cached_update()` call in `self.call_after_refresh(...)`; the tests define the contract):

```python
    def _maybe_show_cached_update(self) -> None:
        version = update_check.pending_notice()
        if version:
            self._show_update_notice(version)

    def _show_update_notice(self, version: str) -> None:
        if self._update_notice_shown:
            return
        installed = update_check.installed_version()
        if not installed:
            return
        notice = update_check.format_notice(version, installed)
        for transcript in self.query(TranscriptView):
            transcript.append_notice(notice)
        update_check.mark_notified(version)
        self._update_notice_shown = True

    async def _refresh_update_check(self) -> None:
        try:
            version = await update_check.refresh()
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return
        if version:
            self._show_update_notice(version)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_tui_update.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite to prove nothing else changed**

Run: `uv run pytest -q`
Expected: all pass (the autouse fixture keeps every other test offline).

- [ ] **Step 6: Commit**

```bash
git add zenagent/cli/tui/app.py tests/conftest.py tests/test_tui_update.py
git commit -m "feat(tui): quiet update notice from a cached PyPI check"
```

---

### Task 4: Documentation and the repo gate

**Files:**
- Modify: `README.md` (Install section, after the install code block and before the `Requires Python` paragraph)
- Modify: `docs/guide/tui.md` (new section after `## 模型管理`)

**Interfaces:**
- Consumes: nothing at runtime; documents Task 3's behavior.
- Produces: user-facing docs only.

- [ ] **Step 1: Add the README note**

Insert into `README.md` after the Install code fence:

```markdown
The interactive TUI checks PyPI at most once a day for a newer `zenagent-py`
and prints one quiet line in the transcript when one exists; `-p`/`--json`
never check. Disable it with `ZENAGENT_NO_UPDATE_CHECK=1`.
```

- [ ] **Step 2: Add the TUI guide section**

Insert into `docs/guide/tui.md` after `## 模型管理`:

```markdown
## 更新提醒

TUI 启动后如果 PyPI 上有更新的 `zenagent-py`，转录区会出现一行：

```
Update available: v0.4.0 (you have v0.3.0) · uv tool upgrade zenagent-py
```

- 本地缓存 `~/.zenagent/update.json` 24 小时内不重复联网；每个新版本只提醒一次，每次
  会话最多一条。
- 升级命令按安装方式给出（uv tool / pipx / pip）；`[cli]` 是 extra，核心 agent 与 TUI
  一起升级。
- `-p`、`--json`、REPL、RPC 不检查、不输出。
- 关闭：`ZENAGENT_NO_UPDATE_CHECK=1`。
```

- [ ] **Step 3: Run the repo gate**

Run: `bash scripts/check.sh`
Expected: ruff clean, full pytest suite passes.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/guide/tui.md
git commit -m "docs: document the TUI update notice and its kill switch"
```
