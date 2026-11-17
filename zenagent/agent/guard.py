"""Loop-guard heuristics: detecting a model stuck repeating itself.

Two guards live here because they answer the same question from different
evidence:

* :data:`LOOP_GUARD_TEXT` — the same call with the same arguments repeated
  several times (byte-identical).
* :data:`STALL_GUARD_TEXT` — the model keeps asking for evidence this run
  already has: the same file range, the same grep, the same URL. Wording and
  flags drift, so the repeat guard misses it, yet no call can return anything
  new.

The stall guard is deliberately narrow. It records read-shaped calls only, so a
poll, a test run or an edit never counts as a repeat, and a write/edit clears
the coverage of the file it touched. A batch is stalled only when every
read-shaped call in it is already covered, and the notice fires after
consecutive stalled batches: one re-read to double-check is progress. The
ledger sees calls, not the filesystem, so a file changed by another process
looks stale — which is another reason the notice is advisory, needs two rounds,
and fires at most once per run.

Kept out of :mod:`zenagent.agent.loop` so the loop reads as a turn machine. The
corrective message it injects is model-facing only: it is never persisted, never
rendered as a user turn, and never counted by fork/retry — hence the prefix
checks the UI uses to filter it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from zenagent.types import ToolCall, UserMessage

__all__ = [
    "LOOP_GUARD_PREFIX",
    "LOOP_GUARD_TEXT",
    "LOOP_GUARD_THRESHOLD",
    "STALL_GUARD_BATCHES",
    "STALL_GUARD_PREFIX",
    "STALL_GUARD_TEXT",
    "EvidenceLedger",
    "Probe",
    "calls_signature",
    "is_loop_guard_message",
    "is_poll_call",
    "is_stall_guard_message",
    "probe_for",
]

LOOP_GUARD_THRESHOLD = 5

#: Prefix of the synthetic, non-persisted corrective message (the UI filters it).
LOOP_GUARD_PREFIX = "[tool-loop guard]"

LOOP_GUARD_TEXT = (
    LOOP_GUARD_PREFIX + " The same tool call has repeated {n} times with identical "
    "arguments and produced no new progress. Stop repeating it: change the "
    "arguments, use a different tool, or explain to the user what is blocking you."
)

#: Consecutive rounds whose every read-shaped call was already satisfied. One
#: re-read to double-check after an edit is normal; two rounds of nothing new
#: means the model is re-asking instead of moving.
STALL_GUARD_BATCHES = 2

#: Prefix of the synthetic, non-persisted evidence-stall corrective.
STALL_GUARD_PREFIX = "[evidence-loop guard]"

STALL_GUARD_TEXT = (
    STALL_GUARD_PREFIX + " The last {n} tool rounds asked only for evidence this "
    "run already has ({sample}). Re-reading it cannot produce a different answer: "
    "use what you already read, change the target, or state what is still missing."
)

#: Commands that are legitimate to repeat: they observe a changing state.
POLL_COMMAND = re.compile(r"^\s*(sleep|wait|tail|ps|pgrep|top|jobs)\b")

#: ``read_file``'s page size when the call does not name one. Mirrors
#: ``zenagent.tools.fs.DEFAULT_READ_LIMIT`` at the conservative end: the kernel
#: guard must not import a tool module (that is an import cycle), and
#: under-counting a page only ever makes the guard quieter.
_ASSUMED_READ_LINES = 1000

#: Sentinel end for "to the end of the file" (``cat``, paging forwards).
_EOF = 1 << 60

_MUTATING_TOOLS = frozenset({"write_file", "edit_file"})

_SED_RANGE = re.compile(r"sed\s+-n\s+['\"]?(\d+),(\d+)p['\"]?\s+(\S+)")
_CAT = re.compile(r"(?:^|&&|;|\|)\s*cat\s+(\S+)")
_HEAD = re.compile(
    r"(?:^|&&|;|\|)\s*head\s+(?:(?:-n\s*(\d+)|-(\d+))\s+)?(\S+)"
)
_GREP_BASH = re.compile(r"\bgrep\b[^|;]*?['\"]([^'\"]+)['\"]([^|;]*)")
_GIT_SHOW = re.compile(r"\bgit\s+show\s+([^|;&]+)")


def is_loop_guard_message(message: object) -> bool:
    """Whether ``message`` is the synthetic loop-guard corrective."""
    return isinstance(message, UserMessage) and str(
        getattr(message, "content", "")
    ).startswith(LOOP_GUARD_PREFIX)


def is_stall_guard_message(message: object) -> bool:
    """Whether ``message`` is the synthetic evidence-stall corrective."""
    return isinstance(message, UserMessage) and str(
        getattr(message, "content", "")
    ).startswith(STALL_GUARD_PREFIX)


def is_poll_call(call: ToolCall) -> bool:
    """Whether this call is a pure wait/observe poll (exempt from the guard)."""
    if call.name == "wait":
        # Waiting for a background job is exactly the legitimate long poll.
        return True
    if call.name != "bash":
        return False
    return bool(POLL_COMMAND.match(str(call.arguments.get("command", ""))))


def calls_signature(calls: list[ToolCall]) -> tuple:
    """Stable signature of a tool batch, for detecting identical repeats."""
    return tuple(
        sorted(
            (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
            for call in calls
        )
    )


# ---------------------------------------------------------------------------
# Evidence stall guard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """One request for evidence, normalized so repeats compare equal.

    ``kind`` is ``"range"`` (a file interval, ``start``/``end`` are 0-based and
    end-exclusive) or ``"query"`` (a grep/glob/fetch/search identity carried by
    ``target``). ``label`` is what a corrective quotes back at the model.
    """

    kind: str
    target: str
    start: int = 0
    end: int = _EOF
    label: str = ""


def _norm_path(raw: object) -> str:
    """A path as the coverage key: unquoted, forward slashes, no ``./``."""
    if not isinstance(raw, str):
        return ""
    path = raw.strip().strip("'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/") or path


def _range_probe(
    raw_path: object, start: int, end: int, *, label: str = ""
) -> Probe | None:
    path = _norm_path(raw_path)
    if not path or path.startswith((">", "<", "-")) or path in {"&&", ";", "|"}:
        # ``cat > file`` writes, ``cat <<EOF`` is stdin, ``cat -n file`` has no
        # path in that slot.
        return None
    start = max(start, 0)
    end = max(end, start)
    text = label or f"{path}:{start}-{'EOF' if end == _EOF else end}"
    return Probe("range", path, start, end, text)


def probe_for(call: ToolCall) -> Probe | None:
    """The evidence ``call`` asks for, or ``None`` when it is not a read."""
    args = call.arguments if isinstance(call.arguments, dict) else {}
    name = call.name

    if name == "read_file":
        try:
            offset = int(args.get("offset") or 0)
            limit = int(args.get("limit") or _ASSUMED_READ_LINES)
        except (TypeError, ValueError):
            return None
        return _range_probe(args.get("path"), offset, offset + max(limit, 0))

    if name == "grep":
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return None
        root = _norm_path(args.get("path") or ".")
        return Probe(
            "query",
            f"grep\x00{pattern}\x00{root}",
            label=f"grep {pattern!r} in {root}",
        )

    if name == "glob":
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return None
        root = _norm_path(args.get("path") or ".")
        return Probe(
            "query", f"glob\x00{pattern}\x00{root}", label=f"glob {pattern!r} in {root}"
        )

    if name == "web_search":
        query = args.get("query")
        if isinstance(query, str) and query:
            return Probe("query", f"search\x00{query}", label=f"search {query!r}")
        return None

    if name == "fetch":
        url = args.get("url")
        if isinstance(url, str) and url:
            return Probe("query", f"fetch\x00{url}", label=f"fetch {url}")
        return None

    if name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command:
            return _shell_probe(command)
        return None

    return None


#: Grep flags whose value is a separate token (``-A 5``); the value is not a path.
_GREP_VALUE_FLAGS = frozenset({"-A", "-B", "-C", "-m", "-e", "-f", "--include"})


def _grep_root(rest: str) -> str:
    """The first non-flag token after a grep pattern: its search root."""
    skip = False
    for token in rest.split():
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = token in _GREP_VALUE_FLAGS
            continue
        if token in {"&&", ";", "|", ">"}:
            break
        return _norm_path(token)
    return ""


def _shell_probe(command: str) -> Probe | None:
    """Recognize the read-shaped shell idioms; ignore everything else.

    Anything a shell command might *change* (tests, edits, checkouts) is not a
    probe: only bytes the model asked to see count as evidence.
    """
    match = _SED_RANGE.search(command)
    if match:
        return _range_probe(
            match.group(3), int(match.group(1)) - 1, int(match.group(2))
        )

    match = _CAT.search(command)
    if match:
        return _range_probe(match.group(1), 0, _EOF)

    match = _HEAD.search(command)
    if match and not match.group(3).startswith("-"):
        lines = int(match.group(1) or match.group(2) or 10)
        return _range_probe(match.group(3), 0, lines)

    match = _GREP_BASH.search(command)
    if match:
        pattern = match.group(1)
        root = _grep_root(match.group(2))
        if root:
            return Probe(
                "query",
                f"grep\x00{pattern}\x00{root}",
                label=f"grep {pattern!r} in {root}",
            )
        return None

    match = _GIT_SHOW.search(command)
    if match:
        ref = " ".join(match.group(1).split())
        # Only commits are immutable within a run; HEAD and branches move, so
        # re-showing them can legitimately return new bytes.
        if re.match(r"^[0-9a-f]{7,40}(?::.*)?$", ref):
            return Probe("query", f"git-show\x00{ref}", label=f"git show {ref[:60]}")
    return None


class EvidenceLedger:
    """Which evidence this run has already pulled, to catch re-reading.

    Per run, not per session: a new user turn may legitimately ask the model to
    look at the same file again, and the budget the guard protects is the
    current run's.
    """

    def __init__(self) -> None:
        self._spans: dict[str, list[tuple[int, int]]] = {}
        self._queries: set[str] = set()

    def observe(self, calls: list[ToolCall]) -> tuple[bool, list[str]]:
        """Record one tool batch; return ``(stalled, repeat_labels)``.

        A batch with no read-shaped call is never stalled. A batch is stalled
        only when every read-shaped call in it was already covered — one new
        page or one new query makes the round progress.
        """
        probes: list[Probe] = []
        for call in calls:
            if call.name in _MUTATING_TOOLS:
                # The file changed underneath; reading it again is new evidence.
                args = call.arguments if isinstance(call.arguments, dict) else {}
                self._spans.pop(_norm_path(args.get("path")), None)
                continue
            probe = probe_for(call)
            if probe is not None:
                probes.append(probe)
        if not probes:
            return False, []

        repeated: list[str] = []
        for probe in probes:
            if self._covered(probe):
                repeated.append(probe.label)
            else:
                self._remember(probe)
        return len(repeated) == len(probes), repeated

    # -- coverage bookkeeping ------------------------------------------------

    def _covered(self, probe: Probe) -> bool:
        if probe.kind == "query":
            return probe.target in self._queries
        covered = 0
        for start, end in self._spans.get(probe.target, ()):
            overlap = min(end, probe.end) - max(start, probe.start)
            if overlap > 0:
                covered += overlap
        return covered >= probe.end - probe.start

    def _remember(self, probe: Probe) -> None:
        if probe.kind == "query":
            self._queries.add(probe.target)
            return
        spans = self._spans.setdefault(probe.target, [])
        spans.append((probe.start, probe.end))
        spans.sort()
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self._spans[probe.target] = merged
