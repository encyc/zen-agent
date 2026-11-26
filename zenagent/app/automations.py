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

import re
import time
from dataclasses import dataclass

__all__ = [
    "SCHEDULE_DAILY",
    "SCHEDULE_EVERY",
    "Schedule",
]

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
