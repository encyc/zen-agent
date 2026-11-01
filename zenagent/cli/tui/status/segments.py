"""Segment renderers for the status band.

Every renderer returns a styled ``Text`` or ``None``; ``None`` means "not
applicable in this state" and the segment vanishes without leaving a separator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from zenagent.app.views import RunStatus
from zenagent.cli.tui import palette
from zenagent.cli.tui.textutil import clip_cells, clip_cells_left
from zenagent.i18n import t, tn

__all__ = ["SEGMENTS", "SEGMENT_IDS", "SegmentContext", "render_status_line"]


SEPARATOR = " · "


@dataclass(frozen=True)
class SegmentContext:
    """Everything a segment may read, plus the room it is being rendered into."""

    status: RunStatus
    width: int
    path_max: int = 40
    #: Branch names are unbounded; keep the head and clip before marks so a
    #: long feature branch cannot crowd the queued/fact segments off the band.
    git_max: int = 18


Renderer = Callable[[SegmentContext], Text | None]


def _muted(value: str) -> Text:
    return Text(value, style=palette.MUTED)


def _render_model(ctx: SegmentContext) -> Text | None:
    return Text(ctx.status.model_id, style=f"bold {palette.ACCENT}")


def _render_pi(ctx: SegmentContext) -> Text | None:
    """Brand mark: omp's `pi` segment doubles as the run indicator."""
    if ctx.status.running:
        return Text("◐", style=palette.ACCENT)
    return Text("zen", style=palette.MUTED)


def _render_thinking(ctx: SegmentContext) -> Text | None:
    level = ctx.status.thinking
    if not level or level == "off":
        return None
    return _muted(level)


def _render_mode(ctx: SegmentContext) -> Text | None:
    if not ctx.status.mode:
        return None
    return Text(ctx.status.mode, style=palette.ACCENT)


def _render_path(ctx: SegmentContext) -> Text | None:
    cwd = ctx.status.cwd
    if not cwd:
        return None
    home = str(Path.home())
    if cwd == home:
        shown = "~"
    elif cwd.startswith(home + "/"):
        shown = "~" + cwd[len(home) :]
    else:
        shown = cwd
    shown = clip_cells_left(shown, ctx.path_max)
    return _muted(shown)


def _render_git(ctx: SegmentContext) -> Text | None:
    """Branch plus the three change counts the app layer measured.

    ``+N`` staged, ``~N`` modified in the working tree, ``?N`` untracked; a
    clean tree renders the branch alone. The segment hides itself outside a
    repository instead of showing a stale branch.
    """
    status = ctx.status
    branch = status.git_branch
    if not branch:
        return None
    marks = []
    if status.git_staged:
        marks.append(f"+{status.git_staged}")
    if status.git_dirty:
        marks.append(f"~{status.git_dirty}")
    if status.git_untracked:
        marks.append(f"?{status.git_untracked}")
    mark_text = " ".join(marks)
    if mark_text:
        branch = clip_cells(branch, max(8, ctx.git_max - len(mark_text) - 1))
        return _muted(f"{branch} {mark_text}")
    return _muted(clip_cells(branch, ctx.git_max))


def _render_tokens(ctx: SegmentContext) -> Text | None:
    status = ctx.status
    if not status.input_tokens and not status.output_tokens:
        return None
    return _muted(
        t("tui.status.tokens", input=status.input_tokens, output=status.output_tokens)
    )


def _render_cost(ctx: SegmentContext) -> Text | None:
    return _muted(f"${ctx.status.cost:.4f}")


def _render_cache_hit(ctx: SegmentContext) -> Text | None:
    """Share of prompt tokens served from the provider cache.

    The ratio is computed in the app layer (``support.cache_hit_ratio``) because
    ``Usage.input`` includes cached tokens on OpenAI/Gemini and excludes them on
    Anthropic; a segment-local formula would report half the real hit rate.
    """
    ratio = ctx.status.cache_hit_ratio
    if ratio is None:
        return None
    return _muted(t("tui.status.cache", pct=f"{ratio * 100:.0f}"))


def _render_cache_read(ctx: SegmentContext) -> Text | None:
    if not ctx.status.cache_read:
        return None
    return _muted(t("tui.status.cache_read", n=ctx.status.cache_read))


def _render_cache_write(ctx: SegmentContext) -> Text | None:
    if not ctx.status.cache_write:
        return None
    return _muted(t("tui.status.cache_write", n=ctx.status.cache_write))


def _render_time_spent(ctx: SegmentContext) -> Text | None:
    if not ctx.status.turn_ms:
        return None
    return _muted(f"{ctx.status.turn_ms / 1000:.1f}s")


def _render_token_rate(ctx: SegmentContext) -> Text | None:
    status = ctx.status
    if not status.turn_ms or not status.turn_output_tokens:
        return None
    rate = status.turn_output_tokens / (status.turn_ms / 1000)
    return _muted(t("tui.status.token_rate", rate=f"{rate:.1f}"))


def _render_context(ctx: SegmentContext) -> Text | None:
    status = ctx.status
    window = status.context_window or 0
    if not window:
        return None
    percent = status.context_used * 100 / window
    return _muted(t("tui.status.context", pct=f"{percent:.1f}"))


def _render_queued(ctx: SegmentContext) -> Text | None:
    status = ctx.status
    parts: list[str] = []
    if status.follow_ups:
        parts.append(tn("tui.status.queued", status.follow_ups))
    if status.steering:
        parts.append(tn("tui.status.steering", status.steering))
    if not parts:
        return None
    return Text(" · ".join(parts), style=palette.ACCENT if status.running else palette.MUTED)


def _render_session(ctx: SegmentContext) -> Text | None:
    name = ctx.status.session_name
    if not name:
        return None
    return Text(name, style=palette.MUTED)


SEGMENTS: dict[str, Renderer] = {
    "pi": _render_pi,
    "model": _render_model,
    "thinking": _render_thinking,
    "mode": _render_mode,
    "path": _render_path,
    "git": _render_git,
    "tokens": _render_tokens,
    "cost": _render_cost,
    "cache_hit": _render_cache_hit,
    "cache_read": _render_cache_read,
    "cache_write": _render_cache_write,
    "context_pct": _render_context,
    "time_spent": _render_time_spent,
    "token_rate": _render_token_rate,
    "queued": _render_queued,
    "session_name": _render_session,
}

SEGMENT_IDS = tuple(SEGMENTS)


def _join(parts: list[Text]) -> Text:
    joined = Text()
    for index, part in enumerate(parts):
        if index:
            joined.append(SEPARATOR, style=palette.MUTED)
        joined.append_text(part)
    return joined


def _visible(ctx: SegmentContext, ids: list[str]) -> list[Text]:
    parts: list[Text] = []
    for segment_id in ids:
        rendered = SEGMENTS[segment_id](ctx)
        if rendered is not None and rendered.plain:
            parts.append(rendered)
    return parts


def _compose(ctx: SegmentContext, left_ids: list[str], right_ids: list[str]) -> tuple[Text, Text]:
    return _join(_visible(ctx, left_ids)), _join(_visible(ctx, right_ids))


def render_status_line(
    status: RunStatus,
    width: int,
    *,
    preset: str = "default",
) -> tuple[Text, str]:
    """Compose the band; degrade in omp's fixed order until it fits ``width``.

    The order matters and is the whole point of the segment list:

    1. drop the outermost right-group segment (facts go before identity);
    2. shorten the path in 8-column steps down to 8;
    3. drop left-group segments from the tail, but never ``path`` before the
       rest (you must always know where you are);
    4. as a last resort truncate the whole line.
    """
    from zenagent.cli.tui.status.presets import PRESETS

    chosen = PRESETS[preset]
    left_ids = list(chosen.left)
    right_ids = list(chosen.right)
    path_max = 40

    while True:
        ctx = SegmentContext(status=status, width=width, path_max=path_max)
        left, right = _compose(ctx, left_ids, right_ids)
        if width - 1 - len(left.plain) - len(right.plain) >= 1:
            break
        visible_right = [segment_id for segment_id in right_ids if SEGMENTS[segment_id](ctx)]
        visible_left = [segment_id for segment_id in left_ids if SEGMENTS[segment_id](ctx)]
        if visible_right:
            # Facts degrade before identity, but "n queued" is actionable state:
            # keep it until nothing else is left to drop.
            droppable_right = [
                segment_id for segment_id in visible_right if segment_id != "queued"
            ] or visible_right
            right_ids.remove(droppable_right[-1])
            continue
        if "path" in visible_left and path_max > 8:
            path_max = max(8, path_max - 8)
            continue
        droppable = [segment_id for segment_id in visible_left if segment_id != "path"]
        if droppable:
            left_ids.remove(droppable[-1])
            continue
        if len(visible_left) > 1:
            left_ids.remove(visible_left[-1])
            continue
        break

    ctx = SegmentContext(status=status, width=width, path_max=path_max)
    left, right = _compose(ctx, left_ids, right_ids)
    line = Text(" ")
    line.append_text(left)
    gap = max(0, width - 1 - len(left.plain) - len(right.plain))
    if right.plain:
        line.append(" " * gap)
        line.append_text(right)
    if len(line.plain) > width:
        line.truncate(width)
    return line, line.plain.rstrip()
