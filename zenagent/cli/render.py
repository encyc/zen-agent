"""Pure rendering: agent events in, rich renderables out.

Nothing here touches a terminal, so it is unit-testable: :class:`AssistantLiveRenderer`
turns the streaming events of one assistant message into a rich renderable, and
the helper functions turn a tool result / progress update / session into one
line of text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from zenagent.agent.loop import ToolResultAgent
from zenagent.agent.memory_store import split_title_body
from zenagent.agent.session import Session, sessions_dir
from zenagent.app.modes import describe_modes
from zenagent.app.views import usage_total
from zenagent.cli.editor import run_editor
from zenagent.cli.theme import DEFAULT_THEME, Theme
from zenagent.config import config_path
from zenagent.events import AssistantEvent
from zenagent.i18n import t
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)

#: Print callback shape shared by every CLI renderer.
PrintFn = Callable[..., None]

__all__ = [
    "AssistantLiveRenderer",
    "LiveUpdate",
    "aggregate_usage",
    "context_percent",
    "render_diff",
    "session_header",
    "status_line",
    "tool_progress_line",
    "tool_result_line",
]

#: Characters of tool output kept in a one-line preview.
_PREVIEW_CHARS = 100


@dataclass
class LiveUpdate:
    """What to draw now, and whether the message is finished."""

    renderable: RenderableType | None
    finished: bool


@dataclass
class AssistantLiveRenderer:
    """Accumulates one streamed assistant message into a live renderable.

    The adapter guarantees every event carries a complete ``partial`` snapshot,
    so the renderer just reads that snapshot instead of stitching deltas itself.
    """

    thinking: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_tokens: int | None = None

    def feed(self, event: AssistantEvent) -> LiveUpdate:
        partial: AssistantMessage = event.partial
        self.thinking = "".join(
            block.thinking for block in partial.content if isinstance(block, ThinkingContent)
        )
        self.text = "".join(
            block.text for block in partial.content if isinstance(block, TextContent)
        )
        self.tool_calls = [b for b in partial.content if isinstance(b, ToolCall)]
        self.reasoning_tokens = partial.usage.reasoning
        finished = event.type in ("done", "error")
        return LiveUpdate(renderable=self.render(), finished=finished)

    def render(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self.thinking:
            tokens = self.reasoning_tokens
            if tokens is None:
                tokens = max(1, len(self.thinking) // 4)  # cheap estimate
            parts.append(
                Panel(
                    Text(self.thinking, style="grey62"),
                    title=f"thinking · ~{tokens} tokens",
                    border_style="grey35",
                    padding=(0, 1),
                )
            )
        if self.text:
            parts.append(Markdown(self.text))
        for call in self.tool_calls:
            parts.append(Text(f"⚙ {call.name}({_brief(call.arguments)})", style="cyan"))
        return Group(*parts) if len(parts) > 1 else parts[0] if parts else Text("")


def _brief(arguments: dict, limit: int = 60) -> str:
    rendered = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _sanitize(text: str) -> str:
    """Collapse to a single printable line (no ANSI, no control characters)."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    return cleaned.strip()


def _first_line(result: ToolResultAgent) -> str:
    for block in result.content:
        if isinstance(block, TextContent) and block.text.strip():
            return _sanitize(block.text.strip().splitlines()[0])
    return ""


def tool_result_line(
    tool_name: str,
    result: ToolResultAgent,
    duration_ms: int | None = None,
    *,
    status: str | None = None,
) -> str:
    """One-line summary of a finished tool call.

    ``status`` is the settled status from the view layer (``settle_tool_status``):
    ``ok`` renders like the old ✓/✗-by-is_error line; any other settled status
    (``timed_out``, ``interrupted``) renders ✗ with the reason — a command that
    exits non-zero is information (✗ with its preview), but a killed one is a
    distinct outcome the old mark could not show.
    """
    settled = status if status is not None else ("error" if result.is_error else "ok")
    mark = "✗" if settled != "ok" else "✓"
    reason = "" if settled in ("ok", "error") else f" ({settled})"
    timing = f" {duration_ms}ms" if duration_ms is not None else ""
    preview = _first_line(result)
    if len(preview) > _PREVIEW_CHARS:
        preview = preview[: _PREVIEW_CHARS - 1] + "…"
    return f"{mark} {tool_name}{reason}{timing}  {preview}".rstrip()


def tool_progress_line(tool_name: str, partial: ToolResultAgent) -> str:
    """One-line, overwrite-in-place progress for a running tool."""
    text = "".join(
        block.text for block in partial.content if isinstance(block, TextContent)
    )
    lines = text.splitlines()
    last = _sanitize(lines[-1]) if lines else ""
    if len(last) > _PREVIEW_CHARS:
        last = last[: _PREVIEW_CHARS - 1] + "…"
    return f"⚙ {tool_name} ({len(text)} bytes) {last}".rstrip()


def aggregate_usage(messages: list) -> Usage:
    """Sum the usage of every assistant message (the run's bill)."""
    return usage_total(messages)


def _human_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def context_percent(usage: Usage, context_window: int) -> int | None:
    """How full the context is, as a rounded percentage (None without a window)."""
    if context_window <= 0:
        return None
    used = usage.input + usage.cache_read
    return round(100 * used / context_window)


def status_line(
    *,
    model_id: str,
    thinking: str | None,
    messages: int,
    usage: Usage,
    context_window: int,
    session_name: str | None = None,
) -> str:
    """One line of run state: model, level, size, context fill, spend."""
    used = usage.input + usage.cache_read
    context = f"ctx {_human_tokens(used)}/{_human_tokens(context_window)}"
    percent = context_percent(usage, context_window)
    if percent is not None:
        context += f" ({percent}%)"
    parts = [
        model_id,
        f"thinking {thinking or 'off'}",
        f"{messages} msgs",
        context,
        f"${usage.cost.total:.4f}",
    ]
    if session_name:
        parts.append(session_name)
    return " · ".join(parts)


def render_diff(diff: str, theme: Theme | None = None) -> RenderableType:
    """Colour a unified diff for the terminal using a theme."""
    palette = theme or DEFAULT_THEME
    text = Text()
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = palette.diff_meta
        elif line.startswith("+"):
            style = palette.diff_add
        elif line.startswith("-"):
            style = palette.diff_del
        elif line.startswith("@@"):
            style = palette.diff_hunk
        else:
            style = palette.dim
        text.append(line + "\n", style=style)
    return text


def session_header(session: Session) -> str:
    """A one-line header for the current session."""
    count = len(session.messages)
    return f"session {session.path.name} · {count} messages"


# --- moved from repl.py (audit P2 Task 29): pure rendering shared by CLI ---

def _render_sessions(services, print_fn: PrintFn, scope: str) -> None:
    """The text form of the TUI's session picker."""
    entries = services.sessions.entries(scope=scope)
    if not entries:
        print_fn(t("repl.no_sessions"))
        if scope == "project":
            print_fn(t("repl.sessions_hint"))
        return
    for position, entry in enumerate(entries, start=1):
        mark = "*" if entry.current else " "
        label = (
            f"[{entry.project or t('repl.legacy_project')}] "
            if scope == "all"
            else ""
        )
        title = entry.title or t("repl.empty_title")
        print_fn(
            t(
                "repl.session_row",
                mark=mark,
                position=position,
                label=label,
                title=title,
                messages=entry.messages,
                path=entry.path.name,
            )
        )


def _render_todo(services, print_fn: PrintFn) -> None:
    rows = [
        f"[{task.get('status', 'pending')}] {task.get('content', '')}"
        for phase in services.chat.todos
        for task in (phase.get("tasks") or [])
        if isinstance(task, dict)
    ]
    print_fn("\n".join(rows) if rows else t("cmdmsg.todo_empty"))


def _render_memory_index(services, print_fn: PrintFn) -> None:
    store = services.runtime.memory_store
    entries = store.list_entries() if store else []
    if not entries:
        print_fn(t("cmdmsg.memory_empty"))
        return
    for entry in entries:
        print_fn(f"{entry.id}  {entry.title}  {entry.updated}")


def _memory_editor_flow(services, entry_id: str | None, title_prefill: str, print_fn: PrintFn) -> None:
    store = services.runtime.memory_store
    entry = store.read_entry(entry_id) if (store and entry_id) else None
    if entry_id and entry is None:
        print_fn(t("cmdmsg.memory_not_found", id=entry_id))
        return
    initial = f"title: {entry.title if entry else title_prefill}\n\n{entry.body if entry else ''}"
    text = run_editor(initial)
    if text is None:
        print_fn(t("repl.no_editor"))
        return
    title, body = split_title_body(text)
    saved = (
        store.update_entry(entry.id, title=title, body=body, reason="ui edit")
        if entry
        else store.write_entry(title=title, body=body, source="ui", reason="ui edit")
    )
    print_fn(t("cmdmsg.memory_saved", id=saved.id, title=saved.title))


def _render_settings(services, print_fn: PrintFn) -> None:
    from zenagent import settings as settings_store

    prefs = settings_store.describe()["prefs"]
    print_fn(t("repl.settings_header"))
    for key, value in (
        (t("repl.settings.model"), services.runtime.agent.model.id),
        (t("repl.settings.thinking"), services.runtime.agent.thinking or "off"),
        (t("repl.settings.theme"), str(prefs.get("theme", "auto"))),
        (t("repl.settings.language"), str(prefs.get("language", "auto"))),
    ):
        print_fn(t("repl.settings_row", key=key, value=value))
    print_fn(
        t(
            "repl.settings_paths",
            config=config_path(),
            settings=settings_store.settings_path(),
            sessions=sessions_dir(),
        )
    )


def render_command_result(
    result,
    *,
    name: str,
    services,
    print_fn: PrintFn,
    keybindings: dict[str, str] | None,
    set_theme,
    set_language,
) -> str | None:
    """Render one ``CommandResult`` as terminal text; ``"exit"`` means stop.

    The TUI turns these results into pickers and overlays. The REPL has no
    widgets, so every screen gets a text form instead of silently doing
    nothing: a picker prints its current value, a list prints its rows.
    """
    if result.quit:
        return "exit"
    if result.model:
        switch = services.models.switch(result.model)
        if switch.ok:
            print_fn(t("repl.model_switch", model=services.models.current()))
        else:
            print_fn(switch.reason or t("repl.unknown_model", model=result.model))
    if result.appearance:
        set_theme(result.appearance)
    if result.language:
        set_language(result.language)
    if result.message:
        print_fn(result.message)

    screen = result.screen
    if screen == "model-picker":
        print_fn(t("repl.current_model", model=services.models.current()))
    elif screen == "mode-picker":
        print_fn(
            t("cmdmsg.mode_set", mode=services.runtime.mode, modes=describe_modes())
        )
    elif screen == "thinking-picker":
        print_fn(
            t("repl.thinking_info", level=services.runtime.agent.thinking or "off")
        )
    elif screen == "theme-picker":
        print_fn(t("repl.theme_usage"))
    elif screen == "language-picker":
        print_fn(t("repl.lang_usage"))
    elif screen == "session-picker":
        _render_sessions(services, print_fn, "project")
    elif screen == "session-picker-all":
        _render_sessions(services, print_fn, "all")
    elif screen == "hotkeys":
        if keybindings:
            for action, key in sorted(keybindings.items()):
                print_fn(f"{action}: {key}")
        else:
            print_fn(t("repl.no_keybindings"))
    elif screen == "todo-edit":
        _render_todo(services, print_fn)
    elif screen == "memory-manager":
        _render_memory_index(services, print_fn)
    elif screen == "memory-add":
        _memory_editor_flow(services, None, result.target or "", print_fn)
    elif screen == "memory-edit":
        _memory_editor_flow(services, result.target, "", print_fn)
    elif screen == "settings":
        _render_settings(services, print_fn)
    elif screen == "plan-review":
        plan = services.chat.last_assistant_text()
        print_fn(plan if plan else t("cmdmsg.no_plan"))
        if plan:
            print_fn(t("repl.plan_review_hint"))
    return None


def render_help(commands, template_list, print_fn: PrintFn) -> None:
    """The REPL's ``/help``: the shared registry plus prompt templates."""
    entries = [(f"/{command.name}", command.description) for command in commands.names()]
    width = max(len(name) for name, _ in entries)
    rows = [f"  {name:<{width}}  {description}" for name, description in entries]
    print_fn(
        t("repl.help.header")
        + "\n"
        + "\n".join(rows)
        + "\n"
        + t("repl.help.footer")
    )
    if template_list:
        print_fn(t("repl.templates_header"))
        for template in template_list:
            print_fn(
                t(
                    "repl.template_row",
                    name=template.name,
                    description=template.description,
                )
            )


