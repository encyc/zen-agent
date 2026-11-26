"""Login-service installation for the automation daemon (launchd / systemd).

Split from :mod:`zenagent.cli.daemon` so the scheduler loop contains no
subprocess code, and so ``zen daemon status`` can read the installed unit
without pulling in the service plumbing.

Two rules shape every string below:

* **Absolute paths only.** Neither launchd nor systemd gives the process a login
  shell's ``PATH`` or working directory, so the interpreter, the project and the
  log file are all spelled out in full.
* **One label per project.** Two projects each want a daemon; a shared label
  would let the second install silently replace the first.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from zenagent import __version__, project

__all__ = [
    "VERSION_MARKER",
    "install",
    "plist_text",
    "systemd_unit_text",
    "uninstall",
    "unit_label",
    "unit_path",
]

_BASE_LABEL = "com.zenagent.daemon"

#: Mirrors :data:`zenagent.cli.daemon.VERSION_MARKER`; a literal here so reading
#: the unit never drags in the scheduler loop.
VERSION_MARKER = "ZENAGENT_DAEMON_VERSION="


def unit_label(root: Path, *, base: str = _BASE_LABEL) -> str:
    """A stable, per-project service name (the project key's trailing hash)."""
    key = project.project_key(root)
    return f"{base}.{key.rsplit('-', 1)[-1]}"


def unit_path(
    *, platform: str = sys.platform, home: Path | None = None, root: Path | None = None
) -> Path:
    """Where this project's unit file lives on ``platform``."""
    base = Path(home).expanduser() if home is not None else Path.home()
    label = unit_label(root) if root is not None else _BASE_LABEL
    if platform == "darwin":
        return base / "Library" / "LaunchAgents" / f"{label}.plist"
    if platform.startswith("linux"):
        return base / ".config" / "systemd" / "user" / f"zenagent-daemon-{label}.service"
    raise RuntimeError(f"zen daemon install: unsupported platform {platform!r}")


def _log_path(root: Path) -> Path:
    return project.project_dir(root=root) / "daemon.log"


def plist_text(*, python: str, root: Path, label: str | None = None) -> str:
    """A LaunchAgent that starts at login and is restarted when it dies."""
    name = label or unit_label(root)
    root = Path(root)
    log = _log_path(root)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{name}</string>
  <!-- {VERSION_MARKER}{__version__} -->
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>zenagent</string>
    <string>daemon</string>
    <string>--cwd</string>
    <string>{root}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def systemd_unit_text(*, python: str, root: Path) -> str:
    """A user unit that is enabled at login and restarted on failure."""
    root = Path(root)
    log = _log_path(root)
    return f"""# {VERSION_MARKER}{__version__}
[Unit]
Description=ZenAgent automation daemon for {root}
After=default.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={python} -m zenagent daemon --cwd {root}
Restart=on-failure
RestartSec=10
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def install(
    root: Path, *, platform: str = sys.platform, home: Path | None = None
) -> Path:
    """Write the unit and register it with the platform's supervisor."""
    root = Path(root)
    path = unit_path(platform=platform, home=home, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        path.write_text(plist_text(python=sys.executable, root=root), encoding="utf-8")
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True
        )
        return path
    if platform.startswith("linux"):
        path.write_text(
            systemd_unit_text(python=sys.executable, root=root), encoding="utf-8"
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", path.name], check=True)
        return path
    raise RuntimeError(f"zen daemon install: unsupported platform {platform!r}")


def uninstall(
    *, platform: str = sys.platform, home: Path | None = None, root: Path | None = None
) -> bool:
    """Stop and remove this project's unit; ``False`` when there was none."""
    if root is None:
        return False
    path = unit_path(platform=platform, home=home, root=root)
    if not path.exists():
        return False
    if platform == "darwin":
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False
        )
    elif platform.startswith("linux"):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", path.name], check=False
        )
    else:
        raise RuntimeError(f"zen daemon uninstall: unsupported platform {platform!r}")
    path.unlink(missing_ok=True)
    return True
