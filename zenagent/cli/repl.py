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
from collections import deque
from pathlib import Path

from rich.console import Console

from zenagent.app.agent_profile import AgentProfile
from zenagent.app.appearance import THEME_CHOICES
from zenagent.app.bootstrap import prepare
from zenagent.app.modes import describe_modes
from zenagent.app.permissions import (
    render_approval_prompt,
)
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.skill_commands import build_skill_submission
from zenagent.app.support import api_key_for
from zenagent.app.views import TranscriptBuilder
from zenagent.attachments import read_image_argument
from zenagent.cli.completion import (
    slash_commands,
)
from zenagent.cli.interaction import InputFn, _make_default_reader, split_input
from zenagent.cli.keybindings import load_keybindings
from zenagent.cli.render import (
    PrintFn,
    aggregate_usage,
    render_command_result,
    render_diff,
    render_help,
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
from zenagent.events import AgentEvent
from zenagent.i18n import LANGUAGE_CHOICES, resolve_language, set_locale, t
from zenagent.providers.registry import StreamFn
from zenagent.templates import expand
from zenagent.types import ImageContent

__all__ = [
    "THINKING_LEVELS",
    "repl",
    "split_input",
]

THINKING_LEVELS = ("off", "low", "medium", "high", "max")

#: Minimum seconds between two in-place progress repaints of a running tool.
_PROGRESS_INTERVAL_S = 0.2


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
        self._builder = TranscriptBuilder()
        self._line_open = False
        #: A tool progress line is currently painted on the terminal.
        self._progress_open = False
        self._last_progress_at = 0.0

    def __call__(self, event: AgentEvent) -> None:
        # Facts come from the card: the builder settles statuses the way the
        # TUI/RPC views do; the live print stays line-per-event.
        self._builder.apply(event)
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
            card = self._builder.cards.get(event.tool_call_id or "")
            line = tool_result_line(
                event.tool_name or "?",
                event.result,
                event.duration_ms,
                status=card.status if card is not None else None,
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


def _saved_theme() -> str | None:
    """The persisted ``theme`` preference (tests patch the settings store)."""
    from zenagent import settings

    return settings.get_pref("theme")


def _saved_language() -> str | None:
    """The persisted ``language`` preference (tests patch the settings store)."""
    from zenagent import settings

    return settings.get_pref("language")



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
    thinking: str | None = None,
    images: list[Path] | None = None,
) -> int:
    """Run the interactive loop; returns the process exit code."""
    set_locale(resolve_language(lang, pref=_saved_language()))
    options = RuntimeOptions(
        model_id=model_id,
        thinking=thinking,
        resume=resume,
        mcp_file=Path(mcp_file) if mcp_file else None,
        mcp_servers=mcp_servers,
        profile=AgentProfile(
            mcp=mcp, skills=skills, templates=templates, context_files=context_files
        ),
        extension_paths=list(extension_paths or []),
        read_only=read_only,
        allow_dangerous=allow_dangerous,
        allow_outside=list(allow_outside or []),
        allow_destructive=allow_destructive,
        allow_protected=allow_protected,
        mode=mode,
        retries=retries,
        max_turns=max_turns,
        max_cost=max_cost,
        api_key=api_key,
        trace=trace,
        metrics=Path(metrics) if metrics else None,
        auto_compact=auto_compact,
        tools=tools,
        mcp_retry_interval_s=mcp_retry_interval_s,
    )
    try:
        prepared = await prepare(options, stream_fn=stream_fn)
    except KeyError as exc:
        print("zenagent: " + t("repl.unknown_model", model=exc), file=sys.stderr)
        return 1
    except (RuntimeError, LookupError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 1
    runtime = prepared.runtime
    broker = prepared.broker
    # The broker owns the interactive surface: approvals travel through the
    # same single-stdin protocol as every other question (see the interaction
    # gate below), instead of a blocking input() that fought the steering
    # reader for stdin and froze the event loop mid-prompt.
    for warning in runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    agent = runtime.agent
    mcp_manager = runtime.mcp
    skill_list = list(runtime.skills)
    template_list = list(runtime.templates)
    template_by_name = {template.name: template for template in template_list}
    extension_commands = dict(runtime.extension_commands)
    model = agent.model

    initial_images: list[ImageContent] = []
    for image_path in images or []:
        if not model.supports_vision:
            print(
                f"zenagent: {model.id} does not accept images "
                "(pick a vision-capable model)",
                file=sys.stderr,
            )
            await runtime.close()
            return 1
        try:
            initial_images.append(read_image_argument(Path(image_path)))
        except ValueError as exc:
            print(f"zenagent: {exc}", file=sys.stderr)
            await runtime.close()
            return 1

    model_values = [
        (
            candidate.id,
            f"{provider_id} · "
            + ("ready" if api_key_for(runtime.catalog, candidate) else "no API key"),
        )
        for provider_id, provider in runtime.catalog.items()
        for candidate in provider.models
    ]

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

    def set_mode(argument: str) -> str:
        requested = argument.strip()
        if requested:
            runtime.set_mode(requested)  # ValueError drives the user-facing message
        return t("cmdmsg.mode_set", mode=runtime.mode, modes=describe_modes())

    async def refresh_mcp(force: bool = False) -> None:
        """Reconnect failed servers and swap refreshed tools into the agent."""
        if mcp_manager is None:
            return
        for warning in await runtime.refresh_mcp(force=force):
            print_fn(f"mcp: {warning}")
        print_fn(f"mcp: tools updated ({len(agent.tools)} available)")

    def _refresh_lists() -> None:
        """Re-read the rosters the slash layer uses, then say so."""
        nonlocal skill_list, template_list, template_by_name, extension_commands
        skill_list = list(runtime.skills)
        template_list = list(runtime.templates)
        template_by_name = {template.name: template for template in template_list}
        extension_commands = dict(runtime.extension_commands)
        print_fn(
            t(
                "repl.reloaded",
                tools=len(agent.tools),
                skills=len(skill_list),
                templates=len(template_list),
            )
        )

    async def reload_runtime() -> None:
        """Re-discover skills, templates and extensions without restarting."""
        nonlocal skill_list, template_list, template_by_name, extension_commands
        if agent.is_streaming:
            print_fn("cannot reload while the agent is working")
            return
        notes = await runtime.reconcile_async()
        _refresh_lists()
        for note in notes:
            print_fn(note)

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

    chat = prepared.chat
    services = prepared.services
    commands = prepared.commands
    for block in initial_images:
        chat.attach_image(block)

    def print_help() -> None:
        render_help(commands, template_list, print_fn)

    async def dispatch_slash(name: str, argument: str) -> str | None:
        """Run one slash command through the shared registry."""
        nonlocal skill_list, template_list, template_by_name, extension_commands
        if name in ("exit", "quit"):
            return "exit"
        if name == "theme" and argument.strip():
            # The REPL keeps its legacy theme names (`mono`, `solarized`, a JSON
            # path); the shared command only knows auto/dark/light.
            set_theme(argument)
            return None
        if name == "doctor":
            # The TUI only points at `zen doctor`; the REPL can run it inline.
            from zenagent.cli.doctor import run_doctor

            await run_doctor(print_fn=print_fn)
            return None
        if name == "mcp" and argument.strip().lower().startswith("retry"):
            before = [tool.name for tool in agent.tools]
            for note in await runtime.refresh_mcp(force=True):
                print_fn(f"mcp: {note}")
            if [tool.name for tool in agent.tools] != before:
                print_fn(f"mcp: tools updated ({len(agent.tools)} available)")
            if runtime.mcp is not None:
                for status in runtime.mcp.status():
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
        if commands.get(name) is None:
            extension = (extension_commands or {}).get(name)
            if extension is not None:
                result = extension.handler(agent, argument)
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    print_fn(str(result))
                return None
            print_fn(t("repl.unknown_command", command=name))
            return None
        result = await commands.dispatch(name, argument)
        if result.show_help:
            print_help()
            return None
        outcome = render_command_result(
            result,
            name=name,
            services=services,
            print_fn=print_fn,
            keybindings=active_keybindings,
            set_theme=set_theme,
            set_language=set_language,
        )
        if name == "reload":
            _refresh_lists()
        return outcome

    def on_event(event: AgentEvent) -> None:
        printer(event)

    agent.subscribe(on_event)
    if console is not None:
        console.print(
            f"[grey50]{runtime.session.path.name}[/grey50] · "
            + t("repl.help_hint")
        )

    def skill_provider() -> list[tuple[str, str]]:
        return [(skill.name, skill.description) for skill in skill_list]

    read = input_fn or _make_default_reader(
        completion=completion,
        keybindings=active_keybindings,
        extra_commands=slash_commands(commands),
        extra_values={"/model": model_values},
        typed_provider=typed_commands,
        skill_provider=skill_provider,
    )
    #: Only one reader may own stdin. The steering reader parks in ``read()``;
    #: an interaction that starts mid-run (``ask``, tool approval, a wizard)
    #: claims the *next* line: the parked reader hands its result over through
    #: ``pending_lines`` instead of steering with it, and an interaction that
    #: starts while nothing is parked (inside command dispatch) reads directly.
    #: Before this protocol the ask handler raced the steering reader for the
    #: answer line and approvals used a blocking ``input()`` on the loop thread.
    input_lock = asyncio.Lock()
    interaction_active = asyncio.Event()
    parked = [False]
    pending_lines: deque = deque()
    interaction_depth = 0

    def _begin_interaction() -> None:
        nonlocal interaction_depth
        interaction_depth += 1
        interaction_active.set()

    def _end_interaction() -> None:
        nonlocal interaction_depth
        interaction_depth = max(0, interaction_depth - 1)
        if interaction_depth == 0:
            interaction_active.clear()

    async def _interaction_line() -> str:
        """One line for the active interaction, through the single stdin owner."""
        if parked[0]:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            pending_lines.append(future)
            try:
                line = await future
            except BaseException:
                # Cancelled before the line arrived: withdraw the claim so the
                # steering loop is not left handing a line to nobody.
                if future in pending_lines:
                    pending_lines.remove(future)
                raise
            if line is None:
                raise EOFError from None
            return str(line)
        async with input_lock:
            line = await read()
        if line is None:
            raise EOFError from None
        return str(line)

    async def ask_user(questions: list[dict]) -> list[dict]:
        """Answer an ``ask`` tool call from the terminal (blank = dismiss)."""
        _begin_interaction()
        try:
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
                        raw = await _interaction_line()
                    except (EOFError, KeyboardInterrupt):
                        return answers
                    parts = [
                        part
                        for part in raw.replace(",", " ").split()
                        if part.strip()
                    ]
                    if not parts:
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
            return answers
        finally:
            _end_interaction()

    async def confirm_via_stdin(tool_name: str, arguments: dict, verdict=None) -> str:
        """The broker's approval callback, on the ask question protocol.

        Returns ``"allow" | "always" | "deny"``; EOF/Ctrl+C deny, as the old
        blocking ``input()`` gate did.
        """
        _begin_interaction()
        try:
            print_fn(render_approval_prompt(tool_name, arguments, verdict))
            try:
                answer = (await _interaction_line()).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if answer in ("a", "all"):
                return "always"
            if answer in ("y", "yes"):
                return "allow"
            return "deny"
        finally:
            _end_interaction()

    was_busy = [False]

    async def reap() -> None:
        """Print the status line once the current run (if any) finishes."""
        if chat.busy:
            was_busy[0] = True
            return
        if not was_busy[0]:
            return
        await chat.wait_for_idle()
        was_busy[0] = False
        # Out-of-band lines (auto-compaction dividers) and provider failures
        # are part of the run's outcome: the TUI renders them in the transcript
        # and the error banner; the REPL must not swallow them.
        for notice in chat.take_notices():
            print_fn(notice.text)
        error = chat.status.last_error
        if error:
            chat.clear_last_error()
            print_fn(t("repl.run_error", error=error))
        print_fn(
            status_line(
                model_id=agent.model.id,
                thinking=agent.thinking,
                messages=len(agent.messages),
                usage=aggregate_usage(agent.messages),
                context_window=agent.model.context_window,
                session_name=runtime.session.path.name,
            )
        )

    runtime.ask_handler = ask_user
    broker.set_confirm(confirm_via_stdin)
    try:
        while True:
            await reap()
            if interaction_active.is_set():
                # A pending interaction (ask, approval, wizard) owns stdin.
                # Event.wait() on an already-set event does not yield to the
                # loop — and the interaction usually needs exactly one tick to
                # consume the answer the steering reader handed over. sleep(0)
                # provides that tick; only then park until it is done.
                await asyncio.sleep(0)
                if interaction_active.is_set():
                    await interaction_active.wait()
                continue
            line = None
            did_read = False
            try:
                async with input_lock:
                    if interaction_active.is_set():
                        line = None
                    else:
                        parked[0] = True
                        try:
                            line = await read()
                        finally:
                            parked[0] = False
                        did_read = True
            except (EOFError, KeyboardInterrupt) as exc:
                if pending_lines:
                    # The interrupt landed on a reader that had promised its
                    # line to an interaction: deny/dismiss that interaction
                    # instead of tearing down the whole session.
                    claimant = pending_lines.popleft()
                    if not claimant.done():
                        claimant.set_exception(exc)
                    continue
                if chat.busy:
                    chat.abort()
                    await chat.wait_for_idle()
                print_fn("")
                return 0
            if did_read and pending_lines:
                claimant = pending_lines.popleft()
                if not claimant.done():
                    claimant.set_result(line)
                continue
            if line is None:
                # Same no-yield trap as above: the inner check saw an active
                # interaction, so give it a tick before parking on it.
                await asyncio.sleep(0)
                if interaction_active.is_set():
                    await interaction_active.wait()
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
                max_body_bytes=runtime.limits.max_body_bytes,
            )
            if resolution is not None:
                if resolution.error is not None:
                    print_fn(resolution.error)
                    continue
                if resolution.submission is not None:
                    skill_prompt = resolution.submission.prompt
                    prompt_text = skill_prompt
                    command = ""
            if command:
                if await dispatch_slash(command, argument) == "exit":
                    chat.abort()
                    await chat.wait_for_idle()
                    return 0
                continue

            if chat.busy:
                # A run is still going: this line steers it instead of stacking.
                chat.steer(skill_prompt if skill_prompt is not None else line.strip())
                continue

            try:
                await runtime.refresh_mcp()
            except RuntimeError:
                pass
            await chat.submit(prompt_text)
    finally:
        runtime.ask_handler = None
        for claimant in pending_lines:
            if not claimant.done():
                claimant.cancel()
        if chat.busy:
            chat.abort()
            await chat.wait_for_idle()
        await runtime.close()
