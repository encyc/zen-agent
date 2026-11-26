"""Cross-session search: three read-only tools over the saved transcripts.

``list_sessions`` lists, ``search_sessions`` scans; ``read_session`` is declared
(name, schema, block, ``approval="read"``) and lands in the next task, so the
registration surface, the prompt budget and the permission gate are proven
before its body exists.

``search_sessions`` scans the JSONL directly (``agent.session_text``) instead of
shelling out to ``rg``: ripgrep matches raw *lines*, so a 59 KB single-line tool
result yields no usable excerpt and structural keys such as ``"arguments"``
match every stored call. A full pass over this machine's store (103 sessions,
35 MB) measures 56-145 ms, which no index can meaningfully improve on.

Two contracts shape this module, and both come from ``approval="read"``:

* **no path argument, ever.** The transcripts live under ``~/.zenagent``,
  which the permission gate treats as ``protected``: a tool that took a
  ``path`` would be stopped before it ran, and the model could point a search
  at any file on the box. The three tools take data-shaped arguments only
  (``all``, ``limit``, ``query``, ``id``) and read what the runtime's own
  project owns.
* **the project comes from the builder, not from the process.**
  :func:`zenagent.agent.session.sessions_dir` resolves the project from the
  process cwd, which is wrong for any Runtime whose root lives elsewhere (an
  RPC host serving slots for arbitrary roots; a ``task`` subagent working in a
  worktree). :func:`build_session_tools` closes over the runtime's root and
  pins every lookup to it.

Nothing here writes. ``list_sessions`` passes ``cache=False`` so the listing
index never flushes ``_index.json`` back into a sessions directory -- the whole
point of listing through the read-only index mode.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.agent.session import SessionInfo
from zenagent.agent.session import list_sessions as list_saved_sessions
from zenagent.agent.session_text import (
    LEVELS,
    count_messages,
    excerpt,
    iter_turns,
)
from zenagent.prompts import tool_description
from zenagent.tools._result import clip, text_result

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "DEFAULT_READ_LIMIT",
    "MAX_LIST_LIMIT",
    "MAX_READ_LIMIT",
    "MAX_SESSIONS_PER_PAGE",
    "SEARCH_TIMEOUT_S",
    "build_session_tools",
]

#: Sessions listed when the model does not say.
DEFAULT_LIST_LIMIT = 20
#: Ceiling on one listing; a bigger ``limit`` is clamped, not refused.
MAX_LIST_LIMIT = 200
#: Distinct sessions with matches shown per ``search_sessions`` page; ``skip``
#: counts sessions, mirroring ``grep``'s ``MAX_GREP_FILES_PER_PAGE``.
MAX_SESSIONS_PER_PAGE = 20
#: Candidates one search examines. Not a page size: it bounds the walk against a
#: runaway store (this machine holds ~105 files). ``search`` paginates its
#: matches, so it has no use for the listing's smaller default.
MAX_SEARCH_SESSIONS = 1000
#: Excerpts one session shows before its remainder is counted but not printed.
DEFAULT_MAX_HITS = 5
#: Ceiling on ``max_hits``; a bigger value is clamped, not refused.
MAX_HITS_PER_SESSION = 20
#: Characters of rendered page one search body may spend (shared clip notice).
MAX_SEARCH_BODY = 20_000
#: Wall-clock budget for one search. See the note on the declaration below.
SEARCH_TIMEOUT_S = 60.0
#: Messages returned by one ``read_session`` when the model does not say.
DEFAULT_READ_LIMIT = 60
#: Ceiling on one ``read_session`` page; a bigger value is clamped.
MAX_READ_LIMIT = 400
#: Characters of rendered page one read may spend (shared clip notice).
MAX_READ_BODY = 20_000

_NO_SESSIONS_PROJECT = (
    "no saved sessions in this project (all=true lists every project)"
)
_NO_SESSIONS_ALL = "no saved sessions in any project"

#: What each level looks at, as the model should hear it.
_LEVEL_LABELS = {
    "conversation": "conversation text",
    "tools": "tool arguments",
    "all": "tool result bodies",
}
#: The level to retry with when this one found nothing (``all`` is the end).
_WIDER_LEVEL = {"conversation": "tools", "tools": "all"}

_LIST_PARAMETERS = {
    "type": "object",
    "properties": {
        "all": {
            "type": "boolean",
            "description": "List every project's sessions, not just this one.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_LIST_LIMIT,
            "description": f"Sessions to list (default {DEFAULT_LIST_LIMIT}).",
        },
    },
}


_SEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Regular expression to look for.",
        },
        "include": {
            "type": "string",
            "enum": list(LEVELS),
            "description": (
                "How much to search: conversation (default), tools, or all."
            ),
        },
        "all": {
            "type": "boolean",
            "description": "Search every project's sessions, not just this one.",
        },
        "skip": {
            "type": "integer",
            "minimum": 0,
            "description": "Sessions to skip: addresses the next page.",
        },
        "max_hits": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_HITS_PER_SESSION,
            "description": f"Excerpts per session (default {DEFAULT_MAX_HITS}).",
        },
    },
    "required": ["query"],
}


_READ_PARAMETERS = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": (
                "Session id (a bare stem), as listed by list_sessions or "
                "search_sessions."
            ),
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "Message number to start at (default 0).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_READ_LIMIT,
            "description": f"Messages to return (default {DEFAULT_READ_LIMIT}).",
        },
        "include": {
            "type": "string",
            "enum": list(LEVELS),
            "description": (
                "How much to read: conversation (default), tools, or all."
            ),
        },
    },
    "required": ["id"],
}


@dataclass
class _SessionHits:
    """One matching session: how many turns matched, and the excerpts shown."""

    info: SessionInfo
    hits: int = 0
    excerpts: list[tuple[int, str, str]] = field(default_factory=list)


def _row(info: SessionInfo, every_project: bool) -> str:
    """One listing line: id, count, date, then the labelled title."""
    day = datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d")
    project = f"[{info.project}] " if every_project and info.project else ""
    source = f"  (fork of {info.derived_from})" if info.derived_from else ""
    return (
        f"{info.path.stem}  {info.messages} msgs  {day}  "
        f"{project}{info.title}{source}"
    )


async def _list_sessions(
    root: Path, tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    raw = args.get("limit")
    if raw is None:
        limit = DEFAULT_LIST_LIMIT
    else:
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return text_result(
                f"limit must be a whole number, got {raw!r}", is_error=True
            )
        if limit < 1:
            return text_result("limit must be at least 1", is_error=True)
        limit = min(limit, MAX_LIST_LIMIT)
    every_project = args.get("all") is True
    infos = list_saved_sessions(
        limit=limit,
        scope="all" if every_project else "project",
        root=root,
        cache=False,  # read-only tool: never write _index.json
    )
    scope = "all" if every_project else "project"
    if not infos:
        return text_result(
            _NO_SESSIONS_ALL if every_project else _NO_SESSIONS_PROJECT,
            count=0,
            scope=scope,
        )
    text = "\n".join(_row(info, every_project) for info in infos)
    if len(infos) >= limit:
        text += f"\n… newest {limit}; raise limit for more"
    return text_result(text, count=len(infos), scope=scope)


def _newest_first(infos: list[SessionInfo]) -> list[SessionInfo]:
    """Newest first, ties broken by id so that paging is stable.

    Sessions written in the same second share an mtime (a batch of tiny
    transcripts lands in the same clock tick), and a bare mtime sort would leave
    their order to the directory walk -- two pages of the same search could then
    overlap or drop a session. ``(-mtime, id)`` is total, so ``skip`` addresses
    the same sequence every call.
    """
    return sorted(infos, key=lambda info: (-info.mtime, info.path.stem))


def _scan(
    pattern: re.Pattern[str],
    include: str,
    candidates: list[SessionInfo],
    max_hits: int,
    abort=None,
) -> list[_SessionHits]:
    """Scan every candidate transcript at ``include``; matching sessions only.

    ``max_hits`` caps the excerpts kept per session, never the hit count: the
    header states how many turns matched, so a capped page cannot understate
    it. ``abort`` is honoured between files (never mid-file: one session is
    small) and returns the part collected so far.

    Raises ``ValueError`` for an unknown ``include`` -- ``iter_turns`` validates
    it before touching a file; the caller turns that into an error result.
    """
    found: list[_SessionHits] = []
    for info in candidates:
        if abort is not None and abort.aborted:
            break
        entry = _SessionHits(info)
        for turn in iter_turns(info.path, include):
            match = pattern.search(turn.text)
            if match is None:
                continue
            entry.hits += 1
            if len(entry.excerpts) < max_hits:
                entry.excerpts.append(
                    (
                        turn.index,
                        turn.role,
                        excerpt(turn.text, match.start(), match.end()),
                    )
                )
        if entry.hits:
            found.append(entry)
    return found


def _header(hits: _SessionHits, every_project: bool) -> str:
    """A session line, at column 0: id, hit count, date, messages, fork."""
    info = hits.info
    day = datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d")
    fork = f", fork of {info.derived_from}" if info.derived_from else ""
    project = f"  [{info.project}]" if every_project and info.project else ""
    hit_word = "hit" if hits.hits == 1 else "hits"
    return (
        f"{info.path.stem}  {hits.hits} {hit_word}  "
        f"({day}, {info.messages} msgs{fork}){project}"
    )


def _excerpt_line(index: int, role: str, text: str) -> str:
    """An excerpt line, indented: column 0 is a session line, column 2 a hit."""
    return f"  #{index} {role:<9} {text}"


async def _no_matches(
    pattern: re.Pattern[str],
    include: str,
    candidates: list[SessionInfo],
    capped: bool,
    abort,
) -> ToolResultAgent:
    """Say nothing matched -- and, when it can, name the level that would.

    A miss at ``conversation`` is the common false negative: what a session
    *did* lives in its tool arguments, not in what was typed. Counting at the
    next level costs one more streaming pass (no excerpts, hits only) and turns
    "no matches" into an actionable retry. ``all`` is the widest level, so it
    just says so once.
    """
    wider = _WIDER_LEVEL.get(include)
    if wider is not None:
        found = await asyncio.to_thread(_scan, pattern, wider, candidates, 0, abort)
        if found:
            return text_result(
                f"no matches in {_LEVEL_LABELS[include]}; the same query "
                f"matches {len(found)} session(s) in {_LEVEL_LABELS[wider]} — "
                f'retry with include="{wider}"',
                sessions=0,
                hits=0,
                next_skip=None,
                include=include,
            )
    searched = (
        f"the newest {len(candidates)} saved sessions" if capped
        else f"the {len(candidates)} saved sessions"
    )
    return text_result(
        f"no matches for {pattern.pattern!r} at include={include!r} in {searched}",
        sessions=0,
        hits=0,
        next_skip=None,
        include=include,
    )


async def _search_sessions(
    root: Path, tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    query = args.get("query")
    if not isinstance(query, str) or not query:
        return text_result("query must be a non-empty regular expression", is_error=True)
    try:
        pattern = re.compile(query)
    except re.error as exc:
        return text_result(
            f"invalid regular expression {query!r}: {exc}", is_error=True
        )
    include = str(args.get("include") or "conversation")
    if include not in LEVELS:
        return text_result(
            f"unknown include {include!r}; choose one of {', '.join(LEVELS)}",
            is_error=True,
        )
    raw_skip = args.get("skip")
    if raw_skip is None:
        skip = 0
    else:
        try:
            skip = int(raw_skip)
        except (TypeError, ValueError):
            return text_result(
                f"skip must be a whole number, got {raw_skip!r}", is_error=True
            )
        if skip < 0:
            return text_result("skip must be at least 0", is_error=True)
    raw_hits = args.get("max_hits")
    if raw_hits is None:
        max_hits = DEFAULT_MAX_HITS
    else:
        try:
            max_hits = int(raw_hits)
        except (TypeError, ValueError):
            return text_result(
                f"max_hits must be a whole number, got {raw_hits!r}", is_error=True
            )
        if max_hits < 1:
            return text_result("max_hits must be at least 1", is_error=True)
        max_hits = min(max_hits, MAX_HITS_PER_SESSION)
    every_project = bool(args.get("all", False))
    infos = list_saved_sessions(
        limit=MAX_SEARCH_SESSIONS,
        scope="all" if every_project else "project",
        root=root,
        cache=False,  # read-only tool: never write _index.json
    )
    if not infos:
        return text_result(
            _NO_SESSIONS_ALL if every_project else _NO_SESSIONS_PROJECT,
            sessions=0,
            hits=0,
            next_skip=None,
            include=include,
        )
    candidates = _newest_first(infos)
    # A store bigger than the walk cap would otherwise read as "this is
    # everything": say which slice was searched when the cap bites.
    capped = len(infos) >= MAX_SEARCH_SESSIONS
    try:
        matching = await asyncio.to_thread(
            _scan, pattern, include, candidates, max_hits, abort
        )
    except ValueError as exc:  # unknown include, from iter_turns
        return text_result(str(exc), is_error=True)
    if not matching:
        return await _no_matches(pattern, include, candidates, capped, abort)
    total_hits = sum(entry.hits for entry in matching)
    if skip >= len(matching):
        return text_result(
            f"skip={skip} is past the last of the {len(matching)} matching "
            f"session(s)",
            sessions=len(matching),
            hits=total_hits,
            next_skip=None,
            include=include,
        )
    page = matching[skip : skip + MAX_SESSIONS_PER_PAGE]
    next_skip = (
        skip + MAX_SESSIONS_PER_PAGE
        if len(matching) > skip + MAX_SESSIONS_PER_PAGE
        else None
    )
    lines: list[str] = []
    for entry in page:
        lines.append(_header(entry, every_project))
        lines.extend(_excerpt_line(*item) for item in entry.excerpts)
    if next_skip is not None:
        # Indented like an excerpt on purpose: column 0 is reserved for a
        # session line, which is what the model reads there to page.
        lines.append(
            f"  … {len(matching) - next_skip} more session(s); skip={next_skip}"
        )
    if capped:
        lines.append(
            f"  … only the newest {MAX_SEARCH_SESSIONS} saved sessions were searched"
        )
    body = clip("\n".join(lines), MAX_SEARCH_BODY)[0]
    return text_result(
        body,
        sessions=len(matching),  # every match, not just this page
        hits=total_hits,
        next_skip=next_skip,
        include=include,
    )


def _int_arg(args: dict, name: str, default: int, *, minimum: int | None = None):
    """``(value, problem)`` for one integer argument; ``problem`` is ""."""
    raw = args.get(name)
    if raw is None:
        return default, ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be a whole number, got {raw!r}"
    if minimum is not None and value < minimum:
        return None, f"{name} must be at least {minimum}"
    return value, ""


def _resolve_session(
    root: Path, session_id: str
) -> tuple[SessionInfo | None, str, list[SessionInfo]]:
    """``(session, problem, candidates)`` for one ``id``.

    ``id`` is a *stem*, never a path: it is compared against the ids a listing
    produces, so nothing a caller sends is ever joined onto the store
    directory. ``../../secrets`` or an absolute path therefore matches no
    session -- there is no code path in which that string reaches the
    filesystem.

    Every project is searched regardless of how the caller found the id: a
    search may legitimately point at another project's session, and an id that
    cannot then be read is a false address.
    """
    wanted = session_id.strip()
    infos = list_saved_sessions(
        limit=MAX_SEARCH_SESSIONS,
        scope="all",
        root=root,
        cache=False,  # read-only tool: never write _index.json
    )
    matches = [info for info in infos if info.path.stem == wanted]
    if len(matches) == 1:
        return matches[0], "", infos
    if len(matches) > 1:
        # Two projects holding the same stem. Refuse rather than guess:
        # reading the wrong transcript silently is worse than naming them.
        labelled = ", ".join(
            f"{info.path.stem} [{info.project or 'legacy'}]" for info in matches
        )
        return None, f"session {wanted!r} is ambiguous: {labelled}", infos
    return None, f"no session {wanted!r} in any project", infos


def _read_page(
    path: Path, include: str, offset: int, limit: int
) -> list[tuple[int, str, str]]:
    """``(index, role, text)`` for message numbers ``[offset, offset + limit)``.

    The window is over *message line numbers*, not over rendered turns: a page
    whose messages render nothing at this ``include`` still consumes its
    messages, so ``next_offset`` cannot stall on a tool-call-only stretch.
    Each turn is flattened to one line so a transcript line always starts with
    its own ``#N`` marker.
    """
    end = offset + limit
    page: list[tuple[int, str, str]] = []
    for turn in iter_turns(path, include):
        if turn.index < offset:
            continue
        if turn.index >= end:
            break
        page.append((turn.index, turn.role, " ".join(turn.text.split())))
    return page


async def _read_session(
    root: Path, tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    raw_id = args.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return text_result("id must be a non-empty session id", is_error=True)
    include = str(args.get("include") or "conversation")
    if include not in LEVELS:
        return text_result(
            f"unknown include {include!r}; choose one of {', '.join(LEVELS)}",
            is_error=True,
        )
    offset, problem = _int_arg(args, "offset", 0, minimum=0)
    if problem:
        return text_result(problem, is_error=True)
    limit, problem = _int_arg(args, "limit", DEFAULT_READ_LIMIT, minimum=1)
    if problem:
        return text_result(problem, is_error=True)
    limit = min(limit, MAX_READ_LIMIT)

    info, problem, infos = _resolve_session(root, raw_id)
    if info is None:
        known = ", ".join(sorted(item.path.stem for item in infos)[-10:]) or "(none)"
        return text_result(
            f"{problem}; known ids: {known}", is_error=True, session=raw_id
        )
    total = count_messages(info.path)
    if total == 0:
        return text_result(
            f"session {info.path.stem} has no messages",
            session=info.path.stem,
            messages=0,
            next_offset=None,
        )
    if offset >= total:
        return text_result(
            f"offset {offset} is past the end: {info.path.stem} has {total} "
            f"message(s) (use offset 0..{total - 1})",
            is_error=True,
            session=info.path.stem,
        )
    page = _read_page(info.path, include, offset, limit)
    next_offset = min(offset + limit, total)
    page_end = next_offset if next_offset < total else None
    window = f"#{offset}..#{next_offset - 1}"
    if not page:
        # Every message in the window rendered nothing at this level (a stretch
        # of tool calls read as conversation text). Not an error, and not a dead
        # end: name where the content is and how to continue.
        hint = "" if include == "all" else ', the same messages at include="all"'
        return text_result(
            f"no text at include={include!r} in {window} of {info.path.stem}{hint}",
            session=info.path.stem,
            messages=0,
            next_offset=page_end,
        )
    lines = [f"{info.path.stem}  {window} of {total}  (include={include})"]
    lines.extend(f"  #{index} {role:<9} {text}" for index, role, text in page)
    if page_end is not None:
        lines.append(f"  … next_offset={page_end}")
    body = clip("\n".join(lines), MAX_READ_BODY)[0]
    return text_result(
        body,
        session=info.path.stem,
        messages=len(page),
        next_offset=page_end,
    )


def build_session_tools(root: Path) -> list[AgentTool]:
    """The three session tools, pinned to ``root``'s project.

    ``root`` is the Runtime's project root (``runtime.root``), not the process
    cwd: the same Runtime answers correctly for a worktree, an RPC slot or a
    child agent whose cwd differs. Order is the prompt order of the blocks.
    """
    pinned = Path(root)
    return [
        AgentTool(
            name="list_sessions",
            description=tool_description("list_sessions"),
            parameters=_LIST_PARAMETERS,
            execute=partial(_list_sessions, pinned),
            approval="read",
            timeout_s=30.0,
        ),
        AgentTool(
            name="search_sessions",
            description=tool_description("search_sessions"),
            parameters=_SEARCH_PARAMETERS,
            execute=partial(_search_sessions, pinned),
            approval="read",
            # The one tool that reads every project's transcripts. A declared
            # budget is enforceable here where local tools normally refuse one:
            # the scan runs in ``asyncio.to_thread``, so the loop's
            # ``wait_for`` can return control at the await even though the
            # thread is blocking. 60 s is ~400x the measured full store scan
            # (103 sessions / 35 MB in 56-145 ms), leaves room for the
            # escalation pass (a second scan) and a machine far slower or
            # larger than this one, and still bounds a stalled network mount.
            timeout_s=SEARCH_TIMEOUT_S,
        ),
        AgentTool(
            name="read_session",
            description=tool_description("read_session"),
            parameters=_READ_PARAMETERS,
            execute=partial(_read_session, pinned),
            approval="read",
        ),
    ]
