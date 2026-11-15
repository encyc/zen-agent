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


def test_auto_note_only_in_auto_mode() -> None:
    ask = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="ask")).text
    auto = compose_system(PromptContext(cwd=CWD, tool_names=(), mode="auto")).text
    assert "AUTO MODE:" not in ask
    assert auto.startswith(ask)
    assert "AUTO MODE:" in auto




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


def test_extension_sections_render_one_block_each() -> None:
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=(),
            extension_fragments=("LEGACY FRAGMENT",),
            extension_sections=(("style", "STYLE SECTION"), ("extra", "EXTRA SECTION")),
            include_mode=False,
        )
    )

    ids = [block.id for block in rendered.blocks]
    assert ids.count("extension.style") == 1
    assert ids.count("extension.extra") == 1
    assert "LEGACY FRAGMENT" in rendered.text
    assert "STYLE SECTION" in rendered.text and "EXTRA SECTION" in rendered.text
    # fragments land before the named sections; sections keep insertion order
    assert rendered.text.index("LEGACY FRAGMENT") < rendered.text.index(
        "STYLE SECTION"
    )
    assert rendered.text.index("STYLE SECTION") < rendered.text.index("EXTRA SECTION")


def test_extension_sections_absent_leaves_extension_block_untouched() -> None:
    without = compose_system(
        PromptContext(cwd=CWD, tool_names=(), include_mode=False)
    )
    with_only_fragments = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=(),
            extension_fragments=("LEGACY FRAGMENT",),
            include_mode=False,
        )
    )
    assert "LEGACY FRAGMENT" not in without.text
    assert not [b for b in without.blocks if b.id.startswith("extension.")]
    assert [b.id for b in with_only_fragments.blocks].count("extensions") == 1
