"""TUI image attachments: /image, the chip bar, and sending the block."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.attachments import AttachmentChips, Chip
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def recording_stream(seen: list[list]):
    async def stream(model, messages, tools, options):
        seen.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="ok")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def _attach(app, pilot, path) -> None:
    prompt = app.query_one(PromptInput)
    prompt.focus()
    prompt.insert(f"/image {path}")
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def test_image_command_shows_a_chip_and_submits_the_image(
    tmp_path, runtime_factory
):
    seen: list[list] = []
    runtime = await runtime_factory(stream_fn=recording_stream(seen))
    app = ZenAgentApp(runtime)
    png = tmp_path / "pic.png"
    png.write_bytes(PNG)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await _attach(app, pilot, png)

        chips = app.query_one(AttachmentChips)
        assert chips.display is True
        assert any("image/png" in chip.plain for chip in app.query(Chip))

        prompt = app.query_one(PromptInput)
        prompt.insert("look")
        await pilot.press("enter")
        await app.chat.wait_for_idle()
        await pilot.pause()

        assert app.chat.attachments == [] and chips.display is False
        user = next(m for m in seen[-1] if m.to_json()["role"] == "user")
        assert any(block.to_json()["type"] == "image" for block in user.content)


async def test_clicking_a_chip_removes_the_attachment(tmp_path, fake_runtime):
    app = ZenAgentApp(fake_runtime)
    png = tmp_path / "pic.png"
    png.write_bytes(PNG)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await _attach(app, pilot, png)
        assert len(app.chat.attachments) == 1

        await pilot.click(app.query(Chip).first())
        await pilot.pause()

        assert app.chat.attachments == []
        assert app.query_one(AttachmentChips).display is False
