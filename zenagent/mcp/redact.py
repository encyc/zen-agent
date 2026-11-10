"""Credential redaction for MCP error text.

A remote server's URL can *be* the credential (DingTalk and WeCom host the
key in the query string), and httpx error strings quote the full URL. Every
error this package authors passes through here first, so warnings, RPC
frames and session records never carry a usable key.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["redact_text", "redact_url"]

_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_SECRET_PAIR_RE = re.compile(
    r"(?i)\b(key|token|access_token|secret|signature|sig)=([^&\s'\"<>]+)"
)


def redact_url(url: str) -> str:
    """Mask userinfo, every query value and any fragment."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    query = parts.query
    if query:
        masked = []
        for item in query.split("&"):
            name, sep, _ = item.partition("=")
            masked.append(f"{name}=***" if sep else f"{item}=***")
        query = "&".join(masked)
    fragment = "***" if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def redact_text(text: str) -> str:
    """Redact URLs and credential-like ``name=value`` pairs in free text.

    Server bodies sometimes echo the request query, so masking only URLs is
    not enough.
    """
    masked = _URL_RE.sub(lambda match: redact_url(match.group(0)), text)
    return _SECRET_PAIR_RE.sub(lambda match: f"{match.group(1)}=***", masked)
