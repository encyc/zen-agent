"""TUI: /skill:<name> hints, submit injection and the expandable chip."""

from __future__ import annotations

from pathlib import Path

from conftest import text_stream

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.command_hints import CommandHints
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.rows import NoticeRow
from zenagent.cli.tui.widgets.skill_block import SkillBlock
from zenagent.skills import Skill
from zenagent.types import UserMessage


def make_skill(tmp_path: Path) -> Skill:
    directory = tmp_path / "skills" / "pdf-report"
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    body = "Do the report thing."
    path.write_text(
        f"---\nname: pdf-report\ndescription: Build reports\n---\n{body}\n"
    )
    return Skill(
        name="pdf-report", description="Build reports", path=path, body=body
    )


async def make_app(runtime_factory, tmp_path: Path) -> ZenAgentApp:
    runtime = await runtime_factory(stream_fn=text_stream("done"))
    runtime.skills = [make_skill(tmp_path)]
    return ZenAgentApp(runtime)


async def _type(pilot, text: str) -> None:
    for character in text:
        await pilot.press(character)
    await pilot.pause()


async def test_line_start_hints_include_skill_commands(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.query_one(PromptInput).focus()
        await _type(pilot, "/skill:p")

        hints = app.query_one(CommandHints)
        assert hints.display is True
        assert "skill:pdf-report" in hints.plain
        assert "skill · Build reports" in hints.plain
        assert any(name == "skill:pdf-report" for name, _ in hints.matches)


async def test_mid_sentence_hints_include_skills_only(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.query_one(PromptInput).focus()
        await _type(pilot, "please /p")

        hints = app.query_one(CommandHints)
        assert hints.display is True
        assert "skill:pdf-report" in hints.plain
        assert "mode" not in hints.plain, "sentence-middle only offers skills"


async def test_unknown_skill_submission_is_a_notice(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.on_prompt_input_submitted(
            PromptInput.Submitted("/skill:nope do it")
        )
        await pilot.pause()

        assert app.runtime.agent.messages == []
        assert any(
            "unknown skill: nope" in row.plain for row in app.query(NoticeRow)
        )


async def test_submitting_a_skill_injects_the_body_and_shows_a_chip(
    runtime_factory, tmp_path
):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.on_prompt_input_submitted(
            PromptInput.Submitted("/skill:pdf-report review it")
        )
        await app.chat.wait_for_idle()
        await pilot.pause()

        (user_message,) = [
            message
            for message in app.runtime.agent.messages
            if isinstance(message, UserMessage)
        ]
        assert isinstance(user_message.content, str)
        assert user_message.content.startswith("[skill:pdf-report] review it")
        assert "Do the report thing." in user_message.content
        assert "[IMPORTANT: User invoked the" in user_message.content

        (chip,) = app.query(SkillBlock)
        assert chip.collapsed is True
        assert chip.plain.startswith("✦ skill:pdf-report")
        assert "review it" in chip.plain
        assert "Do the report thing." not in chip.plain

        chip.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert chip.collapsed is False
        assert "Do the report thing." in chip.plain

        await pilot.press("enter")
        await pilot.pause()
        assert chip.collapsed is True


async def test_tab_accepts_a_line_start_skill_hint(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        await _type(pilot, "/skill:p")
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "/skill:pdf-report "


async def test_tab_accepts_a_mid_sentence_skill_hint(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        await _type(pilot, "please /p")
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "please /skill:pdf-report "


async def test_mid_sentence_submission_also_injects(runtime_factory, tmp_path):
    app = await make_app(runtime_factory, tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.on_prompt_input_submitted(
            PromptInput.Submitted("please /skill:pdf-report review this")
        )
        await app.chat.wait_for_idle()
        await pilot.pause()

        (user_message,) = [
            message
            for message in app.runtime.agent.messages
            if isinstance(message, UserMessage)
        ]
        assert isinstance(user_message.content, str)
        assert user_message.content.startswith("[skill:pdf-report] please review this")
        assert "Do the report thing." in user_message.content
        (chip,) = app.query(SkillBlock)
        assert "please review this" in chip.plain
