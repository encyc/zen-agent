"""Shell abstraction and shared process helpers for the bash tools.

``resolve_shell`` is the single place that turns one command line into an
``(executable, argv)`` pair, so the foreground bash tool and the background job
runner spawn exactly the same way (both use ``create_subprocess_exec``).

The module also owns the small pieces those two runners must share:

* the non-interactive environment defaults (``command_env``);
* ``cwd`` resolution for the optional argument (``resolve_cwd``);
* the bounded head+tail output buffer used by both runners;
* process-group spawning and killing, so a timeout takes the whole tree down.

Platform notes: POSIX starts every command in a new session (``setsid``) and
kills it with ``killpg``. Windows has no ``killpg``; the runner asks
``taskkill /T`` to take the tree down and falls back to ``Process.kill``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

__all__ = [
    "DRAIN_TIMEOUT_S",
    "MAX_OUTPUT_BYTES",
    "NON_INTERACTIVE_ENV",
    "READ_CHUNK",
    "OutputBuffer",
    "ShellError",
    "command_env",
    "decode_output",
    "kill_process_group",
    "resolve_cwd",
    "resolve_shell",
    "spawn_group_kwargs",
    "spawn_shell_process",
]

#: Total output kept before head/tail truncation kicks in.
MAX_OUTPUT_BYTES = 30_000
#: Split of the kept bytes between the head and the tail.
_HEAD_BYTES = MAX_OUTPUT_BYTES // 2
_TAIL_BYTES = MAX_OUTPUT_BYTES - _HEAD_BYTES
#: Read size from a pipe.
READ_CHUNK = 8192
#: Grace period for a read loop to drain after a kill.
DRAIN_TIMEOUT_S = 5.0

#: Flags that make a PowerShell invocation one-shot and non-interactive.
POWERSHELL_FLAGS = ["-NoLogo", "-NonInteractive", "-Command"]
#: cmd.exe's "run this one command line" flag.
CMD_FLAGS = ["/c"]
#: POSIX shell's "run this command string" flag.
POSIX_FLAGS = ["-c"]
#: The POSIX shell used when nothing is configured.
POSIX_EXECUTABLE = "/bin/sh"

#: Environment defaults that keep commands from waiting on a human/TTY.
#: The caller's real environment always wins (``setdefault``): these are only
#: filled in when the process does not already define them.
NON_INTERACTIVE_ENV: dict[str, str] = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MANPAGER": "cat",
    "SYSTEMD_PAGER": "cat",
    "TERM": "dumb",
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GCM_INTERACTIVE": "never",
    "PIP_NO_INPUT": "1",
    "DEBIAN_FRONTEND": "noninteractive",
    "npm_config_yes": "true",
}


class ShellError(ValueError):
    """The configured shell is unknown and cannot be resolved."""


def decode_output(data: bytes) -> str:
    """Decode process output leniently: agents must not crash on odd bytes."""
    return data.decode("utf-8", errors="replace")


class OutputBuffer:
    """Bounded head+tail buffer for process output."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self.head) < _HEAD_BYTES:
            take = min(len(chunk), _HEAD_BYTES - len(self.head))
            self.head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            self.tail.extend(chunk)
            if len(self.tail) > _TAIL_BYTES:
                del self.tail[: len(self.tail) - _TAIL_BYTES]

    @property
    def truncated(self) -> bool:
        return self.total > MAX_OUTPUT_BYTES

    def text(self) -> str:
        if not self.truncated:
            return decode_output(bytes(self.head) + bytes(self.tail))
        dropped = self.total - len(self.head) - len(self.tail)
        return (
            decode_output(bytes(self.head))
            + f"\n... [truncated {dropped} bytes of output] ...\n"
            + decode_output(bytes(self.tail))
        )


def command_env() -> dict[str, str]:
    """Process environment with non-interactive defaults for unset keys."""
    env = dict(os.environ)
    for key, value in NON_INTERACTIVE_ENV.items():
        env.setdefault(key, value)
    return env


def resolve_cwd(raw: object) -> Path | str:
    """Resolve the optional ``cwd`` argument; returns an error string if bad."""
    if raw is None or raw == "":
        return Path(os.getcwd())
    try:
        path = Path(str(raw)).expanduser()
    except (TypeError, ValueError):
        return f"invalid cwd: {raw!r}"
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    if not path.is_dir():
        return f"cwd does not exist or is not a directory: {path}"
    return path


def _named_shell(name: str, command: str) -> tuple[str, list[str]] | None:
    """Resolve one of the recognised ``ZENAGENT_SHELL`` names, or None."""
    if name == "posix":
        return POSIX_EXECUTABLE, [*POSIX_FLAGS, command]
    if name == "pwsh":
        return (shutil.which("pwsh") or "pwsh"), [*POWERSHELL_FLAGS, command]
    if name == "powershell":
        return (
            shutil.which("powershell") or "powershell",
            [*POWERSHELL_FLAGS, command],
        )
    if name == "cmd":
        return "cmd.exe", [*CMD_FLAGS, command]
    return None


def resolve_shell(command: str) -> tuple[str, list[str]]:
    """Return ``(executable, argv)`` for one command line.

    ``ZENAGENT_SHELL`` selects the shell: ``posix`` (the default), ``pwsh``,
    ``powershell``, ``cmd``, or an absolute executable path (run like a POSIX
    shell, i.e. ``path -c command``). Without it, POSIX uses ``/bin/sh`` and
    Windows prefers pwsh, then Windows PowerShell, then ``cmd.exe``.

    Raises :class:`ShellError` for an unrecognised ``ZENAGENT_SHELL`` value.
    """
    configured = os.environ.get("ZENAGENT_SHELL", "").strip()
    if configured:
        named = _named_shell(configured.lower(), command)
        if named is not None:
            return named
        if os.path.isabs(configured):
            return configured, [*POSIX_FLAGS, command]
        raise ShellError(
            f"unknown ZENAGENT_SHELL value {configured!r}: expected posix, "
            "powershell, pwsh, cmd, or an absolute executable path"
        )
    if os.name == "nt":
        for candidate in ("pwsh", "powershell"):
            found = shutil.which(candidate)
            if found:
                return found, [*POWERSHELL_FLAGS, command]
        return "cmd.exe", [*CMD_FLAGS, command]
    return POSIX_EXECUTABLE, [*POSIX_FLAGS, command]


def spawn_group_kwargs() -> dict:
    """Platform kwargs that give the child a process group we can kill."""
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


async def spawn_shell_process(
    command: str, *, cwd: Path, env: dict[str, str]
) -> asyncio.subprocess.Process:
    """Spawn one shell command line in its own process group.

    Raises :class:`ShellError` for an unknown shell and ``OSError`` when the
    executable cannot be started; callers turn both into tool errors.
    """
    executable, argv = resolve_shell(command)
    return await asyncio.create_subprocess_exec(
        executable,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=str(cwd),
        env=env,
        **spawn_group_kwargs(),
    )


async def kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the command *and its children*, then reap it (bounded wait)."""
    if proc.returncode is not None:
        return
    if os.name == "nt":
        # No killpg on Windows; taskkill /T is the portable tree kill.
        with contextlib.suppress(OSError):
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    # The direct-child fallback also covers a taskkill that did not exist.
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=DRAIN_TIMEOUT_S)
