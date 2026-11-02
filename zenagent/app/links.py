"""Pull session links out of raw markdown.

The transcript is a renderer, and a lossy one: long tokens cannot wrap
(Textual has no word-break), so a URL inside a code block loses everything
past the right edge, and markdown-it rewrites hrefs on the way to the
browser (``%3A%2F%2F`` becomes ``://``). ``/urls``, ``/copy <n>`` and
``/open <n>`` therefore read the raw markdown text instead of the cells.
"""

from __future__ import annotations

import re

__all__ = ["extract_links", "parse_index"]

#: Bare http(s) URLs, including those inside fenced code blocks. CJK sentence
#: punctuation ends a URL the way a space does: `(https://x/a)。另外` must not
#: swallow the rest of the sentence. Quotes are *allowed* inside a URL
#: (`.../wiki/O'Brien`) and trimmed only when they end it.
_LINK_RE = re.compile(r"""https?://[^\s<>`。，、；：！？）》】」』]+""")

#: Characters that end a URL instead of belonging to it: sentence punctuation,
#: markdown emphasis/table delimiters, and closers.
_TRAILING = ".,;:!?、。，）》】\"']}|*_~"


def _trim(url: str) -> str:
    """Drop trailing punctuation, keeping the URL's own balanced brackets."""
    while url:
        last = url[-1]
        unbalanced = last == ")" and url.count("(") < url.count(")")
        if last not in _TRAILING and not unbalanced:
            break
        url = url[:-1]
    return url


def extract_links(text: str) -> list[str]:
    """Every http(s) link in ``text``, verbatim, deduped in first-seen order."""
    seen: dict[str, None] = {}
    for raw in _LINK_RE.findall(text):
        url = _trim(raw)
        if url:
            seen.setdefault(url, None)
    return list(seen)


def parse_index(argument: str) -> int | None:
    """The number in a ``<n>``/``#<n>`` argument, or ``None`` for free text."""
    value = argument.strip().lstrip("#")
    # ``isdecimal`` (not ``isdigit``) keeps superscripts like "²" out: they pass
    # ``isdigit`` and then blow up in ``int``. The guard also covers pasted
    # numbers beyond CPython's int-from-string digit limit.
    if not value.isdecimal():
        return None
    try:
        return int(value)
    except ValueError:
        return None
