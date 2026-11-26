"""Login-service installation for the automation daemon (launchd / systemd).

Split from :mod:`zenagent.cli.daemon` so the scheduler loop has no subprocess
code in it, and so ``zen daemon status`` can import one function without pulling
in the service plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["install", "plist_text", "systemd_unit_text", "unit_label", "unit_path"]


def unit_label(root: Path, *, base: str = "com.zenagent.daemon") -> str:
    raise NotImplementedError


def unit_path(
    *, platform: str = sys.platform, home: Path | None = None, root: Path | None = None
) -> Path:
    raise NotImplementedError


def plist_text(*, python: str, root: Path, label: str = "com.zenagent.daemon") -> str:
    raise NotImplementedError


def systemd_unit_text(*, python: str, root: Path) -> str:
    raise NotImplementedError


def install(root: Path, *, platform: str = sys.platform, home: Path | None = None) -> Path:
    raise NotImplementedError


def uninstall(
    *, platform: str = sys.platform, home: Path | None = None, root: Path | None = None
) -> bool:
    raise NotImplementedError
