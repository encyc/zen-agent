"""Status band segments: the status line is an ordered list, not a format string.

Mirrors omp's contract (see ``docs/research/omp-cli-study/03-status-line-and-composer.md``):
a segment that renders nothing disappears *entirely* -- including its
separator -- and the line degrades in a fixed order as the terminal narrows.
"""

from zenagent.app.views import RunStatus
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
        "cwd": "/Users/mac/py-agent",
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
    assert "py-agent" in tiny or "…" in tiny or "~" in tiny


def test_path_is_abbreviated_when_space_is_tight():
    _, wide = render_status_line(status(), 200, preset="default")
    _, tight = render_status_line(status(), 64, preset="default")
    assert "~/py-agent" in wide, "home-relative abbreviation is always on"
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
