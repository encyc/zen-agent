"""The tunable budgets behind the settings page: ``[limits]`` and ``[task]``.

These values used to be module constants scattered across the kernel
(``skills.MAX_INDEX_BYTES``, ``loop._MAX_TURNS``, ``shell.MAX_OUTPUT_BYTES``)
or reachable only by hand-editing ``config.toml`` (``[task]``). One registry
here is the single description of every knob — its table, range, default,
unit and when a change takes effect — so the settings page, ``zen doctor`` and
the config parser cannot drift apart:

    [limits.skills]
    max_index_bytes = 12288

    [limits.context]
    compact_threshold = 0.85

    [task]                    # subagent budgets, already shipped
    max_turns = 300

Three rules shape this module:

* **Safe ranges, never silent clamping.** A value outside a knob's range is an
  error naming the file and key, like every other config error. Clamping would
  let the UI show one number while the kernel used another.
* **Global-only.** A committed ``<repo>/.zenagent/config.toml`` must not be
  able to widen the budgets of the machine it is cloned onto — the same reason
  ``[safety]``/``[backup]`` are global-only.
* **Surgical writes.** ``config.toml`` is hand-edited, so :func:`write_managed`
  replaces only the managed tables' lines and leaves every other byte
  (comments, ordering, blank lines) untouched. There is no TOML serializer on
  purpose: a round-trip would delete the comments.

Not to be confused with :mod:`zenagent.providers.limits`, which estimates token
counts from model metadata.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

__all__ = [
    "ALL_KNOBS",
    "GROUP_ORDER",
    "KNOBS_BY_TABLE",
    "KNOB_BY_ATTR",
    "KNOB_BY_KEY",
    "LIMITS",
    "MANAGED_TABLES",
    "SWARM_KNOBS",
    "TASK_KNOBS",
    "ZEN_KNOBS",
    "Limit",
    "Limits",
    "LimitsWriteError",
    "coerce_value",
    "describe",
    "parse_limits",
    "read_managed",
    "render_limits_block",
    "write_limits",
    "write_managed",
]

Kind = Literal["int", "float", "str"]
Apply = Literal["immediate", "session"]


@dataclass(frozen=True)
class Limit:
    """One tunable knob: where it lives, what it accepts, when it takes effect."""

    table: str
    """The TOML table holding it: ``limits.<section>`` or ``task``."""

    name: str
    """The TOML key inside that table."""

    section: str
    """Presentation group on the settings page."""

    attr: str
    """Field name: on :class:`Limits` for ``[limits]``, on ``UserConfig`` for
    ``[task]`` (``task_max_turns`` and friends)."""

    default: int | float | str | None
    kind: Kind
    minimum: float
    maximum: float
    apply: Apply
    """``immediate`` = the next call sees it; ``session`` = next session."""

    unit: str = ""
    nullable: bool = False
    """``None`` is legal and means "off"/"uncapped" (``task.max_cost``)."""

    choices: tuple[str, ...] = ()
    """Allowed values for ``kind="str"``."""

    @property
    def key(self) -> str:
        return f"{self.table}.{self.name}"


#: The ``[limits]`` registry: one row per knob, in presentation order.
LIMITS: tuple[Limit, ...] = (
    Limit(
        table="limits.skills",
        name="max_index_bytes",
        section="skills",
        attr="max_index_bytes",
        default=12288,
        kind="int",
        minimum=1024,
        maximum=262144,
        apply="immediate",
        unit="bytes",
    ),
    Limit(
        table="limits.skills",
        name="max_body_bytes",
        section="skills",
        attr="max_body_bytes",
        default=32768,
        kind="int",
        minimum=1024,
        maximum=1048576,
        apply="immediate",
        unit="bytes",
    ),
    Limit(
        table="limits.context",
        name="max_context_bytes",
        section="context",
        attr="max_context_bytes",
        default=32768,
        kind="int",
        minimum=0,
        maximum=1048576,
        apply="session",
        unit="bytes",
    ),
    Limit(
        table="limits.context",
        name="compact_threshold",
        section="context",
        attr="compact_threshold",
        default=0.85,
        kind="float",
        minimum=0.3,
        maximum=0.99,
        apply="session",
        unit="fraction of the context window",
    ),
    Limit(
        table="limits.agent",
        name="max_turns",
        section="agent",
        attr="max_turns",
        default=100,
        kind="int",
        minimum=1,
        maximum=10000,
        apply="session",
        unit="provider requests per run",
    ),
    Limit(
        table="limits.agent",
        name="loop_guard_threshold",
        section="agent",
        attr="loop_guard_threshold",
        default=5,
        kind="int",
        minimum=2,
        maximum=100,
        apply="immediate",
        unit="identical repeats",
    ),
    Limit(
        table="limits.agent",
        name="max_result_bytes",
        section="agent",
        attr="max_result_bytes",
        default=40000,
        kind="int",
        minimum=4096,
        maximum=1048576,
        apply="session",
        unit="bytes per tool result",
    ),
    Limit(
        table="limits.tools",
        name="shell_max_output_bytes",
        section="tools",
        attr="shell_max_output_bytes",
        default=30000,
        kind="int",
        minimum=1024,
        maximum=1048576,
        apply="immediate",
        unit="bytes of bash output",
    ),
)

#: The ``[task]`` registry: subagent budgets that already shipped as config.
TASK_KNOBS: tuple[Limit, ...] = (
    Limit(
        table="task",
        name="max_turns",
        section="subagents",
        attr="task_max_turns",
        default=300,
        kind="int",
        minimum=1,
        maximum=100000,
        apply="session",
        unit="provider requests per subagent",
    ),
    Limit(
        table="task",
        name="timeout_s",
        section="subagents",
        attr="task_timeout_s",
        default=900,
        kind="int",
        minimum=0,
        maximum=86400,
        apply="session",
        unit="seconds (0 = no watchdog)",
    ),
    Limit(
        table="task",
        name="max_cost",
        section="subagents",
        attr="task_max_cost",
        default=None,
        kind="float",
        minimum=0.000001,
        maximum=1000000.0,
        apply="session",
        unit="USD per subagent",
        nullable=True,
    ),
    Limit(
        table="task",
        name="keep_transcripts",
        section="subagents",
        attr="task_keep_transcripts",
        default=64,
        kind="int",
        minimum=1,
        maximum=10000,
        apply="session",
        unit="transcripts per session",
    ),
    Limit(
        table="task",
        name="max_transcript_bytes",
        section="subagents",
        attr="task_max_transcript_bytes",
        default=32 * 1024 * 1024,
        kind="int",
        minimum=1024 * 1024,
        maximum=2 * 1024 * 1024 * 1024,
        apply="session",
        unit="bytes per session",
    ),
    Limit(
        table="task",
        name="max_transcript_file_bytes",
        section="subagents",
        attr="task_max_transcript_file_bytes",
        default=8 * 1024 * 1024,
        kind="int",
        minimum=1024 * 1024,
        maximum=2 * 1024 * 1024 * 1024,
        apply="session",
        unit="bytes per transcript",
    ),
)

#: The ``[zen]`` registry: fleet behaviour. Defaults mirror
#: ``zenagent.config``; both are global-only.
ZEN_KNOBS: tuple[Limit, ...] = (
    Limit(
        table="zen",
        name="max_concurrent",
        section="zen",
        attr="zen_max_concurrent",
        default=3,
        kind="int",
        minimum=1,
        maximum=16,
        apply="session",
        unit="workers running at once",
    ),
    Limit(
        table="zen",
        name="batch_window_s",
        section="zen",
        attr="zen_batch_window_s",
        default=0.5,
        kind="float",
        minimum=0.0,
        maximum=10.0,
        apply="session",
        unit="seconds; 0 disables batching",
    ),
    Limit(
        table="zen",
        name="report_limit",
        section="zen",
        attr="zen_report_limit",
        default=800,
        kind="int",
        minimum=200,
        maximum=20000,
        apply="session",
        unit="characters per worker report",
    ),
    Limit(
        table="zen",
        name="worker_mode",
        section="zen",
        attr="zen_worker_mode",
        default="inherit",
        kind="str",
        minimum=0,
        maximum=0,
        apply="session",
        unit="inherit|ask|plan|zen",
        choices=("inherit", "ask", "plan", "zen"),
    ),
)

#: The ``[swarm]`` registry: per-room policy. Defaults mirror ``zenagent.config``.
SWARM_KNOBS: tuple[Limit, ...] = (
    Limit(
        table="swarm",
        name="max_concurrent",
        section="swarm",
        attr="swarm_max_concurrent",
        default=3,
        kind="int",
        minimum=1,
        maximum=16,
        apply="session",
        unit="role runs in flight",
    ),
    Limit(
        table="swarm",
        name="max_chain_depth",
        section="swarm",
        attr="swarm_max_chain_depth",
        default=8,
        kind="int",
        minimum=1,
        maximum=32,
        apply="session",
        unit="delegations per chain",
    ),
    Limit(
        table="swarm",
        name="max_chain_cost",
        section="swarm",
        attr="swarm_max_chain_cost",
        default=5.0,
        kind="float",
        minimum=0.01,
        maximum=1000.0,
        apply="session",
        unit="USD per chain",
    ),
    Limit(
        table="swarm",
        name="runtime_idle_s",
        section="swarm",
        attr="swarm_runtime_idle_s",
        default=600.0,
        kind="float",
        minimum=0.0,
        maximum=86400.0,
        apply="session",
        unit="seconds idle before a run unit is reclaimed; 0 disables",
    ),
)

ALL_KNOBS: tuple[Limit, ...] = LIMITS + TASK_KNOBS + ZEN_KNOBS + SWARM_KNOBS
KNOB_BY_KEY: dict[str, Limit] = {knob.key: knob for knob in ALL_KNOBS}
KNOB_BY_ATTR: dict[str, Limit] = {knob.attr: knob for knob in ALL_KNOBS}
KNOBS_BY_TABLE: dict[str, tuple[Limit, ...]] = {
    table: tuple(knob for knob in ALL_KNOBS if knob.table == table)
    for table in {knob.table for knob in ALL_KNOBS}
}
#: Tables this module owns end-to-end (rewriting one is lossless).
MANAGED_TABLES: tuple[str, ...] = (
    "limits.skills",
    "limits.context",
    "limits.agent",
    "limits.tools",
    "task",
    "zen",
    "swarm",
)

#: Presentation order of the groups, shared by the page and the docs.
GROUP_ORDER: tuple[str, ...] = ("skills", "context", "agent", "subagents", "tools")


@dataclass(frozen=True)
class Limits:
    """Effective ``[limits]`` values, plus which of them the file sets.

    ``explicit`` matters for two behaviours: "reset to default" deletes the key
    (so a future release changing the default is inherited), and the writer only
    emits keys that are explicit.
    """

    max_index_bytes: int = 12288
    max_body_bytes: int = 32768
    max_context_bytes: int = 32768
    compact_threshold: float = 0.85
    max_turns: int = 100
    loop_guard_threshold: int = 5
    max_result_bytes: int = 40000
    shell_max_output_bytes: int = 30000
    explicit: frozenset[str] = frozenset()

    def value(self, attr: str) -> int | float:
        return getattr(self, attr)

    def with_value(self, attr: str, value: float, *, explicit: bool = True) -> Limits:
        names = set(self.explicit)
        if explicit:
            names.add(attr)
        else:
            names.discard(attr)
        return replace(self, **{attr: value}, explicit=frozenset(names))


class LimitsWriteError(RuntimeError):
    """The file shape makes a surgical write unsafe; the message says why."""


def coerce_value(knob: Limit, value: object, target: Path) -> int | float | str | None:
    """Validate one raw value against its knob; returns the coerced value."""
    if knob.kind == "str":
        if not isinstance(value, str) or value not in knob.choices:
            raise ValueError(
                f"config {target}: {knob.key} must be one of {list(knob.choices)}"
            )
        return value
    if value is None:
        if knob.nullable:
            return None
        raise ValueError(f"config {target}: {knob.key} must be a number")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: {knob.key} must be a "
            f"{'number' if knob.kind == 'float' else 'integer'}"
        )
    if knob.kind == "int":
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"config {target}: {knob.key} must be an integer")
            value = int(value)
        coerced: int | float | None = int(value)
    else:
        coerced = float(value)
    if not (knob.minimum <= coerced <= knob.maximum):
        raise ValueError(
            f"config {target}: {knob.key} ({coerced}) must be between "
            f"{knob.minimum} and {knob.maximum}"
        )
    return coerced


def parse_limits(raw: object, target: Path) -> Limits:
    """Parse the ``[limits]`` table (absent → defaults); raises ``ValueError``."""
    if raw is None:
        return Limits()
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: [limits] must be a table"
        )
    sections = {knob.section for knob in LIMITS}
    unknown_sections = set(raw) - sections
    if unknown_sections:
        raise ValueError(
            f"config {target}: [limits] has unknown tables "
            f"{sorted(unknown_sections)} (allowed: {sorted(sections)})"
        )
    values: dict[str, int | float] = {}
    explicit: set[str] = set()
    for section in sorted(sections):
        table = raw.get(section)
        if table is None:
            continue
        if not isinstance(table, dict):
            raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
                f"config {target}: [limits.{section}] must be a table"
            )
        known = {knob.name for knob in LIMITS if knob.section == section}
        unknown = set(table) - known
        if unknown:
            raise ValueError(
                f"config {target}: [limits.{section}] has unknown keys "
                f"{sorted(unknown)} (allowed: {sorted(known)})"
            )
        for knob in LIMITS:
            if knob.section != section or knob.name not in table:
                continue
            coerced = coerce_value(knob, table[knob.name], target)
            if coerced is None:  # [limits] knobs are never nullable
                raise ValueError(f"config {target}: {knob.key} must be a number")
            values[knob.attr] = coerced
            explicit.add(knob.attr)
    return Limits(**values, explicit=frozenset(explicit))


# -- writing -----------------------------------------------------------------

_TABLE_RE = re.compile(r"^\s*\[(?P<name>[^\[\]]+)\]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\s*=")

MANAGED_NOTES: dict[str, str] = {
    "limits": (
        "# Managed by zen's settings page (Parameters). Global-only: a project "
        "layer may not set these."
    ),
    "task": (
        "# Managed by zen's settings page (Parameters · Subagents). Global-only: "
        "subagent budgets are a machine-level guard."
    ),
    "zen": (
        "# Managed by zen's settings page (Zen). Global-only: fleet limits are a "
        "machine-level guard."
    ),
    "swarm": (
        "# Managed by zen's settings page (Swarm). Global-only: room policy is a "
        "machine-level guard."
    ),
}


def _full_path(table: str, key: str) -> str:
    return f"{table}.{key}" if table else key


def _owned(path: str) -> bool:
    """Whether ``path`` is a managed table or a key inside one."""
    return any(
        path == table or path.startswith(f"{table}.") for table in MANAGED_TABLES
    )


def _render_value(knob: Limit, value: object) -> str:
    if value is None:
        return ""
    if knob.kind == "str":
        return json.dumps(str(value))
    if knob.kind == "float":
        return repr(float(value))
    return str(int(value))


def _render_tables(tables: dict[str, dict[str, object]]) -> str:
    """Render managed tables in registry order, skipping ones left empty."""
    blocks: list[str] = []
    noted: set[str] = set()
    for table in MANAGED_TABLES:
        values = tables.get(table) or {}
        written = [
            (knob, values[knob.name])
            for knob in KNOBS_BY_TABLE[table]
            if values.get(knob.name) is not None
        ]
        if not written:
            continue
        root = table.split(".")[0]
        if root not in noted:
            if blocks:
                blocks.append("")
            blocks.append(MANAGED_NOTES[root])
            noted.add(root)
        blocks.append("")
        blocks.append(f"[{table}]")
        for knob, value in written:
            blocks.append(f"{knob.name} = {_render_value(knob, value)}")
    return "\n".join(blocks) + "\n" if blocks else ""


def render_limits_block(limits: Limits) -> str:
    """The canonical ``[limits.*]`` text for the explicit knobs ("" when none)."""
    if not limits.explicit:
        return ""
    return _render_tables(
        {
            table: {
                knob.name: limits.value(knob.attr)
                for knob in KNOBS_BY_TABLE[table]
                if table.startswith("limits") and knob.attr in limits.explicit
            }
            for table in MANAGED_TABLES
        }
    )


def read_managed(path: Path) -> dict[str, dict[str, object]]:
    """The managed tables as they are on disk (raw values, managed keys only)."""
    path = Path(path).expanduser()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise LimitsWriteError(f"cannot read {path}: {exc}") from exc
    found: dict[str, dict[str, object]] = {}
    for table in MANAGED_TABLES:
        node: object = data
        for part in table.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            known = {knob.name for knob in KNOBS_BY_TABLE[table]}
            rows = {key: value for key, value in node.items() if key in known}
            if rows:
                found[table] = rows
    return found


def _scan_owned(lines: list[str]) -> tuple[int, set[int]]:
    """Return (index of the first managed line, indexes to drop).

    Dropping is region-aware: a managed table header takes its whole region
    (the blank lines and comments under it belong to that table), while an
    owned assignment inside someone else's table only takes itself — the rest
    of that table is the user's.
    """
    drop: set[int] = set()
    first = -1
    table = ""
    index = 0
    while index < len(lines):
        if lines[index].strip() in MANAGED_NOTES.values():
            first = index if first < 0 else first
            drop.add(index)
            index += 1
            continue
        header = _TABLE_RE.match(lines[index])
        if header:
            table = header.group("name").strip()
            if _owned(table):
                first = index if first < 0 else first
                drop.add(index)
                index += 1
                while index < len(lines) and not _TABLE_RE.match(lines[index]):
                    drop.add(index)
                    index += 1
                continue
            index += 1
            continue
        assignment = _KEY_RE.match(lines[index])
        if assignment and _owned(_full_path(table, assignment.group("key"))):
            first = index if first < 0 else first
            drop.add(index)
        index += 1
    return first, drop


def _unknown_managed_keys(original: str) -> list[str]:
    """Managed paths that are not registered knobs (the writer refuses those)."""
    unknown: list[str] = []
    table = ""
    for line in original.splitlines():
        header = _TABLE_RE.match(line)
        if header:
            table = header.group("name").strip()
            if _owned(table) and table not in MANAGED_TABLES:
                unknown.append(table)
            continue
        assignment = _KEY_RE.match(line)
        if not assignment:
            continue
        full = _full_path(table, assignment.group("key"))
        if _owned(full) and full not in KNOB_BY_KEY:
            unknown.append(full)
    return unknown


def write_managed(path: Path, tables: dict[str, dict[str, object]]) -> None:
    """Rewrite the managed tables in ``path``; every other byte is preserved.

    ``tables`` maps a managed table to its ``{name: value}`` rows; an empty or
    missing table means "no keys" and its lines are deleted (that is how
    "reset to default" is represented). Raises :class:`LimitsWriteError` when
    the file carries managed keys this build does not know — rewriting those
    would silently delete a newer version's settings.
    """
    path = Path(path).expanduser()
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    unknown = _unknown_managed_keys(original)
    if unknown:
        raise LimitsWriteError(
            f"{path}: unknown keys {sorted(set(unknown))} in managed tables; "
            "fix the file by hand before using the settings page"
        )
    lines = original.splitlines(keepends=True)
    first, drop = _scan_owned(lines)
    kept = [(index, line) for index, line in enumerate(lines) if index not in drop]
    block = _render_tables(tables)
    at = (
        sum(1 for index, _ in kept if index < first)
        if block and first >= 0
        else len(kept)
    )
    head = "".join(line for _, line in kept[:at])
    tail = "".join(line for _, line in kept[at:])
    _atomic_write(path, _join(head, block, tail))


def write_limits(path: Path, limits: Limits) -> None:
    """Write only the ``[limits]`` half (the ``[task]`` half is left alone)."""
    tables = read_managed(path)
    for table in MANAGED_TABLES:
        if not table.startswith("limits"):
            continue
        tables[table] = {
            knob.name: limits.value(knob.attr)
            for knob in KNOBS_BY_TABLE[table]
            if knob.attr in limits.explicit
        }
    write_managed(path, tables)


def _join(head: str, block: str, tail: str) -> str:
    """Splice the managed block back in, keeping one blank line at each seam."""
    parts: list[str] = []
    if head.strip("\n"):
        parts.append(head.rstrip("\n"))
    if block.strip():
        parts.append(block.rstrip("\n"))
    if tail.strip("\n"):
        parts.append(tail.lstrip("\n").rstrip("\n"))
    return ("\n\n".join(parts) + "\n") if parts else ""


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` through a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o644
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(name, mode)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def describe(limits: Limits) -> list[str]:
    """``key = value`` rows for the ``[limits]`` knobs the file sets."""
    return [
        f"{knob.key} = {_render_value(knob, limits.value(knob.attr))}"
        for knob in LIMITS
        if knob.attr in limits.explicit
    ]


def _self_check() -> None:
    """The dataclass defaults and the registry must agree (cheap invariant)."""
    defaults = Limits()
    for field in fields(Limits):
        if field.name == "explicit":
            continue
        knob = next((item for item in LIMITS if item.attr == field.name), None)
        if knob is None:
            raise AssertionError(f"Limits.{field.name} has no registry entry")
        if defaults.value(field.name) != knob.default:
            raise AssertionError(
                f"limits.{field.name}: default {defaults.value(field.name)} != "
                f"registry {knob.default}"
            )


_self_check()
