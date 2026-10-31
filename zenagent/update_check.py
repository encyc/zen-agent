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
from pathlib import Path

__all__ = [
    "CACHE_TTL_S",
    "PYPI_URL",
    "REQUEST_TIMEOUT_S",
    "cache_path",
    "compare_versions",
    "installed_version",
    "is_disabled",
    "read_cache",
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
