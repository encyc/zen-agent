"""Turn raw run failures into actionable notices (every frontend shares these).

A provider failure today surfaces as an English HTTP exception string, and a
genuine zen-agent bug crashes the run without any notice at all — the user
cannot tell "top up and continue" from "file an issue". Classification lives
at the single choke point (``ChatController._run`` sets ``last_error``), so
the TUI banner, the REPL line and the RPC ``last_error`` payload all improve
together. The raw detail always rides along: the friendly headline tells the
user what to do, the tail preserves the evidence an issue needs.
"""

from __future__ import annotations

import re

from zenagent.i18n import t

__all__ = ["bug_notice", "run_error_notice"]


#: Category → pattern, checked in order; the first match wins, so the most
#: specific category comes first. Numeric status codes use word boundaries:
#: a bare "402" would otherwise match inside a random request id (e.g.
#: ``…da402ff``), misfiling a server outage as an empty balance.
_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "balance",
            r"insufficient (?:balance|quota|funds)|\b402\b|payment required|billing|arrears",
        ),
        (
            "auth",
            r"\b40[13]\b|unauthorized|forbidden|invalid api[ -]?key|authentication",
        ),
        (
            "context",
            r"context length|context window|maximum context|too many tokens|prompt is too long",
        ),
        ("rate_limit", r"\b429\b|rate limit|too many requests|throttl"),
        (
            "network",
            (
                r"connecterror|connection (?:refused|reset|timed? ?out)|connect timeout"
                r"|nodename nor servname|name or service not known|getaddrinfo"
                r"|network is unreachable|no route to host|temporary failure in name resolution"
            ),
        ),
        (
            "outage",
            (
                r"\b50[0234]\b|bad gateway|service unavailable|overloaded"
                r"|temporarily unavailable|internal server error"
            ),
        ),
    )
)


def _category(detail: str) -> str:
    for name, pattern in _CATEGORIES:
        if pattern.search(detail):
            return name
    return "provider"


def run_error_notice(detail: str | None) -> str:
    """One actionable line for a provider-side run failure.

    The headline is the instruction ("top up and continue", "check the key"),
    the raw provider text follows after ``｜`` as evidence. ``None``/blank
    details (a provider that failed without saying why) fall back to the
    generic run-failed notice.
    """
    if not detail or detail == "error":
        return t("notice.run_failed")
    headline = t(f"notice.error.{_category(detail)}")
    return f"{headline} ｜ {detail.strip()}"


def bug_notice(detail: str) -> str:
    """One actionable line for an unexpected crash (a real zen-agent bug).

    The user should be able to file an issue straight from the message, so it
    names the report path and keeps the exception text for reproduction.
    """
    return f"{t('notice.error.bug')} ｜ {detail.strip()}"
