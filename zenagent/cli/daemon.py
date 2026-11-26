"""``zen daemon``: the resident scheduler for one project's automations.

One instance per project, and only for projects that actually have automations:
without ``automations.json`` there is nothing to wake for. The daemon owns three
files (see :mod:`zenagent.app.automations`):

* it *reads* ``automations.json`` and notices edits by mtime, so the CLI and the
  desktop can rewrite schedules without restarting anything;
* it *writes* ``automations.state.json`` — single writer, atomic replace;
* it *consumes* ``automations.request.json``, the "run this now" mailbox the CLI
  and the UI drop a line into.

The loop is deliberately small: sleep until the soonest due time (capped at 30
seconds so a request file or an edited schedule is noticed promptly), then run
whatever is due through :func:`zenagent.app.automation_runner.run_round`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import json
import logging
import os
import sys
import time
from pathlib import Path

from zenagent import __version__, project
from zenagent.app.automation_runner import RoundResult, run_round
from zenagent.app.automations import (
    AutomationStore,
    Due,
    Job,
    JobState,
    StateStore,
    due_list,
    state_for,
)
from zenagent.cli.daemon_service import VERSION_MARKER, unit_path

__all__ = [
    "WAKE_CAP_S",
    "daemon_lock",
    "daemon_running",
    "next_wake",
    "run_daemon",
    "status_text",
    "tick",
]

_log = logging.getLogger(__name__)

#: Upper bound on one sleep. Short enough that an edited schedule or a request
#: file is honoured promptly, long enough to cost nothing while idle.
WAKE_CAP_S = 30.0

_REQUEST = "automations.request.json"


def daemon_lock(root: Path) -> Path:
    """The per-project advisory lock that keeps one daemon per project."""
    return project.project_dir(root=root) / "daemon.lock"


def _take_daemon_lock(root: Path) -> int | None:
    """Open and flock the lock file; ``None`` when the filesystem cannot lock.

    Raises :class:`BlockingIOError` when another process holds it — that case is
    "a daemon is already running", not a degraded filesystem.
    """
    path = daemon_lock(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        return fd
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise BlockingIOError("daemon lock is held") from exc
        return None
    return fd


def _drop_daemon_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    except (ImportError, OSError):  # pragma: no cover - best effort
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def daemon_running(root: Path) -> bool:
    """Whether some process currently holds this project's daemon lock."""
    try:
        fd = _take_daemon_lock(root)
    except BlockingIOError:
        return True
    except OSError:
        return False
    if fd is None:
        # The filesystem cannot lock: report "not running" rather than inventing
        # a daemon that may not exist.
        return False
    _drop_daemon_lock(fd)
    return False


def next_wake(jobs: list[Job], states: dict[str, JobState], now: float) -> float:
    """The soonest instant any enabled job comes due, else a minute out."""
    soonest: float | None = None
    for job in jobs:
        if not job.enabled:
            continue
        when = job.schedule.next_due(now, state_for(states, job).last_run_at)
        if soonest is None or when < soonest:
            soonest = when
    return now + 60.0 if soonest is None else soonest


def _consume_requests(path: Path, jobs: list[Job], states: dict[str, JobState]) -> list[Due]:
    """Read and delete the request mailbox; forced runs come back as due jobs."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        _log.warning("discarding unreadable request file %s", path)
        path.unlink(missing_ok=True)
        return []
    path.unlink(missing_ok=True)
    wanted: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("jobs"), list):
        for entry in raw["jobs"]:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name:
                    wanted.append(name)
    by_name = {job.name: job for job in jobs}
    forced: list[Due] = []
    for name in wanted:
        job = by_name.get(name)
        if job is None:
            _log.warning("request names an unknown automation: %s", name)
            continue
        forced.append(
            Due(
                job=job,
                missed=0,
                run_number=state_for(states, job).run_count + 1,
            )
        )
    return forced


async def tick(
    runtime,
    chat,
    *,
    root: Path,
    now: float,
    request_path: Path,
    warnings: list[str] | None = None,
) -> list[RoundResult]:
    """One scheduler step: honour the mailbox, run what is due, record state."""
    store = AutomationStore(root)
    store.reload_if_changed()
    jobs = store.load()
    if store.warnings:
        for warning in store.warnings:
            _log.warning("automations: %s", warning)
        if warnings is not None:
            warnings.extend(store.warnings)
    state_store = StateStore(root)
    states = state_store.load()

    forced = _consume_requests(request_path, jobs, states)
    due = forced + due_list(jobs, states, now)
    results: list[RoundResult] = []
    for item in due:
        job = item.job
        state = state_for(states, job)
        result = await run_round(
            runtime,
            chat,
            job,
            run_number=item.run_number,
            now=now,
            session_root=root,
        )
        results.append(result)
        if result.session:
            state.session = result.session
        state.missed = item.missed
        if result.status == "skipped":
            state.last_skip = result.skip_reason
        else:
            state.last_skip = ""
            state.last_run_at = now
            state.run_count = item.run_number
            state.last_status = result.status
            state.last_error = result.error
            state.last_run_id = result.run_id
        states[job.id] = state
        # Persist per round, not per tick: a crash costs at most one round.
        state_store.save(states)
    return results


def status_text(root: Path, *, now: float) -> str:
    """``zen daemon status``: whether it is up, what is queued, when it wakes."""
    running = daemon_running(root)
    store = AutomationStore(root)
    jobs = store.load()
    states = StateStore(root).load()
    if not jobs:
        wake = "none"
    else:
        wake = time.strftime(
            "%Y-%m-%d %H:%M",
            time.localtime(next_wake([job for job in jobs if job.enabled], states, now)),
        )
        if not any(job.enabled for job in jobs):
            wake = "none"
    lines = [
        f"daemon: {'running' if running else 'stopped'}",
        f"jobs: {len(jobs)}",
        f"next wake: {wake}",
    ]
    installed = _installed_version(root)
    if installed and installed != __version__:
        lines.append(
            f"installed unit runs {installed}, this is {__version__}: "
            "run `zen daemon install` again"
        )
    for warning in store.warnings:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def _installed_version(root: Path) -> str:
    """The version recorded in this project's unit file, when there is one."""
    try:
        path = unit_path(root=root)
    except RuntimeError:  # pragma: no cover - unsupported platform
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        # The marker rides along in a plist <string> or a systemd comment; peel
        # the wrapper characters one at a time rather than with a multi-char
        # strip, which would eat legitimate dashes out of the version.
        marker = line.find(VERSION_MARKER)
        if marker < 0:
            continue
        value = line[marker + len(VERSION_MARKER):]
        for suffix in ("</string>", "-->"):
            value = value.removesuffix(suffix)
        return value.strip()
    return ""


async def run_daemon(root: Path, *, foreground: bool = False) -> int:
    """Bootstrap one Runtime and sleep until something is due, forever."""
    del foreground  # both modes run the same loop; the flag is for supervisors
    try:
        fd = _take_daemon_lock(root)
    except BlockingIOError:
        print(
            f"zen daemon: another daemon already runs for {root}",
            file=sys.stderr,
        )
        return 1

    from zenagent.app import bootstrap
    from zenagent.app.chat import ChatController
    from zenagent.app.runtime import RuntimeOptions

    try:
        prepared = await bootstrap.prepare(
            RuntimeOptions(root=root), assume_yes=True, allow_missing_key=False
        )
    except Exception as exc:  # noqa: BLE001 - fail fast, let the supervisor retry
        print(f"zen daemon: cannot start: {exc}", file=sys.stderr)
        _drop_daemon_lock(fd)
        return 2

    runtime = prepared.runtime
    chat = ChatController(runtime)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            import signal as signal_module

            loop.add_signal_handler(
                getattr(signal_module, signal_name), stop.set
            )
    request_path = project.project_dir(root=root) / _REQUEST
    try:
        while not stop.is_set():
            try:
                await tick(
                    runtime,
                    chat,
                    root=root,
                    now=time.time(),
                    request_path=request_path,
                )
            except Exception:  # noqa: BLE001 - one bad tick must not end the daemon
                _log.exception("daemon tick failed")
            jobs = AutomationStore(root).load()
            states = StateStore(root).load()
            wake = next_wake(jobs, states, time.time())
            delay = max(0.0, min(wake - time.time(), WAKE_CAP_S))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
    finally:
        # Never abandon a live round: let it settle before releasing the lock.
        await chat.wait_for_idle()
        await runtime.close()
        _drop_daemon_lock(fd)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zen daemon", description="run this project's automation scheduler"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="keep the process in the foreground (the default when run by hand)",
    )
    parser.add_argument("--cwd", default=None, help="project directory to serve")
    parser.add_argument(
        "action",
        nargs="?",
        default="run",
        choices=("run", "status", "install", "uninstall"),
        help="run (default), status, install a login service, or uninstall it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (
        Path(args.cwd).expanduser().resolve()
        if args.cwd
        else project.find_project_root()
    )
    if args.action == "status":
        print(status_text(root, now=time.time()))
        return 0
    if args.action in ("install", "uninstall"):
        from zenagent.cli.daemon_service import install, uninstall

        if args.action == "install":
            path = install(root)
            print(f"zen daemon: installed {path}")
            return 0
        removed = uninstall(root=root)
        print("zen daemon: uninstalled" if removed else "zen daemon: nothing to remove")
        return 0
    return asyncio.run(run_daemon(root, foreground=args.foreground))
