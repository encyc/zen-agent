"""Automation schedules, job definitions and the due-time arithmetic.

Deliberately UI-free and Runtime-free: nothing here imports ``zenagent.cli``,
argparse, textual or the app assembly, so every rule in this module is testable
offline and the same logic serves the CLI, the daemon and the rpc surface.

``now`` is always a parameter, never read from the clock inside the logic, so a
test can pin a date without freezing time.

Three shapes live here:

* :class:`Schedule` — ``daily HH:MM`` or ``every N<m|h>``, local time;
* :class:`Job` — one automation definition (the file the CLI/UI writes);
* :class:`JobState` — the scheduler's own bookkeeping (the file only the daemon
  writes), kept separate so a corrupt definition can never cost a run history.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from zenagent import project

__all__ = [
    "SCHEDULE_DAILY",
    "SCHEDULE_EVERY",
    "AutomationStore",
    "Due",
    "Job",
    "JobState",
    "Schedule",
    "StateStore",
    "due_list",
    "resolve_session",
    "state_for",
    "wrap_prompt",
]

#: Bumped when the on-disk shape changes incompatibly. Readers refuse what they
#: do not recognize rather than silently reinterpreting a newer file.
_STORE_VERSION = 1

SCHEDULE_DAILY = "daily"
SCHEDULE_EVERY = "every"

_DAILY_RE = re.compile(r"^daily ([01]?\d|2[0-3]):([0-5]\d)$")
_EVERY_RE = re.compile(r"^every (\d+)([mh])$")

#: Multiplier for the ``every`` unit suffix.
_UNIT_SECONDS = {"m": 60, "h": 3600}


@dataclass(frozen=True)
class Schedule:
    """When a job runs: one local-time clock time, or a fixed interval."""

    kind: str
    at: str = "09:00"
    seconds: int = 0

    @classmethod
    def parse(cls, raw: str) -> Schedule:
        """Parse ``"daily HH:MM"`` or ``"every N<m|h>"``.

        Anything else is a ``ValueError``: a silently-misread schedule would
        fire at the wrong hour for weeks before anyone noticed.
        """
        if not isinstance(raw, str):
            raise TypeError("schedule must be a string")
        text = raw.strip()

        daily = _DAILY_RE.match(text)
        if daily is not None:
            hour, minute = int(daily.group(1)), int(daily.group(2))
            return cls(kind=SCHEDULE_DAILY, at=f"{hour:02d}:{minute:02d}")

        every = _EVERY_RE.match(text)
        if every is not None:
            count = int(every.group(1))
            if count <= 0:
                raise ValueError("schedule interval must be at least one unit")
            return cls(
                kind=SCHEDULE_EVERY, seconds=count * _UNIT_SECONDS[every.group(2)]
            )

        raise ValueError(f"unknown schedule: {raw!r}")

    def next_due(self, now: float, last_run_at: float | None) -> float:
        """The next epoch second this schedule comes due, at or after ``now``.

        A job with no history waits one full period from ``now`` — never
        "immediately, because its slot passed while it did not exist yet". That
        is the rule that stops a freshly added ``daily 09:00`` from spending
        money the moment it is created at 14:00.
        """
        if self.kind == SCHEDULE_EVERY:
            base = now if last_run_at is None else last_run_at
            return base + self.seconds

        if self.kind != SCHEDULE_DAILY:  # pragma: no cover - parses only two kinds
            raise ValueError(f"unknown schedule kind: {self.kind!r}")

        hour, minute = (int(part) for part in self.at.split(":"))
        if last_run_at is None:
            # No history: the first slot is strictly after ``now``, so a job
            # created at 14:00 for "daily 09:00" waits until tomorrow.
            return self._slot_on_or_after(now, hour, minute, inclusive=False)
        # With history the candidate is the slot one local day after the last
        # run. Anchoring on last_run_at (not on now) is what makes an overdue
        # slot land in the past, which is how due_list sees "run me now".
        local = time.localtime(last_run_at)
        return time.mktime(
            (
                local.tm_year,
                local.tm_mon,
                local.tm_mday + 1,
                hour,
                minute,
                0,
                0,
                0,
                -1,
            )
        )

    @staticmethod
    def _slot_on_or_after(now: float, hour: int, minute: int, *, inclusive: bool) -> float:
        """Today's ``hour:minute`` local time, rolled to tomorrow if past.

        The roll uses the calendar (``mktime`` normalizes day 32 into the next
        month) rather than adding 86400, which would walk over a DST boundary
        into the wrong hour.
        """
        local = time.localtime(now)
        today = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1)
        )
        if today > now or (inclusive and today == now):
            return today
        return time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday + 1, hour, minute, 0, 0, 0, -1)
        )

    def to_json(self) -> dict:
        if self.kind == SCHEDULE_EVERY:
            return {"kind": self.kind, "seconds": self.seconds}
        return {"kind": self.kind, "at": self.at}

    @classmethod
    def from_json(cls, raw: object) -> Schedule:
        if not isinstance(raw, dict):
            raise TypeError("schedule must be an object")
        kind = raw.get("kind")
        if kind == SCHEDULE_EVERY:
            seconds = raw.get("seconds")
            if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
                raise ValueError("every schedule needs a positive 'seconds'")
            return cls(kind=SCHEDULE_EVERY, seconds=seconds)
        if kind == SCHEDULE_DAILY:
            at = raw.get("at")
            if not isinstance(at, str):
                raise ValueError("daily schedule needs an 'at' string")
            parsed = cls.parse(f"{SCHEDULE_DAILY} {at}")
            return parsed
        raise ValueError(f"unknown schedule kind: {kind!r}")


# -- job definitions -------------------------------------------------------

#: Fields ``AutomationStore.update`` will change. Everything else in a job is
#: settled at creation: moving a job to another session or another project
#: would silently split its history, so that is a remove-and-add, not an edit.
_UPDATABLE = frozenset({"enabled", "schedule", "prompt", "timeout_s"})


@dataclass(frozen=True)
class Job:
    """One automation: what to run, where, how often, and in which session.

    ``session`` is a bare file name, not a path: the project's session
    directory plus this name is the transcript, so moving the project
    directory keeps the definition valid.
    """

    id: str
    name: str
    prompt: str
    schedule: Schedule
    session: str
    timeout_s: int = 1800
    enabled: bool = True
    created_at: int = 0

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": self.schedule.to_json(),
            "session": self.session,
            "timeout_s": self.timeout_s,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, raw: object) -> Job:
        if not isinstance(raw, dict):
            raise TypeError("job must be an object")
        name = raw.get("name")
        prompt = raw.get("prompt")
        session = raw.get("session")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("job needs a name")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("job needs a prompt")
        if not isinstance(session, str) or not session.strip():
            raise ValueError("job needs a session")
        timeout = raw.get("timeout_s", 1800)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout_s must be a positive integer")
        created = raw.get("created_at", 0)
        return cls(
            id=str(raw.get("id") or ""),
            name=name,
            prompt=prompt,
            schedule=Schedule.from_json(raw.get("schedule")),
            session=session,
            timeout_s=timeout,
            enabled=bool(raw.get("enabled", True)),
            created_at=int(created) if isinstance(created, int) else 0,
        )


@dataclass
class JobState:
    """The scheduler's view of one job: when it last ran, and how it ended.

    Separate from :class:`Job` on purpose — the definition is user data that
    any frontend may rewrite, while this is the daemon's own bookkeeping with a
    single writer, so a mangled definition never costs the run history.
    """

    session: str = ""
    last_run_at: float | None = None
    last_status: str = ""
    last_skip: str = ""
    last_error: str = ""
    last_run_id: str = ""
    missed: int = 0
    run_count: int = 0

    def to_json(self) -> dict:
        return {
            "session": self.session,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_skip": self.last_skip,
            "last_error": self.last_error,
            "last_run_id": self.last_run_id,
            "missed": self.missed,
            "run_count": self.run_count,
        }

    @classmethod
    def from_json(cls, raw: object) -> JobState:
        if not isinstance(raw, dict):
            raise TypeError("state must be an object")
        last_run = raw.get("last_run_at")
        state = cls(
            session=str(raw.get("session") or ""),
            last_run_at=(
                float(last_run)
                if isinstance(last_run, (int, float)) and not isinstance(last_run, bool)
                else None
            ),
            last_status=str(raw.get("last_status") or ""),
            last_skip=str(raw.get("last_skip") or ""),
            last_error=str(raw.get("last_error") or ""),
            last_run_id=str(raw.get("last_run_id") or ""),
        )
        for field_name in ("missed", "run_count"):
            value = raw.get(field_name, 0)
            setattr(
                state,
                field_name,
                int(value)
                if isinstance(value, int) and not isinstance(value, bool)
                else 0,
            )
        return state


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def resolve_session(root: Path, name: str) -> Path:
    """The transcript path for one job: project sessions dir + bare name."""
    return project.project_sessions_dir(root=root) / name


def _new_id() -> str:
    return f"a_{secrets.token_hex(4)}"


class AutomationStore:
    """The project's ``automations.json``: definitions, read by the daemon.

    Any frontend may rewrite this file (CLI, rpc/desktop); the daemon only
    reads it and notices changes by mtime. Writes are atomic, and a corrupt
    file leaves the last good snapshot serving rather than taking the
    scheduler down with it.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = project.project_dir(root=self.root) / "automations.json"
        self.warnings: list[str] = []
        self._jobs: list[Job] = []
        self._stamp: int | None = None
        self._loaded = False

    # -- reads --------------------------------------------------------------

    def load(self) -> list[Job]:
        """Parse the file; a bad file keeps the last good snapshot + warns."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = self._parse(raw)
        except FileNotFoundError:
            self._jobs = []
            self._stamp = None
            self._loaded = True
            return list(self._jobs)
        except (OSError, ValueError, TypeError) as exc:
            self.warnings.append(f"{self.path}: {exc}")
            self._stamp = self._stamp_of()
            self._loaded = True
            return list(self._jobs)
        self._jobs = jobs
        self._stamp = self._stamp_of()
        self._loaded = True
        return list(self._jobs)

    def _parse(self, raw: object) -> list[Job]:
        if not isinstance(raw, dict):
            raise TypeError("automations file must be an object")
        entries = raw.get("jobs", [])
        if not isinstance(entries, list):
            raise TypeError("'jobs' must be a list")
        return [Job.from_json(entry) for entry in entries]

    def _stamp_of(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def reload_if_changed(self) -> bool:
        """Reload when the file changed (or vanished); ``True`` when it did."""
        if not self._loaded:
            self.load()
            return True
        if self._stamp_of() == self._stamp:
            return False
        self.load()
        return True

    def get(self, name: str) -> Job | None:
        for entry in self._jobs:
            if entry.name == name:
                return entry
        return None

    # -- writes (CLI / rpc only; never the daemon) ---------------------------

    def _save(self) -> None:
        _atomic_write_json(
            self.path,
            {"version": _STORE_VERSION, "jobs": [job.to_json() for job in self._jobs]},
        )
        self._stamp = self._stamp_of()
        self._loaded = True

    def add(self, job: Job) -> Job:
        if self.get(job.name) is not None:
            raise ValueError(f"a job named {job.name!r} already exists")
        saved = replace(
            job,
            id=job.id or _new_id(),
            created_at=job.created_at or int(time.time()),
        )
        self._jobs = [*self._jobs, saved]
        self._save()
        return saved

    def update(self, name: str, **changes) -> Job:
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot change {', '.join(sorted(unknown))}")
        current = self.get(name)
        if current is None:
            raise KeyError(name)
        updated = replace(current, **changes)
        self._jobs = [updated if job.name == name else job for job in self._jobs]
        self._save()
        return updated

    def remove(self, name: str) -> bool:
        kept = [job for job in self._jobs if job.name != name]
        if len(kept) == len(self._jobs):
            return False
        self._jobs = kept
        self._save()
        return True


class StateStore:
    """The project's ``automations.state.json``: the daemon's single-writer file."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = project.project_dir(root=self.root) / "automations.state.json"

    def load(self) -> dict[str, JobState]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        entries = raw.get("jobs", {})
        if not isinstance(entries, dict):
            return {}
        states: dict[str, JobState] = {}
        for job_id, entry in entries.items():
            try:
                states[str(job_id)] = JobState.from_json(entry)
            except (TypeError, ValueError):
                continue
        return states

    def save(self, states: dict[str, JobState]) -> None:
        _atomic_write_json(
            self.path,
            {
                "version": _STORE_VERSION,
                "jobs": {job_id: state.to_json() for job_id, state in states.items()},
            },
        )


def state_for(states: dict[str, JobState], job: Job) -> JobState:
    """The state for ``job``, defaulted, with its session preferred from disk.

    The on-disk ``session`` wins when present: it is where the daemon actually
    wrote, which can differ from the definition after a transcript was lost and
    re-minted.
    """
    found = states.get(job.id)
    if found is None:
        return JobState(session=job.session)
    if not found.session:
        found.session = job.session
    return found


# -- due detection ---------------------------------------------------------


@dataclass(frozen=True)
class Due:
    """One job that should run now, with the bookkeeping the runner needs."""

    job: Job
    missed: int
    run_number: int


def _missed_rounds(schedule: Schedule, now: float, last_run_at: float | None) -> int:
    """How many whole periods were skipped since the last run (0 when none).

    "Missed" counts the slots the scheduler slept through, so the first overdue
    round reports ``missed == 0`` and the user only sees a number when something
    was genuinely dropped.
    """
    if last_run_at is None:
        return 0
    if schedule.kind == SCHEDULE_EVERY:
        behind = int((now - last_run_at) // schedule.seconds) - 1
        return max(behind, 0)
    local_now = time.localtime(now)
    local_then = time.localtime(last_run_at)
    days = time.mktime(
        (local_now.tm_year, local_now.tm_mon, local_now.tm_mday, 0, 0, 0, 0, 0, -1)
    ) - time.mktime(
        (local_then.tm_year, local_then.tm_mon, local_then.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    return max(int(days // 86400) - 1, 0)


def due_list(
    jobs: list[Job], states: dict[str, JobState], now: float
) -> list[Due]:
    """Every enabled job whose next scheduled instant has passed, soonest first.

    A job with no history never appears: its first slot is in the future by
    construction, so adding a job is not the same as asking to run it.
    """
    due: list[tuple[float, Due]] = []
    for job in jobs:
        if not job.enabled:
            continue
        state = state_for(states, job)
        when = job.schedule.next_due(now, state.last_run_at)
        if when > now:
            continue
        due.append(
            (
                when,
                Due(
                    job=job,
                    missed=_missed_rounds(job.schedule, now, state.last_run_at),
                    run_number=state.run_count + 1,
                ),
            )
        )
    due.sort(key=lambda pair: pair[0])
    return [item for _, item in due]


def wrap_prompt(job: Job, run_number: int, now: float) -> str:
    """The user's prompt, prefixed with the fact that nobody is watching.

    The original instruction is appended verbatim: this wrapper adds context,
    never rewrites what the user asked for.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    return (
        f"[automation] {job.name} · 第 {run_number} 次运行 · {stamp}\n"
        "\n"
        "这个 session 由定时器写入。现在没有人在看，也没有人能回答你的提问（ask 会直接\n"
        '返回"无交互 UI"）。做完把结论写成一段可以直接读的汇报：先一句话结论，再列异常\n'
        "（按严重程度排序）；没有异常也要用一句话说明。\n"
        "\n"
        f"{job.prompt}\n"
    )
