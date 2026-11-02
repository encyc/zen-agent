"""Interactive REPL: streamed answers, Ctrl+C, steering, slash commands.

Line-based (the full-screen TUI is explicitly out of scope): prompt_toolkit
reads input, rich formats tool feedback, and the Agent runs one turn at a time.
While a run is in flight, anything typed is queued as a *steering* message
instead of starting a second run.

Testability: ``input_fn`` and ``print_fn`` are injectable, so the logic can be
driven without a terminal.
"""

from __future__ import annotations

import asyncio
import inspect
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.console import Console

from zenagent.agent import Agent
from zenagent.agent.artifacts import build_artifact_sink
from zenagent.agent.session import (
    Session,
    latest_session,
    list_sessions,
    session_title,
    sessions_dir,
    set_session_title,
    truncate_last_turn,
    unique_session_path,
)
from zenagent.app.appearance import THEME_CHOICES
from zenagent.app.compact import compact, maybe_auto_compact
from zenagent.app.journal import attach_journal, journal_for, load_backup_policy
from zenagent.app.modes import describe_modes, mode_prompt_note
from zenagent.app.permissions import build_permission_gate
from zenagent.app.restore import run_restore_command
from zenagent.app.retry import prepare_retry
from zenagent.app.safety import GuardPolicy
from zenagent.app.skill_commands import build_skill_submission
from zenagent.app.support import describe_tools
from zenagent.attachments import read_image_argument
from zenagent.cli.completion import (
    ReplCompleter,
    help_text,
    slash_commands,
)
from zenagent.cli.keybindings import build_key_bindings, load_keybindings
from zenagent.cli.main import (
    _api_key_for,
    resolve_model,
    session_id_for,
)
from zenagent.cli.render import (
    aggregate_usage,
    render_diff,
    status_line,
    tool_progress_line,
    tool_result_line,
)
from zenagent.cli.theme import (
    DEFAULT_THEME,
    LEGACY_THEME_NAMES,
    Theme,
    resolve_theme_spec,
)
from zenagent.config import (
    ContextGuard,
    apply_user_config,
    config_path,
    load_user_config,
)
from zenagent.context_files import (
    discover_context_files,
    load_context_files,
)
from zenagent.events import AgentEvent
from zenagent.extensions import load_extensions
from zenagent.i18n import LANGUAGE_CHOICES, resolve_language, set_locale, t
from zenagent.mcp.config import load_mcp_config
from zenagent.mcp.manager import McpManager
from zenagent.prompts import PromptContext, compose_system
from zenagent.providers.catalog import find_model, load_catalog
from zenagent.providers.registry import StreamFn
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from zenagent.telemetry import MetricsRecorder, append_metrics
from zenagent.templates import default_template_roots, discover_templates, expand
from zenagent.tools import ALL_TOOLS
from zenagent.tools.ask import set_ask_handler
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool
from zenagent.trace import TraceRecorder
from zenagent.types import ImageContent, TextContent

__all__ = [
    "THINKING_LEVELS",
    "handle_slash",
    "repl",
    "split_input",
]

InputFn = Callable[[], Awaitable[str]]
PrintFn = Callable[..., None]

THINKING_LEVELS = ("off", "low", "medium", "high", "max")

#: Minimum seconds between two in-place progress repaints of a running tool.
_PROGRESS_INTERVAL_S = 0.2


def split_input(line: str) -> tuple[str, str]:
    """``("", text)`` for a prompt, or ``(command, argument)`` for ``/command``."""
    stripped = line.strip()
    if stripped.startswith("/"):
        command, _, argument = stripped[1:].partition(" ")
        return command.lower(), argument.strip()
    return "", stripped


async def handle_slash(
    agent: Agent,
    command: str,
    argument: str,
    *,
    print_fn: PrintFn = print,
    guard: ContextGuard | None = None,
    session_path: Path | None = None,
    session: Session | None = None,
    switch_session=None,
    images: list[ImageContent] | None = None,
    mcp_manager: McpManager | None = None,
    refresh_mcp=None,
    skill_list=None,
    skills_enabled: bool = True,
    template_list=None,
    extension_commands=None,
    reload_runtime=None,
    set_theme=None,
    set_language=None,
    keybindings=None,
    set_mode=None,
    restore=None,
    extra_dirs: list[str] | None = None,
    trace_path: str | None = None,
    catalog=None,
) -> str | None:
    """Run one slash command; returns ``"exit"`` when the REPL should stop."""
    if command in ("exit", "quit"):
        return "exit"
    if command == "restore":
        if restore is None:
            print_fn(t("cmdmsg.restore_unavailable"))
            return None
        print_fn(restore(argument))
        return None
    if command in ("help", "?"):
        print_fn(help_text())
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
        return None
    if command == "mode":
        if set_mode is None:
            print_fn(t("repl.mode_unavailable"))
            return None
        try:
            print_fn(set_mode(argument))
        except ValueError as exc:
            print_fn(t("repl.mode_error", error=exc))
        return None
    if command == "rename":
        if session is None:
            print_fn(t("repl.rename_unavailable"))
            return None
        requested = argument.strip()
        if not requested:
            print_fn(
                t("repl.session_name", name=session_title(session.path))
            )
            return None
        try:
            stored = set_session_title(session.path, requested)
        except ValueError as exc:
            print_fn(t("repl.rename_error", error=exc))
        else:
            print_fn(t("repl.renamed", name=stored))
        return None
    if command == "clear":
        agent.reset()
        print_fn(t("repl.cleared"))
        return None
    if command == "model":
        if not argument:
            print_fn(t("repl.current_model", model=agent.model.id))
            return None
        from zenagent.app.models import apply_model_switch

        result = apply_model_switch(
            agent,
            catalog if catalog is not None else load_catalog(),
            guard,
            argument,
        )
        if result.ok:
            print_fn(t("repl.model_switch", model=agent.model.id))
        elif result.reason and result.reason.startswith("unknown model"):
            print_fn(t("repl.unknown_model", model=argument))
        else:
            print_fn(result.reason or t("repl.unknown_model", model=argument))
        return None
    if command == "thinking":
        if not argument:
            print_fn(
                t(
                    "repl.thinking_info", level=agent.thinking or "off"
                )
            )
            return None
        level = argument
        if level not in THINKING_LEVELS:
            print_fn(t("repl.thinking_usage"))
            return None
        agent.thinking = level
        print_fn(t("repl.thinking_set", level=level))
        return None
    if command == "sessions":
        scope = "all" if argument.strip().lower() == "all" else "project"
        infos = list_sessions(scope=scope)
        if not infos:
            print_fn(t("repl.no_sessions"))
            if scope == "project":
                print_fn(t("repl.sessions_hint"))
            return None
        current = session.path if session is not None else None
        for position, info in enumerate(infos, start=1):
            mark = "*" if info.path == current else " "
            label = (
                f"[{info.project or t('repl.legacy_project')}] "
                if scope == "all"
                else ""
            )
            title = info.title or t("repl.empty_title")
            print_fn(
                t(
                    "repl.session_row",
                    mark=mark,
                    position=position,
                    label=label,
                    title=title,
                    messages=info.messages,
                    path=info.path.name,
                )
            )
        return None
    if command == "fork":
        if session is None:
            print_fn(t("repl.fork_unavailable"))
            return None
        try:
            index = int(argument)
        except ValueError:
            print_fn(t("repl.fork_usage"))
            return None
        try:
            forked_path = session.fork_at(index)
        except ValueError as exc:
            print_fn(str(exc))
            return None
        forked = Session(forked_path)
        agent.replace_messages(forked.load())
        if switch_session is not None:
            switch_session(forked)
        print_fn(t("repl.forked", index=index, path=forked_path.name))
        return None
    if command == "import":
        if not argument:
            print_fn(t("repl.import_usage"))
            return None
        imported_path = Path(argument).expanduser()
        if not imported_path.is_file():
            print_fn(t("repl.no_such_session", path=imported_path))
            return None
        imported = Session(imported_path)
        messages = imported.load()
        for warning in imported.warnings:
            print_fn(warning)
        agent.replace_messages(messages)
        if switch_session is not None:
            switch_session(imported)
        print_fn(
            t(
                "repl.imported",
                count=len(messages),
                path=imported_path.name,
            )
        )
        return None
    if command == "undo":
        if agent.is_streaming:
            print_fn(t("repl.cannot_undo"))
            return None
        truncated = truncate_last_turn(agent.messages)
        dropped = len(agent.messages) - len(truncated)
        if dropped == 0:
            print_fn(t("repl.nothing_to_undo"))
            return None
        agent.replace_messages(truncated)
        if session is not None:
            new_path = session.rewrite(truncated)
            if switch_session is not None:
                switch_session(Session(new_path))
            print_fn(
                t("repl.undone_path", count=dropped, path=new_path.name)
            )
        else:
            print_fn(t("repl.undone", count=dropped))
        return None
    if command == "session":
        print_fn(
            status_line(
                model_id=agent.model.id,
                thinking=agent.thinking,
                messages=len(agent.messages),
                usage=aggregate_usage(agent.messages),
                context_window=agent.model.context_window,
            )
        )
        if session_path is not None:
            print_fn(t("repl.session_path", path=session_path))
        return None
    if command == "image":
        if images is None:
            print_fn(t("repl.images_unavailable"))
            return None
        if not agent.model.supports_vision:
            print_fn(
                t("repl.model_no_images", model=agent.model.id)
            )
            return None
        if not argument:
            print_fn(t("repl.image_usage"))
            return None
        try:
            block = read_image_argument(Path(argument).expanduser())
        except ValueError as exc:
            print_fn(str(exc))
            return None
        images.append(block)
        print_fn(
            t(
                "repl.image_attached",
                path=argument,
                mime=block.mime_type,
            )
        )
        return None
    if command == "images":
        if argument == "clear":
            if images is not None:
                images.clear()
            print_fn(t("repl.images_cleared"))
            return None
        if not images:
            print_fn(t("repl.no_images"))
            return None
        for position, block in enumerate(images, start=1):
            print_fn(
                t(
                    "repl.image_row",
                    position=position,
                    mime=block.mime_type,
                    chars=len(block.data),
                )
            )
        return None
    if command == "theme":
        if set_theme is None:
            print_fn(t("repl.themes_unavailable"))
            return None
        if not argument:
            print_fn(t("repl.theme_usage"))
            return None
        set_theme(argument)
        return None
    if command == "lang":
        if set_language is None:
            print_fn(t("repl.lang_unavailable"))
            return None
        if not argument:
            print_fn(t("repl.lang_usage"))
            return None
        set_language(argument)
        return None
    if command == "hotkeys":
        if not keybindings:
            print_fn(t("repl.no_keybindings"))
            return None
        for action, key in sorted(keybindings.items()):
            print_fn(f"{action}: {key}")
        return None
    if command == "reload":
        if reload_runtime is None:
            print_fn(t("repl.reload_unavailable"))
            return None
        await reload_runtime()
        return None
    if command == "skills":
        # Rescan for display only. The REPL's discovery is monolithic (skills,
        # templates and extensions in one pass), so adopting here would mean
        # re-executing extension files inside a display command; `/reload`
        # remains the operation that applies changes. Showing the disk truth and
        # naming what is pending is the honest middle: the list is never stale,
        # and the hint says how to make the model see it.
        if not skills_enabled:
            print_fn(t("repl.skills_disabled"))
            return None
        fresh, _warnings = discover_skills(default_skill_roots(Path.cwd()))
        if not fresh:
            print_fn(t("repl.no_skills"))
            return None
        for skill in fresh:
            print_fn(
                t(
                    "repl.skill_row",
                    name=skill.name,
                    description=skill.description,
                )
            )

        # ``skill_list`` is None for callers that do not track what the agent
        # loaded; without it there is nothing to diff against, so no hint.
        if skill_list is not None:
            def signature(skills):
                return {
                    (skill.name, skill.description, skill.body) for skill in skills
                }

            pending = sorted(
                {item[0] for item in signature(fresh) ^ signature(skill_list)}
            )
            if pending:
                print_fn(
                    t("repl.changed_on_disk", names=", ".join(pending))
                )
        return None
    if command == "mcp":
        if mcp_manager is None:
            print_fn(t("repl.no_mcp"))
            return None
        if argument.startswith("retry") and refresh_mcp is not None:
            await refresh_mcp(force=True)
        for status in mcp_manager.status():
            if status.state == "connected":
                print_fn(
                    t(
                        "repl.mcp_connected",
                        name=status.name,
                        count=len(status.tools),
                    )
                )
            else:
                print_fn(
                    t(
                        "repl.mcp_failed",
                        name=status.name,
                        error=status.error or t("repl.unknown_error"),
                    )
                )
        return None
    if command == "tools":
        print_fn(describe_tools(agent.tools))
        return None
    if command == "pin":
        if session is None:
            print_fn(t("repl.pin_unavailable"))
            return None
        requested = argument.strip().lower()
        if requested not in ("", "on", "off"):
            print_fn(t("repl.pin_usage"))
            return None
        from zenagent.agent.session import read_session_pins, set_session_pin

        pinned_now = session.path.name in read_session_pins(session.path.parent)
        target = {"on": True, "off": False}.get(requested, not pinned_now)
        try:
            state = set_session_pin(session.path, target)
        except ValueError as exc:
            print_fn(str(exc))
            return None
        print_fn(
            t(
                "repl.pinned" if state else "repl.unpinned",
                name=session_title(session.path),
            )
        )
        return None
    if command == "new":
        if switch_session is None:
            print_fn(t("repl.new_unavailable"))
            return None
        fresh = Session(unique_session_path())
        agent.replace_messages([])
        switch_session(fresh)
        print_fn(t("repl.new_session", path=fresh.path.name))
        return None
    if command == "settings":
        from zenagent import settings as settings_store

        prefs = settings_store.describe()["prefs"]
        print_fn(t("repl.settings_header"))
        for key, value in (
            (t("repl.settings.model"), agent.model.id),
            (t("repl.settings.thinking"), agent.thinking or "off"),
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
        return None
    if command == "search":
        from zenagent.tools.web import format_results, run_search
        from zenagent.tools.web.registry import default_provider

        query = argument.strip()
        if not query or query in ("settings", "provider", "status"):
            print_fn(
                t(
                    "cmdmsg.search_provider_info",
                    provider=default_provider() or "auto",
                )
            )
            return None
        result = await run_search(query, limit=5)
        print_fn(format_results(result))
        return None
    if command == "doctor":
        from zenagent.cli.doctor import run_doctor

        await run_doctor(print_fn=print_fn)
        return None
    if command == "compact":
        try:
            path = await compact(agent)
        except Exception as exc:  # noqa: BLE001 - report, keep the REPL alive
            print_fn(t("repl.compaction_failed", error=exc))
        else:
            # The summary is the continuation: persist subsequent turns to the
            # new file, or they would be appended to the pre-compaction one.
            if switch_session is not None:
                switch_session(Session(path))
            print_fn(t("repl.compacted", path=path.name))
        return None

    if command == "todo":
        from zenagent.tools.todo import load_todo_phases

        phases = load_todo_phases(session.path if session is not None else None)
        rows = [
            f"[{task.get('status', 'pending')}] {task.get('content', '')}"
            for phase in phases
            for task in (phase.get("tasks") or [])
            if isinstance(task, dict)
        ]
        if not rows:
            print_fn(t("cmdmsg.todo_empty"))
            return None
        print_fn("\n".join(rows))
        return None
    if command == "jobs":
        from zenagent.tools.jobs import UnknownJobError, get_job_manager

        manager = get_job_manager()
        raw = argument.strip()
        if raw.startswith("kill"):
            job_id = raw[len("kill") :].strip()
            if not job_id:
                print_fn(t("cmdmsg.jobs_usage"))
                return None
            try:
                job = await manager.kill(job_id)
            except UnknownJobError as exc:
                print_fn(str(exc))
                return None
            print_fn(t("cmdmsg.job_killed", id=job.id))
            return None
        jobs = manager.list()
        if not jobs:
            print_fn(t("cmdmsg.no_jobs"))
            return None
        for job in jobs:
            print_fn(
                t(
                    "cmdmsg.jobs_row",
                    id=job.id,
                    status=job.status,
                    command=job.command[:70],
                )
            )
        return None
    if command == "copy":
        from zenagent.cli.tui.clipboard import write_clipboard_text
        from zenagent.types import AssistantMessage

        text = argument.strip()
        if not text:
            for message in reversed(agent.messages):
                if not isinstance(message, AssistantMessage):
                    continue
                text = "".join(
                    block.text
                    for block in message.content
                    if isinstance(block, TextContent)
                ).strip()
                if text:
                    break
        if not text:
            print_fn(t("cmdmsg.copy_empty"))
            return None
        print_fn(
            t("cmdmsg.copied") if write_clipboard_text(text) else t("cmdmsg.copy_failed")
        )
        return None
    if command == "btw":
        from zenagent.app.btw import ask_side_question

        if not argument.strip():
            print_fn(t("cmdmsg.btw_usage"))
            return None
        try:
            answer = await ask_side_question(agent, argument)
        except (ValueError, RuntimeError) as exc:
            print_fn(str(exc))
            return None
        print_fn(answer)
        return None
    if command == "shake":
        from zenagent.app.shake import KINDS, shake

        raw = argument.strip().lower()
        if raw in ("", "all"):
            kinds = set(KINDS)
        elif raw in KINDS:
            kinds = {raw}
        else:
            print_fn(t("cmdmsg.shake_usage"))
            return None
        try:
            changed, new_session = shake(agent, kinds)
        except ValueError as exc:
            print_fn(str(exc))
            return None
        if switch_session is not None:
            switch_session(new_session)
        print_fn(t("cmdmsg.shaken", count=changed, path=new_session.path.name))
        return None
    if command == "handoff":
        from zenagent.app.compact import compact as compact_session

        try:
            path = await compact_session(agent)
        except (ValueError, RuntimeError) as exc:
            print_fn(str(exc))
            return None
        summary = "\n".join(
            block.text
            for message in agent.messages
            for block in getattr(message, "content", [])
            if isinstance(block, TextContent)
        ).strip()
        document = Path.cwd() / f"handoff-{time.strftime('%Y%m%d-%H%M%S')}.md"
        try:
            document.write_text(f"# Handoff\n\n{summary}\n", encoding="utf-8")
        except OSError as exc:
            print_fn(str(exc))
            return None
        if switch_session is not None:
            switch_session(Session(path))
        print_fn(t("cmdmsg.handoff", path=document.name))
        return None
    if command in ("add-dir", "remove-dir", "dirs"):
        if extra_dirs is None:
            print_fn(t("cmdmsg.dirs_usage"))
            return None
        if command == "dirs":
            print_fn(
                t("cmdmsg.dirs", dirs="\n".join([str(Path.cwd()), *extra_dirs]))
            )
            return None
        raw = argument.strip()
        if not raw:
            print_fn(t("cmdmsg.dirs_usage"))
            return None
        path = Path(raw).expanduser().resolve()
        if command == "add-dir":
            if not path.is_dir():
                print_fn(t("cmdmsg.dir_missing", path=path))
                return None
            if str(path) not in extra_dirs:
                extra_dirs.append(str(path))
            print_fn(t("cmdmsg.dir_added", path=path))
            return None
        if str(path) in extra_dirs:
            extra_dirs.remove(str(path))
            print_fn(t("cmdmsg.dir_removed", path=path))
            return None
        print_fn(t("cmdmsg.dir_missing", path=path))
        return None
    if command == "fresh":
        print_fn(t("cmdmsg.fresh"))
        return None
    if command == "trace":
        if not trace_path:
            print_fn(t("cmdmsg.trace_off"))
            return None
        print_fn(t("cmdmsg.trace_path", path=trace_path))
        return None
    if extension_commands and command in extension_commands:
        handler = extension_commands[command].handler
        result = handler(agent, argument)
        if inspect.isawaitable(result):
            result = await result
        if result:
            print_fn(str(result))
        return None
    print_fn(t("repl.unknown_command", command=command))
    return None


class _EventPrinter:
    """Streams assistant text and prints one line per tool result."""

    def __init__(
        self,
        console: Console | None = None,
        print_fn: PrintFn = print,
        theme: Theme | None = None,
    ) -> None:
        self.console = console
        self.print = print_fn
        self.theme = theme or DEFAULT_THEME
        self._line_open = False
        #: A tool progress line is currently painted on the terminal.
        self._progress_open = False
        self._last_progress_at = 0.0

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._clear_progress()
            self._line_open = False
            return
        if event.type == "tool_execution_update":
            self._progress(event)
            return
        if event.type == "message_update" and event.assistant_event is not None:
            assistant_event = event.assistant_event
            if assistant_event.type == "text_delta" and assistant_event.delta:
                self._clear_progress()
                self.print(assistant_event.delta, end="")
                if self.print is print:
                    # stdout is line-buffered on a TTY (block-buffered on a
                    # pipe), so an unflushed delta would only surface when the
                    # closing newline arrives — the whole answer at once.
                    sys.stdout.flush()
                self._line_open = True
            elif assistant_event.type == "thinking_end" and self.console is not None:
                self._close_line()
                self.console.print("[grey50]· thought[/grey50]", highlight=False)
            return
        if event.type == "tool_execution_end":
            self._clear_progress()
            self._close_line()
            line = tool_result_line(
                event.tool_name or "?", event.result, event.duration_ms
            )
            if self.console is not None:
                self.console.print(line, highlight=False)
                diff = getattr(event.result, "details", {}).get("diff")
                if diff:
                    self.console.print(render_diff(diff, self.theme), highlight=False)
            else:
                self.print(line)
            return
        if event.type == "message_end":
            self._clear_progress()
            self._close_line()

    def _progress(self, event: AgentEvent) -> None:
        """Paint a running tool's latest output in place (TTY only).

        The tool layer already throttles updates (bash: 0.5 s / 4 KB); the
        printer adds a small guard so an MCP server streaming a line per token
        cannot make the REPL redraw on every event. Pipes and non-TTY runs get
        nothing — a carriage-return progress line would corrupt captured text.
        """
        if self.console is None or event.partial_result is None:
            return
        now = time.monotonic()
        if now - self._last_progress_at < _PROGRESS_INTERVAL_S:
            return
        line = tool_progress_line(event.tool_name or "?", event.partial_result)
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
        if len(line) > width:
            line = line[: width - 1] + "…"
        sys.stdout.write("\r" + line + "\x1b[K")
        sys.stdout.flush()
        self._progress_open = True
        self._last_progress_at = now

    def _clear_progress(self) -> None:
        if not self._progress_open:
            return
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()
        self._progress_open = False

    def _close_line(self) -> None:
        if self._line_open:
            self.print("")
            self._line_open = False


def _session_for(resume: str | Path | None) -> Session:
    """Open the resumed session, or start a fresh one."""
    if resume in (None, "", "latest"):
        path = latest_session() if resume == "latest" else None
        if path is None:
            path = unique_session_path()
    else:
        path = Path(resume)
    return Session(path)


def _saved_theme() -> str | None:
    """The persisted ``theme`` preference (tests patch the settings store)."""
    from zenagent import settings

    return settings.get_pref("theme")


def _saved_language() -> str | None:
    """The persisted ``language`` preference (tests patch the settings store)."""
    from zenagent import settings

    return settings.get_pref("language")


def _make_default_reader(
    completion: bool = True,
    extra_commands: dict[str, str] | None = None,
    keybindings: dict[str, str] | None = None,
    extra_values: dict[str, list[tuple[str, str]]] | None = None,
    *,
    typed_provider: Callable[[], list[tuple[str, str, str]]] | None = None,
    skill_provider: Callable[[], list[tuple[str, str]]] | None = None,
) -> InputFn:
    """prompt_toolkit reader with persistent history and completion."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    history = Path.home() / ".zenagent" / "history"
    history.parent.mkdir(parents=True, exist_ok=True)
    commands = {**slash_commands(), **(extra_commands or {})}
    session = PromptSession(
        history=FileHistory(str(history)),
        completer=(
            ReplCompleter(
                slash_commands=commands,
                extra_values=extra_values,
                typed_provider=typed_provider,
                skill_provider=skill_provider,
            )
            if completion
            else None
        ),
        key_bindings=build_key_bindings(keybindings),
    )

    async def read() -> str:
        return await asyncio.to_thread(session.prompt, "zenagent> ")

    return read


def _compose_prompt(
    current_tools, *, context_files: bool, extra_text: str = ""
) -> str:
    """Compose the REPL system prompt through the single compose contract."""
    context_text = (
        load_context_files(discover_context_files(Path.cwd()))
        if context_files
        else ""
    )
    context = PromptContext(
        cwd=Path.cwd(),
        tool_names=tuple(tool.name for tool in current_tools),
        context_text=context_text,
        extension_fragments=(extra_text,) if extra_text else (),
        include_context=context_files,
        include_skills=False,
        include_mode=False,
    )
    return compose_system(context).text


async def repl(
    model_id: str | None = None,
    resume: str | Path | None = None,
    *,
    stream_fn: StreamFn | None = None,
    input_fn: InputFn | None = None,
    print_fn: PrintFn = print,
    api_key: str | None = None,
    tools: list | None = None,
    allow_dangerous: bool = False,
    allow_outside: list[str] | None = None,
    allow_destructive: bool = False,
    allow_protected: bool = False,
    max_turns: int | None = None,
    max_cost: float | None = None,
    retries: int = 0,
    context_files: bool = True,
    completion: bool = True,
    auto_compact: bool = True,
    trace: Path | str | None = None,
    mcp: bool = True,
    mcp_servers: list[str] | None = None,
    mcp_file: Path | str | None = None,
    mcp_retry_interval_s: float = 10.0,
    skills: bool = True,
    templates: bool = True,
    extension_paths: list[str] | None = None,
    read_only: bool = False,
    mode: str = "ask",
    theme: str | None = None,
    lang: str | None = None,
    metrics: Path | None = None,
) -> int:
    """Run the interactive loop; returns the process exit code."""
    set_locale(resolve_language(lang, pref=_saved_language()))
    user_config = load_user_config()
    config_warnings: list[str] = []
    catalog = apply_user_config(load_catalog(), user_config, config_warnings)
    for warning in config_warnings:
        print(f"zenagent: {warning}", file=sys.stderr)
    try:
        model = resolve_model(catalog, model_id or user_config.default_model)
    except KeyError as exc:
        print(
            "zenagent: " + t("repl.unknown_model", model=exc), file=sys.stderr
        )
        return 1
    key = api_key or _api_key_for(catalog, model)
    if not key:
        provider = catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        print(
            "zenagent: "
            + t("runtime.missing_key", model=model.id, env=env_name),
            file=sys.stderr,
        )
        return 1

    model_values = [
        (
            candidate.id,
            f"{provider_id} · "
            + ("ready" if _api_key_for(catalog, candidate) else "no API key"),
        )
        for provider_id, provider in catalog.items()
        for candidate in provider.models
    ]

    session = _session_for(resume)
    tool_list = list(ALL_TOOLS if tools is None else tools)
    todo_tool = build_todo_tool(session.path) if tools is None else None
    if todo_tool is not None:
        tool_list.append(todo_tool)
    artifact_sink = build_artifact_sink(session.path)
    task_tool = (
        build_task_tool(
            model=lambda: agent.model,
            base_tools=list(ALL_TOOLS),
            api_key=lambda: agent.api_key,
            stream_fn=stream_fn,
            thinking=lambda: agent.thinking,
            max_cost=max_cost,
            # Read through the current sink so /fork, /compact and /import
            # retarget child artifacts too.
            artifact_sink=lambda tool_name, text: artifact_sink(tool_name, text),
            session_path=session.path,
        )
        if tools is None
        else None
    )
    if task_tool is not None:
        tool_list.append(task_tool)
    guard = ContextGuard(model.context_window)
    base_tools = list(tool_list)
    extension_files = [*user_config.extensions, *(extension_paths or [])]

    def warn(message: str) -> None:
        print(f"zenagent: {message}", file=sys.stderr)

    def build_prompt(current_tools, extra_text: str = "") -> str:
        return _compose_prompt(
            current_tools, context_files=context_files, extra_text=extra_text
        )

    mcp_manager: McpManager | None = None
    if mcp and tools is None:
        configs = load_mcp_config(Path(mcp_file) if mcp_file else None)
        if mcp_servers:
            configs = {
                name: config for name, config in configs.items() if name in mcp_servers
            }
        if configs:
            mcp_manager = McpManager(configs, retry_interval_s=mcp_retry_interval_s)
            for warning in await mcp_manager.start():
                warn(f"mcp {warning}")

    def discover_runtime():
        """(tools, extra prompt, skills, templates, commands, hooks)"""
        found_tools = list(base_tools)
        if mcp_manager is not None:
            found_tools.extend(mcp_manager.tool_defs())
        prompt_parts: list[str] = []
        found_skills: list = []
        if skills and tools is None:
            found_skills, skill_warnings = discover_skills(
                default_skill_roots(Path.cwd())
            )
            for warning in skill_warnings:
                warn(f"skills {warning}")
            if found_skills:
                found_tools.append(build_skill_tool(found_skills))
                prompt_parts.append(skills_prompt_index(found_skills))
        found_templates: list = []
        if templates:
            found_templates, template_warnings = discover_templates(
                default_template_roots(Path.cwd())
            )
            for warning in template_warnings:
                warn(f"templates {warning}")
        found_commands: dict = {}
        found_hooks: dict = {}
        if extension_files:
            loaded = load_extensions(extension_files)
            for warning in loaded.warnings:
                warn(f"extension {warning}")
            found_tools.extend(loaded.tools)
            if loaded.prompt_text:
                prompt_parts.append(loaded.prompt_text)
            found_commands = loaded.commands
            found_hooks = loaded.hooks
        return (
            found_tools,
            "\n\n".join(prompt_parts),
            found_skills,
            found_templates,
            found_commands,
            found_hooks,
        )

    (
        tool_list,
        extra_text,
        skill_list,
        template_list,
        extension_commands,
        extension_hooks,
    ) = discover_runtime()
    template_by_name = {template.name: template for template in template_list}

    def typed_commands() -> list[tuple[str, str, str]]:
        """Slash entries with their type label; re-read after /reload."""
        entries = [
            (f"/{template.name}", template.description, "template")
            for template in template_list
        ]
        entries.extend(
            (f"/{command.name}", command.description, "extension")
            for command in extension_commands.values()
        )
        return entries

    policy = GuardPolicy.from_config(
        user_config,
        cwd=Path.cwd(),
        allow_outside=allow_outside or [],
        allow_destructive=allow_destructive,
        allow_protected=allow_protected,
    )
    gate = build_permission_gate(
        interactive=True,
        allow_dangerous=allow_dangerous,
        allow=user_config.tool_allow,
        deny=user_config.tool_deny,
        mode=mode,
        policy=policy,
    )

    def system_prompt_for(base: str) -> str:
        note = mode_prompt_note(gate.mode)
        return f"{base}\n\n{note}" if note else base

    base_prompt = build_prompt(tool_list, extra_text)
    prompt = system_prompt_for(base_prompt)
    agent = Agent(
        model=model,
        system_prompt=prompt,
        tools=tool_list,
        api_key=key,
        thinking="high" if model.reasoning else "off",
        session_id=session_id_for(model, session.path.stem),
        stream_fn=stream_fn,
        transform_context=guard.transform,
        max_turns=max_turns,
        max_cost=max_cost,
        retries=retries,
        before_tool_call=gate.before_tool_call,
        artifact_sink=artifact_sink,
    )
    journal = journal_for(session.path, load_backup_policy(user_config))
    if journal is not None:
        attach_journal(agent, journal)

    def set_mode(argument: str) -> str:
        requested = argument.strip()
        if not requested:
            return t("cmdmsg.mode_set", mode=gate.mode, modes=describe_modes())
        gate.set_mode(requested)  # ValueError drives the user-facing message
        agent.system_prompt = system_prompt_for(base_prompt)
        return t("cmdmsg.mode_set", mode=gate.mode, modes=describe_modes())
    if resume is not None:
        agent.replace_messages(session.load())
        for warning in session.warnings:
            print(f"zenagent: {warning}", file=sys.stderr)

    async def refresh_mcp(force: bool = False) -> None:
        """Reconnect failed servers and swap refreshed tools into the agent."""
        nonlocal base_prompt
        if mcp_manager is None:
            return
        for warning in await mcp_manager.retry_failed(force=force):
            print_fn(f"mcp: {warning}")
        refreshed = [*base_tools, *mcp_manager.tool_defs()]
        if [tool.name for tool in refreshed] != [tool.name for tool in agent.tools]:
            agent.tools = refreshed
            base_prompt = build_prompt(refreshed)
            agent.system_prompt = system_prompt_for(base_prompt)
            print_fn(f"mcp: tools updated ({len(refreshed)} available)")

    async def reload_runtime() -> None:
        """Re-discover skills, templates and extensions without restarting."""
        nonlocal tool_list, skill_list, template_list, template_by_name
        nonlocal extension_commands
        nonlocal extension_hooks, base_prompt
        if agent.is_streaming:
            print_fn("cannot reload while the agent is working")
            return
        (
            tools_now,
            extra_now,
            skills_now,
            templates_now,
            commands_now,
            hooks_now,
        ) = discover_runtime()
        tool_list, skill_list, template_list = tools_now, skills_now, templates_now
        template_by_name = {template.name: template for template in templates_now}
        extension_commands, extension_hooks = commands_now, hooks_now
        agent.tools = tools_now
        base_prompt = build_prompt(tools_now, extra_now)
        agent.system_prompt = system_prompt_for(base_prompt)
        print_fn(
            t(
                "repl.reloaded",
                tools=len(tools_now),
                skills=len(skills_now),
                templates=len(templates_now),
            )
        )

    console = Console() if sys.stdout.isatty() else None
    theme_warnings: list[str] = []
    try:
        active_theme = resolve_theme_spec(theme, pref=_saved_theme(), warnings=theme_warnings)
    except ValueError as exc:
        theme_warnings.append(str(exc))
        active_theme = DEFAULT_THEME
    for warning in theme_warnings:
        print(f"zenagent: theme {warning}", file=sys.stderr)
    keybinding_warnings: list[str] = []
    active_keybindings = load_keybindings(warnings=keybinding_warnings)
    for warning in keybinding_warnings:
        print(f"zenagent: {warning}", file=sys.stderr)
    printer = _EventPrinter(console=console, print_fn=print_fn, theme=active_theme)

    def set_theme(spec: str) -> None:
        nonlocal active_theme
        normalized = spec.strip().lower()
        legacy = normalized in LEGACY_THEME_NAMES or Path(spec).expanduser().is_file()
        if normalized not in THEME_CHOICES and not legacy:
            print_fn(t("repl.theme_unknown", spec=spec))
            return
        try:
            theme = resolve_theme_spec(spec)
        except ValueError as exc:
            print_fn(str(exc))
            return
        if normalized in THEME_CHOICES:
            from zenagent import settings

            settings.set_pref("theme", normalized)
        active_theme = theme
        printer.theme = active_theme
        print_fn(t("repl.theme_set", name=active_theme.name))

    def set_language(spec: str) -> None:
        normalized = spec.strip().lower()
        if normalized not in LANGUAGE_CHOICES:
            print_fn(t("repl.lang_unknown", spec=spec))
            return
        code = set_locale(resolve_language(normalized))
        from zenagent import settings

        settings.set_pref("language", normalized)
        print_fn(t("repl.lang_set", code=code))

    session_box: list[Session] = [session]
    pending_images: list[ImageContent] = []

    def switch_session(new_session: Session) -> None:
        nonlocal artifact_sink
        session_box[0] = new_session
        # Artifacts belong to the session in context: retarget the spill sink
        # (and the task child's sink, which reads this variable).
        artifact_sink = build_artifact_sink(new_session.path)
        agent.artifact_sink = artifact_sink
        if todo_tool is not None:
            todo_tool.bind_session(new_session.path)

    def on_event(event: AgentEvent) -> None:
        if event.type == "agent_end" and event.new_messages:
            for message in event.new_messages:
                session_box[0].append(message)
        printer(event)

    metrics_recorder = MetricsRecorder(model.id) if metrics else None
    if metrics_recorder is not None:
        agent.subscribe(metrics_recorder)
    tracer = TraceRecorder(trace) if trace else None
    if tracer is not None:
        agent.subscribe(tracer)
    if extension_hooks:
        for event_type, callbacks in extension_hooks.items():
            def hook(event: AgentEvent, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - a hook must not kill the run
                        print(f"zenagent: extension hook failed: {exc}", file=sys.stderr)

            agent.subscribe(hook)
    agent.subscribe(guard.on_event)
    agent.subscribe(on_event)
    if console is not None:
        console.print(
            f"[grey50]{session.path.name}[/grey50] · "
            + t("repl.help_hint")
        )

    def skill_provider() -> list[tuple[str, str]]:
        return [(skill.name, skill.description) for skill in skill_list]

    read = input_fn or _make_default_reader(
        completion=completion,
        keybindings=active_keybindings,
        extra_values={"/model": model_values},
        typed_provider=typed_commands,
        skill_provider=skill_provider,
    )
    run_task: asyncio.Task | None = None
    #: Only one reader may own stdin: the REPL's steering reader and the ask
    #: handler would otherwise steal each other's lines during a run.
    input_lock = asyncio.Lock()
    ask_active = asyncio.Event()

    async def ask_user(questions: list[dict]) -> list[dict]:
        """Answer an ``ask`` tool call from the terminal (blank = dismiss)."""
        ask_active.set()
        answers: list[dict] = []
        for index, question in enumerate(questions, start=1):
            title = (
                question.get("header")
                or question.get("id")
                or t("repl.question_default", index=index)
            )
            print_fn(
                t(
                    "repl.question_title",
                    index=index,
                    total=len(questions),
                    title=title,
                    question=question["question"],
                )
            )
            options = question["options"]
            for position, option in enumerate(options, start=1):
                if option.get("description"):
                    line = t(
                        "repl.question_option_desc",
                        position=position,
                        label=option["label"],
                        description=option["description"],
                    )
                else:
                    line = t(
                        "repl.question_option",
                        position=position,
                        label=option["label"],
                    )
                if option["label"] == question.get("recommended"):
                    line += t("tui.dialog.recommended_suffix")
                print_fn(line)
            print_fn(
                t(
                    "repl.answer_hint",
                    kind=t(
                        "repl.answer_kind_multi"
                        if question.get("multi")
                        else "repl.answer_kind_single"
                    ),
                )
            )
            while True:
                try:
                    async with input_lock:
                        raw = await read()
                except (EOFError, KeyboardInterrupt):
                    ask_active.clear()
                    return answers
                parts = [
                    part
                    for part in raw.replace(",", " ").split()
                    if part.strip()
                ]
                if not parts:
                    ask_active.clear()
                    return answers
                picks: list[str] = []
                for part in parts:
                    try:
                        value = int(part)
                    except ValueError:
                        continue
                    if 1 <= value <= len(options):
                        label = options[value - 1]["label"]
                        if label not in picks:
                            picks.append(label)
                if not question.get("multi"):
                    picks = picks[:1]
                if picks:
                    answers.append({"id": question["id"], "selected": picks})
                    break
                print_fn(t("repl.invalid_selection"))
        ask_active.clear()
        return answers

    async def reap() -> None:
        """Clear a finished run so the next line is treated as a new prompt."""
        nonlocal run_task, metrics_recorder
        if run_task is not None and run_task.done():
            await asyncio.gather(run_task, return_exceptions=True)
            run_task = None
            if auto_compact:
                compacted = await maybe_auto_compact(agent, print_fn=print_fn)
                if compacted is not None:
                    switch_session(Session(compacted))
            if metrics_recorder is not None:
                append_metrics(metrics, metrics_recorder.build(agent.termination))
                metrics_recorder = MetricsRecorder(model.id)
                agent.subscribe(metrics_recorder)
            print_fn(
                status_line(
                    model_id=agent.model.id,
                    thinking=agent.thinking,
                    messages=len(agent.messages),
                    usage=aggregate_usage(agent.messages),
                    context_window=agent.model.context_window,
                    session_name=session_box[0].path.name,
                )
            )

    extra_dirs: list[str] = []
    set_ask_handler(ask_user)
    try:
        while True:
            await reap()
            if ask_active.is_set():
                # A pending ask (from inside the running agent) owns stdin.
                await ask_active.wait()
                continue
            try:
                async with input_lock:
                    if ask_active.is_set():
                        line = None
                    else:
                        line = await read()
            except (EOFError, KeyboardInterrupt):
                if run_task is not None:
                    agent.abort()
                    await asyncio.gather(run_task, return_exceptions=True)
                print_fn("")
                return 0
            if line is None:
                await ask_active.wait()
                continue

            await reap()  # the run may have finished while the user was typing
            if not line.strip():
                continue

            command, argument = split_input(line)
            prompt_text = line.strip()
            skill_prompt: str | None = None
            if command and command in template_by_name:
                prompt_text = expand(template_by_name[command].body, argument)
                command = ""
            resolution = build_skill_submission(
                line,
                skill_list,
                reserved_names={*template_by_name, *extension_commands},
            )
            if resolution is not None:
                if resolution.error is not None:
                    print_fn(resolution.error)
                    continue
                if resolution.submission is not None:
                    skill_prompt = resolution.submission.prompt
                    prompt_text = skill_prompt
                    command = ""
            if command == "retry":
                # Retry is a loop-level command: it truncates the session and
                # turns the recovered prompt into this iteration's input.
                prepared = (
                    None
                    if run_task is not None
                    else prepare_retry(agent, session_box[0])
                )
                if prepared is None:
                    print_fn(t("repl.retry_nothing"))
                    continue
                prompt_text, new_session = prepared
                switch_session(new_session)
                command = ""
            if command:
                if (
                    await handle_slash(
                        agent,
                        command,
                        argument,
                        print_fn=print_fn,
                        guard=guard,
                        session_path=session_box[0].path,
                        session=session_box[0],
                        switch_session=switch_session,
                        images=pending_images,
                        mcp_manager=mcp_manager,
                        refresh_mcp=refresh_mcp,
                        skill_list=skill_list,
                        skills_enabled=skills,
                        template_list=template_list,
                        extension_commands=extension_commands,
                        reload_runtime=reload_runtime,
                        set_theme=set_theme,
                        set_language=set_language,
                        keybindings=active_keybindings,
                        set_mode=set_mode,
                        restore=lambda argument: run_restore_command(
                            journal,
                            argument,
                            running=agent.is_streaming,
                        ),
                        extra_dirs=extra_dirs,
                        trace_path=trace,
                        catalog=catalog,
                    )
                    == "exit"
                ):
                    if run_task is not None:
                        agent.abort()
                        await asyncio.gather(run_task, return_exceptions=True)
                    return 0
                continue

            if run_task is not None:
                # A run is still going: this line steers it instead of stacking.
                agent.steer(
                    skill_prompt if skill_prompt is not None else line.strip()
                )
                continue

            await refresh_mcp()
            if pending_images:
                if not agent.model.supports_vision:
                    print_fn(
                        t("repl.images_unsupported", model=agent.model.id)
                    )
                    continue
                run_task = asyncio.create_task(
                    agent.prompt(prompt_text, images=list(pending_images))
                )
                pending_images.clear()
            else:
                run_task = asyncio.create_task(agent.prompt(prompt_text))
    finally:
        set_ask_handler(None)
        if run_task is not None:
            agent.abort()
            await asyncio.gather(run_task, return_exceptions=True)
        if tracer is not None:
            tracer.close()
        if mcp_manager is not None:
            await mcp_manager.close()
