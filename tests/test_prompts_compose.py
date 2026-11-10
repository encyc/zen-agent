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


def test_zen_note_only_in_zen_mode() -> None:
    ask = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="ask")).text
    zen = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="zen")).text
    assert "ZEN MODE:" not in ask
    assert zen.startswith(ask)
    assert "ZEN MODE:" in zen




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


def test_memory_index_block_renders_when_present() -> None:
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=(),
            memory_index="- a3f2 依赖选型\n- b4c1 测试入口",
        )
    )
    assert "## Project memory" in rendered.text
    assert "- a3f2 依赖选型" in rendered.text
    assert "- b4c1 测试入口" in rendered.text
    assert any(block.id == "memory_index" for block in rendered.blocks)


def test_memory_index_block_absent_when_empty() -> None:
    rendered = compose_system(PromptContext(cwd=CWD, tool_names=(), memory_index=""))
    assert "## Project memory" not in rendered.text
    assert all(block.id != "memory_index" for block in rendered.blocks)


def test_memory_index_comes_after_skills() -> None:
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=("todo",),
            skill_entries="- skill-a",
            memory_index="- a3f2 note",
        )
    )
    ids = [block.id for block in rendered.blocks]
    assert ids.index("skills") < ids.index("memory_index")
