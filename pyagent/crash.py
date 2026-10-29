"""Crash logs and a redacted diagnostics report.

A crash log is written only for an uncaught exception, and never contains
environment values or credentials — writing secrets to disk is worse than no log.
"""

from __future__ import annotations

import platform
import sys
import time
import traceback
from pathlib import Path

from pyagent import __version__

__all__ = [
    "collect_report",
    "crash_log_dir",
    "install_excepthook",
    "write_crash_log",
]

_installed = False
_log_dir: Path | None = None


def crash_log_dir() -> Path:
    return Path.home() / ".pyagent" / "logs"


def write_crash_log(
    exc_type, exc_value, exc_tb, directory: Path | None = None
) -> Path:
    """Write a crash log for one exception and return its path."""
    target_dir = Path(directory) if directory is not None else crash_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    path = target_dir / f"crash-{stamp}-{int(time.time() * 1000) % 1000:03d}.log"
    lines = [
        "pyagent crash report",
        f"version: {__version__}",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"cwd: {Path.cwd()}",
        f"argv: {sys.argv}",
        "",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def install_excepthook(directory: Path | None = None) -> Path:
    """Install a crash-log excepthook once; returns the log directory."""
    global _installed, _log_dir
    if _installed and _log_dir is not None:
        return _log_dir  # already installed: keep the first directory
    target = Path(directory) if directory is not None else crash_log_dir()
    _log_dir = target
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        try:
            path = write_crash_log(exc_type, exc_value, exc_tb, _log_dir)
            print(f"pyagent: crash log written to {path}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - never fail while reporting a failure
            print(f"pyagent: could not write crash log: {exc}", file=sys.stderr)
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    _installed = True
    return target


def collect_report() -> str:
    """Doctor checks plus environment facts, with no credential values."""
    from pyagent.cli.doctor import collect_checks
    from pyagent.config import config_path

    lines = [
        "pyagent diagnostics",
        f"version: {__version__}",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"cwd: {Path.cwd()}",
        f"config: {config_path()}",
        "",
    ]
    for check in collect_checks():
        state = "ok  " if check.ok else "FAIL"
        lines.append(f"{state} {check.name}: {check.detail}")
    return "\n".join(lines) + "\n"
