"""Sanitized error frames shared by every RPC mixin (I3).

An error frame carries a stable snake_case ``code`` the client localizes and a
short, path-free ``message`` fallback. Only the op, an errno and a file's
basename may travel in ``params``; the raw exception — which routinely quotes
absolute paths or provider payloads — goes to the server log instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)


class Refusal(ValueError):
    """A refusal whose message this server authored (path-free, safe to show).

    The message is already the person-facing text; only the frame shape is
    sanitized, so validation hints like "relocate needs to" survive.
    """


def basename(value: object) -> str:
    """The last path component of a wire/log value ('' when unavailable)."""
    if not isinstance(value, (str, os.PathLike)) or str(value) == "":
        return ""
    return Path(os.fspath(value)).name


def error_frame(
    request_id,
    message: str,
    code: str,
    params: dict[str, object] | None = None,
) -> dict:
    """One error frame with only non-empty string params on the wire."""
    frame: dict = {
        "type": "error",
        "id": request_id,
        "message": message,
        "code": code,
    }
    clean = {
        key: str(value)
        for key, value in (params or {}).items()
        if value is not None and str(value) != ""
    }
    if clean:
        frame["params"] = clean
    return frame


def failure_frame(
    request_id,
    *,
    code: str,
    message: str,
    exc: BaseException,
    op: str | None = None,
    path: object = None,
    params: dict[str, object] | None = None,
) -> dict:
    """One sanitized error frame for a caught exception.

    ``message`` stays a short fallback (the client speaks the person's
    language from ``code``); the caller's op, the exception's errno and the
    basename of the file it touched — its own ``filename`` first, else the
    caller's ``path`` — are the only details that cross.
    """
    label = op or code
    if path is not None and str(path) != "":
        logger.warning("%s failed for %r: %s", label, path, exc, exc_info=True)
    else:
        logger.warning("%s failed: %s", label, exc, exc_info=True)
    merged = dict(params or {})
    if op is not None:
        merged.setdefault("op", op)
    errno = getattr(exc, "errno", None)
    if isinstance(errno, int):
        merged.setdefault("errno", str(errno))
    name = basename(getattr(exc, "filename", None)) or basename(path)
    if name:
        merged.setdefault("file", name)
    return error_frame(request_id, message, code, merged)
