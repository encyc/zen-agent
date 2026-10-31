"""compose_system: assembly order, conditions and the override hook."""

from __future__ import annotations

from zenagent.prompts import PromptContext, compose_system

from prompt_fixtures import CWD


def test_todo_line_only_with_todo_tool() -> None:
    with_todo = compose_system(PromptContext(cwd=CWD, tool_names=("todo",))).text
    without_todo = compose_system(PromptContext(cwd=CWD, tool_names=())).text
    assert "- Todo:" in with_todo
    assert "- Todo:" not in without_todo


def test_system_override_wins() -> None:
    prompt = compose_system(
        PromptContext(cwd=CWD, tool_names=(), system_override="OVERRIDE")
    ).text
    assert prompt == "OVERRIDE"


def test_rendered_blocks_are_recorded() -> None:
    rendered = compose_system(PromptContext(cwd=CWD, tool_names=()))
    assert rendered.blocks
    assert rendered.version
    assert all(block.chars > 0 for block in rendered.blocks)
