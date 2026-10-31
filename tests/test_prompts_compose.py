"""compose_system: assembly order, conditions and the override hook."""

from __future__ import annotations

from prompt_fixtures import CWD

from zenagent.prompts import PromptContext, compose_system


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


def test_context_skipped_when_include_context_false() -> None:
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=(),
            context_text="### /repo/AGENTS.md\n- rule\n",
            include_context=False,
        )
    )
    assert "## Project instructions" not in rendered.text


def test_plan_note_only_in_plan_mode() -> None:
    ask = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="ask")).text
    plan = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="plan")).text
    assert "PLAN MODE:" not in ask
    assert plan.startswith(ask)
    assert "PLAN MODE:" in plan


def test_optional_parts_keep_their_order() -> None:
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=(),
            mode="plan",
            context_text="### /repo/AGENTS.md\n- rule\n",
            skill_entries="- alpha: Do alpha things.",
            extension_fragments=("EXT-ONE: keep answers short.",),
        )
    )
    text = rendered.text
    assert text.index("## Project instructions") < text.index("## Skills")
    assert text.index("## Skills") < text.index("EXT-ONE")
    assert text.index("EXT-ONE") < text.index("PLAN MODE:")
