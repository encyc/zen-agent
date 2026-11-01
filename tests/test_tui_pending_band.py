"""PendingBand: the dock band listing queued and steering messages.

The band renders two lanes (``Queued`` / ``Steering``) as one clipped line per
message, folds anything past six message rows, and click-promotes queued rows
only. Headers, steering rows and the hint line are inert.
"""

from zenagent.app.views import PendingMessage
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.pending_band import PendingBand


async def test_band_renders_groups_in_order(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending(
            [
                PendingMessage(id=1, lane="queue", text="first message"),
                PendingMessage(id=2, lane="queue", text="second message"),
                PendingMessage(id=3, lane="steer", text="stop that"),
            ],
            120,
        )
        await pilot.pause()

        lines = band.plain.splitlines()
        assert lines[0] == "Queued · 2"
        assert lines[1].strip().startswith("1. first message")
        assert lines[2].strip().startswith("2. second message")
        assert lines[3] == "Steering · 1"
        assert lines[4].strip().startswith("1. stop that")
        assert lines[-1].startswith("└ alt+↑ edit")
        assert band.display is True


async def test_band_hides_when_empty(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)

        band.set_pending([], 120)
        await pilot.pause()

        assert band.display is False
        assert band.plain == ""


async def test_band_folds_beyond_six_message_rows(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending(
            [PendingMessage(id=i, lane="queue", text=f"m{i}") for i in range(1, 8)],
            120,
        )
        await pilot.pause()

        lines = band.plain.splitlines()
        assert len([line for line in lines if line.strip()[:1].isdigit()]) == 6
        assert "… 1 more" in band.plain


async def test_band_clips_long_text_and_folds_newlines(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        band.set_pending([PendingMessage(id=1, lane="queue", text="a\nb" * 200)], 40)
        await pilot.pause()

        assert "↵" in band.plain
        assert all(len(line) <= 40 for line in band.plain.splitlines())


async def test_band_clicking_a_queued_row_calls_the_handler(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        seen: list[int] = []
        band.set_handler(seen.append)
        band.set_pending(
            [
                PendingMessage(id=7, lane="queue", text="first"),
                PendingMessage(id=8, lane="queue", text="second"),
            ],
            120,
        )
        await pilot.pause()

        await pilot.click(PendingBand, offset=(4, 1))   # 第二行 = 第一条消息
        await pilot.pause()

        assert seen == [7]


async def test_band_clicking_header_steer_row_or_hint_does_nothing(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(PendingBand)
        seen: list[int] = []
        band.set_handler(seen.append)
        band.set_pending(
            [
                PendingMessage(id=7, lane="queue", text="first"),
                PendingMessage(id=9, lane="steer", text="soon"),
            ],
            120,
        )
        await pilot.pause()

        await pilot.click(PendingBand, offset=(2, 0))   # header
        await pilot.click(PendingBand, offset=(2, 3))   # steer 行
        await pilot.click(PendingBand, offset=(2, 4))   # 提示行
        await pilot.pause()

        assert seen == []
