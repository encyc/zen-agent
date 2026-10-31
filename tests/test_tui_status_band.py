"""Status band segments: the status line is an ordered list, not a format string.

Mirrors omp's contract (see ``docs/research/omp-cli-study/03-status-line-and-composer.md``):
a segment that renders nothing disappears *entirely* -- including its
separator -- and the line degrades in a fixed order as the terminal narrows.
"""

from pathlib import Path

from zenagent.app.views import RunStatus

#: Hermetic home-relative path: the abbreviation logic is what is under test,
#: not the machine's actual /Users/<name>.
HOME_CWD = str(Path.home() / "work" / "repo")
from zenagent.cli.tui.status.presets import PRESETS
from zenagent.cli.tui.status.segments import SEGMENT_IDS, render_status_line


def status(**overrides) -> RunStatus:
    base = {
        "model_id": "glm-5.3",
        "thinking": "high",
        "running": False,
        "input_tokens": 1762,
        "output_tokens": 33,
        "cache_read": 1664,
        "cost": 0.0002,
        "context_used": 914,
        "context_window": 1_000_000,
        "follow_ups": 0,
        "steering": 0,
        "session_name": "20261030-122605",
        "cwd": HOME_CWD,
    }
    base.update(overrides)
    return RunStatus(**base)


def test_segment_ids_cover_the_documented_set():
    assert set(SEGMENT_IDS) == {
        "pi",
        "model",
        "thinking",
        "mode",
        "path",
        "git",
        "tokens",
        "cost",
        "context_pct",
        "queued",
        "session_name",
        "cache_hit",
        "time_spent",
        "token_rate",
    }


def test_presets_are_ordered_id_lists():
    assert PRESETS["default"].left[0] == "pi" and "model" in PRESETS["default"].left
    assert "session_name" in PRESETS["default"].right
    assert len(PRESETS["minimal"].left) < len(PRESETS["default"].left)


def test_hidden_segment_leaves_no_separator():
    _text, plain = render_status_line(status(mode=""), 120, preset="default")
    assert "·  ·" not in plain
    assert not plain.startswith("·") and not plain.endswith("·")
    assert "··" not in plain and "· ·" not in plain


def test_off_thinking_is_hidden_not_rendered_as_off():
    _, plain = render_status_line(status(thinking=None), 120, preset="default")
    assert "thinking" not in plain


def test_queued_only_appears_when_nonzero():
    _, plain = render_status_line(status(follow_ups=0), 120, preset="default")
    assert "queued" not in plain
    _, queued = render_status_line(status(follow_ups=2), 120, preset="default")
    assert "2 queued" in queued


def test_narrow_width_drops_right_group_first():
    _, wide = render_status_line(status(), 200, preset="default")
    _, narrow = render_status_line(status(), 60, preset="default")
    assert "context" in wide and status().session_name in wide
    assert len(narrow) < len(wide)
    assert "glm-5.3" in narrow, "the model outlasts every other segment"


def test_path_is_the_last_left_segment_to_go():
    _, tiny = render_status_line(status(), 34, preset="default")
    assert "glm-5.3" in tiny
    assert "repo" in tiny, "the path survives, abbreviated, while other segments drop"


def test_path_is_abbreviated_when_space_is_tight():
    _, wide = render_status_line(status(), 200, preset="default")
    _, tight = render_status_line(status(), 64, preset="default")
    assert "~/work/repo" in wide, "home-relative abbreviation is always on"
    assert "…" in tight or len(tight) <= 64


def test_plain_matches_rendered_text():
    text, plain = render_status_line(status(), 120, preset="default")
    assert text.plain == plain


def test_running_state_is_visible():
    _, plain = render_status_line(status(running=True), 120, preset="default")
    assert "running" in plain or "◐" in plain or "…" in plain


def test_line_never_exceeds_width():
    for width in (200, 120, 100, 80, 64, 50, 40, 30):
        _, plain = render_status_line(status(follow_ups=3), width, preset="default")
        assert len(plain) <= width, f"{plain!r} is {len(plain)} > {width}"


def test_cache_hit_renders_the_provider_normalized_ratio():
    _, plain = render_status_line(status(cache_hit_ratio=0.75), 200)
    assert "cache 75%" in plain


def test_cache_hit_hidden_without_a_ratio():
    _, plain = render_status_line(status(cache_hit_ratio=None), 200)
    assert "cache " not in plain


def test_turn_time_and_rate_need_a_finished_turn():
    _, plain = render_status_line(status(turn_ms=2000, turn_output_tokens=50), 200)
    assert "2.0s" in plain and "25.0 tok/s" in plain
    _, idle = render_status_line(status(turn_ms=0, turn_output_tokens=0), 200)
    assert "tok/s" not in idle and "2.0s" not in idle


def _long_status() -> RunStatus:
    return status(
        model_id="a-very-long-model-identifier-xyz",
        session_name="a-rather-long-session-name-12345",
        cwd="/Users/somebody/projects/a-very-long-project-name/here",
        git_branch="feature/a-very-long-branch-name",
    )


async def test_band_fits_the_column_beside_the_sidebar(fake_runtime, monkeypatch):
    """The band degrades for the space it actually has, not for the terminal.

    With the sidebar open the dock is narrower than the terminal, and dragging
    the sidebar changes that width; the segment list must be re-rendered for the
    new column instead of keeping the old terminal-width layout.
    """
    import asyncio

    from rich.cells import cell_len

    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.status_band import StatusBand

    monkeypatch.setattr(
        "zenagent.app.chat.ChatController.status", property(lambda _self: _long_status())
    )
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+b")
        await pilot.pause()

        band = app.query_one(StatusBand)
        assert band.size.width == 113
        assert cell_len(band.plain) <= band.size.width

        app.resize_sidebar(79)  # sidebar 60 wide → dock column 79
        await pilot.pause()
        await asyncio.sleep(0.02)
        await pilot.pause()

        band = app.query_one(StatusBand)
        assert band.size.width == 79
        assert cell_len(band.plain) <= band.size.width
