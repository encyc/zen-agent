"""One interactive PTY per rpc host.

The terminal is host-level: it is not tied to a project slot, so a project
switch does not kill the shell. The client owns the session token, size and
focus; this module only spawns the login shell, pumps bytes both ways, and
emits output/exit events tagged with that token (a replaced shell must not
leak its last bytes into its successor's pane).
"""

from __future__ import annotations

import codecs
import os
import select
import signal
import struct
import subprocess
import threading
from collections.abc import Callable

# Windows has no pty module; the host still imports this file on every
# platform, so the terminal refuses its ops there instead of failing to boot.
try:
    import fcntl
    import pty
    import termios
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = pty = termios = None  # type: ignore[assignment]

Emit = Callable[[dict], None]
_READ_CHUNK = 65536
_DEFAULT_COLS = 80
_DEFAULT_ROWS = 24
_DIM_MAX = 1000


class TerminalError(RuntimeError):
    """A terminal op the session cannot serve (closed, stale token, bad size)."""


def clamp_dim(value: object, fallback: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return fallback
    return max(1, min(_DIM_MAX, value))


def _set_size(fd: int, cols: int, rows: int) -> None:
    if fcntl is None or termios is None:
        return
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class TerminalSession:
    """A single login shell behind a pty master, with a pumping reader thread."""

    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._master: int | None = None
        self._child: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stop: threading.Event | None = None
        self._token = ""

    def start(self, token: str, cwd: str | None, cols: int, rows: int) -> dict:
        """Replace any running shell with a fresh login shell at ``cwd``."""
        if pty is None:  # pragma: no cover - Windows
            raise TerminalError("terminal is not supported on this platform")
        self.close()
        shell = os.environ.get("SHELL") or "/bin/sh"
        master, slave = pty.openpty()
        try:
            _set_size(slave, cols, rows)
        except OSError:
            pass
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        try:
            child = subprocess.Popen(
                [shell, "-l"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd or None,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        stop = threading.Event()
        reader = threading.Thread(
            target=self._pump,
            args=(master, child, stop, token),
            daemon=True,
            name="zen-terminal",
        )
        with self._lock:
            self._master = master
            self._child = child
            self._stop = stop
            self._reader = reader
            self._token = token
        reader.start()
        return {
            "pty": child.pid,
            "shell": shell,
            "cwd": cwd or os.getcwd(),
            "cols": cols,
            "rows": rows,
        }

    def write(self, token: str, data: str) -> None:
        master = self._master_for(token)
        try:
            os.write(master, data.encode("utf-8", "replace"))
        except OSError as exc:
            raise TerminalError(f"terminal write failed: {exc}") from exc

    def resize(self, token: str, cols: int, rows: int) -> None:
        master = self._master_for(token)
        try:
            _set_size(master, cols, rows)
        except OSError as exc:
            raise TerminalError(f"terminal resize failed: {exc}") from exc

    def close(self, token: str | None = None) -> None:
        with self._lock:
            if token is not None and token != self._token:
                return
            master = self._master
            child = self._child
            stop = self._stop
            reader = self._reader
            self._master = None
            self._child = None
            self._stop = None
            self._reader = None
            self._token = ""
        if stop is not None:
            stop.set()
        if child is not None and child.poll() is None:
            self._signal_group(child, signal.SIGTERM)
            try:
                child.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._signal_group(child, signal.SIGKILL)
                try:
                    child.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        if master is not None:
            try:
                os.close(master)
            except OSError:
                pass
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)

    def _master_for(self, token: str) -> int:
        with self._lock:
            master = self._master
            current = self._token
        if master is None or token != current:
            raise TerminalError("terminal is not running")
        return master

    @staticmethod
    def _signal_group(child: subprocess.Popen[bytes], sig: int) -> None:
        try:
            os.killpg(os.getpgid(child.pid), sig)
        except OSError:
            try:
                child.send_signal(sig)
            except OSError:
                pass

    def _pump(
        self,
        master: int,
        child: subprocess.Popen[bytes],
        stop: threading.Event,
        token: str,
    ) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([master], [], [], 0.2)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    chunk = os.read(master, _READ_CHUNK)
                except OSError:
                    break
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self._output(token, text)
        finally:
            tail = decoder.decode(b"", final=True)
            if tail:
                self._output(token, tail)
            try:
                child.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            self._emit(
                {
                    "type": "event",
                    "event": {
                        "type": "terminal",
                        "session": token,
                        "kind": "exit",
                        "code": child.poll(),
                    },
                }
            )

    def _output(self, token: str, data: str) -> None:
        self._emit(
            {
                "type": "event",
                "event": {
                    "type": "terminal",
                    "session": token,
                    "kind": "output",
                    "data": data,
                },
            }
        )
