"""Appearance tokens: one colour source for the TUI and the REPL."""

import pytest

from zenagent.app.appearance import (
    APPEARANCES,
    THEME_CHOICES,
    detect_dark,
    resolve_theme,
)


def _luminance(value: str) -> float:
    value = value.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def test_both_appearances_define_the_same_tokens():
    dark, light = APPEARANCES["dark"], APPEARANCES["light"]
    assert set(dark.tokens) == set(light.tokens)
    assert len(dark.tokens) >= 21
    assert dark.dark is True
    assert light.dark is False
    assert THEME_CHOICES == ("auto", "dark", "light")


def test_tokens_are_lowercase_hex():
    for appearance in APPEARANCES.values():
        for name, value in appearance.tokens.items():
            assert value == value.lower(), (appearance.name, name, value)
            assert len(value) == 7 and value.startswith("#"), (appearance.name, name)


def test_dark_tokens_keep_todays_values():
    tokens = APPEARANCES["dark"].tokens
    assert tokens["ink"] == "#0d1319"
    assert tokens["surface"] == "#141c24"
    assert tokens["accent"] == "#d9a86c"
    assert tokens["text"] == "#d7e0e8"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"COLORFGBG": "15;0"}, True),  # bright fg on black bg → dark terminal
        ({"COLORFGBG": "0;15"}, False),  # black fg on white bg → light terminal
        ({"COLORFGBG": "0"}, True),  # only the background index, black
        ({"COLORFGBG": "15"}, False),  # only the background index, white
        ({}, True),
        ({"COLORFGBG": "junk"}, True),
    ],
)
def test_detect_dark_uses_colorfgbg_background(environ, expected):
    assert detect_dark(environ) is expected


def test_resolve_theme_prefers_flag_then_pref_then_auto():
    assert resolve_theme("light", pref="dark").name == "light"
    assert resolve_theme(None, pref="light").name == "light"
    assert (
        resolve_theme(None, pref=None, environ={"COLORFGBG": "0;15"}).name == "light"
    )
    assert resolve_theme("auto", environ={"COLORFGBG": "0;15"}).name == "light"
    assert resolve_theme(None, pref=None, environ={}).name == "dark"


def test_resolve_theme_warns_once_and_falls_back():
    warnings: list[str] = []
    appearance = resolve_theme("nonsense", warnings=warnings)
    assert appearance.name == "dark"
    assert len(warnings) == 1
    assert "nonsense" in warnings[0]


def test_empty_specs_are_treated_as_unset():
    assert resolve_theme("", pref="light", environ={}).name == "light"
    assert resolve_theme("  ", pref="light", environ={}).name == "light"
    assert resolve_theme(None, pref="  ", environ={}).name == "dark"


def test_light_text_colours_meet_contrast_on_surface():
    tokens = APPEARANCES["light"].tokens
    surface = tokens["surface"]
    for name in ("text", "muted", "accent", "ok", "error", "warn"):
        assert _ratio(tokens[name], surface) >= 4.5, name
    assert _ratio(tokens["dim"], surface) >= 3.0


def test_dark_text_colours_meet_contrast_on_ink():
    tokens = APPEARANCES["dark"].tokens
    ink = tokens["ink"]
    for name in ("text", "muted", "accent", "ok", "error", "warn"):
        assert _ratio(tokens[name], ink) >= 4.5, name
    assert _ratio(tokens["dim"], ink) >= 3.0
