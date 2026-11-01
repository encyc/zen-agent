"""Hand-slip risk model: where a write lands and which commands lose work.

The kernel has no permission concept on purpose: the harness owns confirmation.
This module is the harness-side *risk model* — it answers what is worth
flagging, and leaves what to do about it (confirm, refuse, honour an explicit
flag) to :mod:`zenagent.app.permissions`.

Two questions, both best-effort:

* :func:`classify_write_path` — does a file write land inside the workspace,
  outside it, or on a credential path a model must never rewrite?
* :func:`destructive_command_reason` — does a shell command look like the kind
  that loses work even when the machine survives (history rewrites, recursive
  deletes, raw overwrites)?

Shell commands cannot be parsed reliably (``cd``, variables and subshells all
defeat static analysis), so the command layer only catches common hand-slips;
the real backstop is OS-level isolation. Paths are always ``expanduser()`` +
``resolve()`` so a symlink cannot smuggle a write out of the workspace.

Protected locations are computed on every call, never at import time, so
``HOME`` monkeypatching in tests and in the process environment is honoured.
"""

from __future__ import annotations

import fnmatch
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from zenagent import project

__all__ = [
    "GuardPolicy",
    "PathScope",
    "RiskKind",
    "Verdict",
    "classify_write_path",
    "default_allow_outside",
    "destructive_command_reason",
    "workspace_root",
]

#: Where a write target sits relative to the workspace fence.
PathScope = Literal["inside", "outside", "protected"]
#: Risk classes raised for a *confirmable* action (never the catastrophic tier).
RiskKind = Literal["outside", "protected", "destructive"]

#: Credential directories under ``$HOME`` a coding task never needs to write.
_PROTECTED_HOME_DIRS = (
    ".zenagent",
    ".ssh",
    ".aws",
    ".azure",
    ".kube",
    ".config/gcloud",
)

#: Individual credential files under ``$HOME``.
_PROTECTED_HOME_FILES = (
    ".docker/config.json",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
)

#: Private-key / client-certificate file names, wherever they live.
_PROTECTED_NAME_PATTERNS = (
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.p12",
    "*.pfx",
)

#: Characters that make a shell expand a path before ``rm`` ever sees it.
_GLOB_CHARS = frozenset("*?[")

#: ``(pattern, reason)`` for commands that are confirmable-destructive.
#: Deliberately narrow and table-driven: everyday commands must never match.
_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:curl|wget)\b[^;&|]*\|\s*(?:sudo\s+)?"
            r"(?:sh|bash|zsh|python[\d.]*|perl|ruby)\b"
        ),
        "download piped into an interpreter",
    ),
    (
        re.compile(r"\bgit\s+reset\s+--hard\b"),
        "git reset --hard discards uncommitted work",
    ),
    (
        re.compile(r"\bgit\s+clean\b[^;&|]*\s(?:--force\b|-[a-z]*f[a-z]*\b)"),
        "git clean removes untracked files",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--\s+\.(?:\s|$)"),
        "git checkout -- . discards uncommitted changes",
    ),
    (
        re.compile(r"\bgit\s+restore\s+\.(?:\s|$)"),
        "git restore . discards uncommitted changes",
    ),
    (
        re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b"),
        "git stash drop/clear discards stashed work",
    ),
    (
        re.compile(r"\bgit\s+push\b[^;&|]*\s--force(?!-with-lease)\b"),
        "git push --force rewrites remote history",
    ),
    (
        re.compile(r"\bfind\b[^;&|]*(?:\s-delete\b|\s-exec\s+rm\b)"),
        "find deleting matched files",
    ),
    (re.compile(r"\btruncate\b[^;&|]*\s-s\s+0\b"), "truncate -s 0 empties a file"),
    (re.compile(r"\bshred\b"), "shred overwrites a file beyond recovery"),
    (
        re.compile(r"\bdd\b[^;&|]*\bof\s*=\s*(?!/dev/)"),
        "dd writing over a file",
    ),
)


@dataclass(frozen=True)
class GuardPolicy:
    """Resolved guardrails for one run.

    ``workspace`` is the resolved project root; ``allow_outside`` entries are
    resolved lazily by :func:`classify_write_path` because callers may hand in
    unresolved paths. The ``allow_*`` flags are the explicit escape hatches:
    they can only be set by CLI flags or the global config, never by a project
    layer.
    """

    workspace: Path
    allow_outside: tuple[Path, ...] = ()
    allow_protected: bool = False
    allow_destructive: bool = False

    @classmethod
    def from_config(
        cls,
        config,
        *,
        cwd: Path | str | None = None,
        allow_outside: Iterable[Path | str] = (),
        allow_destructive: bool = False,
        allow_protected: bool = False,
    ) -> GuardPolicy:
        """Merge the config file, CLI flags and the temp-dir defaults.

        ``config`` is a duck-typed :class:`zenagent.config.UserConfig` (this
        module must not import the app config layer); its ``safety_*`` fields
        are already absolute after parsing. Booleans from the two sources are
        OR-ed — a flag can widen, never narrow, what the config allows.
        """
        candidates = [
            *config.safety_allow_outside,
            *allow_outside,
            *default_allow_outside(),
        ]
        return cls(
            workspace=workspace_root(cwd),
            allow_outside=_dedupe(_resolved(entry) for entry in candidates),
            allow_protected=bool(config.safety_allow_protected or allow_protected),
            allow_destructive=bool(
                config.safety_allow_destructive or allow_destructive
            ),
        )


@dataclass
class Verdict:
    """Outcome of assessing one tool call; produced by the permission gate."""

    kind: Literal["allow", "confirm", "refuse"]
    reason: str
    risk: RiskKind | None = None
    target: str | None = None


def _resolved(path: Path | str) -> Path:
    """Expand ``~`` and follow symlinks (the one path-resolution rule)."""
    return Path(path).expanduser().resolve()


def _dedupe(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Preserve order, drop repeats."""
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return tuple(unique)


def _is_under(path: Path, roots: Iterable[Path]) -> bool:
    """Whether ``path`` equals or is nested anywhere under one of ``roots``."""
    return any(path.is_relative_to(root) for root in roots)


def _protected_roots() -> tuple[Path, ...]:
    """Credential locations under the *current* home, resolved per call."""
    home = Path.home()
    return tuple(
        _resolved(home / relative)
        for relative in (*_PROTECTED_HOME_DIRS, *_PROTECTED_HOME_FILES)
    )


def _is_protected(path: Path) -> bool:
    """Whether ``path`` is a machine-wide credential location or private key."""
    if any(
        fnmatch.fnmatchcase(path.name, pattern) for pattern in _PROTECTED_NAME_PATTERNS
    ):
        return True
    return _is_under(path, _protected_roots())


def workspace_root(cwd: Path | str | None = None) -> Path:
    """Resolved project root for ``cwd`` (nearest ``.git``, else ``cwd``)."""
    return project.find_project_root(cwd).resolve()


def default_allow_outside() -> tuple[Path, ...]:
    """System temp directories every run may write to without a confirmation."""
    return _dedupe(_resolved(path) for path in (tempfile.gettempdir(), Path("/tmp")))


def classify_write_path(raw: str | Path, policy: GuardPolicy) -> tuple[PathScope, Path]:
    """Classify a write target as ``inside``/``outside``/``protected``.

    The target and every policy root are resolved first: a symlink that points
    out of the workspace is classified by where it really lands. Protected
    wins over inside (even if the workspace or an allow entry covers it).
    """
    target = _resolved(raw)
    if not policy.allow_protected and _is_protected(target):
        return "protected", target
    allowed = (
        _resolved(policy.workspace),
        *(_resolved(path) for path in policy.allow_outside),
    )
    if _is_under(target, allowed):
        return "inside", target
    return "outside", target


def _rm_is_recursive(flags: Iterable[str]) -> bool:
    """Whether an ``rm`` flag set asks for a recursive delete."""
    for flag in flags:
        if flag.lower() == "--recursive":
            return True
        # Short flags combine freely (``-rf``), long ones (``--force``) do not.
        if not flag.startswith("--") and "r" in flag.lower():
            return True
    return False


def _recursive_rm_reason(command: str, policy: GuardPolicy) -> str | None:
    """Why a recursive ``rm`` reaches outside its normal sandbox, or ``None``.

    Normal targets are directories *inside* the workspace, the process cwd or
    an ``allow_outside`` entry (``rm -rf ./build``). Everything else — the cwd
    itself, the workspace root or an ancestor of it, a protected path, a glob
    the shell expands first — is confirmable.

    Token case is preserved here (only the command names are compared
    case-insensitively): lowercasing the whole command would corrupt absolute
    paths such as ``/Users/Bob/src`` on a case-sensitive filesystem.
    """
    cwd = Path.cwd()
    workspace = _resolved(policy.workspace)
    allowed = (cwd, workspace, *(_resolved(path) for path in policy.allow_outside))
    for segment in re.split(r"[;&|\n]", command):
        tokens = segment.split()
        if tokens and tokens[0].lower() == "sudo":
            tokens = tokens[1:]
        if not tokens or tokens[0].lower() != "rm":
            continue
        flags = [token for token in tokens[1:] if token.startswith("-")]
        if not _rm_is_recursive(flags):
            continue
        for target in (token for token in tokens[1:] if not token.startswith("-")):
            if any(char in target for char in _GLOB_CHARS):
                return f"recursive delete with a glob target ({target!r})"
            resolved = _resolved(target)
            if resolved == cwd:
                return "recursive delete of the current directory"
            if resolved == workspace or workspace.is_relative_to(resolved):
                return "recursive delete of the workspace"
            if _is_protected(resolved):
                return f"recursive delete under a protected path ({target!r})"
            if not _is_under(resolved, allowed):
                return f"recursive delete outside the workspace ({target!r})"
    return None


def destructive_command_reason(command: str, policy: GuardPolicy) -> str | None:
    """Return why ``command`` is confirmable-destructive, or ``None``.

    This is the tier below :func:`zenagent.app.permissions.dangerous_command_reason`:
    the machine survives but work can be lost, so the gate asks instead of
    refusing. ``policy.allow_destructive`` turns the whole layer off.
    """
    if policy.allow_destructive:
        return None
    compacted = " ".join(command.split())
    normalized = compacted.lower()
    if not normalized:
        return None
    for pattern, reason in _DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return reason
    if normalized.split()[0] == "sudo":
        return "privilege escalation with sudo"
    return _recursive_rm_reason(compacted, policy)
