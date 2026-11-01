"""``/restore`` command core: argument parsing, restore call, formatted output.

Shared by the TUI command registry and the REPL's own slash dispatch so the
two surfaces can never drift apart. Storage lives in :mod:`zenagent.app.journal`.
"""

from __future__ import annotations

from zenagent.app.journal import RestoreReport, RunInfo, SnapshotJournal
from zenagent.i18n import t

__all__ = ["format_restore_report", "run_restore_command"]

_LIST_LIMIT = 10
_PATHS_SHOWN = 3


def run_restore_command(
    journal: SnapshotJournal | None, argument: str, *, running: bool
) -> str:
    """Run one ``/restore`` invocation and return the message to show.

    ``""`` restores the latest run, ``list`` lists recent runs, and ``<n>``
    restores the n-th entry of that list (1 = most recent).
    """
    if journal is None:
        return t("cmdmsg.restore_unavailable")
    if running:
        return t("cmdmsg.restore_running")
    text = (argument or "").strip()
    if text.lower() == "list":
        return _format_list(journal.runs(limit=_LIST_LIMIT))
    if not text:
        runs = journal.runs(limit=1)
        if not runs:
            return t("cmdmsg.restore_none")
        return format_restore_report(journal.restore(runs[0].run))
    try:
        index = int(text)
    except ValueError:
        return t("cmdmsg.restore_bad_arg", argument=text)
    runs = journal.runs(limit=_LIST_LIMIT)
    if index < 1 or index > len(runs):
        return t("cmdmsg.restore_bad_arg", argument=text)
    return format_restore_report(journal.restore(runs[index - 1].run))


def format_restore_report(report: RestoreReport) -> str:
    """Human-facing report for one restore call."""
    if report.run is None:
        return t("cmdmsg.restore_none")
    run = report.run
    lines = [
        t(
            "cmdmsg.restore_done",
            restored=len(report.restored),
            skipped=len(report.skipped),
            session=run.session or "-",
            time=run.started or "-",
        )
    ]
    lines.extend(t("cmdmsg.restore_restored", path=path) for path in report.restored)
    lines.extend(
        t("cmdmsg.restore_skipped", path=path, reason=reason)
        for path, reason in report.skipped
    )
    return "\n".join(lines)


def _format_list(runs: list[RunInfo]) -> str:
    if not runs:
        return t("cmdmsg.restore_none")
    lines = [t("cmdmsg.restore_list_header")]
    for position, run in enumerate(runs, start=1):
        shown = run.paths[:_PATHS_SHOWN]
        suffix = "…" if len(run.paths) > _PATHS_SHOWN else ""
        lines.append(
            t(
                "cmdmsg.restore_list_row",
                position=position,
                time=run.started or "-",
                session=run.session or "-",
                files=run.files,
                paths=", ".join(shown) + suffix,
            )
        )
    lines.append(t("cmdmsg.restore_list_hint"))
    return "\n".join(lines)
