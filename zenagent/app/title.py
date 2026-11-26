"""Auto session title: one small provider call names a new conversation.

The display name lives in the session titles sidecar, so every frontend (TUI
picker, ``--list-sessions``, the desktop sidebar) reads it without new
plumbing. This module owns the two halves that are easy to get wrong: turning
a model reply into something a sidebar can show (:func:`clean_title`) and the
background call itself (:class:`AutoTitler`). Failure is silent by contract —
naming is a convenience, so it may never disturb the run.
"""

from __future__ import annotations

__all__ = ["AUTO_TITLE_LIMIT", "clean_title"]

#: Safety ceiling for a stored name; the prompt targets ~10 Han characters (or
#: 5-6 English words), and a model that ignores that is cut here, not at 10.
AUTO_TITLE_LIMIT = 60

#: Labels models like to prepend ("会话名：…", "Title: …") even when told not to.
_PREFIXES = ("会话名：", "会话名:", "会话名称：", "标题：", "名字：", "Title:", "title:")

#: Wrapping punctuation a name may arrive in: quotes, CJK brackets, code ticks.
_WRAPPERS = "\"'“”‘’「」『』《》`*"

#: Sentence punctuation a bare name should not end with.
_TRAILING = "。．.!！?？,，;；:：、…"


def clean_title(raw: str) -> str:
    """One provider reply -> a display name; ``""`` means give up.

    A fenced block loses its fence, only the first non-blank line survives, a
    known label is dropped, wrapping punctuation is stripped from both ends,
    whitespace flattens, and trailing sentence punctuation goes. Anything left
    longer than :data:`AUTO_TITLE_LIMIT` is cut (no ellipsis: a name is not a
    snippet). Unicode-aware ``len`` keeps CJK characters whole.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text[3:]
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.rstrip().removesuffix("```")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[0]
    for prefix in _PREFIXES:
        if line.startswith(prefix):
            line = line[len(prefix) :].strip()
            break
    line = line.strip(_WRAPPERS).strip()
    line = " ".join(line.split())
    line = line.rstrip(_TRAILING).strip()
    if len(line) > AUTO_TITLE_LIMIT:
        line = line[:AUTO_TITLE_LIMIT].rstrip()
    return line
