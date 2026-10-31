"""The prompt renderer: one pass, no re-rendering, loud on missing values."""

from __future__ import annotations

import pytest

from zenagent.prompts.render import PromptRenderError, render, template_vars


def test_render_substitutes() -> None:
    assert render("a {{x}} b", {"x": "V"}) == "a V b"


def test_render_repeated_and_adjacent() -> None:
    assert render("{{x}}{{x}}/{{y}}", {"x": "1", "y": "2"}) == "11/2"


def test_missing_value_raises() -> None:
    with pytest.raises(PromptRenderError):
        render("{{x}}", {})


def test_template_vars_finds_names() -> None:
    assert template_vars("{{a}} {{b}} {{a}}") == {"a", "b"}


def test_values_are_not_rerendered() -> None:
    assert render("{{x}}", {"x": "keep {{y}} literal"}) == "keep {{y}} literal"


def test_newlines_preserved() -> None:
    assert render("l1\n{{x}}\nl3", {"x": "l2"}) == "l1\nl2\nl3"
