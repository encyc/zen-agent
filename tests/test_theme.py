"""Themes and keybindings: loading, fallbacks, and rendering."""

import json

import pytest

from zenagent.cli.keybindings import (
    DEFAULT_KEYBINDINGS,
    build_key_bindings,
    load_keybindings,
)
from zenagent.cli.render import render_diff
from zenagent.cli.theme import DEFAULT_THEME, THEMES, Theme, load_theme

# ---------------------------------------------------------------------------
# themes
# ---------------------------------------------------------------------------


def test_builtin_themes_load():
    assert load_theme(None) is DEFAULT_THEME
    assert load_theme("") is DEFAULT_THEME
    assert load_theme("mono").diff_add == "white"
    assert load_theme("solarized").diff_add == "#859900"
    assert set(THEMES) == {"default", "mono", "solarized"}


def test_unknown_theme_name_raises():
    with pytest.raises(ValueError, match="unknown theme"):
        load_theme("chartreuse")


def test_custom_theme_file_loads(tmp_path):
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"diff_add": "#00ff00", "dim": "#333333"}))
    theme = load_theme(str(path))
    assert theme.name == "mine"
    assert theme.diff_add == "#00ff00"
    assert theme.diff_del == DEFAULT_THEME.diff_del  # unspecified keeps the default


def test_broken_theme_file_falls_back_with_a_warning(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    warnings: list[str] = []
    assert load_theme(str(path), warnings) is DEFAULT_THEME
    assert warnings and "default theme" in warnings[0]


def test_theme_with_unknown_keys_is_rejected_then_falls_back(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"mystery": "red"}))
    warnings: list[str] = []
    assert load_theme(str(path), warnings) is DEFAULT_THEME
    assert warnings and "unknown keys" in warnings[0]


def test_render_diff_uses_theme_styles():
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
    themed = render_diff(diff, Theme(name="t", diff_add="white", diff_del="white", diff_hunk="white"))
    styles = {str(span.style) for span in themed.spans}
    assert "white" in styles
    assert "green" not in styles and "red" not in styles


def test_render_diff_default_theme_keeps_classic_colours():
    rendered = render_diff("+added\n-removed\n")
    styles = {str(span.style) for span in rendered.spans}
    assert {"green", "red"} <= styles


# ---------------------------------------------------------------------------
# keybindings
# ---------------------------------------------------------------------------


def test_defaults_when_no_file(tmp_path):
    assert load_keybindings(tmp_path / "missing.json") == DEFAULT_KEYBINDINGS


def test_custom_file_overrides_one_action(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"newline": "escape"}))
    bindings = load_keybindings(path)
    assert bindings["newline"] == "escape"
    assert bindings["submit"] == DEFAULT_KEYBINDINGS["submit"]


def test_broken_keybindings_fall_back_with_a_warning(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"unknown_action": "c-x"}))
    warnings: list[str] = []
    assert load_keybindings(path, warnings) == DEFAULT_KEYBINDINGS
    assert warnings and "unknown action" in warnings[0]


def test_invalid_json_falls_back(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("nope")
    warnings: list[str] = []
    assert load_keybindings(path, warnings) == DEFAULT_KEYBINDINGS
    assert warnings


def test_build_key_bindings_returns_bindings():
    from prompt_toolkit.key_binding import KeyBindings

    bindings = build_key_bindings({"newline": "c-j"})
    assert isinstance(bindings, KeyBindings)
    assert build_key_bindings() is not None


# ---------------------------------------------------------------------------
# appearance-derived themes (0.4.x light/dark)
# ---------------------------------------------------------------------------


def test_theme_from_appearance_maps_tokens():
    from zenagent.app.appearance import APPEARANCES
    from zenagent.cli.theme import theme_from_appearance

    theme = theme_from_appearance(APPEARANCES["light"])
    tokens = APPEARANCES["light"].tokens
    assert theme.name == "harbor-light"
    assert theme.diff_add == tokens["ok"]
    assert theme.diff_del == tokens["error"]
    assert theme.diff_hunk == tokens["accent"]
    assert theme.thinking == tokens["dim"]
    assert theme.dim == tokens["dim"]
    assert theme.accent == tokens["accent"]


def test_resolve_theme_spec_keeps_legacy_names(tmp_path):
    from zenagent.cli.theme import resolve_theme_spec

    assert resolve_theme_spec("mono").name == "mono"
    assert resolve_theme_spec("solarized").diff_add == "#859900"

    custom = tmp_path / "mine.json"
    custom.write_text(json.dumps({"diff_add": "#00ff00"}))
    assert resolve_theme_spec(str(custom)).diff_add == "#00ff00"


def test_resolve_theme_spec_uses_the_saved_preference():
    from zenagent.cli.theme import resolve_theme_spec

    theme = resolve_theme_spec(None, pref="light")
    assert theme.name == "harbor-light"
    assert resolve_theme_spec("dark", pref="light").name == "harbor-dark"


def test_resolve_theme_spec_treats_unknown_as_auto():
    from zenagent.cli.theme import resolve_theme_spec

    warnings: list[str] = []
    theme = resolve_theme_spec("chartreuse", warnings=warnings)
    assert theme.name == "harbor-dark"
    assert warnings and "chartreuse" in warnings[0]
