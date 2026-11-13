"""Argument-value hints: one vocabulary shared by the REPL and the TUI."""

from zenagent.app.modes import MODES
from zenagent.app.slash_args import ARG_VALUES, argument_values


def values(command: str, prefix: str = "") -> list[str]:
    return [value for value, _ in argument_values(command, prefix)]


def test_mode_values_are_all_modes_in_order():
    assert values("/mode") == list(MODES)
    assert values("/mode", "p") == ["plan"]
    assert values("/mode", "P") == ["plan"]


def test_thinking_values_match_the_command_levels():
    from zenagent.app.commands import THINKING_LEVELS

    assert values("/thinking") == list(THINKING_LEVELS)
    assert values("/thinking", "h") == ["high"]


def test_theme_values_match_the_appearance_choices():
    from zenagent.app.appearance import THEME_CHOICES

    assert values("/theme") == list(THEME_CHOICES)


def test_argument_filter_is_case_insensitive():
    assert values("/mode", "P") == ["plan"]
    assert values("/mode", "au") == ["auto"]


def test_commands_without_argument_values_return_nothing():
    assert argument_values("/help") == []


def test_unknown_command_returns_nothing():
    assert argument_values("/nope") == []


def test_shared_table_has_descriptions():
    assert all(description for _, description in ARG_VALUES["/mode"])
