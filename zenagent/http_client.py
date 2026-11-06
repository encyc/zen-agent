"""HTTP client construction that survives a hostile environment.

httpx reads proxy settings from the environment when a client is built. A
``NO_PROXY`` entry with a bracketed IPv6 host (``[::1]``, the curl spelling)
makes httpx 0.28 raise ``InvalidURL`` at construction time, before any request
is sent — so a shell that exports it breaks every provider call. Build clients
through :func:`async_client` instead of ``httpx.AsyncClient`` directly.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterator
from typing import Any

import httpx

__all__ = ["async_client"]

_BRACKETED_HOST = re.compile(r"\[([0-9A-Fa-f:.]+)\]")


@contextlib.contextmanager
def _unbracketed_no_proxy() -> Iterator[None]:
    """Temporarily drop IPv6 brackets from NO_PROXY while a client is built.

    Brackets are equivalent to the bare address (``::1``), which httpx parses,
    so the user's intent is preserved. Only construction reads the variable.
    """
    saved: dict[str, str] = {}
    try:
        for key in ("NO_PROXY", "no_proxy"):
            raw = os.environ.get(key)
            if raw is None:
                continue
            cleaned = _BRACKETED_HOST.sub(r"\1", raw)
            if cleaned != raw:
                saved[key] = raw
                os.environ[key] = cleaned
        yield
    finally:
        for key, raw in saved.items():
            os.environ[key] = raw


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` that tolerates a bracketed-IPv6 ``NO_PROXY``."""
    with _unbracketed_no_proxy():
        return httpx.AsyncClient(**kwargs)
