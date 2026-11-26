"""``zen automations``: create and inspect this project's scheduled runs.

Kept free of Runtime/Agent construction, like ``zen sessions``: adding an
automation must work without an API key and without opening a session, because
the point of an automation is to run later, not now.

The scheduler itself is ``zen daemon`` (see :mod:`zenagent.cli.daemon`). This
module only edits the definition file and drops a line in the daemon's request
mailbox, so nothing here can start two runs of the same job.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from zenagent import project
from zenagent.agent.session import unique_session_path
from zenagent.app.automations import (
    AutomationStore,
    Job,
    Schedule,
    StateStore,
    state_for,
)
from zenagent.app.runs import RunLedger
from zenagent.cli.daemon import daemon_running

__all__ = ["build_parser", "main"]

#: The boundary `add` prints, so nobody thinks "unattended" means "unbounded".
PERMISSION_NOTICE = (
    "this automation runs unattended in auto mode (no approval prompts):\n"
    "  · writes anywhere on this machine, including credential paths\n"
    "  · destructive shell commands run as-is\n"
    "  · still refused: catastrophic commands that need --allow-dangerous,\n"
    "    anything matched by [tools] deny in your global config, and reads of\n"
    "    protected credential paths"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zen automations", description="manage this project's scheduled runs"
    )
    subcommands = parser.add_subparsers(dest="action", required=True)

    add = subcommands.add_parser("add", help="create an automation")
    add.add_argument("--name", required=True, help="short name, unique in the project")
    add.add_argument("--prompt", required=True, help="what the agent should do")
    when = add.add_mutually_exclusive_group(required=True)
    when.add_argument("--daily", metavar="HH:MM", help="run once a day at this local time")
    when.add_argument("--every", metavar="N[m|h]", help="run on a fixed interval")
    add.add_argument(
        "--timeout", type=int, default=1800, help="wall-clock budget in seconds"
    )
    add.add_argument(
        "--session", default=None, help="reuse an existing transcript file name"
    )

    listing = subcommands.add_parser("list", help="list this project's automations")
    listing.add_argument("--json", action="store_true", help="emit JSON")

    show = subcommands.add_parser("show", help="show one automation")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")

    for name, help_text in (
        ("rm", "delete an automation (its transcript is kept)"),
        ("enable", "let this automation run again"),
        ("disable", "pause this automation"),
        ("run", "ask the daemon to run it now"),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("name")
    return parser


def _root_or_fail(*, require_root: bool = False) -> Path | None:
    root = project.find_project_root()
    if require_root and Path.cwd().resolve() != root:
        print(
            f"zen automations: run this from the project root ({root})",
            file=sys.stderr,
        )
        return None
    return root


def _row(root: Path, job: Job, states, now: float) -> dict:
    state = state_for(states, job)
    ledger = RunLedger(project.project_dir(root=root) / "runs.jsonl")
    runs = [row for row in ledger.list(kind="automation") if row.label == job.name]
    return {
        "project": str(root),
        "name": job.name,
        "prompt": job.prompt,
        "schedule": _schedule_text(job.schedule),
        "enabled": job.enabled,
        "timeout_s": job.timeout_s,
        "session": state.session or job.session,
        "next_due": job.schedule.next_due(now, state.last_run_at),
        "last_status": state.last_status,
        "last_skip": state.last_skip,
        "last_error": state.last_error,
        "last_run_at": state.last_run_at or 0,
        "missed": state.missed,
        "run_count": state.run_count,
        # `list()` is newest-first, so the most recent run is the first row.
        "cost": runs[0].cost if runs else 0.0,
    }


def _schedule_text(schedule: Schedule) -> str:
    if schedule.kind == "every":
        if schedule.seconds % 3600 == 0:
            return f"every {schedule.seconds // 3600}h"
        return f"every {schedule.seconds // 60}m"
    return f"daily {schedule.at}"


def _collect(root: Path, now: float) -> dict:
    store = AutomationStore(root)
    jobs = store.load()
    states = StateStore(root).load()
    return {
        "jobs": [_row(root, job, states, now) for job in jobs],
        "warnings": list(store.warnings),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:          # argparse already printed usage
        return int(exc.code or 0)
    root = _root_or_fail(require_root=args.action == "add")
    if root is None:
        return 1
    now = time.time()

    if args.action == "add":
        try:
            schedule = Schedule.parse(
                f"daily {args.daily}" if args.daily else f"every {args.every}"
            )
        except (TypeError, ValueError) as exc:
            # The user's primary free-text input: answer with the accepted
            # forms, not a Python traceback and a crash log.
            print(
                f"zen automations: {exc}\n"
                "  expected: --daily HH:MM  or  --every N[m|h]",
                file=sys.stderr,
            )
            return 2
        if args.timeout <= 0:
            print("zen automations: --timeout must be a positive integer", file=sys.stderr)
            return 1
        session = args.session or unique_session_path(root=root).name
        store = AutomationStore(root)
        store.load()
        if any(job.session == session for job in store.load()):
            print(
                f"zen automations: session {session!r} already belongs to another "
                "automation; each job needs its own transcript",
                file=sys.stderr,
            )
            return 1
        try:
            saved = store.add(
                Job(
                    id="",
                    name=args.name,
                    prompt=args.prompt,
                    schedule=schedule,
                    session=session,
                    timeout_s=args.timeout,
                )
            )
        except ValueError as exc:
            print(f"zen automations: {exc}", file=sys.stderr)
            return 1
        print(f"added {saved.name} ({_schedule_text(saved.schedule)})")
        print(f"session: {saved.session} (created on its first run)")
        print(PERMISSION_NOTICE)
        if not daemon_running(root):
            print(
                "\nthis project has no scheduler running: start it with `zen daemon`\n"
                "or install it to start at login with `zen daemon install`"
            )
        return 0

    if args.action == "list":
        payload = _collect(root, now)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if not payload["jobs"]:
            print("no automations in this project")
        else:
            print(f"{'name':20} {'schedule':14} {'last':9} {'cost':8} next due")
            for row in payload["jobs"]:
                due = (
                    "—"
                    if not row["enabled"]
                    else time.strftime("%m-%d %H:%M", time.localtime(row["next_due"]))
                )
                status = row["last_skip"] or row["last_status"] or "—"
                print(
                    f"{row['name'][:20]:20} {row['schedule']:14} {status[:9]:9} "
                    f"{row['cost']:<8.4f} {due}"
                )
        for warning in payload["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        return 0

    store = AutomationStore(root)
    store.load()
    job = store.get(args.name)
    if job is None:
        print(f"zen automations: no automation named {args.name!r}", file=sys.stderr)
        return 1

    if args.action == "show":
        payload = _row(root, job, StateStore(root).load(), now)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.action == "rm":
        store.remove(args.name)
        print(f"removed {args.name} (its transcript is kept)")
        return 0

    if args.action in ("enable", "disable"):
        store.update(args.name, enabled=args.action == "enable")
        print(f"{args.name}: {'enabled' if args.action == 'enable' else 'disabled'}")
        return 0

    # run: the daemon owns state, so it owns the run. Ask it, never race it.
    if not daemon_running(root):
        print(
            "zen automations: the daemon is not running for this project; "
            "start it with `zen daemon` (or `zen daemon install`)",
            file=sys.stderr,
        )
        return 1
    request = project.project_dir(root=root) / "automations.request.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text(
        json.dumps({"jobs": [{"name": args.name, "at": time.time()}]}),
        encoding="utf-8",
    )
    print(f"asked the daemon to run {args.name}")
    return 0
