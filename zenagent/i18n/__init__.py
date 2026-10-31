"""Tiny stdlib-only i18n: dict catalogs, named placeholders, lite plurals.

Why not gettext: this project ships with no build step and no new dependencies,
and the UI vocabulary is small enough that flat Python dicts stay reviewable.
English is the authoritative catalog — every key must exist there; a missing
Chinese key falls back to English, and a missing English key falls back to the
key itself so a typo is visible instead of silently blanking a label.

Catalog keys are dotted and namespaced by surface: ``tui.*`` (Textual widgets
and screens), ``cmd.*`` (slash-command descriptions), ``perm.*`` (approval and
ask dialogs), ``repl.*`` (line REPL output). Plurals use ``<key>.one`` /
``<key>.other`` through :func:`tn`.

Locale resolution order: ``--lang`` flag > saved ``language`` preference >
``ZENAGENT_LANG`` > ``LANG``/``LC_ALL``/``LC_MESSAGES`` (``zh*`` → zh) > en.
This module is a leaf: no textual, no rich, no app imports.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from zenagent.i18n.en import CATALOG as _EN
from zenagent.i18n.zh import CATALOG as _ZH

__all__ = [
    "LANGUAGE_CHOICES",
    "active_locale",
    "available",
    "resolve_language",
    "set_locale",
    "t",
    "tn",
]

#: The user-facing vocabulary of the ``language`` setting and ``--lang`` flag.
LANGUAGE_CHOICES: tuple[str, ...] = ("auto", "en", "zh")

_DEFAULT = "en"
_CATALOGS: dict[str, dict[str, str]] = {"en": _EN, "zh": _ZH}
_active = _DEFAULT


def _catalogs() -> dict[str, dict[str, str]]:
    """The installed catalogs (tests swap this to probe the fallback chain)."""
    return _CATALOGS


def available() -> tuple[str, ...]:
    """Installed locale codes, English first."""
    return tuple(_CATALOGS)


def active_locale() -> str:
    """The locale every ``t()`` call currently reads."""
    return _active


def _normalize(raw: str | None) -> str | None:
    """Map a locale-ish string (``zh_CN.UTF-8``, ``zh-Hans``) to en/zh."""
    spec = (raw or "").strip().lower().replace("_", "-")
    if not spec:
        return None
    if spec.startswith("zh"):
        return "zh"
    if spec.startswith("en"):
        return "en"
    return None


def _environment_language(environ: Mapping[str, str]) -> str:
    for name in ("ZENAGENT_LANG", "LANG", "LC_ALL", "LC_MESSAGES"):
        code = _normalize(environ.get(name, ""))
        if code is not None:
            return code
    return _DEFAULT


def resolve_language(
    flag: str | None = None,
    *,
    pref: str | None = None,
    environ: Mapping[str, str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Pick a locale: explicit flag, then the preference, then the environment."""
    env = os.environ if environ is None else environ
    for source in (flag, pref):
        raw = (source or "").strip().lower()
        if not raw:
            continue
        if raw == "auto":
            return _environment_language(env)
        code = _normalize(raw)
        if code is not None:
            return code
        if warnings is not None:
            warnings.append(
                f"unknown language {raw!r} (choose auto|en|zh); using en"
            )
        return _DEFAULT
    return _environment_language(env)


def set_locale(spec: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
    """Activate a locale for subsequent ``t()``/``tn()`` calls."""
    global _active
    raw = (spec or "").strip().lower()
    if raw in ("", "auto"):
        _active = _environment_language(os.environ if environ is None else environ)
    else:
        _active = _normalize(raw) or _DEFAULT
    return _active


def _lookup(locale: str, key: str) -> str | None:
    return _catalogs().get(locale, {}).get(key)


def t(message_key: str, **fmt: object) -> str:
    """Translate ``message_key``; fall back to English, then to the key itself.

    The key parameter is positional-only in spirit (it is named so a placeholder
    called ``key`` or ``n`` can still be passed as a format argument).
    """
    text = _lookup(_active, message_key)
    if text is None:
        text = _lookup(_DEFAULT, message_key)
    if text is None:
        return message_key
    return text.format(**fmt) if fmt else text


def tn(message_key: str, count: int, **fmt: object) -> str:
    """Plural-aware translate: ``<key>.one`` for 1, ``<key>.other`` otherwise."""
    suffix = "one" if count == 1 else "other"
    text = _lookup(_active, f"{message_key}.{suffix}") or _lookup(
        _DEFAULT, f"{message_key}.{suffix}"
    )
    if text is None:
        return t(message_key, n=count, **fmt)
    return text.format(n=count, **fmt)
