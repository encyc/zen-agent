"""Is there a newer ``zenagent-py`` on PyPI? (Interactive TUI only.)

The TUI reads a small local cache at startup and, when the cache is older than
a day, refreshes it from PyPI in one background worker; if the published
version is newer than the installed one it prints a single quiet line in the
transcript. Headless, ``--json``, REPL and RPC modes never call into this
module, and every failure here is silent: a version check may never delay or
break a session.

Cache format (all keys optional)::

    {"checked_at": <epoch seconds>, "latest": "0.4.0", "notified": "0.4.0"}

``ZENAGENT_NO_UPDATE_CHECK`` disables the whole feature and
``ZENAGENT_UPDATE_CACHE_PATH`` relocates the cache (tests use both).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

__all__ = [
    "CACHE_TTL_S",
    "PYPI_URL",
    "REQUEST_TIMEOUT_S",
    "cache_path",
    "compare_versions",
    "fetch_latest",
    "format_notice",
    "installed_version",
    "is_disabled",
    "mark_notified",
    "pending_notice",
    "read_cache",
    "refresh",
    "upgrade_command",
]

#: The only version source; ``info.version`` is the published release.
PYPI_URL = "https://pypi.org/pypi/zenagent-py/json"
#: Seconds a cached answer is trusted before another network check.
CACHE_TTL_S = 24 * 60 * 60
#: Network budget for the PyPI lookup.
REQUEST_TIMEOUT_S = 3.0

_RELEASE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")
_PRE = re.compile(r"^[-._]?(a|alpha|b|beta|c|rc|pre|preview)(\d*)(?:[-._]?dev(\d*))?$")
_POST = re.compile(r"^[-._]?(post|rev|r)(\d*)(?:[-._]?dev(\d*))?$")
_DEV = re.compile(r"^[-._]?dev(\d*)$")

_PRE_RANK = {"a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}
# Sort categories: a development release precedes any pre-release, a plain
# release precedes its own post-releases.
_DEV_ONLY, _PRE_RELEASE, _FINAL, _POST_RELEASE = 0, 1, 2, 3


def cache_path() -> Path:
    """Where the last PyPI answer is cached."""
    override = os.environ.get("ZENAGENT_UPDATE_CACHE_PATH")
    if override:
        return Path(override)
    return Path.home() / ".zenagent" / "update.json"


def is_disabled() -> bool:
    """True when the user turned update checks off."""
    return os.environ.get("ZENAGENT_NO_UPDATE_CHECK", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def read_cache() -> dict:
    """The cached answer, or ``{}`` when it is missing, corrupt or not an object."""
    try:
        payload = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(payload: dict) -> None:
    """Atomically replace the cache file (mode 0600). Raises ``OSError``."""
    path = cache_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _segments(digits: str) -> list[int]:
    return [int(part) for part in digits.split(".")]


def _suffix_key(rest: str) -> tuple[int, int, int, int]:
    """Ordering key for the part after the release numbers.

    Categories sort dev-only < pre-release < final < post-release; ``dev_flag``
    keeps ``1.0rc1.dev1`` below ``1.0rc1`` (and the same for post-releases).
    Unrecognized suffixes are ignored, so ``1.2.x`` compares as ``1.2``.
    """
    if not rest:
        return (_FINAL, 0, 0, 1)
    dev = _DEV.match(rest)
    if dev is not None:
        return (_DEV_ONLY, 0, int(dev.group(1) or 0), 0)
    pre = _PRE.match(rest)
    if pre is not None:
        attached_dev = 0 if pre.group(3) is not None else 1
        return (_PRE_RELEASE, _PRE_RANK[pre.group(1)], int(pre.group(2) or 0), attached_dev)
    post = _POST.match(rest)
    if post is not None:
        attached_dev = 0 if post.group(3) is not None else 1
        return (_POST_RELEASE, 0, int(post.group(2) or 0), attached_dev)
    return (_FINAL, 0, 0, 1)


def _parse_version(raw: str) -> tuple[list[int], tuple[int, int, int, int]]:
    text = raw.strip()
    if text[:1] in {"v", "V"}:
        text = text[1:]
    text = text.split("+", 1)[0]
    match = _RELEASE.match(text)
    if match is None:
        return [0], (_FINAL, 0, 0, 1)
    release = _segments(match.group(1))
    return release, _suffix_key(match.group(2))


def compare_versions(a: str, b: str) -> int:
    """Compare two release strings PEP 440-style; returns -1, 0 or 1 and never raises."""
    release_a, suffix_a = _parse_version(a)
    release_b, suffix_b = _parse_version(b)
    for index in range(max(len(release_a), len(release_b))):
        left = release_a[index] if index < len(release_a) else 0
        right = release_b[index] if index < len(release_b) else 0
        if left != right:
            return -1 if left < right else 1
    if suffix_a != suffix_b:
        return -1 if suffix_a < suffix_b else 1
    return 0


def _editable(dist: importlib.metadata.Distribution) -> bool:
    try:
        raw = dist.read_text("direct_url.json")
    except (OSError, ValueError):
        return False
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    dir_info = payload.get("dir_info")
    return isinstance(dir_info, dict) and dir_info.get("editable") is True


def installed_version() -> str | None:
    """The installed distribution version, or ``None`` when it is not a real install.

    A source checkout installed with ``uv sync`` is editable; telling its owner
    to upgrade from PyPI would be wrong, so such installs never check.
    """
    try:
        dist = importlib.metadata.distribution("zenagent-py")
    except importlib.metadata.PackageNotFoundError:
        return None
    if _editable(dist):
        return None
    return dist.version


def _update_cache(**updates: object) -> None:
    """Merge ``updates`` into the cache; a failed write never breaks the caller."""
    payload = dict(read_cache())
    payload.update(updates)
    try:
        _write_cache(payload)
    except OSError:
        pass


async def fetch_latest(
    *,
    timeout_s: float = REQUEST_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """The version PyPI publishes, or ``None`` when it cannot be read."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": "zenagent update-check"},
        ) as client:
            response = await client.get(PYPI_URL)
            response.raise_for_status()
            data = response.json()
        version = data["info"]["version"]
    except Exception:  # noqa: BLE001 - a version check never breaks a session
        return None
    return version if isinstance(version, str) and version else None


def pending_notice(*, installed: str | None = None) -> str | None:
    """A newer version the user has not been told about yet, from the cache only."""
    if is_disabled():
        return None
    current = installed if installed is not None else installed_version()
    if not current:
        return None
    cache = read_cache()
    latest = cache.get("latest")
    if not isinstance(latest, str) or not latest:
        return None
    if compare_versions(latest, current) <= 0:
        return None
    if cache.get("notified") == latest:
        return None
    return latest


async def refresh(
    *,
    installed: str | None = None,
    fetch: Callable[[], Awaitable[str | None]] | None = None,
    now: float | None = None,
) -> str | None:
    """Refresh the cache when it is stale; return a version worth notifying.

    A failed lookup still stamps ``checked_at`` so an offline machine waits the
    full TTL instead of paying the timeout on every launch.
    """
    if is_disabled():
        return None
    current_time = time.time() if now is None else now
    cache = read_cache()
    checked_at = cache.get("checked_at")
    if isinstance(checked_at, (int, float)) and current_time - checked_at < CACHE_TTL_S:
        return None
    try:
        latest = await (fetch or fetch_latest)()
    except Exception:  # noqa: BLE001 - a failed lookup just backs off
        latest = None
    if not isinstance(latest, str) or not latest:
        _update_cache(checked_at=current_time)
        return None
    _update_cache(checked_at=current_time, latest=latest)
    current = installed if installed is not None else installed_version()
    if not current:
        return None
    if compare_versions(latest, current) <= 0:
        return None
    if cache.get("notified") == latest:
        return None
    return latest


def mark_notified(version: str) -> None:
    """Remember that this version was already shown, so it is not shown again."""
    _update_cache(notified=version)


def upgrade_command() -> str:
    """The command that upgrades the running installation."""
    prefix = sys.prefix.replace(os.sep, "/")
    if "/uv/tools/" in prefix:
        return "uv tool upgrade zenagent-py"
    if "/pipx/venvs/" in prefix:
        return "pipx upgrade zenagent-py"
    return 'python -m pip install -U "zenagent-py[cli]"'


def format_notice(version: str, installed: str) -> str:
    """The one quiet transcript line the TUI shows."""
    return f"Update available: v{version} (you have v{installed}) · {upgrade_command()}"
