"""Renderer themes.

Pure data: the renderer reads style strings from a Theme instead of hard-coding
rich styles. Built-ins cover a default, a no-colour option for logs, and a
solarized-ish palette; a JSON file can add one without touching code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

__all__ = ["DEFAULT_THEME", "THEMES", "Theme", "load_theme"]


@dataclass
class Theme:
    """Style names (rich styles) used by the terminal renderer."""

    name: str = "default"
    thinking: str = "grey62"
    diff_add: str = "green"
    diff_del: str = "red"
    diff_hunk: str = "cyan"
    diff_meta: str = "bold"
    dim: str = "grey62"
    accent: str = "cyan"


DEFAULT_THEME = Theme()

THEMES: dict[str, Theme] = {
    "default": DEFAULT_THEME,
    "mono": Theme(
        name="mono",
        thinking="white",
        diff_add="white",
        diff_del="white",
        diff_hunk="white",
        diff_meta="bold",
        dim="white",
        accent="white",
    ),
    "solarized": Theme(
        name="solarized",
        thinking="#93a1a1",
        diff_add="#859900",
        diff_del="#dc322f",
        diff_hunk="#268bd2",
        diff_meta="bold",
        dim="#93a1a1",
        accent="#268bd2",
    ),
}


def _theme_from_mapping(name: str, raw: dict) -> Theme:
    known = {field.name for field in fields(Theme)} - {"name"}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"theme {name!r} has unknown keys {sorted(unknown)}")
    values = {key: str(value) for key, value in raw.items()}
    return Theme(name=name, **values)


def load_theme(spec: str | None = None, warnings: list[str] | None = None) -> Theme:
    """Resolve ``None`` / a built-in name / a JSON file into a Theme.

    A broken custom theme file falls back to the default (with a warning), since
    a bad colour file must not stop the session.
    """
    if spec is None or spec == "":
        return DEFAULT_THEME
    if spec in THEMES:
        return THEMES[spec]
    path = Path(spec).expanduser()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
                    "theme file must contain an object"
                )
            return _theme_from_mapping(path.stem, data)
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f"{path}: {exc}; using the default theme")
            return DEFAULT_THEME
    raise ValueError(
        f"unknown theme {spec!r} (built-in: {', '.join(sorted(THEMES))}; or a JSON path)"
    )


def theme_as_json(theme: Theme) -> str:
    """Pretty JSON for documentation and ``/theme export`` style output."""
    return json.dumps(asdict(theme), indent=2, ensure_ascii=False)
