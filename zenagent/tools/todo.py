"""Session todo list: one tool, nine ops, content-addressed tasks.

This is the minimal port of omp's todo tool described in
``docs/research/omp-cli-study/10b-omp-orchestration-tools.md`` §12.4:

* one ``todo`` :class:`~zenagent.agent.loop.AgentTool` with an ``op``
  discriminator; tasks are addressed by their **verbatim content**, never by
  generated IDs, so the model cannot drift out of sync with the list;
* phases run in order and an ``in_progress`` pointer advances automatically
  (earliest pending task), with one call changing one op atomically — a
  validation error discards the whole attempt and leaves the list untouched;
* state lives in a ``<session>.todo.json`` sidecar next to the JSONL
  (atomic replace, like the titles/pins sidecars), so a resumed session keeps
  its list; without a session path the state is memory-only;
* ``content`` is the compact model-facing summary; ``details["phases"]`` and
  ``details["op"]`` are the structured snapshot the TUI HUD renders.

The tool is injected by :meth:`zenagent.app.runtime.Runtime.bootstrap` instead
of being listed in ``zenagent.tools.ALL_TOOLS``: it is per-session state, not a
static built-in.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "DEFAULT_TODO_PHASE",
    "TODO_FILENAME_SUFFIX",
    "TodoPhase",
    "TodoTask",
    "TodoTool",
    "build_todo_tool",
    "coerce_todo_phases",
    "load_todo_phases",
    "summarize_todo",
    "todo_sidecar_path",
]

#: Appended to the session file name: ``20260101-120000.jsonl.todo.json``.
TODO_FILENAME_SUFFIX = ".todo.json"

#: Phase used when ``init`` receives a flat ``items`` list without a phase.
DEFAULT_TODO_PHASE = "Tasks"

_OPS = (
    "init",
    "start",
    "done",
    "rm",
    "drop",
    "block",
    "unblock",
    "append",
    "view",
)

_STATUSES = frozenset(
    {"pending", "in_progress", "completed", "abandoned", "blocked"}
)
_OPEN = frozenset({"pending", "in_progress"})
_CLOSED = frozenset({"completed", "abandoned"})

#: Markers ``view`` uses for its checklist (omp's convention).
_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[/]",
    "completed": "[x]",
    "abandoned": "[-]",
    "blocked": "[!]",
}


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass
class TodoTask:
    """One checklist item; content is the address, status is the state."""

    content: str
    status: str = "pending"
    blocker: str = ""

    def to_json(self) -> dict:
        data: dict = {"content": self.content, "status": self.status}
        if self.blocker:
            data["blocker"] = self.blocker
        return data


@dataclass
class TodoPhase:
    """An ordered stage holding tasks."""

    name: str
    tasks: list[TodoTask] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"name": self.name, "tasks": [task.to_json() for task in self.tasks]}


def _from_details(phases: list[dict]) -> list[TodoPhase]:
    # A hand-edited/corrupt sidecar can carry duplicate content; content is the
    # task identity, so keep the first occurrence and drop the rest.
    built: list[TodoPhase] = []
    for phase in phases:
        seen: set[str] = set()
        tasks: list[TodoTask] = []
        for task in phase["tasks"]:
            content = task["content"]
            if content in seen:
                continue
            seen.add(content)
            tasks.append(
                TodoTask(
                    content=content,
                    status=task["status"],
                    blocker=task.get("blocker", ""),
                )
            )
        built.append(TodoPhase(name=phase["name"], tasks=tasks))
    return built


def _to_details(phases: list[TodoPhase]) -> list[dict]:
    return [phase.to_json() for phase in phases]


def _clone(phases: list[TodoPhase]) -> list[TodoPhase]:
    return [
        TodoPhase(
            name=phase.name,
            tasks=[
                TodoTask(task.content, task.status, task.blocker)
                for task in phase.tasks
            ],
        )
        for phase in phases
    ]


# ---------------------------------------------------------------------------
# sidecar (<session>.todo.json, atomic replace)
# ---------------------------------------------------------------------------


def todo_sidecar_path(session_path: Path) -> Path:
    """``<session>.todo.json``: the sidecar living next to the JSONL."""
    path = Path(session_path)
    return path.with_name(path.name + TODO_FILENAME_SUFFIX)


def coerce_todo_phases(value: Any) -> list[dict]:
    """Validate and scrub a phases value loaded from disk or UI event details.

    The UI must not crash on a hand-edited/corrupt sidecar or on an unexpected
    ``details`` payload, so unknown shapes are dropped instead of raised.
    """
    if not isinstance(value, list):
        return []
    phases: list[dict] = []
    for raw_phase in value:
        if not isinstance(raw_phase, dict):
            continue
        name = raw_phase.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_tasks = raw_phase.get("tasks")
        tasks: list[dict] = []
        seen: set[str] = set()
        for raw_task in raw_tasks if isinstance(raw_tasks, list) else []:
            if not isinstance(raw_task, dict):
                continue
            content = raw_task.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if content.strip() in seen:
                continue  # content is the task identity; drop duplicate rows
            seen.add(content.strip())
            status = raw_task.get("status")
            if status not in _STATUSES:
                status = "pending"
            task: dict = {"content": content.strip(), "status": status}
            blocker = raw_task.get("blocker")
            if status == "blocked" and isinstance(blocker, str) and blocker.strip():
                task["blocker"] = " ".join(blocker.split())
            tasks.append(task)
        phases.append({"name": name.strip(), "tasks": tasks})
    return phases


def _write_todo_sidecar(session_path: Path, phases: list[dict]) -> None:
    """Replace the sidecar atomically: readers never see a half-written file."""
    sidecar = todo_sidecar_path(session_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=sidecar.parent, prefix=f"{sidecar.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "phases": phases},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, sidecar)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def load_todo_phases(session_path: Path | None) -> list[dict]:
    """Persisted phases for one session (UI dicts); ``[]`` when absent/corrupt.

    Todo state is a convenience, so a missing or unreadable sidecar must never
    break a session; the caller simply starts with an empty list.
    """
    if session_path is None:
        return []
    try:
        data = json.loads(todo_sidecar_path(session_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("phases")
    return coerce_todo_phases(data)


# ---------------------------------------------------------------------------
# summary (shared by the tool result text and the HUD)
# ---------------------------------------------------------------------------


def summarize_todo(phases: list[dict]) -> dict:
    """Counts and the current pointer, for the result text and the HUD."""
    all_tasks = [task for phase in phases for task in phase.get("tasks", [])]
    total = len(all_tasks)
    closed = sum(1 for task in all_tasks if task.get("status") in _CLOSED)
    blocked = sum(1 for task in all_tasks if task.get("status") == "blocked")

    phase_name = ""
    phase_index = 0
    phase_total = 0
    phase_closed = 0
    for index, phase in enumerate(phases):
        tasks = phase.get("tasks", [])
        if any(task.get("status") in _OPEN for task in tasks):
            phase_name = phase.get("name", "")
            phase_index = index + 1
            phase_total = len(tasks)
            phase_closed = sum(1 for task in tasks if task.get("status") in _CLOSED)
            break
    if not phase_name and total > closed:
        # Only blocked work is left: keep pointing at a real phase.
        for index in range(len(phases) - 1, -1, -1):
            tasks = phases[index].get("tasks", [])
            if tasks:
                phase_name = phases[index].get("name", "")
                phase_index = index + 1
                phase_total = len(tasks)
                phase_closed = sum(
                    1 for task in tasks if task.get("status") in _CLOSED
                )
                break

    current = ""
    for phase in phases:
        for task in phase.get("tasks", []):
            if task.get("status") == "in_progress":
                current = task.get("content", "")
                break
        if current:
            break
    if not current:
        for phase in phases:
            for task in phase.get("tasks", []):
                if task.get("status") == "pending":
                    current = task.get("content", "")
                    break
            if current:
                break

    return {
        "total": total,
        "closed": closed,
        "remaining": total - closed,
        "blocked": blocked,
        "phase": phase_name,
        "phase_index": phase_index,
        "phase_total": phase_total,
        "phase_closed": phase_closed,
        "current": current,
    }


def _task_tag(task: dict) -> str:
    status = task.get("status")
    if status == "in_progress":
        return " (in progress)"
    if status == "abandoned":
        return " (dropped)"
    if status == "blocked":
        blocker = task.get("blocker")
        return f" (blocked: {blocker})" if blocker else " (blocked)"
    return ""


def _format_summary(phases: list[dict], errors: list[str], *, read_only: bool) -> str:
    lines: list[str] = []
    if errors:
        lines.append("Error: " + "; ".join(errors))
    info = summarize_todo(phases)
    if info["total"] == 0:
        if not errors:
            lines.append("Todo list is empty." if read_only else "Todo list cleared.")
        return "\n".join(lines)

    head = (
        f"todo · {info['closed']}/{info['total']} closed · "
        f"{info['remaining']} remaining"
    )
    if info["blocked"]:
        head += f" · {info['blocked']} blocked"
    lines.append(head)
    if info["remaining"] == 0:
        lines.append("all tasks closed")
    elif info["phase"]:
        tail = f"phase: {info['phase']} ({info['phase_closed']}/{info['phase_total']})"
        if info["current"]:
            tail += f" · working on: {info['current']}"
        lines.append(tail)
    if read_only:
        for phase in phases:
            tasks = phase.get("tasks", [])
            closed = sum(1 for task in tasks if task.get("status") in _CLOSED)
            lines.append(f"  {phase.get('name', '')} ({closed}/{len(tasks)}):")
            for task in tasks:
                marker = _MARKERS.get(task.get("status", "pending"), "[ ]")
                lines.append(
                    f"    {marker} {task.get('content', '')}{_task_tag(task)}"
                )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# op implementations (pure: they only touch the cloned phase list)
# ---------------------------------------------------------------------------


def _find_task(phases: list[TodoPhase], content: str | None) -> TodoTask | None:
    if not isinstance(content, str):
        return None
    needle = content.strip()
    for phase in phases:
        for task in phase.tasks:
            if task.content == needle:
                return task
    return None


def _find_phase(phases: list[TodoPhase], name: str | None) -> TodoPhase | None:
    if not isinstance(name, str):
        return None
    needle = name.strip()
    for phase in phases:
        if phase.name == needle:
            return phase
    return None


def _available_tasks(phases: list[TodoPhase]) -> str:
    contents = [task.content for phase in phases for task in phase.tasks]
    if not contents:
        return ""
    shown = ", ".join(f'"{content}"' for content in contents[:12])
    if len(contents) > 12:
        shown += ", …"
    return shown


def _resolve_task(
    phases: list[TodoPhase], content: str | None, errors: list[str]
) -> TodoTask | None:
    if not isinstance(content, str) or not content.strip():
        errors.append("missing task content (pass the task's exact text)")
        return None
    needle = content.strip()
    hit = _find_task(phases, needle)
    if hit is not None:
        return hit
    if re.fullmatch(r"task-\d+", needle):
        errors.append(
            f'Task "{needle}" not found: tasks are addressed by their exact '
            "content, not by IDs — pass the full task text from the previous result"
        )
        return None
    available = _available_tasks(phases)
    if available:
        errors.append(
            f'Task "{needle}" not found; available tasks: {available}'
        )
    else:
        errors.append(f'Task "{needle}" not found (the todo list is empty)')
    return None


def _resolve_phase(
    phases: list[TodoPhase], name: str | None, errors: list[str]
) -> TodoPhase | None:
    if not isinstance(name, str) or not name.strip():
        errors.append("missing phase name")
        return None
    needle = name.strip()
    phase = _find_phase(phases, needle)
    if phase is not None:
        return phase
    available = ", ".join(f'"{candidate.name}"' for candidate in phases) or "(none)"
    errors.append(f'Phase "{needle}" not found; available phases: {available}')
    return None


def _targets(
    phases: list[TodoPhase], args: dict, errors: list[str]
) -> list[TodoTask]:
    """Tasks a task-or-phase op applies to (all tasks when neither is given)."""
    if args.get("task"):
        hit = _resolve_task(phases, args.get("task"), errors)
        return [hit] if hit is not None else []
    if args.get("phase"):
        phase = _resolve_phase(phases, args.get("phase"), errors)
        return list(phase.tasks) if phase is not None else []
    return [task for phase in phases for task in phase.tasks]


def _init_phases(args: dict, errors: list[str]) -> list[TodoPhase]:
    entries = args.get("list")
    flat = args.get("items")
    if not (isinstance(entries, list) and entries) and isinstance(flat, list) and flat:
        raw_phase = args.get("phase")
        phase_name = (
            raw_phase.strip()
            if isinstance(raw_phase, str) and raw_phase.strip()
            else DEFAULT_TODO_PHASE
        )
        entries = [{"phase": phase_name, "items": flat}]
    if not isinstance(entries, list) or not entries:
        errors.append(
            "missing list for init (pass list=[{phase, items}] or items=[...])"
        )
        return []

    seen_phases: set[str] = set()
    seen_tasks: set[str] = set()
    built: list[TodoPhase] = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("each init entry must be an object {phase, items}")
            continue
        raw_name = entry.get("phase")
        if not isinstance(raw_name, str) or not raw_name.strip():
            errors.append("each init entry needs a non-empty phase name")
            continue
        name = raw_name.strip()
        raw_items = entry.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            errors.append(f'Phase "{name}" needs a non-empty items list')
            continue
        if name in seen_phases:
            errors.append(f'Duplicate phase "{name}" in init list')
        seen_phases.add(name)
        tasks: list[TodoTask] = []
        for raw in raw_items:
            if not isinstance(raw, str) or not raw.strip():
                errors.append(f'Phase "{name}" has a blank task content')
                continue
            content = raw.strip()
            if content in seen_tasks:
                errors.append(f'Duplicate task "{content}" in init list')
            seen_tasks.add(content)
            tasks.append(TodoTask(content))
        built.append(TodoPhase(name, tasks))
    return built


def _append_tasks(
    phases: list[TodoPhase], args: dict, errors: list[str]
) -> list[TodoPhase]:
    name = args.get("phase")
    items = args.get("items")
    if not isinstance(name, str) or not name.strip():
        errors.append("append requires a phase name")
        return phases
    name = name.strip()
    if not isinstance(items, list) or not items:
        errors.append("append requires a non-empty items list")
        return phases

    contents: list[str] = []
    for raw in items:
        if not isinstance(raw, str) or not raw.strip():
            errors.append("append items must be non-empty task contents")
            continue
        content = raw.strip()
        if content in contents or _find_task(phases, content) is not None:
            errors.append(f'Task "{content}" already exists')
        contents.append(content)
    if errors:  # validate the whole batch before creating a phase
        return phases

    phase = _find_phase(phases, name)
    if phase is None:
        phase = TodoPhase(name)
        phases.append(phase)
    phase.tasks.extend(TodoTask(content) for content in contents)
    return phases


def _remove_tasks(
    phases: list[TodoPhase], args: dict, errors: list[str]
) -> list[TodoPhase]:
    if args.get("task"):
        hit = _resolve_task(phases, args.get("task"), errors)
        if hit is None:
            return phases
        for phase in phases:
            if hit in phase.tasks:
                phase.tasks.remove(hit)
                break
        return phases
    if args.get("phase"):
        phase = _resolve_phase(phases, args.get("phase"), errors)
        if phase is not None:
            phase.tasks.clear()
        return phases
    for phase in phases:
        phase.tasks.clear()
    return phases


def _apply(phases: list[TodoPhase], args: dict, errors: list[str]) -> list[TodoPhase]:
    op = args.get("op")
    if op == "init":
        return _init_phases(args, errors)
    if op == "start":
        hit = _resolve_task(phases, args.get("task"), errors)
        if hit is None:
            return phases
        for phase in phases:
            for task in phase.tasks:
                if task is not hit and task.status == "in_progress":
                    task.status = "pending"
        hit.status = "in_progress"
        return phases
    if op == "done":
        for task in _targets(phases, args, errors):
            task.status = "completed"
        return phases
    if op == "drop":
        for task in _targets(phases, args, errors):
            task.status = "abandoned"
        return phases
    if op == "block":
        if not args.get("task") and not args.get("phase"):
            errors.append("block requires a task or phase target")
            return phases
        reason = " ".join(str(args.get("reason") or "").split())
        for task in _targets(phases, args, errors):
            if task.status not in ("pending", "in_progress", "blocked"):
                continue
            task.status = "blocked"
            task.blocker = reason
        return phases
    if op == "unblock":
        if not args.get("task") and not args.get("phase"):
            errors.append("unblock requires a task or phase target")
            return phases
        for task in _targets(phases, args, errors):
            if task.status == "blocked":
                task.status = "pending"
                task.blocker = ""
        return phases
    if op == "rm":
        return _remove_tasks(phases, args, errors)
    if op == "append":
        return _append_tasks(phases, args, errors)
    if op == "view":
        return phases
    errors.append(f'unknown op "{op}" (choose {", ".join(_OPS)})')
    return phases


def _normalize_in_progress(phases: list[TodoPhase]) -> None:
    """One in-progress pointer at most; advances to the earliest pending task."""
    ordered = [task for phase in phases for task in phase.tasks]
    if not ordered:
        return
    in_progress = [task for task in ordered if task.status == "in_progress"]
    if len(in_progress) > 1:
        for task in in_progress[1:]:
            task.status = "pending"
    if in_progress:
        return
    for task in ordered:
        if task.status == "pending":
            task.status = "in_progress"
            return


# ---------------------------------------------------------------------------
# state + the tool
# ---------------------------------------------------------------------------


class _TodoState:
    """Mutable state for one tool instance: sidecar-backed or memory-only."""

    def __init__(self, session_path: Path | None) -> None:
        self._session_path = Path(session_path) if session_path is not None else None
        self._phases = self._load()

    @property
    def storage(self) -> str:
        return "session" if self._session_path is not None else "memory"

    def _load(self) -> list[TodoPhase]:
        return _from_details(load_todo_phases(self._session_path))

    def bind_session(self, session_path: Path | None) -> None:
        """Point the tool at another session's sidecar (loaded when present).

        Called by ``Runtime.switch_session`` so /new, /sessions and compaction
        each show the list belonging to the session now in context.
        """
        self._session_path = Path(session_path) if session_path is not None else None
        self._phases = self._load()

    def _persist(self, phases: list[TodoPhase]) -> None:
        if self._session_path is not None:
            _write_todo_sidecar(self._session_path, _to_details(phases))

    def clear(self) -> None:
        """Drop the list (memory and sidecar)."""
        self._phases = []
        self._persist(self._phases)

    async def execute(
        self,
        tool_call_id: str,
        args: dict,
        abort: Any = None,
        on_update: Callable[[ToolResultAgent], None] | None = None,
    ) -> ToolResultAgent:
        op = str(args.get("op") or "").strip()
        errors: list[str] = []
        previous = _clone(self._phases)
        read_only = op == "view"
        if op not in _OPS:
            errors.append(f'unknown op "{op}" (choose {", ".join(_OPS)})')
            updated = previous
        elif read_only:
            # A pure read: no normalization, no write, no state change.
            updated = previous
        else:
            updated = _apply(_clone(previous), args, errors)
            if not errors:
                _normalize_in_progress(updated)

        failed = bool(errors)
        effective = previous if failed else updated
        if not failed and not read_only:
            try:
                self._persist(updated)
            except OSError as exc:
                errors.append(f"could not save the todo list: {exc}")
                failed = True
                effective = previous
            else:
                self._phases = updated

        details_phases = _to_details(effective)
        return ToolResultAgent(
            content=[
                TextContent(
                    text=_format_summary(details_phases, errors, read_only=read_only)
                )
            ],
            details={
                "op": op,
                "phases": details_phases,
                "storage": self.storage,
            },
            is_error=failed,
        )


def save_todo_phases(session_path: Path, phases: list[dict]) -> None:
    """Write canonical phase details to a session sidecar (atomic)."""
    _write_todo_sidecar(Path(session_path), coerce_todo_phases(phases))


def _noop_bind(session_path: Path | None) -> None:
    """Default ``bind_session`` for the dataclass field (never called)."""


_DESCRIPTION = (
    "Track progress on a multi-step task with a phased checklist. Use it when "
    "the work has 3+ steps, call it in the same response as your plan, and keep "
    "it current: start a task before working on it, mark it done when it is (or "
    "drop it), and block it with a one-line reason when you cannot proceed. "
    "Tasks are addressed by their exact content, never by generated IDs. Phases "
    "run in order and the in-progress pointer advances automatically. One op per "
    "call: init replaces the list; start/done/drop/block/unblock take a task "
    "(done/drop/block/unblock also accept a phase, and default to every task); "
    "rm removes a task or clears a phase; append adds tasks to a phase; view is "
    "a read-only checklist. A failed call changes nothing."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": list(_OPS),
            "description": "operation to apply",
        },
        "list": {
            "type": "array",
            "description": (
                'init: phases as [{"phase": "name", "items": ["task content"]}]'
            ),
        },
        "items": {
            "type": "array",
            "description": (
                "init: flat task contents for one phase (with phase, default "
                f"'{DEFAULT_TODO_PHASE}'); append: task contents to add"
            ),
        },
        "task": {
            "type": "string",
            "description": "verbatim task content (never an ID)",
        },
        "phase": {
            "type": "string",
            "description": (
                "phase name: target for done/drop/block/unblock/rm, or the "
                "phase append adds to"
            ),
        },
        "reason": {
            "type": "string",
            "description": "block: one-line reason the task cannot proceed",
        },
    },
    "required": ["op"],
}


@dataclass
class TodoTool(AgentTool):
    """The ``todo`` tool plus the session-rebinding/clear hooks the runtime uses."""

    bind_session: Callable[[Path | None], None] = _noop_bind
    clear: Callable[[], None] = lambda: None


def build_todo_tool(session_path: Path | None) -> TodoTool:
    """Build the per-session ``todo`` tool (sidecar-backed, or memory-only)."""
    state = _TodoState(session_path)
    return TodoTool(
        name="todo",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        execution_mode="sequential",
        execute=state.execute,
        approval="read",
        bind_session=state.bind_session,
        clear=state.clear,
    )
