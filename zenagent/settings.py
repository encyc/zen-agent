"""User settings and API keys, written by the app instead of hand-edited files.

`~/.zenagent/config.toml` is *configuration* (default model, providers, tools).
Secrets and UI-toggled preferences live here instead:

    ~/.zenagent/settings.json   (mode 0600)
    {"api_keys": {"DEEPSEEK_API_KEY": "..."}, "prefs": {"search_provider": "deepseek"}}

Precedence is deliberate: **this store wins over the environment**, because the
value was set explicitly by the user inside the app. The environment stays as
the fallback for CI/headless runs, and `config.toml` is only read once for
migration (older builds parked keys there, where nothing ever read them).

No UI imports live here: the model resolver (`app/support.py`) and the search
providers both read the same store.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

__all__ = [
    "DOCUMENT_VERSION",
    "SettingsStore",
    "clear_cache",
    "describe",
    "get_api_key",
    "get_pref",
    "load",
    "save",
    "set_api_key",
    "set_pref",
]

#: Bumped when the document shape changes; ``load`` accepts older documents.
DOCUMENT_VERSION = 1

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_LEGACY_LINE = re.compile(r'^\s*([A-Z][A-Z0-9_]*_API_KEY)\s*=\s*"([^"]+)"\s*$')

_cache: dict | None = None


def settings_path() -> Path:
    """``ZENAGENT_SETTINGS_PATH`` overrides the location (tests, sandboxes)."""
    override = os.environ.get("ZENAGENT_SETTINGS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".zenagent" / "settings.json"


def config_path() -> Path:
    return Path.home() / ".zenagent" / "config.toml"


class SettingsStore:
    """The parsed file plus the two accessors the app needs.

    ``problem`` is non-fatal: a corrupt file is reported (and surfaced in
    /settings) while the environment keeps working as the fallback. Never a
    silent reset — that is how keys mysteriously "disappear".
    """

    def __init__(
        self,
        api_keys: dict[str, str] | None = None,
        prefs: dict | None = None,
        problem: str | None = None,
    ) -> None:
        self.api_keys: dict[str, str] = dict(api_keys or {})
        self.prefs: dict = dict(prefs or {})
        self.problem = problem

    def to_json(self) -> dict:
        return {"version": DOCUMENT_VERSION, "api_keys": self.api_keys, "prefs": self.prefs}


def load(*, fresh: bool = False) -> SettingsStore:
    global _cache
    if _cache is not None and not fresh:
        return SettingsStore(**_cache)
    path = settings_path()
    data: dict = {}
    problem: str | None = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                problem = f"{path}: expected a JSON object"
                data = {}
        except (json.JSONDecodeError, OSError) as exc:
            problem = f"{path}: {exc}"
            data = {}
    store = SettingsStore(data.get("api_keys"), data.get("prefs"), problem=problem)
    if _migrate_legacy(store):
        save(store)
    _cache = {"api_keys": store.api_keys, "prefs": store.prefs, "problem": store.problem}
    return store


def save(store: SettingsStore) -> None:
    """Publish atomically: private temp sibling, single rename, mode 0600.

    A reader never observes a half-written file and a failed write leaves the
    previous document untouched (same contract as omp's ``atomic-file.ts`` and
    dsh's credential store).
    """
    global _cache
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(_DIR_MODE)
    except OSError:
        pass

    payload = json.dumps(store.to_json(), indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(_FILE_MODE)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    _cache = {"api_keys": store.api_keys, "prefs": store.prefs, "problem": None}


def clear_cache() -> None:
    """Drop the in-process copy (tests, or after an external edit)."""
    global _cache
    _cache = None


def get_api_key(env_name: str) -> str | None:
    """Stored key wins; the environment is the fallback."""
    store = load()
    value = store.api_keys.get(env_name)
    if value:
        return value
    env_value = os.environ.get(env_name)
    return env_value.strip() if env_value and env_value.strip() else None


def set_api_key(env_name: str, value: str) -> None:
    store = load()
    cleaned = value.strip()
    if cleaned:
        store.api_keys[env_name] = cleaned
    else:
        store.api_keys.pop(env_name, None)
    save(store)


def get_pref(name: str, default=None):
    return load().prefs.get(name, default)


def set_pref(name: str, value) -> None:
    store = load()
    if value is None:
        store.prefs.pop(name, None)
    else:
        store.prefs[name] = value
    save(store)


def describe() -> dict:
    """Non-secret status for UI and logs: which keys exist, never their values."""
    store = load()
    return {
        "path": str(settings_path()),
        "keys": {name: "set" for name in sorted(store.api_keys)},
        "prefs": dict(store.prefs),
        "problem": store.problem,
    }


def _migrate_legacy(store: SettingsStore) -> bool:
    """Import ``KEY="..."`` lines older builds left in ``config.toml``.

    Those lines were valid TOML but nothing read them, so users believed their
    keys were configured while the app fell back to environment variables.
    Importing keeps the file untouched and makes the keys take effect.
    """
    path = config_path()
    if not path.is_file():
        return False
    changed = False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        match = _LEGACY_LINE.match(line)
        if match is None:
            continue
        name, value = match.group(1), match.group(2).strip()
        if name not in store.api_keys and value:
            store.api_keys[name] = value
            changed = True
    return changed
